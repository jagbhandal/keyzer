# Razer Naga Pro on Linux — remap the 12 thumb buttons without Synapse

**Short answer:** Razer Synapse isn't available on Linux, but you can still rebind
the Naga Pro's thumb buttons and main buttons. [KEYZER](../README.md) shows a
picture of the Naga with clickable buttons and writes your bindings to the
open-source [input-remapper](https://github.com/sezanzeb/input-remapper) engine.

## Can I use the Razer Naga Pro's 12 side buttons on Linux?

Yes. By default the Naga Pro's thumb grid emits number-row keycodes, which most
apps ignore as "extra" keys. With KEYZER you click each of the 12 buttons on the
device picture and bind it to whatever you want — a key, a combo, a macro, or a
mouse button.

## How do I remap the Naga Pro on Linux?

1. Install `input-remapper` + KEYZER — see the [install guide](../README.md#install).
2. Open KEYZER, select **Naga Pro**.
3. Use the **top** view for the left/right click, scroll wheel, and sensitivity
   buttons; switch to the **side** view for the face-on 12-button thumb grid.
4. Click a button, pick its output, and **Apply to device** — it's live at once.

Run `python3 app/capture.py` once per machine so KEYZER learns the exact code each
physical button sends.

## Two views, because a mouse isn't flat

The thumb buttons are on the *side* of the mouse, so they foreshorten badly from
overhead. KEYZER gives the Naga two views with a toggle — **top** (main buttons,
wheel, sensitivity) and **side** (the 12-button grid, head-on) — so every button
is easy to click on the view where it's actually visible.

## What about DPI stages and wheel tilt?

Two special cases:

- **DPI / sensitivity** is a hardware function set on the device, not a remap —
  KEYZER marks those buttons accordingly rather than pretending to bind them. (Use
  OpenRazer for DPI if you need it.)
- **Wheel tilt** (left/right) is bindable like any other control — the tilt produces
  Linux input events (a horizontal-scroll notch, and/or a key if your device's
  onboard profile maps tilt to one), so KEYZER captures and binds it directly. No
  Synapse and no background daemon needed.

## Lighting

With [OpenRazer](https://openrazer.github.io/) installed, KEYZER's Lighting mode
controls the Naga Pro's colour, brightness, and effects, including its independent
**logo** and **scroll-wheel** zones. Remapping works fully without OpenRazer.

## Device details

| | |
|---|---|
| Device | Razer Naga Pro |
| USB ID | `1532:008f` |
| Engine | input-remapper ≥ 2.0 (evdev → uinput) |
| Tested on | Ubuntu 26.04, Wayland + GNOME |

→ Get started: [KEYZER README](../README.md). Want another Razer device supported?
It's a single entry in [`layouts.json`](../layouts.json) — PRs welcome.
