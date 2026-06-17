# KEYZER — Architecture & Design Notes

## Goal

Mimic the Razer Synapse experience on Linux: a picture of the real device, click a key, set a binding. Pour all effort into the **UI/UX**; do not reinvent the remapping engine.

## The engine is a black box

KEYZER does **not** implement input remapping. It drives **input-remapper 2.2.1** (already the de-facto Linux remapper, supports keyboards, mice, gamepads, macros).

**Hard rule:** interact with input-remapper only through its **public surface** — its CLI, its preset files (`~/.config/input-remapper-2/presets/<group>/<preset>.json`), and its DBus service. **Never import `inputremapper` Python internals.**

Why: on Ubuntu 26.04 (Python 3.14) input-remapper runs on a Pydantic-v1 compatibility shim and logs an incompatibility warning. It works today, but it's fragile. Talking to it at arm's length means an engine hiccup can never take the KEYZER UI down with it.

## Layers

1. **Device layer** — `layouts.json`: for each device + view, the image and the clickable hotspots (`id`, `x`, `y`, `w`, `h` in image-pixel space). This is the contract between the artwork and the logic. The geometry was produced with the prototype's drag-to-align editor.
2. **Binding layer** — a KEYZER *profile* maps `hotspot id → output` (key / combo / macro / mouse button / disable). Profiles compile to input-remapper preset JSON.
3. **UI layer** — render `image` + overlay hotspots; clicking a hotspot opens the assign panel; applying writes the binding and (in the real app) regenerates the preset and tells the daemon to reload.

### Why each Naga button lives on a specific view

A mouse's thumb buttons are on its *side*, so they foreshorten badly from straight overhead. KEYZER gives the Naga two views with a toggle:

- **top** — Left/Right click, scroll wheel (click) + wheel **tilt-left / tilt-right**, and the two sensitivity buttons.
- **side** — the 12-button thumb grid, face-on.

Each hotspot is aligned on the view where the button is actually visible.

## evdev codes

`layouts.json` carries geometry; the evdev code each physical key emits comes from a one-time capture on the real hardware:

- `capture.py` reads each device read-only via python-evdev — you press each key once and it records the `(type, code)` plus an `origin_hash` into `~/.config/keyzer/captures.json`. A portable default map ships at the repo root (`captures.default.json`) so a fresh clone works before you calibrate. `engine.build_preset` joins those captures with a profile's binds into the input-remapper preset.
- Razer device notes from discovery:
  - Tartarus Pro keys land on `event12`; thumb stick/mouse on `event14`.
  - Naga Pro: pointer/main buttons on `event3`/`mouse0`; keyboard-type interfaces on `event4`/`event5` (the thumb grid often emits number-row keycodes).

## App shell

PySide6 6.10 + QML, hosted in a QQuickView (`app/main.py`). Native to the Python evdev / input-remapper ecosystem, no web runtime to ship, and Qt's scene graph carries the Synapse-style visuals (`app/qml/Main.qml`). The engine and lighting are driven as black boxes. The `prototype/` directory keeps the original HTML/CSS mock that pinned down the visual language.

## Platform notes (target: Ubuntu 26.04, Wayland + GNOME)

- **Remapping** runs below the display server (input-remapper's daemon), so Wayland is no obstacle.
- **App-aware switching** needs the focused-window, which Wayland withholds from clients — handled via a small GNOME Shell extension exposing it over DBus.
- **Global hotkeys** — register a GNOME custom shortcut that calls the KEYZER CLI (avoids needing a Wayland global grab).
- **Tray** — works on Ubuntu's GNOME via AppIndicator.
- **Lighting** — OpenRazer (kernel module + daemon); not yet installed.

## Packaging

A `.deb` that `Depends: input-remapper (>= 2.0)`, ships the app + `layouts.json` + a systemd user service for the app-aware watcher, and udev rules only where needed.
