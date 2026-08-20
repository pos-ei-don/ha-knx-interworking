<p align="center">
  <img src="custom_components/knx_interworking/brand/icon.png" alt="KNX Interworking" width="120">
</p>

# KNX Interworking

[![HACS: Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A companion integration for Home Assistant's built-in KNX integration. It adds **diagnostics** for
finding faults in a grown KNX installation, and **opt-in interworking fixes** for devices that don't
quite behave the way the specification expects.

It does not replace the KNX integration. It runs alongside it and leaves it untouched unless you
explicitly enable something.

> **Early days.** Diagnostics come first; interworking fixes follow. Every fix is off by default and
> version-guarded — if the internals it relies on have changed, it refuses to act and says so instead
> of doing something unexpected.

## What it does

**Diagnostics** — always safe, read-only; no telegram is ever sent that you didn't ask for:

* **ETS project check** — compares your ETS project against the group addresses Home Assistant
  actually polls. Finds addresses that are read on every startup although no communication object has
  the read flag set, and addresses with no object behind them at all.
* **Decode error monitor** — collects telegrams that could not be decoded and groups them **by source
  device**, instead of letting them scroll past in the log.
* **DPT conflict detection** — finds a group address used with two different datapoint types.
* **Duplicate-writer detection** — finds a group address written from more than one place in Home
  Assistant.

Each check is also available on demand through the `knx_interworking.run_check` action (Developer
Tools → Actions) and a button, so you can re-run it after a change instead of restarting.

**Interworking fixes** — every one **off by default**, enabled explicitly, and logged when it acts:

* **Reserved-bit masking** for small payloads (DPT 1/2/3), for actuators that set the reserved bits —
  applied only to the group addresses you list.
* **Summer/winter bit** sent alongside Home Assistant's time server, for installations that expect a
  season bit on the bus.
* **Climate command delay** — for HVAC actuators that switch themselves off when the mode and the
  on/off command arrive back to back: it watches the outgoing mode write and drives a separate
  on/off address a configurable moment later. You take the on/off address out of the climate entity
  and let this drive it.
* **Climate status text** — adds a `status_text` attribute (a 14-byte diagnostic text, DPT 16.x) to
  KNX `climate` entities, and adds the matching group-address field to the KNX entity dialog.
  ⚠️ **This is the only feature here that modifies files of your Home Assistant installation** — and
  only if you turn write-back on; by default it just reports whether the patch is present. It is
  needed because that config field cannot be added from outside. A core update removes it; the
  integration notices at startup and offers to restore it. See the note below before enabling.

> ⚠️ **About the file patch.** The *Climate status text* feature is the one exception to "leaves the
> KNX integration untouched": to add a field to the KNX entity dialog it edits a few Home Assistant
> core files. It is **off by default**, and even when on it only **reports** unless you also enable
> write-back. Everything else in this integration is a runtime hook or read-only and leaves no trace.
> If you prefer nothing ever touch core files, simply leave this one feature off.

## Installation (HACS)

This is a custom HACS repository. Click to add it to your Home Assistant:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=pos-ei-don&repository=ha-knx-interworking&category=integration)

1. Click the button above — it opens HACS with this repository prefilled. (Manual path: HACS → ⋮ →
   **Custom repositories** → add `https://github.com/pos-ei-don/ha-knx-interworking`, category
   **Integration**.)
2. Install **KNX Interworking**, then restart Home Assistant.
3. Add the integration:

   [![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=knx_interworking)

   (Manual path: Settings → Devices & Services → **Add integration** → *KNX Interworking*.)

Then open the integration's options to enable individual features. Diagnostics are safe to turn on
first.

## 🤝 Found a device that misbehaves? Please tell me

**This is the part I'd most like help with.** KNX is a large ecosystem, and no single installation
sees enough devices to know what's out there. If you have a device that Home Assistant doesn't read
correctly, or a telegram that gets dropped, open an issue — even if you don't know the cause. Half of
the work is finding out *that* something is wrong.

Especially interesting:

* values that appear in ETS but never arrive in Home Assistant
* entities that stay `unknown` although the actuator answers on the bus
* decode warnings in the log you couldn't explain
* anything where the ETS group monitor and Home Assistant disagree

**What helps most in a report:**

1. The **diagnostic output** of this integration — it collects most of what's needed by itself.
2. The **device**: manufacturer, order number, hardware revision, application program version
   (ETS → device → information).
3. The **group address** and the DPT configured for it.
4. A short **group monitor capture** from ETS showing the telegram in question.

If you can't provide all of it, report anyway. An incomplete report that gets a device on the list is
worth more than a perfect one that never gets written.

**What happens then** — honestly, so nobody is disappointed:

* If it's something this integration can fix, it becomes an opt-in feature.
* If it belongs in the KNX integration or in xknx, I'll say so and help get it there. This project is
  not a place to route around upstream.
* If it's a device firmware bug, the right address is the manufacturer — and a clean capture is what
  makes such a report effective. I've done this myself and will help you word it.
* Some things will get no fix. I'd rather say that than leave an issue open forever.

### Known device behaviour

Reports that get confirmed end up in a public list: device, what it does, whether a workaround exists,
and whether the manufacturer has been informed. There is no such list for KNX anywhere, and every
entry saves the next person a weekend of debugging.

### One thing to check before you attach anything

ETS exports and telegram captures describe **your whole installation** — addresses, device names,
sometimes room and family names. Please trim them to the telegrams that matter. If you'd rather not
post a capture publicly, say so in the issue and we'll find another way.

## Not affiliated with the KNX Association

"KNX" is a registered trademark of the KNX Association. This is an independent community project and
is neither certified nor endorsed by them.

## License

[MIT](LICENSE).
