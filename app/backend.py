"""KEYZER backend — the bridge between the QML UI and the Python engine layer.

Device geometry comes from ``layouts.json`` at the repo root. Profiles (the
hotspot -> output binds) are persisted to ``~/.config/keyzer/profiles.json`` and
pushed to hardware via the engine. The UI never imports engine internals — it
talks to this object.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import queue
import shutil
import threading
import time
from pathlib import Path

from PySide6.QtCore import (Property, QMetaObject, QObject, Qt, QThread, QTimer,
                            Q_ARG, Signal, Slot)
from PySide6.QtGui import QGuiApplication

import engine
import lighting

REPO = Path(__file__).resolve().parent.parent

# Sample lighting shown in demo mode (no OpenRazer) — the panel's expected shape.
_DEMO_LIGHTING = {"error": None, "devices": [
    {"id": "tartarus", "name": "Razer Tartarus Pro", "brightness": 80,
     "effects": ["static", "reactive", "none"], "zones": [], "error": None},
    {"id": "naga", "name": "Razer Naga Pro", "brightness": 100,
     "effects": ["static", "spectrum", "breath_single", "wave", "none"],
     "zones": [{"name": "logo", "label": "Logo",
                "effects": ["static", "spectrum", "breath_single", "none"]},
               {"name": "scroll_wheel", "label": "Scroll wheel",
                "effects": ["static", "spectrum", "reactive", "none"]}],
     "error": None}]}


class _LightingWorker(threading.Thread):
    """The SINGLE owner of all OpenRazer access, off the GUI thread. Building/using a
    DeviceManager blocks ~25s when the daemon is down, so no lighting call may run on
    the event loop. The GUI enqueues commands (query / effect / brightness / sync);
    between commands the worker polls for device (re)connections to re-apply saved
    looks (devices forget software-set effects on unplug, and OpenRazer can't read
    them back). The command runner marshals results back to the GUI thread. Because
    this is the only thread that touches the cached DeviceManager, no lock is needed.

    A daemon thread (NOT a QThread): it must be abandonable at exit even when blocked
    mid-DBus, with none of the 'QThread: Destroyed while thread is still running'
    teardown abort a blocked QThread would cause."""

    def __init__(self, poll, run_cmd, interval_ms: int = 4000):
        super().__init__(daemon=True)
        self._poll = poll                       # idle tick (Backend._lighting_poll)
        self._run_cmd = run_cmd                  # execute one queued command
        self._interval = interval_ms / 1000.0
        self._q: "queue.Queue" = queue.Queue()
        self._stop = threading.Event()

    def submit(self, cmd) -> None:
        self._q.put(cmd)

    def stop(self) -> None:
        self._stop.set()
        self._q.put(None)   # unblock a waiting get()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                cmd = self._q.get(timeout=self._interval)
            except queue.Empty:
                try:
                    self._poll()           # no commands pending -> reconnect check
                except Exception:
                    pass                   # a watcher hiccup must never crash the app
                continue
            if cmd is None:
                continue
            try:
                self._run_cmd(cmd)
            except Exception:
                pass                       # one bad command must not kill the worker


class _QueueWorker(threading.Thread):
    """Runs submitted callables serially on a daemon thread, to keep blocking work
    (input-remapper subprocess calls during Apply) off the GUI thread. A daemon thread
    so a call wedged on a slow daemon is abandonable at exit."""

    def __init__(self):
        super().__init__(daemon=True)
        self._q: "queue.Queue" = queue.Queue()
        self._stop = threading.Event()

    def submit(self, fn) -> None:
        self._q.put(fn)

    def stop(self) -> None:
        self._stop.set()
        self._q.put(None)   # unblock a waiting get()

    def run(self) -> None:
        while not self._stop.is_set():
            fn = self._q.get()
            if fn is None:
                continue
            try:
                fn()
            except Exception:
                pass   # one bad task must not kill the worker


class _CaptureWorker(QThread):
    """Reads physical presses off one grabbed device on a background thread, one
    'armed' hotspot at a time, and persists + reports each capture. Lives only for
    the duration of an in-app calibration session. evdev reads block, so they must
    not run on the GUI thread."""
    captured = Signal(str, str, str)   # dev_id, hotspot, human label
    failed = Signal(str)

    def __init__(self, dev_id, nodes, device_name, usb, all_names, parent=None):
        super().__init__(parent)
        self._dev_id = dev_id
        self._nodes = nodes
        self._device_name = device_name
        self._usb = usb
        self._all_names = all_names
        self._armed = None     # hotspot id awaiting a press, or None (idle)
        self._stop = False
        self._lock = threading.Lock()   # guards _armed across GUI/worker threads

    def arm(self, hotspot: str) -> None:
        with self._lock:
            self._armed = hotspot

    def disarm(self) -> None:
        with self._lock:
            self._armed = None

    def stop(self) -> None:
        self._stop = True

    def _claim(self, hotspot: str) -> bool:
        """Clear the armed slot iff it's still this hotspot, so a press is recorded
        only for the key that was armed when it arrived (a re-arm mid-read wins)."""
        with self._lock:
            if self._armed == hotspot:
                self._armed = None
                return True
            return False

    def run(self) -> None:
        import capture   # already imported by beginCalibration; cache hit here
        grabbed = []
        try:
            for d in self._nodes:
                try:
                    d.grab()
                    grabbed.append(d)
                except OSError:
                    pass   # another program holds it; reads still work without a grab
            while not self._stop:
                with self._lock:
                    hotspot = self._armed
                if hotspot is None:
                    self.msleep(40)
                    continue
                payload = capture.next_press(self._nodes, timeout=0.2)
                if self._stop:
                    break
                if payload is None or not self._claim(hotspot):
                    continue   # timed out, or re-armed elsewhere meanwhile — discard
                try:
                    engine.record_capture(self._dev_id, self._device_name, hotspot,
                                          capture.capture_entry(payload),
                                          usb=self._usb, all_names=self._all_names)
                except OSError as exc:
                    self.failed.emit(f"couldn't save capture: {exc}")
                    continue
                self.captured.emit(self._dev_id, hotspot, payload.get("name", ""))
        except Exception as exc:   # a worker crash must never take down the app
            self.failed.emit(str(exc))
        finally:
            for d in grabbed:
                try:
                    d.ungrab()
                except OSError:
                    pass
            for d in self._nodes:
                try:
                    d.close()
                except OSError:
                    pass


def _detect_deps() -> dict:
    """What's available on this machine. Keymapping needs input-remapper;
    lighting is optional and needs OpenRazer. The UI degrades gracefully."""
    return {
        "inputRemapper": shutil.which("input-remapper-control") is not None
        or shutil.which("input-remapper-gtk") is not None,
        "openrazer": importlib.util.find_spec("openrazer") is not None,
    }


def _default_profiles() -> dict:
    """Sample profiles, mirroring the prototype's defaults."""
    return {
        "Gaming": {
            "tartarus": {
                "TAR_01": "Esc", "TAR_02": "1", "TAR_03": "2", "TAR_04": "3", "TAR_05": "4",
                "TAR_06": "Tab", "TAR_07": "Q", "TAR_08": "W", "TAR_09": "E", "TAR_10": "R",
                "TAR_11": "Shift", "TAR_12": "A", "TAR_13": "S", "TAR_14": "D", "TAR_15": "F",
                "TAR_16": "Ctrl", "TAR_17": "Z", "TAR_18": "X", "TAR_19": "C", "TAR_20": "V",
                "TAR_THUMB": "Space", "TAR_WHEEL": "Vol",
                "TAR_TPAD_N": "W", "TAR_TPAD_E": "D", "TAR_TPAD_S": "S", "TAR_TPAD_W": "A",
            },
            "naga": {
                "NAGA_L": "LMB", "NAGA_R": "RMB", "NAGA_WHL": "MMB",
                "NAGA_DPI1": "DPI +", "NAGA_DPI2": "DPI -",
                "NAGA_01": "1", "NAGA_02": "2", "NAGA_03": "3", "NAGA_04": "4", "NAGA_05": "5",
                "NAGA_06": "6", "NAGA_07": "Q", "NAGA_08": "E", "NAGA_09": "R",
                "NAGA_10": "F", "NAGA_11": "G", "NAGA_12": "T",
            },
        },
        "Work": {
            "tartarus": {"TAR_01": "Esc", "TAR_07": "C+C", "TAR_08": "C+V",
                         "TAR_09": "C+Z", "TAR_12": "C+S", "TAR_THUMB": "Super"},
            "naga": {"NAGA_L": "LMB", "NAGA_R": "RMB", "NAGA_01": "Copy", "NAGA_02": "Paste"},
        },
        "Default": {"tartarus": {}, "naga": {}},
    }


def _normalize_layouts(layouts: dict) -> dict:
    """Guarantee the {device: {views: {view: {keys: [...]}}}} shape so a malformed
    or community-contributed layouts.json can't crash the UI."""
    clean = {}
    for dev, lay in layouts.items():
        if not isinstance(lay, dict) or not isinstance(lay.get("views"), dict):
            continue
        views = {}
        for vname, view in lay["views"].items():
            if not isinstance(view, dict):
                continue
            view = dict(view)
            view["keys"] = [k for k in (view.get("keys") or [])
                            if isinstance(k, dict) and "id" in k]
            views[vname] = view
        if views:
            lay = dict(lay)
            lay["views"] = views
            clean[dev] = lay
    return clean


class Backend(QObject):
    bindingsChanged = Signal()
    profilesChanged = Signal()
    liveChanged = Signal()
    keyCaptured = Signal(str, str, str)   # in-app calibration: dev, hotspot, label
    calibrationChanged = Signal()         # a calibration session started/ended
    calibrationError = Signal(str)
    lightingDevicesReady = Signal("QVariant")   # async lighting query result {error,devices,sync}
    lightingOpFailed = Signal(str)              # a lighting op failed -> toast
    applyFinished = Signal("QVariant", str)     # async apply done: (ui report, tag)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        data = json.loads((REPO / "layouts.json").read_text())
        self._layouts = _normalize_layouts({k: v for k, v in data.items()
                                            if not k.startswith("_")})
        self._dev_totals = {dev: sum(len(v["keys"]) for v in lay["views"].values())
                            for dev, lay in self._layouts.items()}
        self._hotspot_ids = {dev: {k["id"] for v in lay["views"].values() for k in v["keys"]}
                             for dev, lay in self._layouts.items()}
        # hotspot -> member hotspots for combination inputs (8-way pad diagonals)
        self._combos = {dev: {k["id"]: k["combo"] for v in lay["views"].values()
                              for k in v["keys"] if k.get("combo")}
                        for dev, lay in self._layouts.items()}
        # controls with no Linux event (e.g. Naga wheel tilt) — not bindable
        self._unavailable = {dev: {k["id"] for v in lay["views"].values()
                                   for k in v["keys"] if k.get("unavailable")}
                             for dev, lay in self._layouts.items()}
        self._deps = _detect_deps()
        self._demo = bool(os.environ.get("KEYZER_DEMO"))   # run fully populated, no hardware
        self._live = {}     # dev -> profile currently injected (KEYZER-tracked; daemon can't report it)
        self._cal_worker = None   # active _CaptureWorker during in-app calibration, else None
        self._demo_cal = False    # a simulated (no-hardware) calibration session
        self._cal_dev = ""
        self._shift = {}          # Hypershift layer 2: {profile: {device: {hotspot: value}}}
        self._shiftKeys = {}      # the hold-to-shift key: {profile: {device: hotspot}}
        self._thumbModes = {}     # per-thumb 4-way/8-way: {profile: {device: "4way"|"8way"}}
        self._load_profiles()
        # ----- lighting persistence + reconnect watcher -----
        self._light_lock = threading.Lock()
        # mirror of the persisted looks the watcher reads off-thread (see _remember_look)
        self._light_looks = dict(self._settings.get("lightState") or {})
        self._light_seen = set()   # devices present on the last poll (worker-thread only)
        self._light_worker = None  # background lighting worker; started from main.py
        self._apply_worker = None  # serial worker for off-GUI-thread Apply; from main.py

    def _load_profiles(self) -> None:
        """Restore saved profiles, or seed the defaults on first run."""
        saved = engine.load_json(engine.PROFILES)
        self._settings = {}   # persisted UI prefs (e.g. lighting mode on)
        if isinstance(saved, dict) and isinstance(saved.get("profiles"), dict) and saved["profiles"]:
            self._profiles = saved["profiles"]
            self._active = saved.get("active")
            if isinstance(saved.get("live"), dict):
                self._live = saved["live"]   # KEYZER's last-known live set (UI state only);
                                             # real persistence is engine autoload, below
            if isinstance(saved.get("settings"), dict):
                self._settings = saved["settings"]
                # A hand-edited / legacy / partly-corrupt file could carry a non-dict
                # lightState; drop it so it can't crash the eager read in __init__
                # (matches how the rest of this loader stays defensive).
                if not isinstance(self._settings.get("lightState"), dict):
                    self._settings.pop("lightState", None)
            if isinstance(saved.get("shift"), dict):
                self._shift = saved["shift"]              # v2: Hypershift layer 2
            if isinstance(saved.get("shiftKeys"), dict):
                self._shiftKeys = saved["shiftKeys"]
            if isinstance(saved.get("thumbModes"), dict):
                self._thumbModes = saved["thumbModes"]   # v2: per-device thumb mode
        else:
            self._profiles = _default_profiles()
            self._active = "Gaming"
            self._save()
        if self._active not in self._profiles:
            self._active = next(iter(self._profiles))

    def _save(self) -> None:
        if self._demo:
            return   # demo mode is throwaway — never persist profiles/settings to disk
        engine.save_json(engine.PROFILES,
                         {"version": 2, "active": self._active,
                          "live": self._live, "settings": self._settings,
                          "profiles": self._profiles,
                          "shift": self._shift, "shiftKeys": self._shiftKeys,
                          "thumbModes": self._thumbModes})

    @Slot(str, "QVariant", result="QVariant")
    def getSetting(self, key: str, default):
        """Read a persisted UI preference (default if unset)."""
        return self._settings.get(key, default)

    @Slot(str, "QVariant")
    def setSetting(self, key: str, value) -> None:
        self._settings[key] = value
        self._save()

    @Slot(str, str, result=str)
    def thumbModeFor(self, profile: str, dev: str) -> str:
        """A device's thumb mode for a profile: "4way", "8way", or "" (unset = 8-way)."""
        return (self._thumbModes.get(profile) or {}).get(dev, "")

    @Slot(str, str, str)
    def setThumbMode(self, profile: str, dev: str, mode: str) -> None:
        """Set ("4way"/"8way") or clear ("") a device's thumb mode for a profile."""
        per = self._thumbModes.setdefault(profile, {})
        if mode in ("4way", "8way"):
            per[dev] = mode
        else:
            per.pop(dev, None)
        if not per:
            self._thumbModes.pop(profile, None)
        self._save()
        self.bindingsChanged.emit()

    # ----- geometry (single source of truth) -----
    @Property("QVariant", constant=True)
    def layouts(self) -> dict:
        return self._layouts

    @Property("QVariant", constant=True)
    def deps(self) -> dict:
        # Demo mode reports everything present so the whole UI is enabled and nothing
        # is greyed out / hinting at missing hardware.
        return {"inputRemapper": True, "openrazer": True} if self._demo else self._deps

    @Property(bool, constant=True)
    def demo(self) -> bool:
        return self._demo

    @Slot(result="QVariant")
    def deviceIds(self) -> list[str]:
        return list(self._layouts.keys())

    @Slot(str, result=str)
    def imageUrl(self, rel: str) -> str:
        # Confine to the repo: a layout entry (which may come from a community PR
        # or, later, capture.py) must not point the image loader at arbitrary files.
        p = (REPO / rel).resolve()
        return p.as_uri() if p.is_relative_to(REPO) else ""

    @Slot(str, result="QVariant")
    def viewNames(self, dev: str) -> list[str]:
        # Insertion order (Qt's QVariantMap sorts keys, so the UI can't use Object.keys).
        return list(self._layouts[dev]["views"].keys())

    # ----- bindings / profiles -----
    @Property("QVariant", notify=bindingsChanged)
    def bindings(self) -> dict:
        return self._profiles

    @Property("QVariant", notify=bindingsChanged)
    def shiftBindings(self) -> dict:
        """Hypershift layer-2 binds, same shape as bindings ({profile:{dev:{hotspot:value}}})."""
        return self._shift

    @Property("QVariant", notify=bindingsChanged)
    def shiftKeysMap(self) -> dict:
        """The hold-to-shift key per profile/device ({profile: {dev: hotspot}})."""
        return self._shiftKeys

    def _layer_map(self, layer: str) -> dict:
        return self._shift if layer == "shift" else self._profiles

    @Slot(str, str, str, str, str)
    def setBinding(self, profile: str, dev: str, key: str, value: str, layer: str = "base") -> None:
        self._layer_map(layer).setdefault(profile, {}).setdefault(dev, {})[key] = value
        self._save()
        self.bindingsChanged.emit()

    @Slot(str, str, str, str)
    def clearBinding(self, profile: str, dev: str, key: str, layer: str = "base") -> None:
        self._layer_map(layer).get(profile, {}).get(dev, {}).pop(key, None)
        self._save()
        self.bindingsChanged.emit()

    @Slot(str, str, result=str)
    def shiftKeyFor(self, profile: str, dev: str) -> str:
        return (self._shiftKeys.get(profile, {}) or {}).get(dev, "")

    @Slot(str, str, str)
    def setShiftKey(self, profile: str, dev: str, hotspot: str) -> None:
        """Designate (or clear, with hotspot="") the hold key for a device's shift layer."""
        per = self._shiftKeys.setdefault(profile, {})
        if hotspot:
            per[dev] = hotspot
        else:
            per.pop(dev, None)
            if not per:
                self._shiftKeys.pop(profile, None)   # don't leave an empty residue behind
        self._save()
        self.bindingsChanged.emit()

    # ----- profile management -----
    @Property("QVariant", notify=profilesChanged)
    def profileList(self) -> list[str]:
        return list(self._profiles.keys())

    @Property(str, notify=profilesChanged)
    def activeProfile(self) -> str:
        return self._active

    @Slot(str)
    def setActiveProfile(self, name: str) -> None:
        if name in self._profiles and name != self._active:
            self._active = name
            self._save()
            self.profilesChanged.emit()

    def _reject_name(self, name: str, *, allow: str | None = None) -> dict | None:
        """Validation shared by the name-taking mutators: an error dict if ``name``
        is empty or already taken (``allow`` exempts one existing name, for rename),
        else None."""
        if not name:
            return {"ok": False, "error": "Name can't be empty"}
        if name in self._profiles and name != allow:
            return {"ok": False, "error": "That name is already taken"}
        return None

    def _commit(self) -> None:
        """Persist the profile set and notify the UI — the tail of every mutation."""
        self._save()
        self.profilesChanged.emit()
        self.bindingsChanged.emit()

    @Slot(str, result="QVariant")
    def createProfile(self, name: str) -> dict:
        name = name.strip()
        if err := self._reject_name(name):
            return err
        self._profiles[name] = {d: {} for d in self._layouts}
        self._active = name
        self._commit()
        return {"ok": True, "name": name}

    @Slot(str, str, result="QVariant")
    def renameProfile(self, old: str, new: str) -> dict:
        new = new.strip()
        if old not in self._profiles:
            return {"ok": False, "error": "No such profile"}
        if err := self._reject_name(new, allow=old):
            return err
        self._profiles = {new if k == old else k: v for k, v in self._profiles.items()}
        self._shift = {new if k == old else k: v for k, v in self._shift.items()}
        self._shiftKeys = {new if k == old else k: v for k, v in self._shiftKeys.items()}
        self._thumbModes = {new if k == old else k: v for k, v in self._thumbModes.items()}
        if self._active == old:
            self._active = new
        self._commit()
        return {"ok": True, "name": new}

    @Slot(str, str, result="QVariant")
    def duplicateProfile(self, src: str, new: str) -> dict:
        new = new.strip()
        if src not in self._profiles:
            return {"ok": False, "error": "No such profile"}
        if err := self._reject_name(new):
            return err
        self._profiles[new] = copy.deepcopy(self._profiles[src])
        self._shift[new] = copy.deepcopy(self._shift.get(src, {}))
        self._shiftKeys[new] = copy.deepcopy(self._shiftKeys.get(src, {}))
        self._thumbModes[new] = copy.deepcopy(self._thumbModes.get(src, {}))
        self._active = new
        self._commit()
        return {"ok": True, "name": new}

    @Slot(str, result="QVariant")
    def deleteProfile(self, name: str) -> dict:
        if name not in self._profiles:
            return {"ok": False, "error": "No such profile"}
        if len(self._profiles) <= 1:
            return {"ok": False, "error": "Keep at least one profile"}
        del self._profiles[name]
        self._shift.pop(name, None)
        self._shiftKeys.pop(name, None)
        self._thumbModes.pop(name, None)
        if self._active == name:
            self._active = next(iter(self._profiles))
        self._commit()
        return {"ok": True}

    @Slot(str, result=str)
    def presetNameFor(self, profile: str) -> str:
        return engine.preset_name_for(profile)

    @Slot(str, result="QVariant")
    def exportProfile(self, name: str) -> dict:
        """Serialize one profile to a shareable JSON string (copied to clipboard)."""
        if name not in self._profiles:
            return {"ok": False, "error": "No such profile"}
        payload = {"keyzer": 1, "name": name, "binds": self._profiles[name],
                   "shift": self._shift.get(name, {}), "holdKeys": self._shiftKeys.get(name, {}),
                   "thumbModes": self._thumbModes.get(name, {})}
        return {"ok": True, "name": name, "json": json.dumps(payload, indent=2)}

    @Slot(str, result="QVariant")
    def importProfile(self, text: str) -> dict:
        """Add a profile from an exported JSON string (de-duplicating the name)."""
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"ok": False, "error": "That isn't valid JSON"}
        if not isinstance(data, dict) or not isinstance(data.get("binds"), dict):
            return {"ok": False, "error": "Not a KEYZER profile export"}
        name = base = (str(data.get("name") or "Imported").strip()) or "Imported"
        n = 2
        while name in self._profiles:
            name = f"{base} {n}"
            n += 1
        self._profiles[name] = {d: {hk: str(hv) for hk, hv in v.items()}
                                for d, v in data["binds"].items() if isinstance(v, dict)}
        shift = data.get("shift")
        if isinstance(shift, dict):
            self._shift[name] = {d: {hk: str(hv) for hk, hv in v.items()}
                                 for d, v in shift.items() if isinstance(v, dict)}
        hold = data.get("holdKeys")
        if isinstance(hold, dict):
            self._shiftKeys[name] = {d: h for d, h in hold.items() if isinstance(h, str)}
        modes = data.get("thumbModes")
        if isinstance(modes, dict):
            self._thumbModes[name] = {d: m for d, m in modes.items() if m in ("4way", "8way")}
        self._active = name
        self._commit()
        return {"ok": True, "name": name}

    # ----- engine: capture status + real remapping -----
    @Slot(result="QVariant")
    def captureSummary(self) -> dict:
        """Per device: how many hotspots have a recorded evdev code yet."""
        caps = engine.load_captures()
        out = {}
        for dev in self._layouts:
            cap = caps.get(dev) or {}
            out[dev] = {"captured": len(cap.get("captured") or {}),
                        "total": self._dev_totals.get(dev, 0),
                        "name": cap.get("device_name", "")}
        return out

    @Slot(result=str)
    def capturesSource(self) -> str:
        """'user' (you ran capture.py), 'default' (bundled map), or 'none'."""
        return engine.captures_origin()

    # ----- in-app calibration (live evdev capture) -----
    @Slot(result=bool)
    def calibrating(self) -> bool:
        return self._cal_worker is not None or self._demo_cal

    @Slot(str, result="QVariant")
    def beginCalibration(self, dev: str) -> dict:
        """Open + grab ``dev`` for live capture. Stops input-remapper first so its
        exclusive grab doesn't starve our reads; re-apply the profile on exit (the
        UI does this). Returns {ok, device} or {ok:False, error} for the fallback."""
        if dev not in self._layouts:
            return {"ok": False, "error": "Unknown device"}
        if self._demo:   # no hardware — captures are simulated (see armCalibration)
            self._cal_dev = dev
            self._demo_cal = True
            self.calibrationChanged.emit()
            return {"ok": True, "device": self._layouts[dev].get("name", dev)}
        if self._cal_worker is not None:
            return {"ok": False, "error": "Already calibrating"}
        try:
            import capture
        except (ImportError, SystemExit):
            return {"ok": False, "error": "python-evdev isn't installed — see capture.py setup"}
        name, nodes = capture.open_nodes(self._layouts[dev])
        if not nodes:
            return {"ok": False,
                    "error": "Device not found or /dev/input isn't readable — plug it in, "
                             "or add yourself to the 'input' group and log back in."}
        try:
            worker = _CaptureWorker(dev, nodes, name, self._layouts[dev].get("usb", ""),
                                    sorted({d.name for d in nodes}), parent=self)  # parented: no GC mid-run
            worker.captured.connect(self._on_captured)
            worker.failed.connect(self.calibrationError)
            worker.finished.connect(worker.deleteLater)
            # Only now (device open, worker built): free it from input-remapper so our
            # reads aren't starved. After the guard, so a failed begin never disturbs
            # the user's live remapping.
            if engine.available():
                engine.stop_all()
            self._live = {}        # remapping is now stopped — keep the LIVE pill honest
            self.liveChanged.emit()
            worker.start()
        except Exception as exc:   # something failed before the worker owns the nodes —
            for d in nodes:        # close them here, since the worker's finally never runs
                try:
                    d.close()
                except OSError:
                    pass
            return {"ok": False, "error": f"Couldn't start calibration: {exc}"}
        self._cal_worker = worker
        self._cal_dev = dev
        self.calibrationChanged.emit()
        return {"ok": True, "device": name}

    @Slot(str)
    def armCalibration(self, hotspot: str) -> None:
        """Wait for the next physical press and bind it to ``hotspot``."""
        if self._demo:   # simulate a press landing shortly after arming
            QTimer.singleShot(350, lambda: self._demo_capture(hotspot))
            return
        if self._cal_worker is not None:
            self._cal_worker.arm(hotspot)

    def _demo_capture(self, hotspot: str) -> None:
        if self._demo_cal and self._cal_dev:
            self.keyCaptured.emit(self._cal_dev, hotspot, "demo")

    @Slot()
    def disarmCalibration(self) -> None:
        if self._cal_worker is not None:
            self._cal_worker.disarm()

    @Slot(result="QVariant")
    def endCalibration(self) -> dict:
        """Stop the session and release the device (ungrab happens in the worker's
        finally). The worker is parented to this Backend, so even the pathological
        case where wait() times out can't GC a still-running QThread (no crash)."""
        worker = self._cal_worker
        self._cal_dev = ""
        self._demo_cal = False
        if worker is not None:
            worker.stop()
            worker.wait(3000)   # the read loop polls _stop every 0.2s, so this returns fast
            self._cal_worker = None
        self.calibrationChanged.emit()
        return {"ok": True}

    @Slot()
    def shutdownCalibration(self) -> None:
        """App is quitting — stop any worker so it ungrabs the device and the thread
        isn't torn down mid-run. Wired to QGuiApplication.aboutToQuit in main.py."""
        if self._cal_worker is not None:
            self.endCalibration()

    def _on_captured(self, dev: str, hotspot: str, label: str) -> None:
        # runs on the GUI thread (queued cross-thread signal); the worker already
        # persisted the entry, so just notify the UI to refresh + glow the hotspot.
        self.keyCaptured.emit(dev, hotspot, label)

    @Slot(str, result="QVariant")
    def bindableIds(self, dev: str) -> list:
        """Hotspots that need a captured evdev code — real keys, not derived combos
        or controls with no Linux event. Order follows the layout (views, keys),
        which is the order calibration walks through."""
        out = []
        for view in self._layouts.get(dev, {}).get("views", {}).values():
            for k in view["keys"]:
                if not (k.get("combo") or k.get("unavailable")):
                    out.append(k["id"])
        return out

    @Slot(str, result="QVariant")
    def capturedIds(self, dev: str) -> list:
        """Hotspots the user has personally captured. The bundled default map is
        ignored here on purpose — calibration is about recording *your* device, so
        a fresh user sees every key as still-to-do, not pre-filled by the default."""
        data = engine.load_json(engine.CAPTURES, {})
        cap = data.get(dev) if isinstance(data, dict) else None
        return list((cap.get("captured") or {}).keys()) if isinstance(cap, dict) else []

    def _bindable(self, dev: str, binds: dict | None) -> dict:
        """Keep only real, bindable hotspots that exist on this device — drops orphans
        and unavailable controls (combos are joined separately at apply time)."""
        return {h: v for h, v in (binds or {}).items()
                if h in self._hotspot_ids.get(dev, set())
                and h not in self._unavailable.get(dev, set())}

    @Slot(str, str, str)
    def applyToHardware(self, profile: str, dev: str = "", tag: str = "") -> None:
        """Generate + load an input-remapper preset for the profile — all devices, or
        just ``dev`` — OFF the GUI thread, because a slow/wedged daemon would otherwise
        freeze the UI for up to ~40s on every bind edit / profile switch. The UI-shaped
        report is delivered on applyFinished(report, tag); ``tag`` lets the caller
        correlate the result (e.g. the edited hotspot id)."""
        payload = self._apply_payload(profile, dev)   # snapshot binds on the GUI thread
        if self._demo or self._apply_worker is None:
            # demo (instant, no subprocess) or no worker (tests) -> run inline
            report = self._apply_run(payload)
            self._apply_commit(profile, report)
            self.applyFinished.emit(report, tag)
            return
        self._apply_worker.submit(lambda: self._apply_async(profile, payload, tag))

    def _apply_payload(self, profile: str, dev: str = "") -> dict:
        """Snapshot the bindable binds + Hypershift maps for a profile — built on the
        GUI thread so the worker never iterates the live self._profiles/_shift/_shiftKeys
        the GUI mutates (a cross-thread 'dict changed size during iteration'). _bindable
        produces fresh dicts, so the worker only ever touches its own payload. Also pins
        the apply to the state at click time, independent of later edits."""
        prof = self._profiles.get(profile)
        if prof is None:
            src = {}                            # unknown profile -> nothing to apply
        elif dev:
            src = {dev: prof.get(dev, {})}      # one device (a single bind edit)
        else:
            # a full apply covers EVERY known device, so switching to a profile that
            # leaves a device unbound reverts it rather than leaving the old one live.
            src = {d: prof.get(d, {}) for d in self._layouts}
        binds = {d: self._bindable(d, b) for d, b in src.items()}
        shift = {d: self._bindable(d, (self._shift.get(profile) or {}).get(d)) for d in binds}
        shift_keys = {d: (self._shiftKeys.get(profile) or {}).get(d, "") for d in binds}
        thumb_modes = {d: (self._thumbModes.get(profile) or {}).get(d, "") for d in binds}
        return {"profile": profile, "binds": binds, "shift": shift,
                "shift_keys": shift_keys, "thumb_modes": thumb_modes}

    def _apply_async(self, profile: str, payload: dict, tag: str) -> None:
        # worker thread: do the slow subprocess apply, then hop to the GUI thread to
        # reconcile _live (read by liveStatus) and emit the result.
        report = self._apply_run(payload)
        QMetaObject.invokeMethod(self, "_onApplyDone", Qt.QueuedConnection,
                                 Q_ARG(str, profile), Q_ARG(str, tag), Q_ARG("QVariant", report))

    @Slot(str, str, "QVariant")
    def _onApplyDone(self, profile: str, tag: str, report) -> None:
        self._apply_commit(profile, report)
        self.applyFinished.emit(report, tag)

    def _apply_run(self, payload: dict) -> dict:
        """Build + load the preset (the slow input-remapper subprocess work) from a
        pre-snapshotted payload and return a UI-shaped report. Worker-safe: touches only
        the payload + immutable-after-init state (_layouts/_combos/_demo); does NOT read
        the live profile dicts, nor _live (the GUI commits that in _apply_commit)."""
        profile, binds = payload["profile"], payload["binds"]
        if self._demo:
            devices = [{"dev": d, "name": self._layouts.get(d, {}).get("name", d),
                        "ok": True, "count": len(b), "warnings": [], "error": None}
                       for d, b in binds.items()]
            if not devices:
                return {"ok": False, "message": "Nothing to apply for this profile.", "devices": []}
            total = sum(x["count"] for x in devices)
            return {"ok": True, "message": f"{total} bindings live (demo).", "devices": devices}
        # Apply always persists: the preset is injected now AND registered with
        # input-remapper's autoload so it survives a reboot/replug. Stop is the
        # only "turn it off" — there is no reset-on-restart mode.
        report = engine.apply_profile(profile, binds, engine.load_captures(),
                                      autoload=True, combos=self._combos,
                                      shift=payload["shift"], shift_keys=payload["shift_keys"],
                                      thumb_modes=payload.get("thumb_modes"))
        if "_error" in report:
            return {"ok": False, "message": report["_error"], "devices": []}
        devices = [{"dev": d, "name": self._layouts.get(d, {}).get("name", d),
                    "ok": r.get("ok", False), "count": r.get("count", 0),
                    "warnings": r.get("warnings", []), "error": r.get("error")}
                   for d, r in report.items()]
        if not devices:
            return {"ok": False, "message": "Nothing to apply for this profile.", "devices": []}
        total = sum(d["count"] for d in devices)
        all_ok = all(d["ok"] for d in devices)
        msg = f"{total} bindings live." if all_ok else "Applied with issues — see below."
        return {"ok": all_ok, "message": msg, "devices": devices}

    def _apply_commit(self, profile: str, report: dict) -> None:
        """Reconcile _live with an apply report — GUI thread only (liveStatus reads
        _live, so mutating it off-thread could tear a concurrent read)."""
        devices = (report or {}).get("devices") or []
        if not devices:
            return
        for d in devices:
            if d.get("ok") and d.get("count", 0) > 0:
                self._live[d["dev"]] = profile   # injected
            else:
                self._live.pop(d["dev"], None)    # reverted / not captured -> not live
        self.liveChanged.emit()

    def _apply_sync(self, profile: str, dev: str = "") -> dict:
        """Synchronous apply (snapshot + run + commit) returning the report — used by
        tests and by the inline (demo / no-worker) path."""
        report = self._apply_run(self._apply_payload(profile, dev))
        self._apply_commit(profile, report)
        return report

    @Slot()
    def startApplyWorker(self) -> None:
        """Start the serial Apply worker so applyToHardware runs off the GUI thread.
        Called from main.py once the UI is up."""
        if self._demo or self._apply_worker is not None:
            return
        self._apply_worker = _QueueWorker()
        self._apply_worker.start()

    @Slot()
    def shutdownApply(self) -> None:
        """App is quitting — stop the Apply worker (daemon thread; a wedged apply is
        abandoned, not waited on). Wired to QGuiApplication.aboutToQuit in main.py."""
        w = self._apply_worker
        if w is not None:
            w.stop()
            w.join(timeout=1.0)
            self._apply_worker = None

    @Property("QVariant", notify=liveChanged)
    def liveStatus(self) -> dict:
        return dict(self._live)

    @Slot(result="QVariant")
    def stopAll(self) -> dict:
        """Stop injecting on all devices (back to defaults) and drop KEYZER's
        autoload entries, so the mappings don't reload at the next login/replug."""
        if self._demo:
            self._live = {}
            self.liveChanged.emit()
            return {"ok": True, "error": None}
        if not engine.available():
            return {"ok": False, "error": "input-remapper not installed"}
        ok = engine.stop_all()
        if ok:
            self._live = {}
            engine.clear_autoload()
            self.liveChanged.emit()
        return {"ok": ok, "error": None if ok else "couldn't reach the service"}

    # ----- lighting (optional, OpenRazer) — all hardware access is off the GUI thread
    # on the _LightingWorker; the GUI only enqueues and reacts to signals, because
    # building/using a DeviceManager blocks ~25s when the daemon is down. -----
    @Slot()
    def requestLightingDevices(self) -> None:
        """Ask the worker for the connected, lightable devices; the result arrives on
        lightingDevicesReady. Never blocks the UI (a down daemon would otherwise freeze
        the panel ~25s on open)."""
        if self._demo:
            self.lightingDevicesReady.emit({**_DEMO_LIGHTING, "sync": False})
            return
        if self._light_worker is None:
            self.lightingDevicesReady.emit({"error": "lighting unavailable", "devices": [], "sync": False})
            return
        self._light_worker.submit(("query", None))

    @Slot(str, str, int, int, int, str, str, int)
    def setLightEffect(self, dev: str, effect: str, r: int, g: int, b: int, zone: str,
                       direction: str = "left", react_ms: int = 1000) -> None:
        """Record the chosen look (so it survives a restart and is replayed on
        reconnect — OpenRazer can't read effects back) then apply it to hardware off
        the GUI thread. Fire-and-forget: a failure surfaces via lightingOpFailed."""
        if self._demo:
            return
        self._remember_look(dev, zone, effect, r, g, b, direction, react_ms)
        if self._light_worker is not None:
            self._light_worker.submit(("effect", {"dev": dev, "effect": effect, "r": r,
                                                  "g": g, "b": b, "zone": zone,
                                                  "direction": direction, "react_ms": react_ms}))

    @Slot(result="QVariant")
    def lightState(self) -> dict:
        """KEYZER's persisted record of the applied look per ``device|zone``. QML
        hydrates from this on startup so a chosen look survives across sessions."""
        return dict(self._settings.get("lightState") or {})

    def _remember_look(self, dev: str, zone: str, effect: str, r: int, g: int, b: int,
                       direction: str, react_ms: int) -> None:
        look = {"effect": effect, "r": int(r), "g": int(g), "b": int(b),
                "direction": direction, "react_ms": int(react_ms)}
        looks = dict(self._settings.get("lightState") or {})
        looks[f"{dev}|{zone}"] = look
        self._settings["lightState"] = looks
        with self._light_lock:
            self._light_looks = looks   # publish for the watcher thread
        self._save()

    # ----- reconnect / startup re-apply (background watcher) -----
    def _light_looks_snapshot(self) -> dict:
        with self._light_lock:
            return dict(self._light_looks)

    def _present_devices(self) -> set:
        """Set of target ids OpenRazer currently reports as connected (empty when the
        daemon is unreachable). Builds a fresh DeviceManager — may block ~25s when the
        daemon is down, so only the watcher thread (never the GUI) calls this."""
        d = lighting.devices()
        if not isinstance(d, dict) or "_error" in d:
            return set()
        return {k for k, v in d.items()
                if not (isinstance(v, dict) and "_error" in v)}

    @staticmethod
    def _zone_of(key: str) -> str:
        return key.split("|", 1)[1] if "|" in key else ""

    def _reapply_device(self, dev: str, looks: dict) -> None:
        # whole-device look first, then zones, so a zone look wins where both exist
        keys = sorted((k for k in looks if k.split("|", 1)[0] == dev),
                      key=lambda k: self._zone_of(k) != "")
        for key in keys:
            s = looks.get(key)
            # tolerate a malformed on-disk look value (non-dict / missing fields)
            if not isinstance(s, dict) or not s.get("effect"):
                continue
            rm = s.get("react_ms", 1000)
            lighting.set_effect(dev, s["effect"],
                                (s.get("r", 68), s.get("g", 214), s.get("b", 44)),
                                zone=self._zone_of(key), direction=s.get("direction", "left"),
                                react_ms=int(rm) if isinstance(rm, (int, float)) else 1000)

    def _lighting_poll(self, settle_ms: int = 0) -> None:
        """One watcher tick: re-apply each saved look to every device that has
        (re)appeared since the last tick. The first tick after construction sees an
        empty 'seen' set, so it re-applies everything present — the startup re-apply
        that restores the look after an app or daemon restart."""
        if self._demo:
            return
        present = self._present_devices()
        returned = present - self._light_seen
        self._light_seen = present
        if not returned:
            return   # nothing newly connected -> no re-apply (no steady-state flicker)
        if settle_ms:   # a freshly re-enumerated device can reject effects for a moment
            time.sleep(settle_ms / 1000.0)
        looks = self._light_looks_snapshot()
        for dev in sorted(returned):
            try:
                self._reapply_device(dev, looks)
            except Exception:
                pass   # one device's failure must never starve the others' re-apply

    def _lighting_snapshot(self) -> dict:
        """{error, devices:[...], sync} for the panel — built on the worker thread
        (reuses the cached DeviceManager; sync_enabled() reuses it, no second build)."""
        d = lighting.devices()
        if not isinstance(d, dict):
            return {"error": "lighting unavailable", "devices": [], "sync": False}
        if "_error" in d:
            return {"error": d["_error"], "devices": [], "sync": False}
        devs = [{"id": k, "name": v.get("name", k), "brightness": v.get("brightness", 100),
                 "effects": v.get("effects", []), "zones": v.get("zones", []),
                 "error": v.get("_error")}
                for k, v in d.items()]
        return {"error": None, "devices": devs, "sync": bool(lighting.sync_enabled())}

    def _run_light_cmd(self, cmd) -> None:
        """Execute one queued lighting command on the worker thread, then marshal any
        result/error back to the GUI thread via _deliverLighting (a queued call)."""
        kind, arg = cmd
        if kind == "query":
            self._deliver_light("devices", self._lighting_snapshot())
            return
        if kind == "effect":
            r = lighting.set_effect(arg["dev"], arg["effect"], (arg["r"], arg["g"], arg["b"]),
                                    zone=arg["zone"], direction=arg["direction"], react_ms=arg["react_ms"])
        elif kind == "brightness":
            r = lighting.set_brightness(arg["dev"], arg["pct"])
        elif kind == "sync":
            r = lighting.set_sync(arg["enabled"])
        else:
            return
        if not (isinstance(r, dict) and r.get("ok")):
            self._deliver_light("error", {"error": (r or {}).get("error") or "lighting failed"})

    def _deliver_light(self, kind: str, payload: dict) -> None:
        # hop from the worker thread to the GUI thread (queued) to emit Qt signals
        QMetaObject.invokeMethod(self, "_deliverLighting", Qt.QueuedConnection,
                                 Q_ARG(str, kind), Q_ARG("QVariant", payload))

    @Slot(str, "QVariant")
    def _deliverLighting(self, kind: str, payload) -> None:
        """Runs on the GUI thread: turn a worker result into a Qt signal QML reacts to."""
        if kind == "devices":
            self.lightingDevicesReady.emit(payload)
        elif kind == "error":
            self.lightingOpFailed.emit((payload or {}).get("error", "lighting failed"))

    @Slot()
    def startLightingWatcher(self) -> None:
        """Start the lighting worker: it runs the startup re-apply, watches for device
        (re)connections, and serves the GUI's lighting commands — all off the GUI
        thread. Called from main.py once the UI is up; never in demo or without
        OpenRazer."""
        if self._demo or self._light_worker is not None or not lighting.available():
            return
        self._light_worker = _LightingWorker(
            lambda: self._lighting_poll(settle_ms=400), self._run_light_cmd, interval_ms=4000)
        self._light_worker.start()

    @Slot()
    def shutdownLighting(self) -> None:
        """App is quitting — stop the worker. Wired to QGuiApplication.aboutToQuit in
        main.py (mirrors shutdownCalibration). Best-effort join: a command blocked
        ~25s on a down daemon won't hold up exit — it's a daemon thread."""
        w = self._light_worker
        if w is not None:
            w.stop()
            w.join(timeout=1.0)
            self._light_worker = None

    @Slot(str, int)
    def setLightBrightness(self, dev: str, pct: int) -> None:
        if self._demo or self._light_worker is None:
            return
        self._light_worker.submit(("brightness", {"dev": dev, "pct": pct}))

    @Slot(bool)
    def setLightingSync(self, enabled: bool) -> None:
        if self._demo or self._light_worker is None:
            return
        self._light_worker.submit(("sync", {"enabled": enabled}))

    # ----- align / copy-layout -----
    @Slot(str)
    def copyToClipboard(self, text: str) -> None:
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(text)

    # ----- offscreen QA: drive initial UI state from env vars -----
    @Slot(result="QVariant")
    def qaState(self) -> dict:
        names = ("KEYZER_DEV", "KEYZER_VIEW", "KEYZER_PROFILE", "KEYZER_SELECT",
                 "KEYZER_LIGHTING", "KEYZER_ALIGN", "KEYZER_RESULT",
                 "KEYZER_DIALOG", "KEYZER_LIVE", "KEYZER_LIGHTPANEL", "KEYZER_HINT",
                 "KEYZER_LISTEN", "KEYZER_CALIBRATE", "KEYZER_SHIFT", "KEYZER_COMPARE",
                 "KEYZER_SHOT", "KEYZER_LIGHTFX", "KEYZER_LIGHTZONE", "KEYZER_THUMBMODE")
        return {n: os.environ.get(n, "") for n in names}
