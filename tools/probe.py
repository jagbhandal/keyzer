#!/usr/bin/env python3
"""Raw evdev probe — show exactly what a Razer control emits (read-only).

Opens every /dev/input node for a device (by USB id) WITHOUT grabbing and prints
each key/rel/abs event with its name. Use it when capture.py records nothing for a
control (e.g. the Naga wheel tilt) to find out what — if anything — it sends.

Usage:  python3 tools/probe.py [vendor:product]    (default 1532:008f = Naga Pro)
Tilt the wheel left/right, scroll, click a button, then press Ctrl-C.
"""
import select
import shutil
import subprocess
import sys

import evdev
from evdev import ecodes

usb = sys.argv[1] if len(sys.argv) > 1 else "1532:008f"
try:
    vendor, product = (int(x, 16) for x in usb.split(":"))
except ValueError:
    sys.exit("usage: probe.py [vendor:product]  e.g. 1532:008f")

# release the device from input-remapper so we observe the raw hardware
exe = shutil.which("input-remapper-control")
if exe:
    try:
        subprocess.run([exe, "--command", "stop-all"], capture_output=True, timeout=10)
    except Exception:
        pass

nodes = []
for path in evdev.list_devices():
    try:
        dev = evdev.InputDevice(path)
    except Exception:
        continue
    if dev.info.vendor == vendor and dev.info.product == product:
        nodes.append(dev)
    else:
        dev.close()
if not nodes:
    sys.exit(f"no device {usb} found — plugged in and readable?")

print(f"Watching {usb} on: {[d.path for d in nodes]}")
print("Tilt the wheel LEFT, then RIGHT; scroll up/down; click a button — then Ctrl-C.\n")

seen = set()
fds = {d.fd: d for d in nodes}
try:
    while True:
        ready, _, _ = select.select(list(fds), [], [])
        for fd in ready:
            for ev in fds[fd].read():
                if ev.type in (ecodes.EV_SYN, ecodes.EV_MSC):
                    continue
                tname = ecodes.EV.get(ev.type, str(ev.type))
                cname = ecodes.bytype.get(ev.type, {}).get(ev.code, ev.code)
                if isinstance(cname, (list, tuple)):
                    cname = "/".join(cname)
                print(f"  {tname:7} {cname}  (code {ev.code})  value={ev.value}")
                seen.add((tname, str(cname), ev.code))
except KeyboardInterrupt:
    print("\n=== distinct events seen ===")
    for t, c, code in sorted(seen):
        print(f"  {t:7} {c}  code={code}")
    if not seen:
        print("  (nothing — the control emits no evdev event; likely Synapse/onboard-only)")
    print("\nPaste this list back.")
