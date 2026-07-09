#!/usr/bin/env python3
"""KEYZER capture — learn what each physical key on your Razer gear emits.

Run this ON the machine with the hardware, as a user who can read the Razer
/dev/input nodes (install.sh sets up a vendor-scoped udev rule for exactly
that — see the message printed if access is denied).

For every hotspot defined in ``layouts.json`` it asks you to press the matching
physical key, then records the evdev event it sees — the ``(type, code)`` plus an
``origin_hash`` that pins the event to that exact device node. The result lands
in ``~/.config/keyzer/captures.json``; that file is what lets KEYZER turn a
profile (hotspot -> output) into a real input-remapper preset (input -> output).

It opens evdev directly and only ever reads events (and grabs the device
exclusively); it never injects or writes events, and doesn't need input-remapper
running. While capturing, a device's events are grabbed (so pressing keys doesn't
trigger anything) — press Ctrl-C to abort at any time and the grab is released.

Usage:
    python3 app/capture.py                 # capture every device in layouts.json
    python3 app/capture.py --device naga   # just one
    python3 app/capture.py --no-grab       # don't grab (keys act normally)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    import evdev
    from evdev import ecodes
except ImportError:
    sys.exit(
        "python-evdev is required.\n"
        "  Debian/Ubuntu:  sudo apt install python3-evdev\n"
        "  or:             pip install --user evdev"
    )

import engine   # pure-stdlib sibling; supplies the pointer-button hijack guard

REPO = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "keyzer"
CAPTURES = CONFIG_DIR / "captures.json"

# Event types we treat as a deliberate "press". EV_KEY down = a button/key;
# EV_REL with a nonzero value = a wheel tilt / scroll notch (its sign is the
# direction, stored as analog_threshold so input-remapper can match it).
IGNORED_TYPES = {ecodes.EV_SYN, ecodes.EV_MSC}
# Only wheel scroll/tilt is a bindable relative input — NEVER pointer motion
# (REL_X/REL_Y), or nudging the mouse mid-capture maps movement onto a key.
_WHEEL_CODES = {ecodes.REL_WHEEL, ecodes.REL_HWHEEL}
for _hr in ("REL_WHEEL_HI_RES", "REL_HWHEEL_HI_RES"):
    if hasattr(ecodes, _hr):
        _WHEEL_CODES.add(getattr(ecodes, _hr))


def device_hash(dev: "evdev.InputDevice") -> str:
    """Reproduce input-remapper's get_device_hash so our origin_hash matches the
    one its injector computes for the same node (utils.get_device_hash)."""
    s = str(dev.capabilities(absinfo=False)) + dev.name
    return hashlib.md5(s.encode()).hexdigest().lower()


def code_name(etype: int, code: int) -> str:
    """Human label for an event, e.g. 'KEY_ESC' / 'BTN_LEFT' (display only)."""
    name = ecodes.bytype.get(etype, {}).get(code, str(code))
    return name[0] if isinstance(name, (list, tuple)) else name


def classify_event(ev, dev) -> dict | None:
    """Turn one evdev event into a capture payload, or None if it isn't a
    deliberate press (key-up, autorepeat, pointer motion, SYN/MSC). Shared by the
    CLI prompt loop and the in-app calibrator so both classify presses identically."""
    if ev.type in IGNORED_TYPES:
        return None
    is_key_down = ev.type == ecodes.EV_KEY and ev.value == 1       # a button/key press
    is_wheel = (ev.type == ecodes.EV_REL and ev.value != 0         # a scroll/tilt notch —
                and ev.code in _WHEEL_CODES)                       # never pointer motion
    if not (is_key_down or is_wheel):
        return None
    payload = {"type": ev.type, "code": ev.code,
               "origin_hash": device_hash(dev),
               "node": dev.path, "name": code_name(ev.type, ev.code)}
    if is_wheel:  # store direction so input-remapper can match the notch
        payload["analog_threshold"] = 1 if ev.value > 0 else -1
    return payload


def _entry(payload: dict) -> dict:
    """The persisted shape of one captured event (drops the display-only node/name)."""
    entry = {k: payload[k] for k in ("type", "code", "origin_hash") if k in payload}
    if "analog_threshold" in payload:
        entry["analog_threshold"] = payload["analog_threshold"]
    return entry


def capture_entry(chord):
    """The persisted shape of a captured press. Shared by the CLI and the in-app
    calibrator so captures.json entries match. A single key stays a lone dict (so
    existing captures are byte-identical); a hardware chord — a button that emits
    several keys at once, e.g. a Naga side button sending Ctrl+1 — becomes the list
    of them, which engine expands into a multi-key input_combination so the WHOLE
    chord is matched and consumed (no half of it leaking through to the app)."""
    if isinstance(chord, dict):          # tolerate a single payload
        chord = [chord]
    entries = [_entry(p) for p in chord]
    return entries[0] if len(entries) == 1 else entries


def find_nodes(vendor: int, product: int) -> list["evdev.InputDevice"]:
    """All /dev/input nodes belonging to one USB device (matched by id)."""
    nodes = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except (PermissionError, OSError):
            continue
        if dev.info.vendor == vendor and dev.info.product == product:
            nodes.append(dev)
        else:
            dev.close()
    return nodes


def preset_dir_name(nodes: list["evdev.InputDevice"]) -> str:
    """The device name input-remapper uses as its preset folder: the shortest of
    the node names (groups._Group.name = sorted(names, key=len)[0])."""
    return sorted((d.name for d in nodes), key=len)[0]


def open_nodes(dev_layout: dict) -> tuple[str | None, list]:
    """Open every evdev node for one device from its layouts.json entry. Returns
    (preset_name, nodes), or (None, []) if the usb id is malformed or no matching
    device is plugged in / readable. The caller owns closing the nodes."""
    try:
        vendor, product = (int(x, 16) for x in dev_layout.get("usb", "").split(":"))
    except ValueError:
        return None, []
    nodes = find_nodes(vendor, product)
    if not nodes:
        return None, []
    return preset_dir_name(nodes), nodes


# How long to keep gathering keys pressed together with the first one. A button
# that emits a chord (Ctrl+1) fires its keys back-to-back — usually in one read()
# batch, occasionally split across a couple — so a brief window catches the rest
# without a real key-up. We also stop the instant the first key is released.
CHORD_WINDOW = 0.04


def _gather_chord(fd_to_dev, ready_fds, window: float = CHORD_WINDOW):
    """Read the presses pending on ``ready_fds`` plus any that follow within
    ``window`` seconds, collapsing a hardware chord (e.g. a Naga side button that
    emits Ctrl+1) into one ordered, de-duplicated list of payloads. Returns the
    chord, or None if nothing on those fds was a deliberate press. The first key
    release ends the chord early, so an ordinary single press stays snappy."""
    chord, seen = [], set()
    deadline = None
    while True:
        for fd in ready_fds:
            dev = fd_to_dev.get(fd)
            if dev is None:
                continue
            try:
                events = list(dev.read())
            except (BlockingIOError, OSError):   # a node unplugged mid-capture must
                continue                         # not kill the whole session
            for ev in events:
                if ev.type == ecodes.EV_KEY and ev.value == 0:
                    # a release ends the chord — but only of a key IN it; the user
                    # letting go of some unrelated held key must not truncate it
                    if (ecodes.EV_KEY, ev.code) in seen:
                        return chord
                    continue
                payload = classify_event(ev, dev)
                if payload is None:
                    continue
                key = (payload["type"], payload["code"])
                if key not in seen:                # keep order, drop the autorepeat/dupes
                    seen.add(key)
                    chord.append(payload)
        if not chord:
            return None
        if deadline is None:
            deadline = time.monotonic() + window
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return chord
        ready_fds, _, _ = select.select(list(fd_to_dev), [], [], remaining)
        if not ready_fds:
            return chord


def read_press(fd_to_dev):
    """Block until a real press arrives on one of the device nodes, or the user
    types a command. Returns ("event", chord) — the list of keys the button emits,
    one entry for an ordinary key — or ("cmd", text)."""
    fds = list(fd_to_dev) + [sys.stdin.fileno()]
    while True:
        ready, _, _ = select.select(fds, [], [])
        if sys.stdin.fileno() in ready:
            line = sys.stdin.readline()
            if line == "":   # EOF (closed/redirected stdin) — finish, don't busy-loop
                return "cmd", "q"
            return "cmd", line.strip().lower()
        chord = _gather_chord(fd_to_dev, [fd for fd in ready if fd in fd_to_dev])
        if chord is not None:
            return "event", chord


def next_press(nodes, timeout: float = 0.2):
    """Wait up to ``timeout`` seconds for a deliberate press on ``nodes`` and
    return its chord (a list of payloads — a button emitting Ctrl+1 yields both
    keys; an ordinary key yields one), else None on timeout — so a caller can poll
    in a loop and check for cancellation between calls (used by the calibrator)."""
    fd_to_dev = {d.fd: d for d in nodes}
    ready, _, _ = select.select(list(fd_to_dev), [], [], timeout)
    if not ready:
        return None
    return _gather_chord(fd_to_dev, list(ready))


def drain(nodes) -> None:
    """Flush pending events (key-up, autorepeat) so they don't bleed into the
    next prompt."""
    fds = [d.fd for d in nodes]
    while True:
        ready, _, _ = select.select(fds, [], [], 0.15)
        if not ready:
            return
        for d in nodes:
            if d.fd in ready:
                try:
                    for _ in d.read():
                        pass
                except (BlockingIOError, OSError):
                    pass


def capture_device(dev_id: str, dev_layout: dict, grab: bool, existing: dict) -> dict | None:
    """Walk every hotspot of one device and record what each key emits."""
    usb = dev_layout.get("usb", "")
    name, nodes = open_nodes(dev_layout)
    if not nodes:
        print(f"  ! {dev_layout.get('name', dev_id)} ({usb}) not found or unreadable "
              "(bad usb id, not plugged in, or no access to /dev/input).")
        return None

    all_keys = [k for view in dev_layout["views"].values() for k in view["keys"]]
    # combos are derived from their members; unavailable controls emit no event
    hotspots = [k for k in all_keys if not (k.get("combo") or k.get("unavailable"))]
    nskip = len(all_keys) - len(hotspots)
    print(f"\n=== {dev_layout.get('name', dev_id)} — {len(hotspots)} keys "
          f"(device '{name}', {len(nodes)} nodes) ===")
    if nskip:
        print(f"  ({nskip} keys skipped: 8-way diagonals are derived from their cardinals; "
              "any controls with no Linux event are not bindable)")
    print("  Press each key as prompted. Commands (type + Enter):  "
          "s=skip  b=back  q=finish device\n")

    captured = dict(existing.get(dev_id, {}).get("captured", {})) if existing else {}
    fd_to_dev = {d.fd: d for d in nodes}
    grabbed = []
    try:
        if grab:
            for d in nodes:
                try:
                    d.grab()
                    grabbed.append(d)
                except OSError:
                    pass
            if grabbed:
                print("  (device input is grabbed — keys won't act normally until done)\n")
            else:
                print("  ! couldn't grab the device — another program is holding it.\n"
                      "    Quit (Ctrl-C), run:  input-remapper-control --command stop-all\n"
                      "    then re-run this script.\n")

        i = 0
        while i < len(hotspots):
            spot = hotspots[i]
            kid = spot["id"]
            mark = "  *already set*" if kid in captured else ""
            print(f"  [{i + 1}/{len(hotspots)}] {kid:12} press the key …{mark}",
                  flush=True)
            kind, payload = read_press(fd_to_dev)
            if kind == "cmd":
                if payload in ("s", "skip"):
                    print("      skipped.")
                    i += 1
                elif payload in ("b", "back"):
                    i = max(0, i - 1)
                elif payload in ("q", "quit", "done"):
                    print("      finishing this device.")
                    break
                else:
                    print("      ? commands: s=skip  b=back  q=finish")
                continue
            chord = payload   # the event branch: a list of one-or-more key payloads
            hijack = next((h for e in chord
                           if (h := engine.pointer_hijack_reason(kid, e["type"], e["code"]))),
                          None)
            if hijack:
                # A stray real mouse-button press, not this control — recording it
                # would later remap the user's actual click. Stay on this hotspot.
                print(f"      ✗ {hijack}")
                drain(nodes)
                continue
            captured[kid] = capture_entry(chord)
            label = "+".join(e["name"] for e in chord)
            codes = ", ".join(f"{e['type']}/{e['code']}" for e in chord)
            print(f"      got {label}  ({codes})")
            drain(nodes)
            i += 1
    finally:
        for d in grabbed:
            try:
                d.ungrab()
            except OSError:
                pass
        for d in nodes:
            d.close()

    # prune stale entries (e.g. previously-captured diagonals now skipped)
    captured = {k: v for k, v in captured.items() if k in {h["id"] for h in hotspots}}
    return {"device_name": name, "all_names": sorted({d.name for d in nodes}),
            "usb": usb, "captured": captured}


def save_captures(path: Path, result: dict) -> None:
    """Persist the capture map atomically (via engine.save_json) — a crash
    mid-write must never corrupt an existing captures.json, since a corrupt one
    loads as {} and every recorded key would be lost."""
    engine.save_json(path, result, indent=2)


def _free_devices() -> None:
    """input-remapper exclusively grabs the devices it's injecting on, which
    starves our reads (keys leak to other windows and capture sees nothing). Stop
    its injection first — best-effort, harmless if it isn't installed. Re-apply
    your preset in KEYZER when you're done capturing."""
    exe = shutil.which("input-remapper-control")
    if not exe:
        return
    try:
        subprocess.run([exe, "--command", "stop-all"], capture_output=True, timeout=10)
        print("Released input-remapper devices for capture "
              "(hit Apply in KEYZER again when done).\n")
    except (OSError, subprocess.SubprocessError):
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Record evdev codes for KEYZER hotspots.")
    ap.add_argument("--device", default="all",
                    help="device id from layouts.json (e.g. tartarus, naga) or 'all'")
    ap.add_argument("--output", type=Path, default=CAPTURES,
                    help=f"where to write captures (default {CAPTURES})")
    ap.add_argument("--no-grab", dest="grab", action="store_false",
                    help="don't grab the device exclusively while capturing")
    args = ap.parse_args()

    layouts = engine.load_layouts(REPO / "layouts.json")
    devices = {k: v for k, v in layouts.items() if not k.startswith("_")}
    if args.device != "all":
        if args.device not in devices:
            sys.exit(f"unknown device {args.device!r}; known: {', '.join(devices)}")
        devices = {args.device: devices[args.device]}

    if not evdev.list_devices():
        sys.exit(
            "No input devices are readable — KEYZER can't see your hardware.\n"
            "  Run ./install.sh to install the Razer udev rule, then replug the\n"
            "  device. (Adding yourself to the 'input' group also works, but\n"
            "  grants every process access to every keyboard — the rule is safer.)")

    _free_devices()
    existing = {}
    if args.output.exists():
        try:
            existing = {k: v for k, v in json.loads(args.output.read_text()).items()
                        if not k.startswith("_")}
        except (json.JSONDecodeError, OSError):
            existing = {}

    result = dict(existing)
    for dev_id, layout in devices.items():
        out = capture_device(dev_id, layout, args.grab, existing)
        if out is not None:
            result[dev_id] = out

    result = {"_comment": "Hardware-specific evdev codes per hotspot, recorded by "
                          "capture.py. Safe to delete and re-record.", **result}
    save_captures(args.output, result)

    total = sum(len(v.get("captured", {})) for v in result.values()
                if isinstance(v, dict))
    print(f"\nSaved {total} captured keys -> {args.output}")
    print("Next: open KEYZER, set your binds, and hit Apply to push them live.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\naborted.")
        sys.exit(130)
