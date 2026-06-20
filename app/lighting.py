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
# The Naga Pro exposes independent lighting zones under device.fx.misc.<zone>.
ZONES = ["logo", "scroll_wheel", "backlight", "left", "right"]
_ZONE_LABELS = {"logo": "Logo", "scroll_wheel": "Scroll wheel",
                "backlight": "Backlight", "left": "Left", "right": "Right"}


def available() -> bool:
    return importlib.util.find_spec("openrazer") is not None


# One cached DeviceManager, reused across calls (building it enumerates devices over
# DBus and blocks ~25s when the daemon is down). Reset on any error so a daemon
# restart — which leaves the cached proxies pointing at the old name owner — rebuilds.
# SINGLE-THREADED: only KEYZER's lighting worker thread touches this (the GUI never
# calls into this module), so no lock is needed.
_MGR = None


def _manager():
    """(DeviceManager, None) or (None, error-string) — never raises. Reuses a cached
    manager; call _reset_manager() to force a rebuild after a daemon error."""
    global _MGR
    if _MGR is not None:
        return _MGR, None
    try:
        from openrazer.client import DeviceManager
    except Exception as exc:  # not installed / import error
        return None, f"OpenRazer unavailable: {exc}"
    try:
        _MGR = DeviceManager()
    except Exception as exc:  # daemon not running / DBus unreachable
        return None, f"OpenRazer daemon not reachable: {exc}"
    return _MGR, None


def _reset_manager() -> None:
    """Drop the cached manager so the next _manager() rebuilds it (after a DBus error
    or a daemon restart left it stale)."""
    global _MGR
    _MGR = None


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


def _zone_fx(dev, zone):
    """Effects object for a zone, or the whole device when zone is empty."""
    if not zone:
        return dev.fx
    misc = getattr(dev.fx, "misc", None)
    return getattr(misc, zone, None) if misc is not None else None


def _zones(dev) -> list:
    """[{name,label,effects}] for the independently-controllable zones present."""
    misc = getattr(dev.fx, "misc", None)
    if misc is None:
        return []
    out = []
    for z in ZONES:
        led = getattr(misc, z, None)
        if led is None:
            continue
        caps = [e for e in EFFECTS if led.has(e)]
        if caps:
            out.append({"name": z, "label": _ZONE_LABELS.get(z, z), "effects": caps})
    return out


def devices() -> dict:
    """{keyzer_id: {name, brightness, effects:[supported]}} for connected, lit
    devices; or {"_error": ...} if lighting isn't usable."""
    if not available():
        return {"_error": "openrazer not installed"}
    mgr, err = _manager()
    if err:
        return {"_error": err}
    out = {}
    for kid in TARGETS:
        dev = _device(mgr, kid)
        if dev is None:
            continue
        try:
            out[kid] = {
                "name": dev.name,
                "brightness": float(dev.brightness),
                "effects": [e for e in EFFECTS if dev.fx.has(e)],
                "zones": _zones(dev),
            }
        except Exception as exc:
            _reset_manager()   # device went away / proxy stale -> rebuild next call
            out[kid] = {"_error": str(exc)}
    return out


def sync_enabled() -> bool:
    """Is OpenRazer mirroring effects across all Chroma devices? (a global setting)"""
    mgr, err = _manager()
    if err or mgr is None:
        return False
    try:
        return bool(mgr.sync_effects)
    except Exception:
        _reset_manager()
        return False


def set_sync(enabled: bool) -> dict:
    """Turn cross-device effect syncing on/off (OpenRazer remembers it globally)."""
    if not available():
        return {"ok": False, "error": "openrazer not installed"}
    mgr, err = _manager()
    if err:
        return {"ok": False, "error": err}
    try:
        mgr.sync_effects = bool(enabled)
    except Exception as exc:
        _reset_manager()
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


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
        _reset_manager()
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


def set_effect(kid: str, effect: str, color=(68, 214, 44), color2=(34, 200, 255),
               *, direction: str = "left", react_ms: int = 1000, zone: str = "") -> dict:
    """Apply an effect to the whole device (zone="") or one zone. color is (r,g,b).

    Covers OpenRazer's full effect set: the extra args (color2, direction,
    react_ms) drive the dual-breath, wave, and reactive effects. The UI only
    offers an effect after fx.has() confirms the device supports it."""
    if not available():
        return {"ok": False, "error": "openrazer not installed"}
    mgr, err = _manager()
    if err:
        return {"ok": False, "error": err}
    dev = _device(mgr, kid)
    if dev is None:
        return {"ok": False, "error": "device not connected"}
    fx = _zone_fx(dev, zone)
    if fx is None:
        return {"ok": False, "error": f"{dev.name} has no '{zone}' zone"}
    if not fx.has(effect):
        return {"ok": False, "error": f"{dev.name} can't do '{effect}'" + (f" on {zone}" if zone else "")}
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
            fx.wave(c.WAVE_RIGHT if direction == "right" else c.WAVE_LEFT)
        elif effect == "none":
            fx.none()
        else:
            return {"ok": False, "error": f"unknown effect '{effect}'"}
    except Exception as exc:  # any daemon/DBus hiccup must never crash the UI
        _reset_manager()
        return {"ok": False, "error": str(exc)}
    return {"ok": True}
