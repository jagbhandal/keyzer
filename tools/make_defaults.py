#!/usr/bin/env python3
"""Generate KEYZER's bundled default key map from a real capture.

Reads a ``captures.json`` (yours, by default), strips the environment-specific
bits — the ``origin_hash`` and the ``/dev`` node — and writes a portable
``captures.default.json``. KEYZER ships that file and falls back to it when a
user hasn't run ``capture.py``, so a fresh clone remaps out of the box.

Run this on a device with its FACTORY (default) onboard profile, so the shared
codes match what other people's devices emit by default. Output goes to /tmp,
world-readable, for the maintainer to drop into the repo root.

Usage:
    python3 tools/make_defaults.py [path/to/captures.json]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SRC = (Path(sys.argv[1]) if len(sys.argv) > 1 else
       Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
       / "keyzer" / "captures.json")
OUT = Path("/tmp/keyzer-captures-default.json")


def main() -> int:
    if not SRC.exists():
        sys.exit(f"no capture at {SRC} — run capture.py first")
    data = json.loads(SRC.read_text())
    out = {"_comment": "Bundled default evdev codes per hotspot, origin_hash stripped so "
                       "it's portable across machines. KEYZER uses this when the user hasn't "
                       "run capture.py. Regenerate with tools/make_defaults.py."}
    for dev, v in data.items():
        if dev.startswith("_") or not isinstance(v, dict):
            continue
        caps = {}
        for hid, c in (v.get("captured") or {}).items():
            entry = {"type": int(c["type"]), "code": int(c["code"])}
            if c.get("analog_threshold"):
                entry["analog_threshold"] = int(c["analog_threshold"])
            caps[hid] = entry                # origin_hash + node intentionally dropped
        out[dev] = {"device_name": v.get("device_name", ""),
                    "usb": v.get("usb", ""), "captured": caps}
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    os.chmod(OUT, 0o644)
    devs = [k for k in out if not k.startswith("_")]
    total = sum(len(out[d].get("captured", {})) for d in devs)
    print(f"wrote {OUT}")
    print(f"  {total} keys across {len(devs)} device(s): {', '.join(devs)}")
    print("It's world-readable — the maintainer can pick it up and commit it as "
          "captures.default.json at the repo root.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
