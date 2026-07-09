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


class _FakeNode:
    """A fake evdev node for the select-driven read loops."""
    path = "/dev/input/event99"

    def __init__(self, fd, events=(), name="Razer Test Device"):
        self.fd = fd
        self._events = list(events)
        self.name = name

    def capabilities(self, absinfo=False):
        return {1: [30, 31, 32]}

    def read(self):
        # one-shot, like a real non-blocking node: events once, then "nothing pending"
        if not self._events:
            raise BlockingIOError()
        evs, self._events = self._events, []
        return iter(evs)


@unittest.skipIf(capture is None, "python-evdev not available")
class DeviceResolutionTests(unittest.TestCase):
    """open_nodes / preset_dir_name — pure logic, a real source of 'device not found'."""

    def test_malformed_or_empty_usb_returns_none(self):
        for usb in ("bad", "", "1532", "zz:zz"):
            self.assertEqual(capture.open_nodes({"usb": usb}), (None, []))
        self.assertEqual(capture.open_nodes({}), (None, []))   # missing key

    def test_valid_usb_but_no_device_plugged(self):
        saved = capture.find_nodes
        capture.find_nodes = lambda v, p: []
        try:
            self.assertEqual(capture.open_nodes({"usb": "1532:008f"}), (None, []))
        finally:
            capture.find_nodes = saved

    def test_valid_usb_returns_shortest_name_and_nodes(self):
        nodes = [_FakeNode(5, name="Razer Naga Pro Extra Keyboard"),
                 _FakeNode(6, name="Razer Naga Pro")]
        saved = capture.find_nodes
        capture.find_nodes = lambda v, p: nodes
        try:
            name, got = capture.open_nodes({"usb": "1532:008f"})
            self.assertEqual(name, "Razer Naga Pro")   # input-remapper picks shortest
            self.assertEqual(got, nodes)
        finally:
            capture.find_nodes = saved

    def test_preset_dir_name_picks_shortest(self):
        nodes = [_FakeNode(1, name="AAA Mouse"), _FakeNode(2, name="AA"),
                 _FakeNode(3, name="AAAA Control")]
        self.assertEqual(capture.preset_dir_name(nodes), "AA")


def _scripted_select(*returns):
    """A select.select stand-in that yields each of ``returns`` in turn, repeating
    the last forever — so a chord drain's follow-up selects are deterministic."""
    seq = list(returns)
    def _sel(r, w, x, t=None):
        return seq.pop(0) if len(seq) > 1 else seq[0]
    return _sel


@unittest.skipIf(capture is None, "python-evdev not available")
class NextPressTests(unittest.TestCase):
    def test_timeout_returns_none(self):
        saved = capture.select.select
        capture.select.select = lambda r, w, x, t=None: ([], [], [])
        try:
            self.assertIsNone(capture.next_press([_FakeNode(7)], timeout=0.01))
        finally:
            capture.select.select = saved

    def test_returns_first_real_press_as_single_key_chord(self):
        node = _FakeNode(7, events=[_ev(ecodes.EV_KEY, ecodes.KEY_A, 1)])
        saved = capture.select.select
        # wake once with the press, then the drain's next select times out (no chord)
        capture.select.select = _scripted_select(([node.fd], [], []), ([], [], []))
        try:
            chord = capture.next_press([node])
            self.assertEqual([p["code"] for p in chord], [ecodes.KEY_A])
        finally:
            capture.select.select = saved

    def test_chord_button_captures_every_key(self):
        # a Naga side button that emits Ctrl+1 in one read() batch -> both keys, in order
        node = _FakeNode(7, events=[_ev(ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 1),
                                    _ev(ecodes.EV_KEY, ecodes.KEY_1, 1)])
        saved = capture.select.select
        capture.select.select = _scripted_select(([node.fd], [], []), ([], [], []))
        try:
            chord = capture.next_press([node])
            self.assertEqual([p["code"] for p in chord],
                             [ecodes.KEY_LEFTCTRL, ecodes.KEY_1])
        finally:
            capture.select.select = saved

    def test_release_ends_the_chord_early(self):
        # Ctrl down, 1 down, 1 up (release) all in one batch -> the up stops collection
        node = _FakeNode(7, events=[_ev(ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 1),
                                    _ev(ecodes.EV_KEY, ecodes.KEY_1, 1),
                                    _ev(ecodes.EV_KEY, ecodes.KEY_1, 0)])
        saved = capture.select.select
        capture.select.select = _scripted_select(([node.fd], [], []))  # no 2nd select needed
        try:
            chord = capture.next_press([node])
            self.assertEqual([p["code"] for p in chord],
                             [ecodes.KEY_LEFTCTRL, ecodes.KEY_1])
        finally:
            capture.select.select = saved

    def test_autorepeat_and_dupes_dropped_from_chord(self):
        node = _FakeNode(7, events=[_ev(ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 1),
                                    _ev(ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 2),  # autorepeat
                                    _ev(ecodes.EV_KEY, ecodes.KEY_1, 1)])
        saved = capture.select.select
        capture.select.select = _scripted_select(([node.fd], [], []), ([], [], []))
        try:
            chord = capture.next_press([node])
            self.assertEqual([p["code"] for p in chord],
                             [ecodes.KEY_LEFTCTRL, ecodes.KEY_1])
        finally:
            capture.select.select = saved


@unittest.skipIf(capture is None, "python-evdev not available")
class ReadPressTests(unittest.TestCase):
    """The interactive CLI loop's robustness fixes (EOF guard + OSError tolerance)."""

    def test_eof_on_stdin_finishes_instead_of_busy_looping(self):
        import sys
        self._fd_to_dev = {}
        stdin_fd = sys.stdin.fileno()
        saved_sel, saved_rl = capture.select.select, sys.stdin.readline
        capture.select.select = lambda r, w, x, t=None: ([stdin_fd], [], [])
        sys.stdin.readline = lambda: ""   # EOF
        try:
            self.assertEqual(capture.read_press({}), ("cmd", "q"))
        finally:
            capture.select.select = saved_sel
            sys.stdin.readline = saved_rl

    def test_typed_command_is_lowercased(self):
        import sys
        stdin_fd = sys.stdin.fileno()
        saved_sel, saved_rl = capture.select.select, sys.stdin.readline
        capture.select.select = lambda r, w, x, t=None: ([stdin_fd], [], [])
        sys.stdin.readline = lambda: "S\n"
        try:
            self.assertEqual(capture.read_press({}), ("cmd", "s"))
        finally:
            capture.select.select = saved_sel
            sys.stdin.readline = saved_rl

    def test_oserror_on_node_does_not_crash_the_loop(self):
        import sys
        stdin_fd = sys.stdin.fileno()

        class _Boom:
            fd = 9
            name = "Razer"
            def read(self): raise OSError("ENODEV")

        boom = _Boom()
        # first wake on the dead node (raises OSError -> continue), then a typed 'q'
        seq = iter([([boom.fd], [], []), ([stdin_fd], [], [])])
        saved_sel, saved_rl = capture.select.select, sys.stdin.readline
        capture.select.select = lambda r, w, x, t=None: next(seq)
        sys.stdin.readline = lambda: "q\n"
        try:
            self.assertEqual(capture.read_press({boom.fd: boom}), ("cmd", "q"))
        finally:
            capture.select.select = saved_sel
            sys.stdin.readline = saved_rl


@unittest.skipIf(capture is None, "python-evdev not available")
class CaptureEntryTests(unittest.TestCase):
    """The persisted shape: a single key stays a lone dict (captures.json unchanged
    for existing single-key gear); a chord becomes the list of its keys."""

    def _payload(self, code):
        return capture.classify_event(_ev(ecodes.EV_KEY, code, 1), _FakeDev())

    def test_single_key_persists_as_a_dict(self):
        entry = capture.capture_entry([self._payload(ecodes.KEY_A)])
        self.assertIsInstance(entry, dict)
        self.assertEqual(entry["code"], ecodes.KEY_A)
        self.assertIn("origin_hash", entry)
        self.assertNotIn("name", entry)   # display-only fields are dropped

    def test_lone_dict_is_tolerated(self):
        entry = capture.capture_entry(self._payload(ecodes.KEY_A))
        self.assertEqual(entry["code"], ecodes.KEY_A)

    def test_chord_persists_as_ordered_list(self):
        entry = capture.capture_entry([self._payload(ecodes.KEY_LEFTCTRL),
                                       self._payload(ecodes.KEY_1)])
        self.assertIsInstance(entry, list)
        self.assertEqual([e["code"] for e in entry],
                         [ecodes.KEY_LEFTCTRL, ecodes.KEY_1])


@unittest.skipIf(capture is None, "python-evdev not available")
class ReadPressChordTests(unittest.TestCase):
    def test_event_path_returns_a_chord(self):
        import sys
        node = _FakeNode(7, events=[_ev(ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 1),
                                    _ev(ecodes.EV_KEY, ecodes.KEY_1, 1)])
        saved = capture.select.select
        # device ready (not stdin) once, then the drain's select times out
        capture.select.select = _scripted_select(([node.fd], [], []), ([], [], []))
        try:
            kind, chord = capture.read_press({node.fd: node})
            self.assertEqual(kind, "event")
            self.assertEqual([p["code"] for p in chord],
                             [ecodes.KEY_LEFTCTRL, ecodes.KEY_1])
        finally:
            capture.select.select = saved


@unittest.skipIf(capture is None, "python-evdev not available")
class GatherChordReleaseTests(unittest.TestCase):
    """_gather_chord(): only a release of a key IN the chord ends it — a user
    releasing some unrelated, previously-held key must not truncate a chord."""

    def _dev(self, events):
        return SimpleNamespace(
            name="Razer Test Device", path="/dev/input/event9",
            capabilities=lambda absinfo=False: {1: [30, 31, 32]},
            read=lambda: iter(events))

    def test_unrelated_release_does_not_truncate_chord(self):
        events = [_ev(ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 1),
                  _ev(ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 0),   # not in the chord
                  _ev(ecodes.EV_KEY, ecodes.KEY_1, 1)]
        chord = capture._gather_chord({7: self._dev(events)}, [7], window=0)
        self.assertEqual([p["code"] for p in chord],
                         [ecodes.KEY_LEFTCTRL, ecodes.KEY_1])

    def test_release_of_chord_member_ends_the_chord(self):
        events = [_ev(ecodes.EV_KEY, ecodes.KEY_A, 1),
                  _ev(ecodes.EV_KEY, ecodes.KEY_A, 0),           # A released: done
                  _ev(ecodes.EV_KEY, ecodes.KEY_C, 1)]           # too late
        chord = capture._gather_chord({7: self._dev(events)}, [7], window=0)
        self.assertEqual([p["code"] for p in chord], [ecodes.KEY_A])


@unittest.skipIf(capture is None, "python-evdev not available")
class SaveCapturesTests(unittest.TestCase):
    """The CLI's final write must be atomic — a crash mid-write must never
    corrupt an existing captures.json (engine falls back to {} on corruption,
    losing every recorded key)."""

    def test_partial_write_cannot_corrupt_existing_captures(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "captures.json"
            p.write_text('{"good": 1}')
            real = Path.write_text

            def half_then_die(self, text, *a, **k):
                real(self, text[: len(text) // 2], *a, **k)
                raise OSError("disk full")

            with mock.patch.object(Path, "write_text", half_then_die):
                with self.assertRaises(OSError):
                    capture.save_captures(p, {"naga": {"captured": {}}})
            # the original file survives the failed write, byte-for-byte valid
            self.assertEqual(json.loads(p.read_text()), {"good": 1})


if __name__ == "__main__":
    unittest.main()
