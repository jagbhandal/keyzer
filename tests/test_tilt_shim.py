"""Tests for tilt_shim — hidraw node selection (pure) and uinput emit mapping.

The hidraw read loop and real UInput need hardware, so those are exercised only
through the pure helpers and a mocked uinput, mirroring tests/test_capture.py.
"""
import unittest
from unittest import mock

import conftest  # noqa: F401  (sys.path)
import tilt_shim
from tilt_shim import _select_mouse_hidraw, TiltShim
from tilt_decode import LEFT, RIGHT, DOWN, UP


# what _scan_hidraw_candidates would produce for the real Naga Pro: four HID
# interfaces, only .0001 is the mouse node that carries the tilt bits.
NAGA_CANDIDATES = [
    {"name": "hidraw0", "hid_id": "0003:00001532:0000008F", "iface": "0003:1532:008F.0001"},
    {"name": "hidraw1", "hid_id": "0003:00001532:0000008F", "iface": "0003:1532:008F.0002"},
    {"name": "hidraw2", "hid_id": "0003:00001532:0000008F", "iface": "0003:1532:008F.0003"},
    {"name": "hidraw3", "hid_id": "0003:00001532:0000008F", "iface": "0003:1532:008F.0004"},
]


class TestSelectMouseHidraw(unittest.TestCase):
    def test_picks_the_mouse_interface_0001(self):
        self.assertEqual(_select_mouse_hidraw(NAGA_CANDIDATES), "/dev/hidraw0")

    def test_ignores_other_vendor_product(self):
        others = [{"name": "hidraw9", "hid_id": "0003:0000046D:0000C52B",
                   "iface": "0003:046D:C52B.0001"}]
        self.assertIsNone(_select_mouse_hidraw(others))

    def test_returns_none_when_only_non_mouse_interfaces_present(self):
        no_mouse = [c for c in NAGA_CANDIDATES if not c["iface"].endswith(".0001")]
        self.assertIsNone(_select_mouse_hidraw(no_mouse))

    def test_matches_regardless_of_hex_case(self):
        lowered = [{"name": "hidraw0", "hid_id": "0003:00001532:0000008f",
                    "iface": "0003:1532:008f.0001"}]
        self.assertEqual(_select_mouse_hidraw(lowered), "/dev/hidraw0")


@unittest.skipUnless(tilt_shim.EVDEV_OK, "python-evdev not available")
class TestEmit(unittest.TestCase):
    def _shim(self):
        shim = TiltShim("/dev/null")          # __init__ does no I/O
        shim._ui = mock.MagicMock()
        return shim

    def test_down_emits_key_press_then_syn(self):
        from evdev import ecodes
        shim = self._shim()
        shim._emit([(LEFT, DOWN)])
        shim._ui.write.assert_called_once_with(
            ecodes.EV_KEY, tilt_shim.CODE[LEFT], 1)
        shim._ui.syn.assert_called_once()

    def test_up_emits_key_release(self):
        from evdev import ecodes
        shim = self._shim()
        shim._emit([(RIGHT, UP)])
        shim._ui.write.assert_called_once_with(
            ecodes.EV_KEY, tilt_shim.CODE[RIGHT], 0)

    def test_left_and_right_use_distinct_codes(self):
        self.assertNotEqual(tilt_shim.CODE[LEFT], tilt_shim.CODE[RIGHT])

    def test_empty_events_emit_nothing(self):
        shim = self._shim()
        shim._emit([])
        shim._ui.write.assert_not_called()
        shim._ui.syn.assert_not_called()

    def test_batched_events_one_syn(self):
        shim = self._shim()
        shim._emit([(LEFT, UP), (RIGHT, DOWN)])
        self.assertEqual(shim._ui.write.call_count, 2)
        shim._ui.syn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
