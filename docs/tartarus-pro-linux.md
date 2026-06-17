# Razer Tartarus Pro on Linux — remap keys the way Synapse does

**Short answer:** Razer Synapse doesn't run on Linux, but you can still remap every
key on a Razer Tartarus Pro. [KEYZER](../README.md) gives you a Synapse-style
visual editor — a picture of the Tartarus with clickable keys — on top of the
open-source [input-remapper](https://github.com/sezanzeb/input-remapper) engine.

## Does Razer Synapse work on Linux?

No. Razer Synapse is Windows/macOS only, and there's no official Linux build or
cloud version. Your Tartarus Pro still *works* as a generic keypad on Linux, but
its keys emit fixed codes and you can't rebind them through Razer's software.

## How do I remap a Razer Tartarus Pro on Linux?

1. Install `input-remapper` (the remapping engine) and KEYZER (the UI) — see the
   [install guide](../README.md#install).
2. Run KEYZER, pick **Tartarus Pro** in the device list.
3. Click any key on the picture, then choose what it should do — a key, a combo
   (Ctrl+1), a macro, a mouse button, or *disable* it.
4. Hit **Apply to device**. Your binding is live immediately.

The first time on a new machine, run `python3 app/capture.py` once: it records the
real evdev code each physical key emits so the bindings land on the right keys.

## What about the 20 keys, the thumb pad, and the scroll wheel?

KEYZER models the full Tartarus Pro:

- **All 20 keys** (rows 1–4 plus the right column and the big thumb key), each a
  clickable hotspot on the device image.
- **The 8-way thumb pad** — a dedicated *Thumb* view with true 8-direction support
  (the diagonals are built from input-remapper combination mappings, so a diagonal
  can fire its own binding instead of just the two cardinals).
- **The scroll wheel** click.

## Is the lighting (Chroma) controllable too?

Yes, optionally. With [OpenRazer](https://openrazer.github.io/) installed, KEYZER's
Lighting mode sets the Tartarus Pro's colour, brightness, and effects (Solid,
Pulse, Rainbow, React, Wave). Without OpenRazer, remapping still works fully and
the Lighting toggle simply stays off.

## Device details

| | |
|---|---|
| Device | Razer Tartarus Pro |
| USB ID | `1532:0244` |
| Engine | input-remapper ≥ 2.0 (evdev → uinput) |
| Tested on | Ubuntu 26.04, Wayland + GNOME |

→ Get started: [KEYZER README](../README.md). Have a different Razer device?
Adding one is a single entry in [`layouts.json`](../layouts.json) — PRs welcome.
