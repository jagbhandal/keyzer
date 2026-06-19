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
import shutil
import threading
from pathlib import Path

from PySide6.QtCore import Property, QObject, QThread, QTimer, Signal, Slot
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
        self._load_profiles()

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
            if isinstance(saved.get("shift"), dict):
                self._shift = saved["shift"]              # v2: Hypershift layer 2
            if isinstance(saved.get("shiftKeys"), dict):
                self._shiftKeys = saved["shiftKeys"]
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
                          "shift": self._shift, "shiftKeys": self._shiftKeys})

    @Slot(str, "QVariant", result="QVariant")
    def getSetting(self, key: str, default):
        """Read a persisted UI preference (default if unset)."""
        return self._settings.get(key, default)

    @Slot(str, "QVariant")
    def setSetting(self, key: str, value) -> None:
        self._settings[key] = value
        self._save()

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
                   "shift": self._shift.get(name, {}), "holdKeys": self._shiftKeys.get(name, {})}
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

    @Slot(str, str, result="QVariant")
    def applyToHardware(self, profile: str, dev: str = "") -> dict:
        """Generate + load an input-remapper preset for the profile — all devices,
        or just ``dev``. Returns a UI-shaped report (overall + per device)."""
        # only push bindable hotspots that exist (drops orphans + unavailable
        # controls; thumb-pad diagonals are handled via combos).
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
        if self._demo:
            return self._demo_apply(profile, binds)
        # Hypershift layer 2 — same bindable filter, plus the per-device hold key.
        shift = {d: self._bindable(d, (self._shift.get(profile) or {}).get(d)) for d in binds}
        shift_keys = {d: (self._shiftKeys.get(profile) or {}).get(d, "") for d in binds}
        # Apply always persists: the preset is injected now AND registered with
        # input-remapper's autoload so it survives a reboot/replug. Stop is the
        # only "turn it off" — there is no reset-on-restart mode.
        report = engine.apply_profile(profile, binds, engine.load_captures(),
                                      autoload=True, combos=self._combos,
                                      shift=shift, shift_keys=shift_keys)
        if "_error" in report:
            return {"ok": False, "message": report["_error"], "devices": []}
        devices = [{"dev": d, "name": self._layouts.get(d, {}).get("name", d),
                    "ok": r.get("ok", False), "count": r.get("count", 0),
                    "warnings": r.get("warnings", []), "error": r.get("error")}
                   for d, r in report.items()]
        if not devices:
            return {"ok": False, "message": "Nothing to apply for this profile.", "devices": []}
        for d in devices:                       # keep _live in sync with reality:
            if d["ok"]:
                self._live[d["dev"]] = profile  # injected
            else:
                self._live.pop(d["dev"], None)  # reverted / not captured -> not live
        self.liveChanged.emit()
        total = sum(d["count"] for d in devices)
        all_ok = all(d["ok"] for d in devices)
        msg = f"{total} bindings live." if all_ok else "Applied with issues — see below."
        return {"ok": all_ok, "message": msg, "devices": devices}

    def _demo_apply(self, profile: str, binds: dict) -> dict:
        """Simulated apply for demo mode — updates the live set and returns the same
        report shape as applyToHardware, with no input-remapper involved."""
        devices = [{"dev": d, "name": self._layouts.get(d, {}).get("name", d),
                    "ok": True, "count": len(b), "warnings": [], "error": None}
                   for d, b in binds.items()]
        if not devices:
            return {"ok": False, "message": "Nothing to apply for this profile.", "devices": []}
        for x in devices:
            if x["count"]:
                self._live[x["dev"]] = profile
            else:
                self._live.pop(x["dev"], None)
        self.liveChanged.emit()
        total = sum(x["count"] for x in devices)
        return {"ok": True, "message": f"{total} bindings live (demo).", "devices": devices}

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

    # ----- lighting (optional, OpenRazer) -----
    @Slot(result="QVariant")
    def lightingDevices(self) -> dict:
        """Connected, lightable devices (or an error) for the lighting panel."""
        if self._demo:
            return _DEMO_LIGHTING
        d = lighting.devices()
        if "_error" in d:
            return {"error": d["_error"], "devices": []}
        devs = [{"id": k, "name": v.get("name", k), "brightness": v.get("brightness", 100),
                 "effects": v.get("effects", []), "zones": v.get("zones", []),
                 "error": v.get("_error")}
                for k, v in d.items()]
        return {"error": None, "devices": devs}

    @Slot(str, str, int, int, int, str, result="QVariant")
    def setLightEffect(self, dev: str, effect: str, r: int, g: int, b: int, zone: str) -> dict:
        if self._demo:
            return {"ok": True}
        return lighting.set_effect(dev, effect, (r, g, b), zone=zone)

    @Slot(str, int, result="QVariant")
    def setLightBrightness(self, dev: str, pct: int) -> dict:
        if self._demo:
            return {"ok": True}
        return lighting.set_brightness(dev, pct)

    @Slot(result=bool)
    def lightingSync(self) -> bool:
        """Whether OpenRazer is mirroring effects across all Chroma devices."""
        return False if self._demo else lighting.sync_enabled()

    @Slot(bool, result="QVariant")
    def setLightingSync(self, enabled: bool) -> dict:
        if self._demo:
            return {"ok": True}
        return lighting.set_sync(enabled)

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
                 "KEYZER_LISTEN", "KEYZER_CALIBRATE", "KEYZER_SHIFT", "KEYZER_COMPARE")
        return {n: os.environ.get(n, "") for n in names}
