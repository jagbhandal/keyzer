#!/usr/bin/env python3
"""KEYZER lighting probe — show exactly what the lighting layer sees and resolves.

Run this on the machine with the hardware (as the same user who runs KEYZER),
with the OpenRazer daemon active. It is READ-ONLY by default: it lists every
OpenRazer device and shows which physical device each KEYZER id ("tartarus",
"naga") resolves to — that's what pins down a "wrong device" lighting bug.

    python3 tools/lighting_probe.py            # read-only: list + resolve
    python3 tools/lighting_probe.py --apply    # also briefly flash each target
                                               # static-red per zone to confirm
                                               # the binding visually

It imports openrazer the same way the app does and reuses app/lighting.py, so
whatever it reports is exactly what KEYZER would do.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import lighting  # noqa: E402


def hexid(v) -> str:
    try:
        return f"0x{int(v):04x}"
    except (TypeError, ValueError):
        return str(v)


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe OpenRazer device resolution for KEYZER.")
    ap.add_argument("--apply", action="store_true",
                    help="flash static-red on each resolved target+zone (changes your LEDs)")
    args = ap.parse_args()

    if not lighting.available():
        print("openrazer python client not installed.")
        return 1
    mgr, err = lighting._manager()
    if err:
        print(err)
        return 1

    print("=== every device OpenRazer reports ===")
    for dev in mgr.devices:
        vid = getattr(dev, "_vid", None)
        pid = getattr(dev, "_pid", None)
        serial = getattr(dev, "serial", "?")
        zones = lighting._zones(dev)
        print(f"  name={dev.name!r}  vid={hexid(vid)} pid={hexid(pid)}  "
              f"serial={serial!r}  zones={[z['name'] for z in zones] or '-'}")

    print("\n=== how KEYZER resolves each id (TARGETS) ===")
    for kid, spec in lighting.TARGETS.items():
        dev = lighting._device(mgr, kid)
        want = f"vid=0x{spec['vid']:04x} pid=0x{spec['pid']:04x} / name~{spec['name_substr']!r}"
        if dev is None:
            print(f"  {kid:9} -> (no match)        [wanted {want}]")
        else:
            print(f"  {kid:9} -> {dev.name!r} serial={getattr(dev,'serial','?')!r}  [wanted {want}]")

    print("\n=== lighting.devices() (what the panel shows) ===")
    for kid, info in lighting.devices().items():
        print(f"  {kid}: {info}")

    if args.apply:
        print("\n=== apply test: flashing static-red on each resolved target ===")
        for kid in lighting.TARGETS:
            dev = lighting._device(mgr, kid)
            if dev is None:
                print(f"  {kid}: no device resolved")
                continue
            for zone in [""] + [z["name"] for z in lighting._zones(dev)]:
                r = lighting.set_effect(kid, "static", (255, 0, 0), zone=zone)
                print(f"  {kid} zone={zone or 'All'}: {r}")
                time.sleep(0.6)
        print("  (watch which physical device lit red for each line above)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
