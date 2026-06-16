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

## evdev codes (the one missing piece)

`layouts.json` currently has geometry only. To actually remap, each hotspot needs the **evdev code its physical key emits**. That comes from a one-time capture on the real hardware:

- `capture.py` (planned) uses `python-evdev` to read each device; you press each key once and it records the code, filling in `layouts.json`.
- Razer device notes from discovery:
  - Tartarus Pro keys land on `event12`; thumb stick/mouse on `event14`.
  - Naga Pro: pointer/main buttons on `event3`/`mouse0`; keyboard-type interfaces on `event4`/`event5` (the thumb grid often emits number-row keycodes).

## App shell (decision pending)

Candidates, in order of fit for a Synapse-faithful look:

- **Tauri** (web UI + Rust core) — smallest `.deb`, reuses this prototype's HTML/CSS directly. Recommended.
- **Electron** — simplest, heaviest.
- **Qt/QML (PySide6)** — native, tight integration with the Python evdev/input-remapper ecosystem.

The prototype is intentionally plain HTML/CSS/JS so it can graft onto Tauri or Electron with little rework.

## Platform notes (target: Ubuntu 26.04, Wayland + GNOME)

- **Remapping** runs below the display server (input-remapper's daemon), so Wayland is no obstacle.
- **App-aware switching** needs the focused-window, which Wayland withholds from clients — handled via a small GNOME Shell extension exposing it over DBus.
- **Global hotkeys** — register a GNOME custom shortcut that calls the KEYZER CLI (avoids needing a Wayland global grab).
- **Tray** — works on Ubuntu's GNOME via AppIndicator.
- **Lighting** — OpenRazer (kernel module + daemon); not yet installed.

## Packaging

A `.deb` that `Depends: input-remapper (>= 2.0)`, ships the app + `layouts.json` + a systemd user service for the app-aware watcher, and udev rules only where needed.
