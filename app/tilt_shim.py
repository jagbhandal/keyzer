#!/usr/bin/env python3
"""KEYZER wheel-tilt shim — make the Naga Pro's wheel tilt bindable on Linux.

WHY THIS EXISTS
The Naga Pro reports wheel tilt in two bits of byte 0 of its main mouse HID
report that the firmware's own report descriptor declares as constant padding,
so the kernel discards them and produces no evdev event (the "wheel tilt is
Synapse only" myth). The bytes are on the wire though — see tilt_decode.py.

WHAT IT DOES
Read the raw mouse hidraw node, decode tilt-left / tilt-right (tilt_decode), and
re-emit each as a clean key press on a virtual uinput device that masquerades as
the Naga Pro (same USB vendor/product, distinct name). That synthetic button is
then captured and bound through KEYZER's normal input-remapper pipeline exactly
like any other Naga button — so profiles, Hypershift, and persistence all work
unchanged, and input-remapper (not this shim) handles the real output (chords,
macros, modifiers).

It only ever READS hidraw and WRITES its own uinput device; it never modifies
the real device or other input.

Run standalone (needs read on the Naga hidraw + write on /dev/uinput — see the
udev rules installed by install.sh, or run with sudo):
    python3 app/tilt_shim.py
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import select
import signal
import sys
import time
from pathlib import Path

from tilt_decode import TiltDecoder, LEFT, RIGHT, DOWN, DEFAULT_RELEASE_TIMEOUT

REPO = Path(__file__).resolve().parent.parent
_monotonic = time.monotonic   # module-level: no per-call import in the read loop

try:
    import evdev
    from evdev import ecodes, UInput
    EVDEV_OK = True
except ImportError:  # pragma: no cover - exercised only where evdev is absent
    EVDEV_OK = False

VENDOR = 0x1532
PRODUCT = 0x008F           # Naga Pro (wired)
MOUSE_IFACE = ".0001"      # the interface whose hidraw carries the tilt bits
UINPUT_NAME = "KEYZER Naga Tilt"
HID_REPORT_LEN = 256       # generous read size for one HID report

# The synthetic buttons the two tilt directions emit. BTN_TRIGGER_HAPPY1/2 are
# "extra device button" codes that don't collide with the mouse's real buttons
# (BTN_LEFT..BTN_EXTRA) or any keyboard key, so capture can't confuse them.
if EVDEV_OK:
    CODE = {LEFT: ecodes.BTN_TRIGGER_HAPPY1, RIGHT: ecodes.BTN_TRIGGER_HAPPY2}
else:  # so the module still imports for the pure-logic tests
    CODE = {LEFT: 0x2C0, RIGHT: 0x2C1}


def _select_mouse_hidraw(candidates, vendor=VENDOR, product=PRODUCT, iface=MOUSE_IFACE):
    """Pick the hidraw node for the mouse interface from pre-scanned candidates.

    ``candidates`` is a list of dicts ``{"name", "hid_id", "iface"}`` (name like
    "hidraw0", hid_id like "0003:00001532:0000008F", iface like
    "0003:1532:008F.0001"). Pure — no filesystem — so it is unit-testable.
    Returns the matching ``/dev/<name>`` or None.
    """
    vp = f"{vendor:04X}"
    pp = f"{product:04X}"
    for c in candidates:
        hid = (c.get("hid_id") or "").upper()
        if vp in hid and pp in hid and (c.get("iface") or "").upper().endswith(iface.upper()):
            return f"/dev/{c['name']}"
    return None


def _scan_hidraw_candidates(sysfs_glob="/sys/class/hidraw/hidraw*"):
    """Build the candidate list for _select_mouse_hidraw by reading sysfs."""
    out = []
    for hr in sorted(glob.glob(sysfs_glob)):
        dev = os.path.join(hr, "device")
        hid_id = ""
        try:
            with open(os.path.join(dev, "uevent")) as f:
                for ln in f:
                    if ln.startswith("HID_ID="):
                        hid_id = ln.split("=", 1)[1].strip()
                        break
        except OSError:
            continue
        try:
            iface = os.path.basename(os.path.realpath(dev))
        except OSError:
            iface = ""
        out.append({"name": os.path.basename(hr), "hid_id": hid_id, "iface": iface})
    return out


def find_mouse_hidraw(vendor=VENDOR, product=PRODUCT):
    """Locate /dev/hidrawN for the Naga Pro mouse interface, or None."""
    return _select_mouse_hidraw(_scan_hidraw_candidates(), vendor, product)


def device_usb(layout_key="naga"):
    """(vendor, product) for a device, read from layouts.json — KEYZER's single
    source of truth for device identity — falling back to the Naga Pro defaults
    if it can't be read (e.g. running the shim standalone)."""
    try:
        usb = json.loads((REPO / "layouts.json").read_text())[layout_key]["usb"]
        vendor, product = (int(x, 16) for x in usb.split(":"))
        return vendor, product
    except (OSError, KeyError, ValueError):
        return VENDOR, PRODUCT


class TiltShim:
    """Owns the hidraw read + uinput device and pumps tilt events through it."""

    def __init__(self, hidraw_path: str, release_timeout: float = DEFAULT_RELEASE_TIMEOUT):
        if not EVDEV_OK:
            raise RuntimeError("python-evdev is required to run the tilt shim")
        self._path = hidraw_path
        self._decoder = TiltDecoder(release_timeout)
        self._timeout = release_timeout
        self._fd = None
        self._ui = None
        self._stop = False

    def open(self):
        self._fd = os.open(self._path, os.O_RDONLY | os.O_NONBLOCK)
        self._ui = UInput(
            events={ecodes.EV_KEY: list(CODE.values())},
            name=UINPUT_NAME, vendor=VENDOR, product=PRODUCT,
            version=0x0111, bustype=ecodes.BUS_USB,
        )

    def _emit(self, events):
        if not events:
            return
        for direction, action in events:
            self._ui.write(ecodes.EV_KEY, CODE[direction], 1 if action == DOWN else 0)
        self._ui.syn()

    def run(self):
        """Read/decode/emit until stop() is called or the device disappears."""
        try:
            while not self._stop:
                ready, _, _ = select.select([self._fd], [], [], self._timeout)
                now = _monotonic()
                if ready:
                    try:
                        data = os.read(self._fd, HID_REPORT_LEN)
                    except (BlockingIOError, OSError):
                        break          # device unplugged — exit cleanly
                    if data:
                        self._emit(self._decoder.feed(data, now))
                else:
                    self._emit(self._decoder.tick(now))   # safety auto-release
        finally:
            self.close()

    def stop(self, *_):
        self._stop = True

    def close(self):
        if self._ui is not None:
            self._emit(self._decoder.release_all())   # never leave a key stuck
            self._ui.close()
            self._ui = None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="KEYZER Naga Pro wheel-tilt shim")
    parser.add_argument("--hidraw", help="explicit /dev/hidrawN (else auto-detect)")
    parser.add_argument("--release-timeout", type=float, default=DEFAULT_RELEASE_TIMEOUT)
    args = parser.parse_args(argv)

    if not EVDEV_OK:
        print("python-evdev is required (sudo apt install python3-evdev)", file=sys.stderr)
        return 2
    path = args.hidraw or find_mouse_hidraw(*device_usb())
    if not path:
        print("Razer Naga Pro mouse hidraw node not found (plugged in?)", file=sys.stderr)
        return 1
    try:
        shim = TiltShim(path, args.release_timeout)
        shim.open()
    except PermissionError:
        print(f"cannot open {path} / uinput — need udev rules or sudo", file=sys.stderr)
        return 1
    signal.signal(signal.SIGTERM, shim.stop)
    signal.signal(signal.SIGINT, shim.stop)
    print(f"keyzer tilt shim: reading {path}, emitting {UINPUT_NAME}", file=sys.stderr)
    shim.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
