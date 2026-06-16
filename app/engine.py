"""KEYZER engine — turn a profile into a live input-remapper preset.

input-remapper is a black box: KEYZER writes its preset JSON and drives its
control CLI. It never imports input-remapper internals. The schema here is
verified against input-remapper 2.2.1:

  * a preset file is a JSON *array* of mappings at
    ``$XDG_CONFIG_HOME/input-remapper-2/presets/<device-name>/<preset>.json``
  * a mapping is ``{input_combination:[{type,code,origin_hash?,analog_threshold?}],
    target_uinput, output_symbol}``
  * applying = ``input-remapper-control --command start --device <key> --preset <name>``

The capture map (hotspot -> physical input) comes from ``capture.py``; the
binding map (hotspot -> output) comes from a KEYZER profile. This module joins
them: ``output(input)``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

CONTROL = "input-remapper-control"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "keyzer"
CAPTURES = CONFIG_DIR / "captures.json"
PROFILES = CONFIG_DIR / "profiles.json"


def save_json(path: Path, data) -> None:
    """Write JSON atomically (temp file + os.replace) so a crash mid-write can't
    corrupt the user's data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def load_json(path: Path, fallback=None):
    """Read JSON, returning ``fallback`` on a missing or corrupt file."""
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback


class Untranslatable(Exception):
    """A binding value we can't express as an input-remapper output."""


# ---- output translation: friendly KEYZER labels -> input-remapper symbols ----
# Single keys are X keysyms (a, space, Escape, Control_L); mouse buttons are
# evdev names (BTN_LEFT); a chord is "A+B" (verified: NOT key(...)-wrapped).
_LABELS = {
    "esc": "Escape", "escape": "Escape", "tab": "Tab",
    "space": "space", "spacebar": "space", "enter": "Return", "return": "Return",
    "backspace": "BackSpace", "del": "Delete", "delete": "Delete",
    "shift": "Shift_L", "ctrl": "Control_L", "control": "Control_L",
    "alt": "Alt_L", "super": "Super_L", "win": "Super_L", "meta": "Super_L",
    "caps": "Caps_Lock", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "↑": "Up", "↓": "Down", "←": "Left", "→": "Right", "bksp": "BackSpace",
    "disable": "disable", "disabled": "disable", "none": "disable",
    "lmb": "BTN_LEFT", "rmb": "BTN_RIGHT", "mmb": "BTN_MIDDLE",
    "left click": "BTN_LEFT", "right click": "BTN_RIGHT", "middle click": "BTN_MIDDLE",
    "copy": "Control_L+c", "paste": "Control_L+v", "cut": "Control_L+x",
    "undo": "Control_L+z", "redo": "Control_L+y", "save": "Control_L+s",
    "select all": "Control_L+a",
    "prev": "BTN_BACK", "next": "BTN_FORWARD", "forward": "BTN_FORWARD",
    "vol": "KEY_VOLUMEUP", "vol+": "KEY_VOLUMEUP", "vol-": "KEY_VOLUMEDOWN",
    "mute": "KEY_MUTE", "play": "KEY_PLAYPAUSE", "playpause": "KEY_PLAYPAUSE",
}
# Placeholder labels that have no single-output meaning yet -> skip with a reason.
_UNSUPPORTED = {
    "dpi +": "DPI is a hardware function — set it via OpenRazer, not a remap",
    "dpi -": "DPI is a hardware function — set it via OpenRazer, not a remap",
    "dpi1": "DPI is a hardware function — set it via OpenRazer, not a remap",
    "dpi2": "DPI is a hardware function — set it via OpenRazer, not a remap",
}
# Keysyms KEYZER may emit, so validation works even where `xmodmap` is absent
# (the headless dev box); the user's session validates the full set at apply.
_KNOWN_KEYSYMS = (
    set("abcdefghijklmnopqrstuvwxyz0123456789")
    | {"space", "Tab", "Return", "Escape", "BackSpace", "Delete", "Insert",
       "Home", "End", "Prior", "Next", "Page_Up", "Page_Down",
       "Up", "Down", "Left", "Right", "Caps_Lock",
       "Shift_L", "Shift_R", "Control_L", "Control_R",
       "Alt_L", "Alt_R", "Super_L", "Super_R", "minus", "equal", "plus", "disable"}
    | {f"F{n}" for n in range(1, 13)}
)


@lru_cache(maxsize=1)
def _vocab() -> frozenset[str]:
    """The set of valid symbol names. From `--symbol-names` on a real session,
    unioned with the keysyms above so generation is testable anywhere."""
    names: set[str] = set(_KNOWN_KEYSYMS)
    if shutil.which(CONTROL):
        r = subprocess.run([CONTROL, "--symbol-names"], capture_output=True, text=True)
        if r.returncode == 0:
            names |= {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}
    return frozenset(names)


def _valid_token(tok: str) -> bool:
    return bool(tok) and tok in _vocab()


def _target_uinput(tokens: list[str]) -> str:
    has_btn = any(t.startswith("BTN_") for t in tokens)
    has_key = any(not t.startswith("BTN_") for t in tokens)
    if has_btn and has_key:
        return "keyboard + mouse"
    return "mouse" if has_btn else "keyboard"


def output(value: str) -> tuple[str, str]:
    """Map a KEYZER binding value to (target_uinput, output_symbol).

    Accepts friendly labels ("Copy", "LMB"), the prototype's "C+x" combos, plain
    keys ("W", "1"), or an already-valid symbol / chord. Raises Untranslatable
    for empty, unsupported-placeholder, or unknown values."""
    v = (value or "").strip()
    if not v:
        raise Untranslatable("no binding")
    if v.lower() in _UNSUPPORTED:
        raise Untranslatable(_UNSUPPORTED[v.lower()])

    if "(" in v and ")" in v:  # a macro — let input-remapper validate it
        return "keyboard", v

    combo = re.fullmatch(r"[cC]\+([A-Za-z0-9])", v)  # prototype "C+C" = Ctrl+C
    if combo:
        symbol = "Control_L+" + combo.group(1).lower()
    elif v.lower() in _LABELS:
        symbol = _LABELS[v.lower()]
    else:  # translate each token of a (possibly single-token) chord
        symbol = "+".join(_translate_token(t) for t in v.split("+"))

    tokens = symbol.split("+")
    unknown = [t for t in tokens if not _valid_token(t)]
    if unknown:
        raise Untranslatable(f"unknown key(s): {', '.join(unknown)}")
    return _target_uinput(tokens), symbol


def _translate_token(tok: str) -> str:
    t = tok.strip()
    if t.lower() in _LABELS:
        return _LABELS[t.lower()]
    if re.fullmatch(r"[A-Za-z]", t):  # single letter -> lowercase keysym
        return t.lower()
    return t  # digit, named keysym, or symbol — validated by the caller


# ---- preset files ----
def _ir_config_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "input-remapper-2"


def _sanitize(name: str) -> str:
    return re.sub(r'[/\\?%*:|"<>]', "_", name)  # matches input-remapper paths.py


def preset_path(device_name: str, preset: str) -> Path:
    return _ir_config_dir() / "presets" / _sanitize(device_name) / f"{preset}.json"


def preset_name_for(profile: str) -> str:
    """A namespaced preset filename so we never clobber the user's own presets."""
    slug = re.sub(r"[^a-z0-9]+", "-", profile.lower()).strip("-") or "profile"
    return f"keyzer-{slug}"


def build_preset(binds: dict, captures: dict) -> tuple[list[dict], list[str]]:
    """Join binds (hotspot -> value) with captures (hotspot -> input config) into
    a list of input-remapper mappings, plus a list of human-readable warnings for
    binds that were skipped."""
    mappings, warnings = [], []
    for hotspot, value in binds.items():
        cap = captures.get(hotspot)
        if not cap:
            warnings.append(f"{hotspot}: not captured yet — run capture.py")
            continue
        try:
            target, symbol = output(value)
        except Untranslatable as exc:
            warnings.append(f"{hotspot} = {value!r}: {exc}")
            continue
        inp = {"type": int(cap["type"]), "code": int(cap["code"])}
        if cap.get("origin_hash"):
            inp["origin_hash"] = cap["origin_hash"]
        if cap.get("analog_threshold"):
            inp["analog_threshold"] = int(cap["analog_threshold"])
        mappings.append({"input_combination": [inp],
                         "target_uinput": target, "output_symbol": symbol})
    return mappings, warnings


def write_preset(device_name: str, preset: str, mappings: list[dict]) -> Path:
    path = preset_path(device_name, preset)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mappings, indent=4) + "\n")
    return path


# ---- driving the daemon (control client; the service itself runs as root) ----
def available() -> bool:
    return shutil.which(CONTROL) is not None


def _control(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([CONTROL, *args], capture_output=True, text=True)


def service_ready() -> bool:
    """Is the input-remapper daemon reachable (started, on the system bus)?"""
    return available() and _control("--command", "hello").returncode == 0


def device_keys() -> list[str]:
    if not available():
        return []
    r = _control("--list-devices")
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()] if r.returncode == 0 else []


def resolve_key(device_name: str, keys: list[str] | None = None) -> str:
    """The --device group key for a preset folder name (usually identical; a
    second identical unit gets a ' 2' suffix)."""
    keys = device_keys() if keys is None else keys
    if device_name in keys:
        return device_name
    for k in keys:
        if k.startswith(device_name):
            return k
    return device_name  # fall back; the daemon may still resolve it


def set_autoload(device_key: str, preset: str) -> None:
    """Mark a preset to auto-load at login (config.json autoload: key -> preset)."""
    cfg = _ir_config_dir() / "config.json"
    data = {}
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
    data.setdefault("autoload", {})[device_key] = preset
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(data, indent=4) + "\n")


def load_captures(path: Path = CAPTURES) -> dict:
    if not path.exists():
        return {}
    try:
        return {k: v for k, v in json.loads(path.read_text()).items()
                if not k.startswith("_")}
    except (json.JSONDecodeError, OSError):
        return {}


def apply_profile(profile: str, binds_by_device: dict, captures: dict,
                  *, autoload: bool = False) -> dict:
    """Generate + apply a preset per device. binds_by_device: {dev: {hotspot:value}};
    captures: the captures.json dict. Returns a per-device result report."""
    if not available():
        return {"_error": "input-remapper isn't installed"}
    if not service_ready():
        return {"_error": "input-remapper service isn't running "
                          "(start it, e.g. `systemctl start input-remapper`)"}

    keys = device_keys()
    preset = preset_name_for(profile)
    report: dict = {}
    for dev_id, binds in binds_by_device.items():
        cap = captures.get(dev_id) or {}
        device_name = cap.get("device_name")
        if not device_name:
            report[dev_id] = {"ok": False, "error": "not captured — run capture.py"}
            continue
        mappings, warnings = build_preset(binds, cap.get("captured") or {})
        if not mappings:
            report[dev_id] = {"ok": False, "error": "no applicable binds",
                              "warnings": warnings}
            continue
        path = write_preset(device_name, preset, mappings)
        key = resolve_key(device_name, keys)
        res = _control("--command", "start", "--device", key, "--preset", preset)
        ok = res.returncode == 0
        if ok and autoload:
            set_autoload(key, preset)
        report[dev_id] = {
            "ok": ok, "count": len(mappings), "warnings": warnings,
            "device": key, "preset": str(path),
            "error": None if ok else (res.stderr or res.stdout or "apply failed").strip(),
        }
    return report


def stop(device_key: str) -> bool:
    return _control("--command", "stop", "--device", device_key).returncode == 0


def stop_all() -> bool:
    return _control("--command", "stop-all").returncode == 0
