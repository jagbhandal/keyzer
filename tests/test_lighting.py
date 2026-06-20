"""Direct tests for app/lighting.py's effect-dispatch, device-matching, zone, and
brightness logic — the translation layer that drives real RGB hardware. Only the
DeviceManager build (the ~25s block when the daemon is down) is faked; everything
from _device(mgr, kid) inward runs the REAL code against small fakes — no daemon,
no hardware. Mirrors the _FakeDev style in tests/test_capture.py.
"""
from __future__ import annotations

import unittest

import conftest  # noqa: F401  -- sys.path + offscreen + warning filter

import lighting
from openrazer.client import constants as c


class _FakeFx:
    """Records effect calls and reports per-effect capability via has()."""

    def __init__(self, caps, misc=None):
        self._caps = set(caps)
        self.calls = []
        self.misc = misc

    def has(self, e):
        return e in self._caps

    def static(self, r, g, b): self.calls.append(("static", (r, g, b)))
    def spectrum(self): self.calls.append(("spectrum", ()))
    def breath_single(self, r, g, b): self.calls.append(("breath_single", (r, g, b)))
    def breath_dual(self, r, g, b, r2, g2, b2): self.calls.append(("breath_dual", (r, g, b, r2, g2, b2)))
    def breath_random(self): self.calls.append(("breath_random", ()))
    def reactive(self, r, g, b, speed): self.calls.append(("reactive", (r, g, b, speed)))
    def wave(self, direction): self.calls.append(("wave", (direction,)))
    def none(self): self.calls.append(("none", ()))


class _Misc:
    """A bag of per-zone SingleLed fakes (device.fx.misc.<zone>)."""
    def __init__(self, **zones):
        for name, led in zones.items():
            setattr(self, name, led)


class _FakeDev:
    def __init__(self, name, fx, vid=None, pid=None, brightness=50.0):
        self.name = name
        self.fx = fx
        self._vid = vid
        self._pid = pid
        self.brightness = brightness


class _FakeMgr:
    def __init__(self, devices, sync=False):
        self.devices = devices
        self.sync_effects = sync


_ALL = ("static", "spectrum", "breath_single", "breath_dual",
        "breath_random", "reactive", "wave", "none")


class _LightingTest(unittest.TestCase):
    def setUp(self):
        self._saved = {n: getattr(lighting, n) for n in ("_manager", "available")}
        lighting.available = lambda: True
        self.mgr = _FakeMgr([])
        lighting._manager = lambda: (self.mgr, None)

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(lighting, n, v)

    def _naga(self, caps=_ALL, misc=None, vid=0x1532, pid=0x008F, brightness=50.0):
        fx = _FakeFx(caps, misc=misc)
        dev = _FakeDev("Razer Naga Pro", fx, vid=vid, pid=pid, brightness=brightness)
        self.mgr.devices = [dev]
        return dev, fx


class MatchTests(_LightingTest):
    def test_match_prefers_vid_pid(self):
        self._naga(vid=0x1532, pid=0x008F)
        self.assertIn("naga", lighting.devices())

    def test_match_falls_back_to_name_when_ids_absent(self):
        self._naga(vid=None, pid=None)        # client didn't expose vid/pid
        self.assertIn("naga", lighting.devices())

    def test_wrong_vid_pid_does_not_match_even_with_right_name(self):
        self._naga(vid=0x046D, pid=0xC52B)    # right name, wrong ids -> ids win
        self.assertNotIn("naga", lighting.devices())


class EffectDispatchTests(_LightingTest):
    def test_static_passes_colour(self):
        _, fx = self._naga()
        self.assertTrue(lighting.set_effect("naga", "static", (1, 2, 3))["ok"])
        self.assertEqual(fx.calls, [("static", (1, 2, 3))])

    def test_spectrum_random_none_take_no_colour(self):
        _, fx = self._naga()
        for eff in ("spectrum", "breath_random", "none"):
            fx.calls.clear()
            self.assertTrue(lighting.set_effect("naga", eff, (9, 9, 9))["ok"])
            self.assertEqual(fx.calls, [(eff, ())])

    def test_breath_single_and_dual(self):
        _, fx = self._naga()
        lighting.set_effect("naga", "breath_single", (1, 2, 3))
        lighting.set_effect("naga", "breath_dual", (1, 2, 3), color2=(4, 5, 6))
        self.assertEqual(fx.calls, [("breath_single", (1, 2, 3)),
                                    ("breath_dual", (1, 2, 3, 4, 5, 6))])

    def test_reactive_speed_map(self):
        _, fx = self._naga()
        for ms, const in [(500, c.REACTIVE_500MS), (1000, c.REACTIVE_1000MS),
                          (1500, c.REACTIVE_1500MS), (2000, c.REACTIVE_2000MS),
                          (1234, c.REACTIVE_1000MS)]:   # unknown -> 1000ms default
            fx.calls.clear()
            lighting.set_effect("naga", "reactive", (1, 2, 3), react_ms=ms)
            self.assertEqual(fx.calls, [("reactive", (1, 2, 3, const))])

    def test_wave_direction_map(self):
        _, fx = self._naga()
        lighting.set_effect("naga", "wave", (0, 0, 0), direction="right")
        lighting.set_effect("naga", "wave", (0, 0, 0), direction="left")
        self.assertEqual(fx.calls, [("wave", (c.WAVE_RIGHT,)), ("wave", (c.WAVE_LEFT,))])

    def test_unsupported_effect_makes_no_fx_call(self):
        _, fx = self._naga(caps=("spectrum", "none"))   # device can't do static
        r = lighting.set_effect("naga", "static", (1, 2, 3))
        self.assertFalse(r["ok"])
        self.assertEqual(fx.calls, [])
        self.assertIn("can't do", r["error"])

    def test_device_not_connected(self):
        self.mgr.devices = []
        r = lighting.set_effect("naga", "static", (1, 2, 3))
        self.assertFalse(r["ok"])
        self.assertIn("not connected", r["error"])


class ZoneTests(_LightingTest):
    def test_effect_targets_a_zone(self):
        logo = _FakeFx(("static", "none"))
        self._naga(misc=_Misc(logo=logo))
        self.assertTrue(lighting.set_effect("naga", "static", (7, 8, 9), zone="logo")["ok"])
        self.assertEqual(logo.calls, [("static", (7, 8, 9))])

    def test_unknown_zone_errors_without_call(self):
        self._naga(misc=_Misc())            # no zones present
        r = lighting.set_effect("naga", "static", (1, 2, 3), zone="logo")
        self.assertFalse(r["ok"])
        self.assertIn("zone", r["error"])

    def test_zones_listing_filters_to_capable_leds(self):
        logo = _FakeFx(("static", "spectrum", "none"))
        empty = _FakeFx(())                 # an LED with no usable caps -> dropped
        self._naga(misc=_Misc(logo=logo, scroll_wheel=empty))
        zones = lighting.devices()["naga"]["zones"]
        names = {z["name"]: z for z in zones}
        self.assertIn("logo", names)
        self.assertNotIn("scroll_wheel", names)        # zeroless LED dropped
        self.assertEqual(set(names["logo"]["effects"]), {"static", "spectrum", "none"})


class BrightnessTests(_LightingTest):
    def test_clamps_into_0_100(self):
        dev, _ = self._naga()
        lighting.set_brightness("naga", -10); self.assertEqual(dev.brightness, 0.0)
        lighting.set_brightness("naga", 150); self.assertEqual(dev.brightness, 100.0)
        lighting.set_brightness("naga", 42); self.assertEqual(dev.brightness, 42.0)


if __name__ == "__main__":
    unittest.main()
