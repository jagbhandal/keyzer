"""KEYZER lighting — optional Razer RGB via OpenRazer, kept at arm's length.

Like the input-remapper engine, OpenRazer is a black box and an OPTIONAL
dependency: we ``import openrazer`` lazily *inside* each function so this module
imports fine when it's absent, and every public call returns ``{"ok": False,
"error": ...}`` (or ``{"_error": ...}``) instead of raising. The UI gates on
``available()`` (mirrors ``backend.deps.openrazer``) and falls back to the
in-app glow preview when lighting isn't usable.

Verified against OpenRazer master: Razer Tartarus Pro (1532:0244) and Razer Naga
Pro (1532:008f) are both supported. Effects offered are filtered per device by
``device.fx.has(<effect>)`` so we never call something the hardware can't do.
"""
from __future__ import annotations

import importlib.util

# layouts.json device id -> USB identity (+ name fallback, since the OpenRazer
# client exposes vid/pid only as private attrs).
TARGETS = {
    "tartarus": {"vid": 0x1532, "pid": 0x0244, "name_substr": "Tartarus Pro"},
    "naga":     {"vid": 0x1532, "pid": 0x008F, "name_substr": "Naga Pro"},
}
# Whole-device effects we expose; the UI shows only those a device actually has.
EFFECTS = ["static", "spectrum", "breath_single", "breath_dual",
           "breath_random", "reactive", "wave", "none"]


def available() -> bool:
    return importlib.util.find_spec("openrazer") is not None


def _manager():
    """(DeviceManager, None) or (None, error-string) — never raises."""
    try:
        from openrazer.client import DeviceManager
    except Exception as exc:  # not installed / import error
        return None, f"OpenRazer unavailable: {exc}"
    try:
        return DeviceManager(), None
    except Exception as exc:  # daemon not running / DBus unreachable
        return None, f"OpenRazer daemon not reachable: {exc}"


def _match(dev, spec) -> bool:
    vid, pid = getattr(dev, "_vid", None), getattr(dev, "_pid", None)
    if vid is not None and pid is not None:
        return vid == spec["vid"] and pid == spec["pid"]
    return spec["name_substr"].lower() in (getattr(dev, "name", "") or "").lower()


def _device(mgr, kid):
    spec = TARGETS.get(kid)
    if spec is None:
        return None
    for dev in mgr.devices:
        if _match(dev, spec):
            return dev
    return None


def devices() -> dict:
    """{keyzer_id: {name, brightness, effects:[supported]}} for connected, lit
    devices; or {"_error": ...} if lighting isn't usable."""
    if not available():
        return {"_error": "openrazer not installed"}
    mgr, err = _manager()
    if err:
        return {"_error": err}
    out = {}
    for kid, spec in TARGETS.items():
        dev = _device(mgr, spec and kid)
        if dev is None:
            continue
        try:
            out[kid] = {
                "name": dev.name,
                "brightness": float(dev.brightness),
                "effects": [e for e in EFFECTS if dev.fx.has(e)],
            }
        except Exception as exc:
            out[kid] = {"_error": str(exc)}
    return out


def set_brightness(kid: str, pct: float) -> dict:
    if not available():
        return {"ok": False, "error": "openrazer not installed"}
    mgr, err = _manager()
    if err:
        return {"ok": False, "error": err}
    dev = _device(mgr, kid)
    if dev is None:
        return {"ok": False, "error": "device not connected"}
    try:
        dev.brightness = max(0.0, min(100.0, float(pct)))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


def set_effect(kid: str, effect: str, color=(68, 214, 44), color2=(34, 200, 255),
               *, direction: str = "left", react_ms: int = 1000) -> dict:
    """Apply a whole-device effect. color/color2 are (r,g,b) 0-255."""
    if not available():
        return {"ok": False, "error": "openrazer not installed"}
    mgr, err = _manager()
    if err:
        return {"ok": False, "error": err}
    dev = _device(mgr, kid)
    if dev is None:
        return {"ok": False, "error": "device not connected"}
    fx = dev.fx
    if not fx.has(effect):
        return {"ok": False, "error": f"{dev.name} can't do '{effect}'"}
    try:
        from openrazer.client import constants as c
        r, g, b = (int(x) for x in color)
        r2, g2, b2 = (int(x) for x in color2)
        if effect == "static":
            fx.static(r, g, b)
        elif effect == "spectrum":
            fx.spectrum()
        elif effect == "breath_single":
            fx.breath_single(r, g, b)
        elif effect == "breath_dual":
            fx.breath_dual(r, g, b, r2, g2, b2)
        elif effect == "breath_random":
            fx.breath_random()
        elif effect == "reactive":
            speed = {500: c.REACTIVE_500MS, 1000: c.REACTIVE_1000MS,
                     1500: c.REACTIVE_1500MS, 2000: c.REACTIVE_2000MS}.get(react_ms, c.REACTIVE_1000MS)
            fx.reactive(r, g, b, speed)
        elif effect == "wave":
            fx.wave(c.WAVE_LEFT if direction == "left" else c.WAVE_RIGHT)
        elif effect == "none":
            fx.none()
        else:
            return {"ok": False, "error": f"unknown effect '{effect}'"}
    except Exception as exc:  # any daemon/DBus hiccup must never crash the UI
        return {"ok": False, "error": str(exc)}
    return {"ok": True}
