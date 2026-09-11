#!/usr/bin/env python3
"""KNX cover: berechnete Position aktiv senden ("Rueckmeldung aktiv senden").

Fuer Aktoren, die die Position NICHT selbst melden, ist der TravelCalculator von
Home Assistant die einzige Quelle. Damit Displays (Glastaster) den Fahrtfortschritt
anzeigen koennen, muss HA den Wert senden. Bisher geht das nur ueber `expose` — mit
handgeschriebener Invertierung, eigener DPT-Wahl und der Gefahr, auf die eigene
`ga_position_state` zu schreiben (nachgewiesener Loop, s. ../CLAUDE.md).

Dieser Patch ergaenzt einen **Schalter am vorhandenen Positions-Status-Feld**:

    YAML: position_state_send: true   (+ position_send_cooldown, Default 2 s)
    UI:   "Aktiv senden der berechneten Position", direkt unter "Aktuelle Position"

Es bleibt EIN Feld fuer EINE Adresse; der Schalter dreht ihre Richtung. Ist er
gesetzt, wird die Adresse **nicht** mehr als xknx-position_state angelegt — HA hoert
dort also nicht mehr zu. Damit ist der Loop STRUKTURELL unmoeglich, nicht nur per
Dokumentation vermieden.

Gesendet wird `Cover.current_position()` — bereits in KNX-Semantik (0 = offen,
100 = geschlossen). Der Nutzer kann die Invertierung nicht mehr falsch machen.

**Kein Feld fuer Positionsbefehle.** Das braucht es nicht: xknx hoert auf ALLEN
Adressen eines RemoteValue, auch auf der Schreibadresse. Ein Taster, der auf
`ga_position_set` einen Sollwert schickt, wird von `Cover.process_group_write` ueber
`position_target` verarbeitet und startet die Fahrt in HA. Live belegt 2026-08-05.

    python3 apply_patch.py <ROOT> [--check|--revert|--status|--orphans]

    ROOT = Verzeichnis, das `homeassistant/components/knx/` enthaelt
    --check   nur Anker pruefen, nichts schreiben
    --revert  Backups (.coverfb.bak) zurueckspielen
    --status  EIN Wort auf stdout, rc immer 0 — fuer command_line-Sensoren:
              applied | missing | partial | anchors-missing | file-missing
              Geprueft wird gegen den ERSETZUNGSTEXT, nicht gegen einen Marker:
              damit meldet auch eine veraltete Patch-Variante "partial"/"missing",
              ohne dass irgendwo ein Marker nachgezogen werden muss.
    --orphans Backups auflisten, deren Datei der Patch nicht mehr anfasst
              (rc=1 wenn welche da sind). Deckt Reste auf, die `--revert`
              nicht mehr erreicht, weil die Datei aus dem Patch geflogen ist.

Anker-basiert, also versionsunabhaengig. Details: ../CLAUDE.md
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
MODE = next((a for a in sys.argv[2:] if a.startswith("--")), "")
KNX = ROOT / "homeassistant" / "components" / "knx"
SUFFIX = ".coverfb.bak"
Q = '"""'

# Die Ersetzungspaare werden NICHT von Hand gepflegt, sondern von gen_edits.py aus
# dem git-Diff des PR-Branches erzeugt. Genau hier ist am 2026-08-05 eine Drift
# entstanden: drei Fragmente aus zwei Commits fehlten im handgeschriebenen Skript.
# Beilage traegt den Namen des Skripts, damit mehrere Patches im gemeinsamen
# Verzeichnis /config/.claude_work/scripts/ nicht kollidieren.
EDITS_FILE = Path(__file__).with_suffix(".edits.json")


def load_edits() -> dict[str, list[tuple[str, str]]]:
    """Ersetzungspaare je Datei aus der generierten JSON-Beilage."""
    import json as _json

    if not EDITS_FILE.exists():
        print(f"FEHLT: {EDITS_FILE} - mit gen_edits.py erzeugen")
        sys.exit(2)
    raw = _json.loads(EDITS_FILE.read_text(encoding="utf-8"))
    return {rel: [(a, n) for a, n in pairs] for rel, pairs in raw.items()}


EDITS: dict[str, list[tuple[str, str]]] = load_edits()


# --- 5) Uebersetzung ----------------------------------------------------
LABELS_EN = {
    "position_state_send": {
        "label": "Actively send the calculated position",
        "description": (
            "Publish the position calculated by Home Assistant to the address above "
            "instead of listening on it. This is meant for actuators that do not "
            "report their position themselves, so that displays can show it."
        ),
    },
}

# Nur fuer die lokale Instanz. In einem PR gehoert AUSSCHLIESSLICH strings.json
# (englisch) — alle weiteren Sprachen kommen bei Home Assistant ueber Lokalise,
# deshalb ist translations/ auch gitignored.
LABELS_DE = {
    "position_state_send": {
        "label": "Berechnete Position aktiv senden",
        "description": (
            "Sendet die von Home Assistant berechnete Position auf die Adresse "
            "darueber, statt auf ihr zu lauschen. Fuer Aktoren, die ihre Position "
            "nicht selbst melden, damit Anzeigen sie darstellen koennen."
        ),
    },
}


def patch_json(path: Path, labels: dict = LABELS_EN) -> None:
    import collections
    import json as _json

    if not path.exists():
        print(f"  ~ {path.name}: nicht vorhanden (uebersprungen)")
        return
    d = _json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=collections.OrderedDict
    )
    try:
        knx = d["config_panel"]["entities"]["create"]["cover"]["knx"]
    except KeyError:
        print(f"  ~ {path.name}: config_panel-Struktur fehlt (uebersprungen)")
        return
    if all(k in knx for k in labels):
        print(f"  ~ {path.name}: Labels schon vorhanden")
        return
    # prettier requires the keys inside each entry to be sorted, too
    knx.update({k: dict(sorted(v.items())) for k, v in labels.items()})
    d["config_panel"]["entities"]["create"]["cover"]["knx"] = collections.OrderedDict(
        sorted(knx.items())
    )
    shutil.copy(path, path.with_suffix(path.suffix + SUFFIX))
    path.write_text(
        _json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"  + {path.name} (Label ergaenzt)")


def _json_applied(path: Path, labels: dict) -> bool | None:
    """True/False, oder None wenn die Datei in diesem Baum nicht existiert."""
    import json as _json

    if not path.exists():
        return None
    try:
        d = _json.loads(path.read_text(encoding="utf-8"))
        knx = d["config_panel"]["entities"]["create"]["cover"]["knx"]
    except (KeyError, ValueError, TypeError):
        return False
    return all(k in knx for k in labels)


def status() -> str:
    """Zustand des Patches in ROOT als ein Wort.

    Der Vergleich laeuft gegen den Ersetzungstext (`neu`), nicht gegen einen
    Marker. Deshalb erkennt die Pruefung auch eine aeltere Patch-Variante, die
    den Marker zwar traegt, den aktuellen Code aber nicht.
    """
    done = anchors_gone = total = 0
    for rel, edits in EDITS.items():
        f = KNX / rel
        if not f.exists():
            return "file-missing"
        t = f.read_text(encoding="utf-8")
        total += 1
        # Angewendet = Ersetzungstext da. Dass er EINDEUTIG ist, garantiert
        # gen_edits.py bei der Generierung (Pruefung "ERSETZUNG SCHON IN DER BASIS") -
        # zur Laufzeit zusaetzlich "Anker weg" zu fordern waere falsch, denn bei einem
        # reinen Zusatz-Hunk ist der Anker Teil des Ersetzungstexts und bleibt stehen.
        if all(neu in t for _, neu in edits):
            done += 1
        elif any(alt not in t for alt, _ in edits):
            anchors_gone += 1
    for path, labels in (
        (KNX / "strings.json", LABELS_EN),
        (KNX / "translations" / "en.json", LABELS_EN),
        (KNX / "translations" / "de.json", LABELS_DE),
    ):
        got = _json_applied(path, labels)
        if got is None:
            continue
        total += 1
        done += got
    if done == total:
        return "applied"
    if done:
        return "partial"
    return "anchors-missing" if anchors_gone else "missing"


def orphans() -> list[str]:
    """Backups, deren Datei der Patch nicht mehr anfasst.

    Blindstelle, die sonst niemand sieht: `--revert` laeuft nur ueber die Dateien
    des AKTUELLEN Patches. Wird eine Datei aus dem Patch entfernt (hier
    storage/const.py beim Wegfall des Cooldown-Feldes), ohne vorher zu revertieren,
    bleibt ihre Aenderung stehen und faellt nie mehr auf.
    """
    known = set(EDITS) | {"strings.json", "translations/en.json", "translations/de.json"}
    found = []
    for bak in KNX.rglob("*" + SUFFIX):
        rel = str(bak.relative_to(KNX))[: -len(SUFFIX)]
        if rel not in known:
            found.append(rel)
    return sorted(found)


def main() -> int:
    if MODE == "--status":
        print(status())
        return 0

    if MODE == "--orphans":
        found = orphans()
        for rel in found:
            same = (KNX / rel).exists() and (KNX / rel).read_bytes() == (
                KNX / (rel + SUFFIX)
            ).read_bytes()
            print(f"  WAISE: {rel}{SUFFIX}" + ("  (Datei == Backup, harmlos)" if same else "  ⚠ WEICHT AB — Rest eines alten Patches!"))
        if not found:
            print("keine verwaisten Backups")
        return 1 if found else 0

    if MODE == "--revert":
        for rel in list(EDITS) + [
            "strings.json",
            "translations/en.json",
            "translations/de.json",
        ]:
            bak = KNX / (rel + SUFFIX)
            if bak.exists():
                shutil.copy(bak, KNX / rel)
                print(f"  zurueckgespielt: {rel}")
            else:
                print(f"  kein Backup: {rel}")
        return 0

    plan = []
    for rel, edits in EDITS.items():
        f = KNX / rel
        if not f.exists():
            print(f"  FEHLT: {f}")
            return 2
        t = f.read_text(encoding="utf-8")
        if all(neu in t for _, neu in edits):
            print(f"  ~ {rel}: bereits gepatcht (aktuelle Fassung)")
            continue
        missing = [a for a, _ in edits if a not in t]
        if missing:
            print(f"  ANKER FEHLT in {rel}: {len(missing)} von {len(edits)}")
            for a in missing:
                print(f"      {a.splitlines()[0][:88]}")
            return 2
        plan.append((f, rel, t, edits))
        print(f"  ok {rel}: {len(edits)} Anker gefunden")

    if MODE == "--check":
        print("--check: nichts geschrieben")
        return 0

    for f, rel, t, edits in plan:
        shutil.copy(f, KNX / (rel + SUFFIX))
        for alt, neu in edits:
            t = t.replace(alt, neu, 1)
        f.write_text(t, encoding="utf-8")
        print(f"  + {rel} (Backup: {rel}{SUFFIX})")
    patch_json(KNX / "strings.json")
    patch_json(KNX / "translations" / "en.json")
    patch_json(KNX / "translations" / "de.json", LABELS_DE)
    print("fertig — HA-Neustart noetig.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
