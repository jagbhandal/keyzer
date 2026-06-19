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

import hashlib
import json
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

CONTROL = "input-remapper-control"
# Every preset KEYZER writes is namespaced with this prefix, so autoload cleanup
# can recognise — and only remove — KEYZER's own entries, never a user's.
PRESET_PREFIX = "keyzer-"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "keyzer"
CAPTURES = CONFIG_DIR / "captures.json"
PROFILES = CONFIG_DIR / "profiles.json"
# Bundled, machine-portable default key map (origin_hash stripped) so a fresh
# clone works without running capture.py. The user's own capture overrides it.
DEFAULT_CAPTURES = Path(__file__).resolve().parent.parent / "captures.default.json"


def save_json(path: Path, data, *, indent: int = 2) -> None:
    """Write JSON atomically (temp file + os.replace) so a crash mid-write can't
    corrupt the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=indent) + "\n")
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
_DPI_REASON = "DPI is a hardware function — set it via OpenRazer, not a remap"
_UNSUPPORTED = {label: _DPI_REASON for label in ("dpi +", "dpi -", "dpi1", "dpi2")}
# Punctuation/symbol chars -> X keysym names. input-remapper wants "minus", not
# "-"; the literal char is rejected, so a combo like Ctrl+- must become Control_L+minus.
_PUNCT = {
    "-": "minus", "_": "underscore", "=": "equal", "+": "plus",
    "[": "bracketleft", "]": "bracketright", "{": "braceleft", "}": "braceright",
    ";": "semicolon", ":": "colon", "'": "apostrophe", '"': "quotedbl",
    ",": "comma", ".": "period", "/": "slash", "?": "question",
    "\\": "backslash", "|": "bar", "`": "grave", "~": "asciitilde",
    "!": "exclam", "@": "at", "#": "numbersign", "$": "dollar", "%": "percent",
    "^": "asciicircum", "&": "ampersand", "*": "asterisk",
    "(": "parenleft", ")": "parenright", " ": "space",
}
# Keysyms KEYZER may emit, so validation works even where `xmodmap` is absent
# (the headless dev box); the user's session validates the full set at apply.
_KNOWN_KEYSYMS = (
    set("abcdefghijklmnopqrstuvwxyz0123456789")
    | set(_PUNCT.values())            # supplies space, minus, equal, plus, …
    | {"Tab", "Return", "Escape", "BackSpace", "Delete", "Insert",
       "Home", "End", "Prior", "Next", "Page_Up", "Page_Down",
       "Up", "Down", "Left", "Right", "Caps_Lock",
       "Shift_L", "Shift_R", "Control_L", "Control_R",
       "Alt_L", "Alt_R", "Super_L", "Super_R", "disable"}
    | {f"F{n}" for n in range(1, 13)}
)


@lru_cache(maxsize=1)
def _vocab() -> frozenset[str]:
    """The set of valid symbol names. From `--symbol-names` on a real session,
    unioned with the keysyms above so generation is testable anywhere."""
    names: set[str] = set(_KNOWN_KEYSYMS)
    if shutil.which(CONTROL):
        try:
            r = subprocess.run([CONTROL, "--symbol-names"], capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            return frozenset(names)
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
    if not isinstance(value, str):
        raise Untranslatable("binding isn't text")
    v = value.strip()
    if not v:
        raise Untranslatable("no binding")
    if v.lower() in _UNSUPPORTED:
        raise Untranslatable(_UNSUPPORTED[v.lower()])

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
    if t in _PUNCT:                   # "-" -> "minus", "=" -> "equal", ...
        return _PUNCT[t]
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
    """A namespaced, collision-free preset filename. Distinct profile names that
    slug identically ('Gaming' vs 'gaming') get distinct files via a name hash, so
    Apply never silently overwrites another profile's preset."""
    slug = re.sub(r"[^a-z0-9]+", "-", profile.lower()).strip("-") or "profile"
    digest = hashlib.sha1(profile.encode()).hexdigest()[:6]
    return f"{PRESET_PREFIX}{slug}-{digest}"


def _is_pointer(cap: dict) -> bool:
    return int(cap.get("type", 0)) == 2 and int(cap.get("code", -1)) in (0, 1, 2)


def _input_config(cap: dict) -> dict:
    inp = {"type": int(cap["type"]), "code": int(cap["code"])}
    if cap.get("origin_hash"):
        inp["origin_hash"] = cap["origin_hash"]
    if cap.get("analog_threshold"):
        inp["analog_threshold"] = int(cap["analog_threshold"])
    return inp


def build_preset(binds: dict, captures: dict, combos: dict | None = None,
                 *, shift_binds: dict | None = None, shift_key: str | None = None) -> tuple[list[dict], list[str]]:
    """Join binds with captures into input-remapper mappings (+ skip warnings).

    A hotspot listed in ``combos`` (e.g. an 8-way diagonal -> its two cardinal
    switches) maps to the COMBINATION of its members' captures; input-remapper's
    longest-match then fires it instead of the members' individual mappings."""
    combos = combos or {}
    mappings, warnings = [], []

    def inputs_for(hotspot):
        """The input_combination list for a hotspot (its combo members, or itself),
        or (None, reason) if a member isn't captured / is pointer movement."""
        inputs = []
        for m in (combos.get(hotspot) or [hotspot]):
            cap = captures.get(m)
            if not cap:
                return None, f"{hotspot}: {m} not captured yet — run capture.py"
            if _is_pointer(cap):
                return None, f"{hotspot}: {m} captured pointer movement — re-capture it"
            inputs.append(_input_config(cap))
        return inputs, None

    for hotspot, value in binds.items():
        inputs, bad = inputs_for(hotspot)
        if bad:
            warnings.append(bad)
            continue
        try:
            target, symbol = output(value)
        except (Untranslatable, AttributeError, TypeError, ValueError) as exc:
            warnings.append(f"{hotspot} = {value!r}: {exc}")
            continue
        mappings.append({"input_combination": inputs,
                         "target_uinput": target, "output_symbol": symbol})

    # Hypershift second layer: each bind fires only while the hold key is also down —
    # a [hold, target] combination that input-remapper's longest-match picks over the
    # base single-key mapping. Same primitive as the 8-way thumb diagonals.
    if shift_binds and shift_key:
        hold_cap = captures.get(shift_key)
        if not hold_cap:
            warnings.append(f"shift: hold key {shift_key} not captured — run capture.py")
        elif _is_pointer(hold_cap):
            warnings.append(f"shift: hold key {shift_key} captured pointer movement — re-capture it")
        else:
            hold_input = _input_config(hold_cap)
            for hotspot, value in shift_binds.items():
                if hotspot == shift_key:
                    continue   # the hold key itself can't be a shift target
                inputs, bad = inputs_for(hotspot)
                if bad:
                    warnings.append("shift " + bad)
                    continue
                try:
                    target, symbol = output(value)
                except (Untranslatable, AttributeError, TypeError, ValueError) as exc:
                    warnings.append(f"shift {hotspot} = {value!r}: {exc}")
                    continue
                mappings.append({"input_combination": [hold_input, *inputs],
                                 "target_uinput": target, "output_symbol": symbol})
    return mappings, warnings


def write_preset(device_name: str, preset: str, mappings: list[dict]) -> Path:
    path = preset_path(device_name, preset)
    save_json(path, mappings, indent=4)
    return path


# ---- driving the daemon (control client; the service itself runs as root) ----
def available() -> bool:
    return shutil.which(CONTROL) is not None


def _control(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([CONTROL, *args], capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 1, "", "input-remapper timed out")


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


def sync_keyboard_layout() -> dict:
    """Ensure input-remapper's ``xmodmap.json`` exists, refreshed from the current
    keymap where possible (a stale file still counts as present).

    The root daemon validates every symbolic output (``w``, ``Control_L``,
    ``minus`` …) against the key-name map in ``xmodmap.json``. With no such file,
    EVERY keysym mapping fails validation server-side and is silently dropped, so
    the keys fall through to their hardware defaults. That file is normally
    written by the input-remapper GUI — which KEYZER replaces — so KEYZER has to
    trigger the write itself.

    Rather than reproduce input-remapper's keymap parsing (a brittle coupling to
    its internals that would fail *silently wrong* on an upgrade), we let
    input-remapper do it: ``input-remapper-control --symbol-names`` runs in a
    non-service process, which populates the layout from ``xmodmap -pke`` and
    writes ``xmodmap.json`` with its own, version-matched code — to the exact
    path the daemon reads. No daemon required. Returns ``{ok, path, error}``."""
    out = _ir_config_dir() / "xmodmap.json"
    fail = lambda msg: {"ok": False, "path": str(out), "error": msg}
    if not available():
        return fail("input-remapper isn't installed")
    _control("--symbol-names")  # side effect: writes xmodmap.json when xmodmap works
    if out.exists():
        return {"ok": True, "path": str(out), "error": None}
    # The file wasn't produced — diagnose why, without reproducing the parse.
    if not shutil.which("xmodmap"):
        return fail("xmodmap isn't installed — install x11-xserver-utils so the "
                    "remapper daemon can resolve key names")
    return fail("couldn't read the keyboard layout (`xmodmap -pke` found none — "
                "is an X/XWayland session active?)")


@lru_cache(maxsize=1)
def _ir_version() -> str | None:
    """The installed input-remapper version string (e.g. '2.2.1'), for config.json's
    ``version`` stamp — or None if it can't be read. Without a version field
    input-remapper reads the config as 0.0.0 and needlessly re-runs *every*
    migration on each autoload, so we stamp it when first creating the file."""
    if not available():
        return None
    r = _control("--version")   # logs "input-remapper 2.2.1 <sha> <url>" — to stderr
    if r.returncode != 0:
        return None
    text = f"{r.stdout}\n{r.stderr}"
    m = re.search(r"input-remapper\s+(\d+\.\d+\.\d+)", text)   # anchored: skip the
    if m:                                                      # warning's "3.14"
        return m.group(1)
    m = re.search(r"\b\d+\.\d+\.\d+\b", text)                  # fallback if reworded
    return m.group(0) if m else None


def set_autoload(device_key: str, preset: str) -> None:
    """Mark a preset to auto-load at login *and on reconnect* (config.json
    ``autoload``: device key -> preset). input-remapper's login autostart and udev
    rule consume this map — writing it is the only thing that makes a profile
    survive a reboot or a replug; KEYZER's Apply otherwise injects runtime-only.

    config.json is input-remapper's own file, so write it atomically — a crash
    mid-write must not corrupt the daemon's config, only ours. Stamp the daemon's
    ``version`` when creating the file (see _ir_version) without clobbering an
    existing one."""
    cfg = _ir_config_dir() / "config.json"
    data = load_json(cfg, None)
    if not isinstance(data, dict):
        # Missing -> create fresh. Existing but unparseable -> preserve it as a
        # sidecar rather than silently clobbering input-remapper's own config.
        if cfg.exists():
            try:
                cfg.replace(cfg.with_name(cfg.name + ".corrupt"))
            except OSError:
                pass
        data = {}
    if not data.get("version") and (version := _ir_version()):
        data["version"] = version
    data.setdefault("autoload", {})[device_key] = preset
    save_json(cfg, data, indent=4)


def clear_autoload(device_key: str | None = None) -> None:
    """Remove KEYZER's autoload entries so a stopped or no-longer-kept device won't
    reload at the next login/reconnect. Only entries pointing at a KEYZER preset
    (``keyzer-…``) are touched, so a user's own input-remapper autoloads are left
    intact. ``device_key`` limits it to one device; None clears all KEYZER ones."""
    cfg = _ir_config_dir() / "config.json"
    data = load_json(cfg, None)
    if not isinstance(data, dict) or not isinstance(data.get("autoload"), dict):
        return
    autoload = data["autoload"]
    targets = [device_key] if device_key is not None else list(autoload)
    removed = False
    for key in targets:
        value = autoload.get(key)
        if isinstance(value, str) and value.startswith(PRESET_PREFIX):
            del autoload[key]
            removed = True
    if removed:
        save_json(cfg, data, indent=4)


def load_captures(path: Path = CAPTURES) -> dict:
    """User captures if present, else the bundled per-device defaults."""
    p = path if path.exists() else DEFAULT_CAPTURES
    if not p.exists():
        return {}
    try:
        return {k: v for k, v in json.loads(p.read_text()).items()
                if not k.startswith("_")}
    except (json.JSONDecodeError, OSError):
        return {}


def captures_origin(path: Path = CAPTURES) -> str:
    """Where the active capture map comes from: 'user', 'default', or 'none'."""
    if path.exists():
        return "user"
    if DEFAULT_CAPTURES.exists():
        return "default"
    return "none"


def record_capture(dev_id: str, device_name: str, hotspot: str, entry: dict,
                   *, usb: str = "", all_names: list[str] | None = None,
                   path: Path = CAPTURES) -> None:
    """Merge one freshly-captured hotspot into the user's captures.json, creating
    the file/device entry as needed. ``entry`` is {type, code, origin_hash,
    analog_threshold?}. Atomic write; an existing valid map keeps all its other
    captured hotspots. If the file exists but is unparseable it's preserved as a
    ``.corrupt`` sidecar (not silently discarded) before a fresh one is written."""
    data = load_json(path, None)
    if not isinstance(data, dict):
        if path.exists():   # existing but corrupt — keep it instead of clobbering
            try:
                path.replace(path.with_name(path.name + ".corrupt"))
            except OSError:
                pass
        data = {}
    dev = data.get(dev_id)
    if not isinstance(dev, dict):
        dev = {}
        data[dev_id] = dev
    dev["device_name"] = device_name
    if usb:
        dev["usb"] = usb
    if all_names:
        dev["all_names"] = all_names
    if not isinstance(dev.get("captured"), dict):
        dev["captured"] = {}
    dev["captured"][hotspot] = entry
    save_json(path, data, indent=2)


def apply_profile(profile: str, binds_by_device: dict, captures: dict,
                  *, autoload: bool = False, combos: dict | None = None,
                  shift: dict | None = None, shift_keys: dict | None = None) -> dict:
    """Generate + apply a preset per device. binds_by_device: {dev: {hotspot:value}};
    captures: the captures.json dict. ``shift``/``shift_keys`` add the Hypershift
    second layer ({dev: {hotspot:value}} and {dev: hold-hotspot}). Returns a
    per-device result report."""
    if not available():
        return {"_error": "input-remapper isn't installed"}
    if not service_ready():
        return {"_error": "input-remapper service isn't running "
                          "(start it, e.g. `systemctl start input-remapper`)"}

    # Refresh the key-name map the (root) daemon validates symbols against. Without
    # it every keysym mapping is dropped server-side and keys emit their defaults.
    layout = sync_keyboard_layout()
    if not layout["ok"] and not os.path.exists(layout["path"]):
        return {"_error": "Couldn't resolve key names for the remapper daemon — "
                          f"{layout['error']}. Bindings can't be applied until this "
                          "is fixed (keys would silently fall through to defaults)."}

    keys = device_keys()
    preset = preset_name_for(profile)
    report: dict = {}
    for dev_id, binds in binds_by_device.items():
        cap = captures.get(dev_id) or {}
        device_name = cap.get("device_name")
        if not device_name:
            report[dev_id] = {"ok": False, "error": "not captured — run capture.py"}
            continue
        key = resolve_key(device_name, keys)
        mappings, warnings = build_preset(binds, cap.get("captured") or {},
                                          (combos or {}).get(dev_id),
                                          shift_binds=(shift or {}).get(dev_id),
                                          shift_key=(shift_keys or {}).get(dev_id))
        if not mappings:
            # The active profile leaves this device unbound — revert it to hardware
            # defaults (stop injecting + drop its autoload) so "active profile = live"
            # holds even when a device has no binds. stop is a no-op if not injected.
            stop(key)
            clear_autoload(key)
            report[dev_id] = {"ok": False, "error": "no applicable binds",
                              "warnings": warnings}
            continue
        path = write_preset(device_name, preset, mappings)
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
