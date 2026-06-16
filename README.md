# KEYZER

**Visual key remapping for Razer peripherals on Linux** — a Razer-Synapse-style GUI built on top of the open-source [input-remapper](https://github.com/sezanzeb/input-remapper) engine.

Razer Synapse doesn't exist on Linux, and input-remapper's stock editor is powerful but unintuitive. KEYZER brings back the Synapse workflow you actually want: **see a picture of your real device, click a key, assign a binding** — and it writes straight to input-remapper.

## Status

🟢 **Interactive concept prototype** (this repo). It validates the whole UX with real device photos and pixel-aligned, clickable hotspots.

- Real device images as the canvas (not abstractions)
- Click any key → Synapse-style assign panel (key / combo / macro / mouse / disable)
- Per-key binding labels shown on the device, like Synapse
- Profile switching, app-aware auto-switch, tray + hotkey, and Chroma-lighting concepts
- A built-in **drag-to-align** editor (how the hotspot coordinates in `layouts.json` were produced)

**Target devices**
| Device | USB ID | Views |
|---|---|---|
| Razer Tartarus Pro (keypad) | `1532:0244` | keypad |
| Razer Naga Pro (mouse) | `1532:008f` | top (main buttons + wheel tilt) · side (12-button thumb grid) |

## Try the prototype

Open the self-contained build in any browser — no install, nothing to set up:

```bash
xdg-open dist/keyzer.html
```

Rebuild it from sources (template + device images → single inlined file):

```bash
python3 prototype/build.py
```

## How it works

KEYZER is a **friendly front-end**; the remapping itself is done by a proven engine.

```
┌─ KEYZER ──────────────────────────────────────────────┐
│ device image + clickable hotspot overlay (layouts.json)│
│ assign panel · profiles · app-aware · tray · lighting  │
│            │ generates preset JSON / drives daemon      │
└────────────▼──────────────────────────────────────────┘
┌─ input-remapper (engine — black box) ─────────────────┐
│ evdev → uinput remapping, runs as a system service     │
└────────────────────────────────────────────────────────┘
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design and the rule that keeps us decoupled from the engine.

## Roadmap

- [ ] Pick the app shell (Tauri / Electron / Qt) and port this UI to it
- [ ] `capture.py` — record the real evdev code each physical key emits (fills in `layouts.json`)
- [ ] Generate input-remapper preset JSON from a KEYZER profile + drive the daemon
- [ ] App-aware profile switching (GNOME/Wayland active-window via shell extension)
- [ ] System-tray indicator + global hotkey to cycle profiles
- [ ] OpenRazer lighting sync (active profile reflected on the device LEDs)
- [ ] `.deb` packaging (depends on `input-remapper`)

## Note on artwork

The device images here are Razer product/press renders, used as placeholders during prototyping for personal use. Before any public distribution they'll be swapped for original artwork or properly-licensed device SVGs.
