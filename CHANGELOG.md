# Changelog

All notable changes to KEYZER are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once it tags a
first release.

## [Unreleased]

### Added
- Visual remapping UI: a picture of the device with clickable hotspots, an assign
  panel, multiple profiles, per-device views, and a dark Synapse-style theme.
- Real remapping pipeline — `capture.py` records the evdev code each key emits,
  `engine.py` compiles a profile into an input-remapper preset and applies it,
  with a live-status indicator and one-click stop.
- Multi-profile management with persistence (atomic writes) plus profile
  import/export via the clipboard.
- True 8-way Tartarus thumb pad, built from input-remapper combination mappings.
- Optional OpenRazer lighting: per-device and per-zone Chroma effects + brightness.
- A bundled default key map (`captures.default.json`) so a fresh clone works
  before calibration, with a first-run hint when it's in use.
- Drag-to-align hotspot editor for fitting the overlay to a device image.
- Quality gates: a unit-test suite and an offscreen render gate covering every UI
  state (`tools/check.sh`), plus a UAT guide.

### Security
- `install.sh` now grants Razer input access via a vendor-scoped udev rule
  (`packaging/70-keyzer.rules`) instead of adding the user to the `input` group,
  which let every process running as the user read every keyboard.
- A device advertising a pathological name (`..`, `.`, empty) can no longer
  place its preset file outside the input-remapper presets directory.

### Fixed
- A corrupt or missing `layouts.json` now exits with a clear one-line message
  instead of a raw JSON traceback at startup (app and `capture.py`).
- `capture.py` writes `captures.json` atomically — a crash mid-write can no
  longer corrupt the whole capture map (which loads as empty when corrupt).
- `save_json` fsyncs before its atomic rename, so a power loss right after a
  save can't leave a zero-length profiles/captures/config file.
- A captured device name no longer prefix-matches a *different* product's
  input-remapper group (`Razer Naga` vs `Razer Naga Pro`); only the ` 2`
  second-unit suffix is accepted.
- The `+` key is bindable: a lone `+` or trailing `++` (as in `Ctrl++`) now
  translates to the `plus` keysym, and a mid-chord stray `+` gets a helpful
  error instead of `unknown key(s): ,`.
- Releasing an unrelated held key during calibration no longer truncates a
  chord capture mid-gather.
- Listen mode turns itself off when keyboard focus is stolen (e.g. clicking
  into a text field) instead of looking live while capturing nothing.
- The footer's "Applying…" indicator now tracks overlapping applies with a
  counter, so the first to finish no longer clears it while others run.
- The desktop entry's `Exec` path is quoted (repo paths with spaces work), and
  `install.sh` no longer aborts mid-run when its prompts read EOF
  (non-interactive runs).
- Keysym bindings were silently dropped by the input-remapper daemon when
  `xmodmap.json` was missing, so keys kept emitting their hardware defaults.
  KEYZER now ensures that file exists before applying.
- Capturing pointer motion (`REL_X`/`REL_Y`) as a button could freeze the mouse;
  capture now records wheel notches only and the engine refuses pointer axes.
- `capture.py` releases input-remapper's exclusive device grab before recording.
- A successful bind could be misreported as "not applied" due to a hotspot-id
  prefix collision (`TAR_TPAD_N` vs `TAR_TPAD_NE`).
- `set_autoload` now writes input-remapper's `config.json` atomically.
- F1–F12, modifier combos (Ctrl+1, Ctrl+Shift+1), and punctuation chords
  (Ctrl+-) are bindable.

### Changed
- Binding applies live in a single step instead of two.
- Adopted the "Neon" visual direction.
- Documented the system dependencies (`x11-xserver-utils`, `python3-evdev`, and
  the Razer udev rule) in `install.sh` and `requirements.txt`.
