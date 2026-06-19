"""Tests for app/capture.py — the reusable, hardware-agnostic press classifier.

The evdev read loop (next_press / open_nodes) needs real /dev/input nodes, so it
isn't unit-tested here; classify_event is pure logic over a single event and is.
We lock down exactly which events count as a deliberate 'press' and how a wheel
notch's direction becomes a signed analog_threshold. Fake event/device objects
keep this hermetic — no hardware required. Skipped cleanly if python-evdev is
absent (capture.py sys.exits on a missing import).
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

import conftest  # noqa: F401  -- sys.path + offscreen + warning filter

try:
    import capture
    from evdev import ecodes
except (ImportError, SystemExit):   # capture.py exits if python-evdev is missing
    capture = None


class _FakeDev:
    path = "/dev/input/event99"
    name = "Razer Test Device"

    def capabilities(self, absinfo=False):
        return {1: [30, 31, 32]}


def _ev(etype, code, value):
    return SimpleNamespace(type=etype, code=code, value=value)


@unittest.skipIf(capture is None, "python-evdev not available")
class ClassifyEventTests(unittest.TestCase):
    def setUp(self):
        self.dev = _FakeDev()

    def test_key_down_is_captured(self):
        p = capture.classify_event(_ev(ecodes.EV_KEY, ecodes.KEY_A, 1), self.dev)
        self.assertIsNotNone(p)
        self.assertEqual(p["type"], ecodes.EV_KEY)
        self.assertEqual(p["code"], ecodes.KEY_A)
        self.assertEqual(p["name"], "KEY_A")
        self.assertNotIn("analog_threshold", p)   # a plain key has no direction
        self.assertTrue(p["origin_hash"])         # pins the event to this node

    def test_key_up_and_autorepeat_ignored(self):
        self.assertIsNone(capture.classify_event(_ev(ecodes.EV_KEY, ecodes.KEY_A, 0), self.dev))  # up
        self.assertIsNone(capture.classify_event(_ev(ecodes.EV_KEY, ecodes.KEY_A, 2), self.dev))  # repeat

    def test_wheel_notch_records_signed_direction(self):
        up = capture.classify_event(_ev(ecodes.EV_REL, ecodes.REL_WHEEL, 1), self.dev)
        dn = capture.classify_event(_ev(ecodes.EV_REL, ecodes.REL_WHEEL, -1), self.dev)
        self.assertEqual(up["analog_threshold"], 1)
        self.assertEqual(dn["analog_threshold"], -1)

    def test_pointer_motion_never_captured(self):
        # REL_X/REL_Y are movement, not a bindable press — must be ignored.
        self.assertIsNone(capture.classify_event(_ev(ecodes.EV_REL, ecodes.REL_X, 5), self.dev))
        self.assertIsNone(capture.classify_event(_ev(ecodes.EV_REL, ecodes.REL_Y, -3), self.dev))

    def test_zero_value_rel_ignored(self):
        self.assertIsNone(capture.classify_event(_ev(ecodes.EV_REL, ecodes.REL_WHEEL, 0), self.dev))

    def test_syn_and_msc_ignored(self):
        self.assertIsNone(capture.classify_event(_ev(ecodes.EV_SYN, 0, 0), self.dev))
        self.assertIsNone(capture.classify_event(_ev(ecodes.EV_MSC, ecodes.MSC_SCAN, 1), self.dev))


if __name__ == "__main__":
    unittest.main()
