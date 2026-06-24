# KEYZER — UAT guide

Testing a remapper is mostly a hardware problem: the part that matters (real key
events → live injection) can't be checked headless, so it needs hands. The goal
of this guide is to make that small and systematic — automate everything that
can be, and give the manual pass a fixed script so nothing's covered twice and
nothing's missed.

## 1. Run the automated gate first (catches ~everything that isn't hardware)

```bash
bash tools/check.sh        # compile + 233 unit tests + render all 24 UI states
```
If it's red, stop and fix before touching the app — the bug is already found.
What it covers, so you don't have to: engine translation (keys, combos, F-keys,
mouse, macros, unsupported), preset generation, profile CRUD + persistence +
import/export, and every screen/dialog/overlay loading without a QML error.

## 2. Prerequisites for the manual pass

```bash
systemctl status input-remapper                      # system service, must be active
input-remapper-control --command hello               # daemon reachable
python3 -c "from openrazer.client import DeviceManager; print([d.name for d in DeviceManager().devices])"  # lighting only
groups | grep plugdev                                # for OpenRazer
python3 app/capture.py                               # one-time per machine
```

## 3. Manual pass (only what needs real hardware)

Run KEYZER: `python3 app/main.py`. Work top to bottom; note the **toast/overlay
text** + the **step** for anything that misbehaves.

- [ ] **Capture** — `capture.py` prompts every key, records them, and skips only the
      8-way diagonals (derived from their cardinals). Wheel tilt L/R are capturable.
- [ ] **Bind a key** — select a hotspot → quick-pick or Listen → **Bind**.
      Toast says `… · live`. Press the physical key → it emits the bound output.
      - [ ] a letter (e.g. TAR_08 → `w`)
      - [ ] a combo via Listen (`Ctrl+1`, `Ctrl+Shift+1`)
      - [ ] an F-key (`F5`)
      - [ ] a mouse button output (`LMB`)
- [ ] **8-way thumb pad** — cardinals move (WASD); a diagonal fires both
      (unbound) or its own bind (if set). No mouse weirdness.
- [ ] **Clear** — removes the bind live.
- [ ] **Profiles** — create / rename / duplicate / delete; switch; **restart the
      app** and confirm everything persisted; export → import round-trips.
- [ ] **Conflicts / coverage** — footer shows bound count; amber when two visible
      hotspots share an output.
- [ ] **Lighting** (OpenRazer running) — per-device colour / effect / brightness;
      Naga Logo + Scroll-wheel zones change independently.
- [ ] **Edge** — DPI/sensitivity buttons show dimmed `n/a` (hardware function, not a
      remap); nothing crashes on a profile/device/view switch.

## 4. Reporting a bug

Paste the **toast/overlay message** + the exact step. For a bind that won't fire,
also paste:
```bash
cat ~/.config/input-remapper-2/presets/*/keyzer-* | head -40
```
and which hotspot → which output you set. That's almost always enough to pinpoint.
