"""Tolerant anchor matching and a safety check for the core file patches.

Shared by every ``knx_*_patch.py`` script in this directory. Added in 0.8.1, after
Home Assistant 2026.10 broke both patches at once.

Why anchors are matched loosely
-------------------------------
Until 0.8.0 an anchor was an exact substring. Most upstream changes that broke one
were cosmetic: ``vol.Optional`` became ``probatio.Optional`` (2026.9/2026.10), a
call was re-wrapped by the formatter, a comment changed. None of that moves the
place where our lines belong.

Matching therefore works on **tokens**, not characters:

* whitespace, line breaks and comments are ignored,
* module aliases that Home Assistant swapped in place count as equal
  (``vol`` == ``probatio``), and inserted code is written with the alias the
  target file actually uses,
* only the lines that change must match completely. The lines around them are
  context: as much of it as possible is used, but a hunk still applies when part
  of the context drifted - as long as enough of it matches on **both** sides and
  the best position is **unique**. A tie writes nothing.

Why matching alone is not enough
--------------------------------
A loose anchor finds the place, it cannot tell whether our code still fits the
code around it. 2026.10 is the example: the UI entities receive a typed
``KnxEntityData`` instead of a dict. Every anchor of the climate patch still
matched, yet the inserted ``config[CONF_ENTITY][CONF_NAME]`` would have failed when
the entity was created - after the restart, with the KNX integration down.

So every patched file is checked **before** anything is written:

1. it must compile,
2. it must not use a name that is defined nowhere in the module - unless the
   unpatched file already did (``CONF_ENTITY`` and ``DOMAIN`` vanished from the
   imports of ``cover.py`` in 2026.10, which is exactly what this catches),
3. after writing, the modules are imported in a separate interpreter when the
   installation allows it. If that fails, the originals are put back.

A patch that fails any of these reports ``incompatible`` and writes nothing.
"""

from __future__ import annotations

import ast
import builtins
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Module aliases Home Assistant replaced in place. Both spellings are one token
# for matching; the first entry is the canonical form.
ALIASES: dict[str, str] = {"vol": "probatio"}
_IMPORT_LINE = {
    "vol": re.compile(r"^import voluptuous as vol\b", re.M),
    "probatio": re.compile(r"^import probatio\b", re.M),
}

# Minimum context (in tokens) that has to match on each side of a change when the
# full context does not. Small enough to survive a reformatted neighbour, large
# enough not to find a generic line like ``),`` somewhere else.
MIN_SIDE = 4
MIN_TOTAL = 12

_TOKEN = re.compile(
    r'(?P<str>[rbuRBUfF]{0,2}("""|\'\'\'|"|\')(?:\\.|(?!\2).)*?\2)'
    r"|(?P<comment>#[^\n]*)"
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<num>\d[\d_]*(?:\.\d+)?)"
    r"|(?P<op>\S)",
    re.S,
)

STATE_APPLIED = "applied"
STATE_OPEN = "open"
STATE_GONE = "gone"


@dataclass(frozen=True)
class Tok:
    """One token: normalised text plus its character span."""

    text: str
    start: int
    end: int


def tokens(text: str) -> list[Tok]:
    """Tokenise Python source, dropping comments and whitespace."""
    out: list[Tok] = []
    for m in _TOKEN.finditer(text):
        if m.lastgroup == "comment":
            continue
        t = m.group(0)
        out.append(Tok(ALIASES.get(t, t), m.start(), m.end()))
    return out


def _split_lines(old: str, new: str) -> tuple[str, str, str, str]:
    """Split a pair into common head, old middle, new middle, common tail (whole lines)."""
    a = old.splitlines(keepends=True)
    b = new.splitlines(keepends=True)
    i = 0
    def same(x: str, y: str) -> bool:  # a hunk may end mid-line, without "\n"
        return x.rstrip("\n") == y.rstrip("\n")

    while i < len(a) and i < len(b) and same(a[i], b[i]):
        i += 1
    j = 0
    while j < len(a) - i and j < len(b) - i and same(a[-1 - j], b[-1 - j]):
        j += 1
    head = "".join(a[:i])
    tail = "".join(a[len(a) - j :]) if j else ""
    return head, "".join(a[i : len(a) - j]), "".join(b[i : len(b) - j]), tail


@dataclass
class Hit:
    """Where a hunk's middle sits in the target, and how well the context matched."""

    start: int  # character offset where the middle starts (a line start)
    end: int  # character offset after the middle (after its last newline)
    score: int
    full: bool


def _line_start(text: str, pos: int) -> int:
    return text.rfind("\n", 0, pos) + 1


def _line_end(text: str, pos: int) -> int:
    nl = text.find("\n", pos)
    return len(text) if nl == -1 else nl + 1


def _first_on_line(text: str, tok: Tok) -> bool:
    return text[_line_start(text, tok.start) : tok.start].strip() == ""


def locate(text: str, head: str, middle: str, tail: str) -> Hit | str:
    """Find ``middle`` between ``head`` and ``tail`` in ``text``.

    Returns a :class:`Hit`, or a reason string when there is no unique place.
    """
    tt = tokens(text)
    th, tm, ts = tokens(head), tokens(middle), tokens(tail)
    words = [t.text for t in tt]
    need_h = min(len(th), MIN_SIDE)
    need_t = min(len(ts), MIN_SIDE)
    need = min(MIN_TOTAL, len(th) + len(ts))

    def back(end: int) -> int:  # tokens of head matching right before index `end`
        n = 0
        while n < len(th) and end - 1 - n >= 0 and words[end - 1 - n] == th[-1 - n].text:
            n += 1
        return n

    def fwd(start: int) -> int:  # tokens of tail matching from index `start`
        n = 0
        while n < len(ts) and start + n < len(words) and words[start + n] == ts[n].text:
            n += 1
        return n

    cands: list[tuple[int, int, int, int]] = []  # (score, i, j, h)
    if tm:
        mw = [t.text for t in tm]
        for i in range(len(words) - len(mw) + 1):
            if words[i : i + len(mw)] == mw:
                j = i + len(mw)
                h, t = back(i), fwd(j)
                cands.append((h + t, i, j, h))
    else:
        for i in range(len(words) + 1):
            # a pure insertion goes between two lines, never into one
            if i < len(words) and not _first_on_line(text, tt[i]):
                continue
            h, t = back(i), fwd(i)
            if h == 0 and t == 0:
                continue
            cands.append((h + t, i, i, h))
    if not cands:
        return "changed lines not found" if tm else "context not found"
    cands.sort(key=lambda c: c[0], reverse=True)
    best = cands[0]
    if len(cands) > 1 and cands[1][0] == best[0]:
        return f"ambiguous ({sum(1 for c in cands if c[0] == best[0])} equally good places)"
    score, i, j, h = best
    t = score - h
    if h < need_h or t < need_t or score < need:
        return f"too little context matches (before {h}/{len(th)}, after {t}/{len(ts)})"
    if tm:
        start = _line_start(text, tt[i].start)
        end = _line_end(text, tt[j - 1].end - 1)
    elif i < len(tt):
        start = end = _line_start(text, tt[i].start)
    else:
        start = end = len(text) if text.endswith("\n") else _line_end(text, len(text))
    return Hit(start, end, score, score == len(th) + len(ts))


def _alias_in(text: str) -> str | None:
    for alias, rx in _IMPORT_LINE.items():
        if rx.search(text):
            return alias
    return None


def adapt(code: str, target: str) -> str:
    """Write inserted code with the module alias the target file imports."""
    have = _alias_in(target)
    if have is None:
        return code
    for alias in _IMPORT_LINE:
        if alias != have:
            code = re.sub(rf"\b{alias}\.", f"{have}.", code)
    return code


def _contains(text: str, snippet: str) -> bool:
    """Whether ``snippet`` occurs in ``text`` as a token sequence."""
    words = [t.text for t in tokens(text)]
    want = [t.text for t in tokens(snippet)]
    return any(words[i : i + len(want)] == want for i in range(len(words) - len(want) + 1))


def variants(hunk: object, text: str | None = None) -> list[tuple[str, str]]:
    """The alternatives of a hunk that fit ``text``.

    A hunk is ``[old, new]``, or a list of alternatives. An alternative is
    ``[old, new]`` or ``{"old", "new", "requires": [...]}`` - ``requires`` names
    code that must exist somewhere in the file. It separates two versions whose
    anchors look the same but whose surroundings differ (2026.10:
    ``config.entity.xknx_name`` instead of ``config[CONF_ENTITY][CONF_NAME]``),
    which context matching alone must not decide.
    """
    if (
        isinstance(hunk, (list, tuple))
        and len(hunk) == 2
        and all(isinstance(x, str) for x in hunk)
    ):
        return [(hunk[0], hunk[1])]
    out = []
    for alt in hunk:  # type: ignore[union-attr]
        if isinstance(alt, dict):
            if text is not None and not all(_contains(text, r) for r in alt.get("requires", [])):
                continue
            out.append((alt["old"], alt["new"]))
        else:
            out.append((alt[0], alt[1]))
    return out


def hunk_state(text: str, hunk: object) -> tuple[str, tuple[str, str] | None]:
    """``applied``, ``open`` (a variant's anchor is there) or ``gone``."""
    open_variant = None
    for old, new in variants(hunk, text):
        head, mid_old, mid_new, tail = _split_lines(old, new)
        if isinstance(locate(text, head, adapt(mid_new, text), tail), Hit):
            return STATE_APPLIED, (old, new)
        if open_variant is None and isinstance(locate(text, head, mid_old, tail), Hit):
            open_variant = (old, new)
    return (STATE_OPEN, open_variant) if open_variant else (STATE_GONE, None)


def apply_hunks(text: str, hunks: list[object]) -> tuple[str, list[str]]:
    """Apply all hunks to ``text``. Returns (new text, problems); nothing on problems."""
    problems: list[str] = []
    out = text
    for n, hunk in enumerate(hunks):
        state, variant = hunk_state(out, hunk)
        if state == STATE_APPLIED:
            continue
        if variant is None:
            reasons = []
            fitting = variants(hunk, out)
            if not fitting:
                problems.append(f"hunk #{n}: no variant fits this version (requires not met)")
                continue
            for old, new in fitting:
                head, mid_old, _mid_new, tail = _split_lines(old, new)
                r = locate(out, head, mid_old, tail)
                first = (mid_old or tail or head).strip().splitlines()[:1]
                reasons.append(f"{r} — {first[0][:70] if first else '?'}")
            problems.append(f"hunk #{n}: " + " | ".join(reasons))
            continue
        old, new = variant
        head, mid_old, mid_new, tail = _split_lines(old, new)
        hit = locate(out, head, mid_old, tail)
        assert isinstance(hit, Hit)
        code = adapt(mid_new, out)
        if code and not code.endswith("\n"):
            code += "\n"  # the span always covers whole lines
        out = out[: hit.start] + code + out[hit.end :]
    return (text, problems) if problems else (out, [])


# --- safety check -----------------------------------------------------------
_BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__", "__package__", "__path__", "__builtins__", "__loader__", "__annotations__", "__class__"}


def _bound_and_used(tree: ast.AST) -> tuple[set[str], set[str]]:
    bound: set[str] = set()
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            (used if isinstance(node.ctx, ast.Load) else bound).add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bound.add(node.rest)
        elif hasattr(ast, "TypeVar") and isinstance(node, ast.TypeVar):
            bound.add(node.name)
    return bound, used


def undefined_names(source: str) -> set[str]:
    """Names that are read but bound nowhere in the module (coarse, scope-free)."""
    bound, used = _bound_and_used(ast.parse(source))
    return used - bound - _BUILTINS


def check_source(rel: str, before: str, after: str) -> list[str]:
    """Problems the patched file would have, compared with the unpatched one."""
    if not rel.endswith(".py"):
        return []
    try:
        compile(after, rel, "exec")
    except SyntaxError as err:
        return [f"{rel}: does not compile after patching ({err.msg}, line {err.lineno})"]
    new = undefined_names(after) - undefined_names(before)
    if new:
        return [f"{rel}: patched code uses names this file no longer defines: {', '.join(sorted(new))}"]
    return []


def import_probe(root: Path, rels: list[str]) -> tuple[bool | None, str]:
    """Import the patched modules in a fresh interpreter.

    ``None`` when the installation cannot import Home Assistant at all (a bare
    core checkout without dependencies) - then the static check above is all
    there is.
    """
    mods = [
        "homeassistant.components.knx." + r[:-3].replace("/", ".")
        for r in rels
        if r.endswith(".py")
    ]
    if not mods:
        return True, "nothing to import"
    code = (
        "import sys, importlib\n"
        f"sys.path.insert(0, {str(root)!r})\n"
        "try:\n"
        "    import homeassistant.const, xknx\n"
        "except Exception:\n"
        "    print('SKIP'); raise SystemExit(0)\n"
        f"for m in {mods!r}:\n"
        "    importlib.import_module(m)\n"
        "print('OK')\n"
    )
    try:
        res = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        return False, "import probe timed out"
    out = (res.stdout + res.stderr).strip()
    if res.returncode == 0 and out.endswith("SKIP"):
        return None, "import probe skipped (Home Assistant not importable here)"
    if res.returncode == 0 and out.endswith("OK"):
        return True, "import probe ok"
    return False, "import probe failed: " + out[-400:]
