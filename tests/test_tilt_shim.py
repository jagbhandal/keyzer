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


# what _scan_hidraw_candidates produces for the real Naga Pro: four HID
# interfaces sharing one VID/PID. The hidraw node numbers and the HID instance
# counters (the live device reports .001F-.0022, NEVER .0001) carry no interface
# meaning, so selection keys on the report descriptor actually declaring a Mouse
# usage (is_mouse) — that is the interface carrying the tilt bits.
NAGA_CANDIDATES = [
    {"name": "hidraw0", "hid_id": "0003:00001532:0000008F", "is_mouse": True,  "interface": "00"},
    {"name": "hidraw1", "hid_id": "0003:00001532:0000008F", "is_mouse": False, "interface": "02"},
    {"name": "hidraw2", "hid_id": "0003:00001532:0000008F", "is_mouse": False, "interface": "01"},
    {"name": "hidraw3", "hid_id": "0003:00001532:0000008F", "is_mouse": False, "interface": "03"},
]


class TestSelectMouseHidraw(unittest.TestCase):
    def test_picks_the_mouse_usage_interface(self):
        self.assertEqual(_select_mouse_hidraw(NAGA_CANDIDATES), "/dev/hidraw0")

    def test_real_world_hid_instance_counters_do_not_break_selection(self):
        # Regression for the .0001 bug: the live device's HID instance counters
        # never end in .0001, so any positional-suffix match returns None and the
        # shim can never start. Selection must depend only on the Mouse usage.
        self.assertEqual(_select_mouse_hidraw(NAGA_CANDIDATES), "/dev/hidraw0")

    def test_ignores_other_vendor_product(self):
        others = [{"name": "hidraw9", "hid_id": "0003:0000046D:0000C52B",
                   "is_mouse": True, "interface": "00"}]
        self.assertIsNone(_select_mouse_hidraw(others))

    def test_returns_none_when_no_mouse_usage_interface(self):
        no_mouse = [{**c, "is_mouse": False} for c in NAGA_CANDIDATES]
        self.assertIsNone(_select_mouse_hidraw(no_mouse))

    def test_matches_regardless_of_hex_case(self):
        lowered = [{"name": "hidraw0", "hid_id": "0003:00001532:0000008f",
                    "is_mouse": True, "interface": "00"}]
        self.assertEqual(_select_mouse_hidraw(lowered), "/dev/hidraw0")

    def test_picks_lowest_interface_when_multiple_mouse_usages(self):
        dupe = [
            {"name": "hidrawB", "hid_id": "0003:00001532:0000008F", "is_mouse": True, "interface": "02"},
            {"name": "hidrawA", "hid_id": "0003:00001532:0000008F", "is_mouse": True, "interface": "00"},
        ]
        self.assertEqual(_select_mouse_hidraw(dupe), "/dev/hidrawA")


class TestDescriptorIsMouse(unittest.TestCase):
    def test_mouse_application_collection_detected(self):
        # 05 01 09 02 a1 01 = Usage Page (Generic Desktop), Usage (Mouse),
        # Collection (Application) — the real Naga mouse descriptor head.
        self.assertTrue(tilt_shim._descriptor_is_mouse(bytes.fromhex("05010902a1010901")))

    def test_keyboard_descriptor_is_not_mouse(self):
        # 05 01 09 06 = Usage (Keyboard) — a sibling Naga interface.
        self.assertFalse(tilt_shim._descriptor_is_mouse(bytes.fromhex("05010906a1010507")))

    def test_vendor_descriptor_is_not_mouse(self):
        self.assertFalse(tilt_shim._descriptor_is_mouse(bytes.fromhex("05590901a1018501")))

    def test_empty_descriptor_is_not_mouse(self):
        self.assertFalse(tilt_shim._descriptor_is_mouse(b""))

    def test_nested_mouse_usage_is_not_the_primary_collection(self):
        # Anchored at the start: a descriptor that only nests the mouse usage
        # later (a non-mouse primary collection) is not the tilt-bearing node.
        nested = bytes.fromhex("0509") + tilt_shim.MOUSE_USAGE_PREFIX
        self.assertFalse(tilt_shim._descriptor_is_mouse(nested))


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
