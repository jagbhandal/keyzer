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
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtGui import QGuiApplication

import engine
import lighting

REPO = Path(__file__).resolve().parent.parent


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
        self._live = {}     # dev -> profile currently injected (KEYZER-tracked; daemon can't report it)
        self._load_profiles()

    def _load_profiles(self) -> None:
        """Restore saved profiles, or seed the defaults on first run."""
        saved = engine.load_json(engine.PROFILES)
        if isinstance(saved, dict) and isinstance(saved.get("profiles"), dict) and saved["profiles"]:
            self._profiles = saved["profiles"]
            self._active = saved.get("active")
            if isinstance(saved.get("live"), dict):
                self._live = saved["live"]   # presets persist in the daemon across restarts
        else:
            self._profiles = _default_profiles()
            self._active = "Gaming"
            self._save()
        if self._active not in self._profiles:
            self._active = next(iter(self._profiles))

    def _save(self) -> None:
        engine.save_json(engine.PROFILES,
                         {"version": 1, "active": self._active,
                          "live": self._live, "profiles": self._profiles})

    # ----- geometry (single source of truth) -----
    @Property("QVariant", constant=True)
    def layouts(self) -> dict:
        return self._layouts

    @Property("QVariant", constant=True)
    def deps(self) -> dict:
        return self._deps

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

    @Slot(str, str, str, str)
    def setBinding(self, profile: str, dev: str, key: str, value: str) -> None:
        self._profiles.setdefault(profile, {}).setdefault(dev, {})[key] = value
        self._save()
        self.bindingsChanged.emit()

    @Slot(str, str, str)
    def clearBinding(self, profile: str, dev: str, key: str) -> None:
        self._profiles.get(profile, {}).get(dev, {}).pop(key, None)
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

    @Slot(str, result="QVariant")
    def createProfile(self, name: str) -> dict:
        name = name.strip()
        if not name:
            return {"ok": False, "error": "Name can't be empty"}
        if name in self._profiles:
            return {"ok": False, "error": "That name is already taken"}
        self._profiles[name] = {d: {} for d in self._layouts}
        self._active = name
        self._save()
        self.profilesChanged.emit()
        self.bindingsChanged.emit()
        return {"ok": True, "name": name}

    @Slot(str, str, result="QVariant")
    def renameProfile(self, old: str, new: str) -> dict:
        new = new.strip()
        if old not in self._profiles:
            return {"ok": False, "error": "No such profile"}
        if not new:
            return {"ok": False, "error": "Name can't be empty"}
        if new != old and new in self._profiles:
            return {"ok": False, "error": "That name is already taken"}
        self._profiles = {new if k == old else k: v for k, v in self._profiles.items()}
        if self._active == old:
            self._active = new
        self._save()
        self.profilesChanged.emit()
        self.bindingsChanged.emit()
        return {"ok": True, "name": new}

    @Slot(str, str, result="QVariant")
    def duplicateProfile(self, src: str, new: str) -> dict:
        new = new.strip()
        if src not in self._profiles:
            return {"ok": False, "error": "No such profile"}
        if not new:
            return {"ok": False, "error": "Name can't be empty"}
        if new in self._profiles:
            return {"ok": False, "error": "That name is already taken"}
        self._profiles[new] = copy.deepcopy(self._profiles[src])
        self._active = new
        self._save()
        self.profilesChanged.emit()
        self.bindingsChanged.emit()
        return {"ok": True, "name": new}

    @Slot(str, result="QVariant")
    def deleteProfile(self, name: str) -> dict:
        if name not in self._profiles:
            return {"ok": False, "error": "No such profile"}
        if len(self._profiles) <= 1:
            return {"ok": False, "error": "Keep at least one profile"}
        del self._profiles[name]
        if self._active == name:
            self._active = next(iter(self._profiles))
        self._save()
        self.profilesChanged.emit()
        self.bindingsChanged.emit()
        return {"ok": True}

    @Slot(str, result=str)
    def presetNameFor(self, profile: str) -> str:
        return engine.preset_name_for(profile)

    @Slot(str, result="QVariant")
    def exportProfile(self, name: str) -> dict:
        """Serialize one profile to a shareable JSON string (copied to clipboard)."""
        if name not in self._profiles:
            return {"ok": False, "error": "No such profile"}
        payload = {"keyzer": 1, "name": name, "binds": self._profiles[name]}
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
        self._active = name
        self._save()
        self.profilesChanged.emit()
        self.bindingsChanged.emit()
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

    @Slot(str, str, result="QVariant")
    def applyToHardware(self, profile: str, dev: str = "") -> dict:
        """Generate + load an input-remapper preset for the profile — all devices,
        or just ``dev``. Returns a UI-shaped report (overall + per device)."""
        # only push bindable hotspots that exist (drops orphans + unavailable
        # controls; thumb-pad diagonals are handled via combos).
        src = self._profiles.get(profile, {})
        if dev:
            src = {dev: src.get(dev, {})}
        binds = {d: {h: v for h, v in (b or {}).items()
                     if h in self._hotspot_ids.get(d, set())
                     and h not in self._unavailable.get(d, set())}
                 for d, b in src.items()}
        report = engine.apply_profile(profile, binds, engine.load_captures(), combos=self._combos)
        if "_error" in report:
            return {"ok": False, "message": report["_error"], "devices": []}
        devices, all_ok, total = [], True, 0
        for dev, r in report.items():
            devices.append({"dev": dev, "name": self._layouts.get(dev, {}).get("name", dev),
                            "ok": r.get("ok", False), "count": r.get("count", 0),
                            "warnings": r.get("warnings", []), "error": r.get("error")})
            all_ok = all_ok and r.get("ok", False)
            total += r.get("count", 0)
            if r.get("ok"):
                self._live[dev] = profile
        if not devices:
            return {"ok": False, "message": "Nothing to apply for this profile.", "devices": []}
        self.liveChanged.emit()
        msg = (f"{total} bindings live." if all_ok
               else "Applied with issues — see below.")
        return {"ok": all_ok, "message": msg, "devices": devices}

    @Property("QVariant", notify=liveChanged)
    def liveStatus(self) -> dict:
        return dict(self._live)

    @Slot(result="QVariant")
    def stopAll(self) -> dict:
        """Tell the daemon to stop injecting on all devices (back to defaults)."""
        if not engine.available():
            return {"ok": False, "error": "input-remapper not installed"}
        ok = engine.stop_all()
        if ok:
            self._live = {}
            self.liveChanged.emit()
        return {"ok": ok, "error": None if ok else "couldn't reach the service"}

    # ----- lighting (optional, OpenRazer) -----
    @Slot(result="QVariant")
    def lightingDevices(self) -> dict:
        """Connected, lightable devices (or an error) for the lighting panel."""
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
        return lighting.set_effect(dev, effect, (r, g, b), zone=zone)

    @Slot(str, int, result="QVariant")
    def setLightBrightness(self, dev: str, pct: int) -> dict:
        return lighting.set_brightness(dev, pct)

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
                 "KEYZER_LIGHTING", "KEYZER_ALIGN", "KEYZER_APPAWARE", "KEYZER_RESULT",
                 "KEYZER_DIALOG", "KEYZER_LIVE", "KEYZER_LIGHTPANEL", "KEYZER_HINT")
        return {n: os.environ.get(n, "") for n in names}
