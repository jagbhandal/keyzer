"""Tests for the Naga Pro wheel-tilt decoder (pure logic, no hardware).

The bytes used here are the REAL reports captured from the device's
/dev/hidraw0 (mouse interface .0001) while tilting the wheel — see the
investigation that established data[0] bit 5 = tilt-left, bit 6 = tilt-right
(matching OpenRazer's BIT_TILT_L=5 / BIT_TILT_R=6).
"""
import unittest

import conftest  # noqa: F401  (sets sys.path so `import tilt_decode` works)
from tilt_decode import TiltDecoder, LEFT, RIGHT, DOWN, UP


def rpt(b0):
    """An 8-byte mouse report with byte0=b0 and the rest zero."""
    return bytes([b0, 0, 0, 0, 0, 0, 0, 0])


# real captures from the device
LEFT_CLEAN = bytes.fromhex("2000000000000000")   # bit5 set, nothing else
RIGHT_CLEAN = bytes.fromhex("4000000000000000")  # bit6 set
RELEASE = bytes.fromhex("0000000000000000")      # all clear
LEFT_JITTER = bytes.fromhex("2000ff00000000ffff")[:8]   # bit5 + tiny mouse move
RIGHT_JITTER = bytes.fromhex("40ff000000ffff0000")[:8]  # bit6 + tiny mouse move


class TestTiltDecode(unittest.TestCase):
    def setUp(self):
        self.d = TiltDecoder(release_timeout=0.15)

    def test_left_press_emits_down(self):
        self.assertEqual(self.d.feed(LEFT_CLEAN, now=0.0), [(LEFT, DOWN)])

    def test_right_press_emits_down(self):
        self.assertEqual(self.d.feed(RIGHT_CLEAN, now=0.0), [(RIGHT, DOWN)])

    def test_explicit_release_emits_up(self):
        self.d.feed(LEFT_CLEAN, now=0.0)
        self.assertEqual(self.d.feed(RELEASE, now=0.01), [(LEFT, UP)])

    def test_held_repeat_does_not_re_emit_down(self):
        # device repeats the tilt bit while held — only ONE down should fire
        self.assertEqual(self.d.feed(LEFT_CLEAN, now=0.0), [(LEFT, DOWN)])
        self.assertEqual(self.d.feed(LEFT_CLEAN, now=0.02), [])
        self.assertEqual(self.d.feed(LEFT_CLEAN, now=0.04), [])

    def test_jitter_report_keeps_tilt_not_a_new_press(self):
        self.d.feed(LEFT_CLEAN, now=0.0)
        # a report with the bit still set plus mouse-move noise must not re-press
        self.assertEqual(self.d.feed(LEFT_JITTER, now=0.02), [])

    def test_jitter_can_start_a_press(self):
        self.assertEqual(self.d.feed(RIGHT_JITTER, now=0.0), [(RIGHT, DOWN)])

    def test_plain_mouse_move_emits_nothing(self):
        move = bytes([0x00, 0x00, 0x00, 0x00, 0x05, 0x00, 0x05, 0x00])
        self.assertEqual(self.d.feed(move, now=0.0), [])

    def test_direction_switch_left_to_right(self):
        self.assertEqual(self.d.feed(LEFT_CLEAN, now=0.0), [(LEFT, DOWN)])
        # tilt the other way without an explicit release in between
        evs = self.d.feed(RIGHT_CLEAN, now=0.01)
        self.assertIn((LEFT, UP), evs)
        self.assertIn((RIGHT, DOWN), evs)
        self.assertLess(evs.index((LEFT, UP)), evs.index((RIGHT, DOWN)))  # release first

    def test_auto_release_after_timeout_without_explicit_clear(self):
        # safety net: device stops sending reports mid-hold (missed clear)
        self.d.feed(LEFT_CLEAN, now=0.0)
        self.assertEqual(self.d.tick(now=0.10), [])             # still within timeout
        self.assertEqual(self.d.tick(now=0.20), [(LEFT, UP)])   # timed out -> released
        self.assertEqual(self.d.tick(now=0.30), [])             # idempotent

    def test_tick_does_not_release_while_reports_keep_arriving(self):
        self.d.feed(LEFT_CLEAN, now=0.0)
        self.d.feed(LEFT_CLEAN, now=0.14)
        self.assertEqual(self.d.tick(now=0.20), [])  # last seen 0.14, 0.20-0.14<0.15

    def test_both_bits_set_presses_both(self):
        evs = self.d.feed(bytes([0x60, 0, 0, 0, 0, 0, 0, 0]), now=0.0)  # 0x20|0x40
        self.assertIn((LEFT, DOWN), evs)
        self.assertIn((RIGHT, DOWN), evs)

    def test_short_report_is_ignored_safely(self):
        self.assertEqual(self.d.feed(b"", now=0.0), [])
        self.assertEqual(self.d.feed(b"\x20", now=0.0), [(LEFT, DOWN)])  # 1 byte is enough


if __name__ == "__main__":
    unittest.main()
