#!/usr/bin/env python3
"""KEYZER capture — learn what each physical key on your Razer gear emits.

Run this ON the machine with the hardware, as a user who can read /dev/input
(your normal desktop login usually can; otherwise add yourself to the ``input``
group and log back in — see the message printed if access is denied).

For every hotspot defined in ``layouts.json`` it asks you to press the matching
physical key, then records the evdev event it sees — the ``(type, code)`` plus an
``origin_hash`` that pins the event to that exact device node. The result lands
in ``~/.config/keyzer/captures.json``; that file is what lets KEYZER turn a
profile (hotspot -> output) into a real input-remapper preset (input -> output).

It reads evdev directly and read-only. It never writes to your devices and does
not need input-remapper to be running. While capturing a device its events are
grabbed exclusively (so pressing keys doesn't trigger anything) — press Ctrl-C
to abort at any time and the grab is always released.

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

REPO = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "keyzer"
CAPTURES = CONFIG_DIR / "captures.json"

# Event types we treat as a deliberate "press". EV_KEY down = a button/key;
# EV_REL with a nonzero value = a wheel tilt / scroll notch (its sign is the
# direction, stored as analog_threshold so input-remapper can match it).
IGNORED_TYPES = {ecodes.EV_SYN, ecodes.EV_MSC}


def device_hash(dev: "evdev.InputDevice") -> str:
    """Reproduce input-remapper's get_device_hash so our origin_hash matches the
    one its injector computes for the same node (utils.get_device_hash)."""
    s = str(dev.capabilities(absinfo=False)) + dev.name
    return hashlib.md5(s.encode()).hexdigest().lower()


def code_name(etype: int, code: int) -> str:
    """Human label for an event, e.g. 'KEY_ESC' / 'BTN_LEFT' (display only)."""
    name = ecodes.bytype.get(etype, {}).get(code, str(code))
    return name[0] if isinstance(name, (list, tuple)) else name


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


def read_press(nodes, fd_to_dev, grabbed):
    """Block until a real press arrives on one of the device nodes, or the user
    types a command. Returns ("event", payload) or ("cmd", text)."""
    fds = list(fd_to_dev) + [sys.stdin.fileno()]
    while True:
        ready, _, _ = select.select(fds, [], [])
        if sys.stdin.fileno() in ready:
            return "cmd", sys.stdin.readline().strip().lower()
        for fd in ready:
            dev = fd_to_dev.get(fd)
            if dev is None:
                continue
            try:
                events = list(dev.read())
            except BlockingIOError:
                continue
            for ev in events:
                if ev.type in IGNORED_TYPES:
                    continue
                if ev.type == ecodes.EV_KEY and ev.value == 1:  # key/button down
                    return "event", {"type": ev.type, "code": ev.code,
                                     "origin_hash": device_hash(dev),
                                     "node": dev.path, "name": code_name(ev.type, ev.code)}
                if ev.type == ecodes.EV_REL and ev.value != 0:  # wheel tilt / notch
                    return "event", {"type": ev.type, "code": ev.code,
                                     "analog_threshold": 1 if ev.value > 0 else -1,
                                     "origin_hash": device_hash(dev),
                                     "node": dev.path, "name": code_name(ev.type, ev.code)}
                # key-up / autorepeat / zero rel -> ignore, keep waiting


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
    try:
        vendor, product = (int(x, 16) for x in usb.split(":"))
    except ValueError:
        print(f"  ! {dev_id}: bad usb id {usb!r} in layouts.json — skipping.")
        return None

    nodes = find_nodes(vendor, product)
    if not nodes:
        print(f"  ! {dev_layout.get('name', dev_id)} ({usb}) not found. "
              "Is it plugged in? Can you read /dev/input?")
        return None

    name = preset_dir_name(nodes)
    hotspots = [k for view in dev_layout["views"].values() for k in view["keys"]]
    print(f"\n=== {dev_layout.get('name', dev_id)} — {len(hotspots)} keys "
          f"(device '{name}', {len(nodes)} nodes) ===")
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
            kind, payload = read_press(nodes, fd_to_dev, grabbed)
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
            entry = {"type": payload["type"], "code": payload["code"],
                     "origin_hash": payload["origin_hash"]}
            if "analog_threshold" in payload:
                entry["analog_threshold"] = payload["analog_threshold"]
            captured[kid] = entry
            print(f"      got {payload['name']}  (type={payload['type']} "
                  f"code={payload['code']})")
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

    return {"device_name": name, "all_names": sorted({d.name for d in nodes}),
            "usb": usb, "captured": captured}


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

    layouts = json.loads((REPO / "layouts.json").read_text())
    devices = {k: v for k, v in layouts.items() if not k.startswith("_")}
    if args.device != "all":
        if args.device not in devices:
            sys.exit(f"unknown device {args.device!r}; known: {', '.join(devices)}")
        devices = {args.device: devices[args.device]}

    if not evdev.list_devices():
        sys.exit(
            "No input devices are readable — KEYZER can't see your hardware.\n"
            "  Add yourself to the 'input' group and log back in:\n"
            "      sudo usermod -aG input \"$USER\"\n"
            "  (then re-login). Running this whole script under sudo also works.")

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
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")

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
