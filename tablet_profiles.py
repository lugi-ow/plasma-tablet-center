#!/usr/bin/env python3
# tablet_profiles.py - profile files (read/write/validate), checkpoint() and switch_to() - Qt-free, imported by tablet_center.py. Part of plasma-tablet-center (MIT).
import base64
import configparser
import fcntl
import importlib.util
import io
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

# ── chunk: paths
CONF = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "tabprec.conf"
KCMINPUTRC = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kcminputrc"
RD = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "tabprec"
CTL = RD / "hover.ctl"
DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "tablet-center"
PROFILES_DIR = DATA / "profiles"
BACKUP_DIR = DATA / "backup"
PROFILES_INI = DATA / "profiles.ini"
TOGGLE = Path(__file__).resolve().parent / "tablet-precision.sh"   # beside this file: ~/.local/bin or the package folder
TOGGLE_COMPONENT = "net.local.tabprec-toggle.desktop"
TOGGLE_FALLBACK = "Meta+Shift+F12"
PKG_DIR = Path("/usr/lib/plasma-tablet-center")      # the .deb's folder (packaging/build-deb.sh)
SETUP_MARK = DATA / "package-setup-done"
OLD_DATA = DATA.parent / "wacom-center"      # keep-old-name
OLD_EXT = ".wcprofile"                       # keep-old-name
MOVED_NOTE = "MOVED-TO-tablet-center.txt"    # the one-line file migrate_old_data leaves in the old folder
MOVE_MARK = ".moved-in"                      # the done-mark in the NEW folder: a verified copy never rolls back for the note
HOVER_UNIT = "app-tablet\\x2dhover@autostart.service"   # systemd's name for /etc/xdg/autostart/tablet-hover.desktop
SHORTCUTS = (("net.local.tabprec-toggle.desktop", "Precision mode toggle", 318767163),    # Meta+Shift+F12
             ("net.local.tabprec-bigger.desktop", "Precision area bigger", 318767161),    # Meta+Shift+F10
             ("net.local.tabprec-smaller.desktop", "Precision area smaller", 318767160))  # Meta+Shift+F9
OLD_COPIES = (".local/bin/tablet-precision.sh", ".local/share/applications/net.local.tabprec-toggle.desktop",
              ".config/autostart/tablet-hover.desktop")   # install.sh's: they shadow the package's entries
DAEMON_DOWN = ("The pad helper is not running. Restart it: "
               "systemctl --user restart 'app-tablet\\x2dhover@autostart.service'")
PNG_SIG = b"\x89PNG\r\n\x1a\n"
NUM_RE = re.compile(r"[0-9]+(\.[0-9]+)?")
CURVE_RE = re.compile(r"^([0-9.]+,[0-9.]+;){2}$")

# [Pad] keys = 0-based libinput pad button numbers; [Pen] keys = evdev codes (331/332/329).
# Same file shape, two different numbering schemes - do not "fix" one to match the other.
CONF_KEYS = (["SCALE", "SCALE_MIN", "SCALE_MAX", "DIM", "HOLD", "LONG", "RING_STEP", "RECONNECT", "HOVER_MASK"]
             + [f"CHORD_{i}" for i in range(1, 9)]
             + [f"TOUCH_CHORD_{i}" for i in range(1, 9)]
             + [f"TOUCH_HOLD_{i}" for i in range(1, 9)])
SECTION_ORDER = ("Conf", "Pad", "Ring", "Pen", "Pressure", "Image")
SECTION_KEYS = {
    "Conf": CONF_KEYS,
    "Pad": [str(i) for i in range(8)],
    "Ring": [str(i) for i in range(4)],
    "Pen": ["331", "332", "329"],
    "Pressure": ["TabletToolPressureCurve", "TabletToolPressureRangeMin", "TabletToolPressureRangeMax"],
    "Image": ["png"],
}
_backed_up = set()          # profile names already copied to backup/ in this process (window)


# ── chunk: ProfileError
class ProfileError(Exception):
    """A .tcprofile this reader refuses outright: read_profile never returns partial values for it."""


# ── chunk: SwitchStopped
class SwitchStopped(Exception):
    """A switch_to step failed; nothing after that step ran."""

    def __init__(self, step, message):
        super().__init__(f"The switch stopped at step {step}: {message}. Switch again to finish.")
        self.step = step
        self.message = message


# ── chunk: atomic_write
def atomic_write(path, text):
    """A temp file beside path, then os.replace - never a half-written file."""
    path = Path(path)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(text)
    os.replace(tmp, path)


# ── chunk: conf_lock
class conf_lock:
    """$RD/conf.lock, retried non-blocking up to 5 s. Raises TimeoutError when
    another writer holds it that long - the caller must write NOTHING then."""

    def __enter__(self):
        RD.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(RD / "conf.lock", os.O_CREAT | os.O_RDWR, 0o600)
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(self._fd)
                    raise TimeoutError("the settings file is busy")
                time.sleep(0.05)

    def __exit__(self, *exc):
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        return False


# ── chunk: save_conf_key
def save_conf_key(key, value):
    """Set key=value in tabprec.conf, keeping every other line. Locked and
    atomic (conf_lock + atomic_write); a lock not taken within 5 s writes
    NOTHING and raises TimeoutError."""
    with conf_lock():
        try:
            lines = CONF.read_text().splitlines()
        except OSError:
            lines = []
        out, done = [], False
        for line in lines:
            if line.split("=", 1)[0].strip() == key:
                if not done:
                    out.append(f"{key}={value}")
                    done = True
                continue
            out.append(line)
        if not done:
            out.append(f"{key}={value}")
        atomic_write(CONF, "\n".join(out) + "\n")


# ── chunk: drop_conf_key
def drop_conf_key(key):
    """Remove key from tabprec.conf, keeping every other line. Same lock and
    atomic replace as save_conf_key."""
    with conf_lock():
        try:
            lines = CONF.read_text().splitlines()
        except OSError:
            return
        out = [line for line in lines if line.split("=", 1)[0].strip() != key]
        if len(out) != len(lines):
            atomic_write(CONF, "\n".join(out) + ("\n" if out else ""))


# ── chunk: poke_daemon
def poke_daemon():
    """One line into the daemon's control pipe: re-read the conf now. False
    when no process reads the pipe (no daemon, or not open yet)."""
    try:
        fd = os.open(CTL, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        os.write(fd, b"reload\n")
    except OSError:
        return False
    finally:
        os.close(fd)
    return True


# ── chunk: kreadconfig6
def kreadconfig6(groups, key, file="kcminputrc"):
    args = ["kreadconfig6", "--file", file]
    for g in groups:
        args += ["--group", str(g)]
    args += ["--key", key]
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


# ── chunk: kwriteconfig6
def kwriteconfig6(args, file="kcminputrc"):
    """--notify always; raises RuntimeError on a nonzero exit (rule 7: an
    unwritable or immutable target exits 2 with no message)."""
    result = subprocess.run(["kwriteconfig6", "--notify", "--file", file, *args],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"kwriteconfig6 failed ({result.returncode}): {' '.join(args)}")


# ── chunk: kwrite_literal
def kwrite_literal(groups, key, value, file="kcminputrc"):
    """value written exactly as given (already the raw kwriteconfig6 form);
    empty = --delete, which needs no trailing value despite the help text."""
    args = []
    for g in groups:
        args += ["--group", str(g)]
    args += ["--key", key]
    args += ["--delete"] if value == "" else [value]
    kwriteconfig6(args, file=file)


# ── chunk: busctl_set
def busctl_set(sysname, prop, sig, value):
    """The KWin InputDevice's live property - effect only, never persistence
    (KWin's own setter carries no notify and skips an unchanged value)."""
    try:
        subprocess.run(["busctl", "--user", "set-property", "org.kde.KWin",
                       f"/org/kde/KWin/InputDevice/{sysname}", "org.kde.KWin.InputDevice",
                       prop, sig, value], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        pass


# ── chunk: busctl_get
def busctl_get(sysname, prop):
    """The raw text after the type letter (e.g. '"0,0;1,1;"' or '0.5'), or ''."""
    try:
        out = subprocess.run(["busctl", "--user", "get-property", "org.kde.KWin",
                             f"/org/kde/KWin/InputDevice/{sysname}", "org.kde.KWin.InputDevice",
                             prop], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    parts = out.split(None, 1)
    if len(parts) != 2:
        return ""
    return parts[1].strip('"')


# ── chunk: toggle_chord
def toggle_chord():
    """The precision toggle's global shortcut from kglobalshortcutsrc; the
    documented default when the entry is missing."""
    out = kreadconfig6(["services", TOGGLE_COMPONENT], "_launch", file="kglobalshortcutsrc")
    seq = out.split(",", 1)[0].strip()
    return seq if seq and seq.lower() != "none" else TOGGLE_FALLBACK


# ── chunk: load_parse_chord
def load_parse_chord():
    """parse_chord from tablet-pointer-warp.py beside this file - the
    daemon's own chord alphabet. None when the import fails."""
    try:
        spec = importlib.util.spec_from_file_location(
            "tablet_pointer_warp", Path(__file__).resolve().parent / "tablet-pointer-warp.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.parse_chord
    except Exception:                    # noqa: BLE001 - any import trouble = no validation
        return None


# ── chunk: pen_groups_in_kcminputrc
def pen_groups_in_kcminputrc(pen_name):
    """Every (vendor, product) decimal pair of a [Libinput][v][p][pen_name]
    group actually present in kcminputrc, from a plain text read."""
    try:
        text = KCMINPUTRC.read_text()
    except OSError:
        return []
    pat = re.compile(r"^\[Libinput\]\[(\d+)\]\[(\d+)\]\[" + re.escape(pen_name) + r"\]\s*$", re.M)
    return [(int(v), int(p)) for v, p in pat.findall(text)]


# ── chunk: libwacom_sibling_pairs
def libwacom_sibling_pairs(vendor, product, libwacom_dir):
    """Every (vendor, product) decimal pair from the DeviceMatch line of the
    .tablet file that matches (vendor, product), or None with no match."""
    try:
        files = sorted(Path(libwacom_dir).glob("*.tablet"))
    except OSError:
        return None
    for f in files:
        try:
            text = f.read_text()
        except OSError:
            continue
        m = re.search(r"^DeviceMatch=(.*)$", text, re.M)
        if not m:
            continue
        pairs, matched = [], False
        for entry in m.group(1).split(";"):
            fields = entry.split("|")
            if len(fields) != 3:
                continue
            try:
                v, p = int(fields[1], 16), int(fields[2], 16)
            except ValueError:
                continue
            pairs.append((v, p))
            matched = matched or (v == vendor and p == product)
        if matched:
            return pairs
    return None


# ── chunk: apply_pressure
def apply_pressure(curve, rmin, rmax, pen_name, sysname, status=None):
    """Write the three pressure keys into every kcminputrc pen group for this
    pen, PLUS the sibling transport's group from libwacom's DeviceMatch
    (created if absent) so a curve set on one transport survives on the
    other. Then, when connected (sysname given), the live D-Bus properties -
    the only route with an immediate effect. Qt-free: the switch reuses this,
    so there is one pressure write path."""
    def note(msg):
        if status:
            status(msg)

    pairs = pen_groups_in_kcminputrc(pen_name)
    if not pairs and not sysname:
        note("Pressure settings apply after the pen connects once.")
        return
    libwacom_dir = os.environ.get("WCC_LIBWACOM_DIR", "/usr/share/libwacom")
    extra = libwacom_sibling_pairs(*pairs[0], libwacom_dir) if pairs else None
    if extra:
        for pair in extra:
            if pair not in pairs:
                pairs.append(pair)
    elif len(pairs) == 1:
        note("Pressure for the other connection (USB or Bluetooth) applies "
             "after it connects once and you press Apply again.")
    for vendor, product in pairs:
        for key, value in (("TabletToolPressureCurve", curve),
                           ("TabletToolPressureRangeMin", str(rmin)),
                           ("TabletToolPressureRangeMax", str(rmax))):
            kwrite_literal(["Libinput", vendor, product, pen_name], key, value)
    if sysname:
        for prop, sig, value in (("pressureCurve", "s", curve),
                                 ("pressureRangeMin", "d", str(rmin)),
                                 ("pressureRangeMax", "d", str(rmax))):
            busctl_set(sysname, prop, sig, value)


# ── chunk: apply_left_handed
def apply_left_handed(on, pen_name, sysname, status=None):
    """KDE's Left-handed mode for the pen (libinput turns the tablet 180 degrees): LeftHanded into
    every kcminputrc pen group PLUS the sibling transport's (created if absent), so one tick holds
    on the cable and on Bluetooth - System Settings writes only the connected one. Then the live
    D-Bus property when connected. The pad's own flag stays KDE's: the ring rebinds use the turn's
    delta, which a 180 degree turn keeps. Never in a profile: it is the desk, not the app."""
    def note(msg):
        if status:
            status(msg)

    value = "true" if on else "false"
    pairs = pen_groups_in_kcminputrc(pen_name) if pen_name else []
    if not pairs and not sysname:
        note("Left-handed mode applies after the pen connects once.")
        return
    libwacom_dir = os.environ.get("WCC_LIBWACOM_DIR", "/usr/share/libwacom")
    extra = libwacom_sibling_pairs(*pairs[0], libwacom_dir) if pairs else None
    if extra:
        for pair in extra:
            if pair not in pairs:
                pairs.append(pair)
    elif len(pairs) == 1:
        note("Left-handed mode for the other connection (USB or Bluetooth) applies "
             "after it connects once and you tick the box again.")
    for vendor, product in pairs:
        kwrite_literal(["Libinput", vendor, product, pen_name], "LeftHanded", value)
    if sysname:
        busctl_set(sysname, "leftHanded", "b", value)


# ── chunk: left_handed_state
def left_handed_state(pen_name, sysname):
    """Left-handed mode as KWin has it now, else as any of the pen's kcminputrc groups holds it."""
    if sysname:
        live = busctl_get(sysname, "leftHanded")
        if live in ("true", "false"):
            return live == "true"
    return bool(pen_name) and any(
        kreadconfig6(["Libinput", vendor, product, pen_name], "LeftHanded") == "true"
        for vendor, product in pen_groups_in_kcminputrc(pen_name))


# ── chunk: resolve_pad_name
def resolve_pad_name(live_pad_name):
    """live_pad_name if KWin found one; else profiles.ini's recorded pad,
    else the only [ButtonRebinds][Tablet][<name>] group in kcminputrc."""
    if live_pad_name:
        return live_pad_name
    recorded = get_device_ini().get("pad")
    if recorded:
        return recorded
    return _only_group(r"^\[ButtonRebinds\]\[Tablet\]\[(.+)\]\s*$")


# ── chunk: resolve_pen_name
def resolve_pen_name(live_pen_name):
    """live_pen_name if KWin found one; else profiles.ini's recorded pen,
    else the only [ButtonRebinds][TabletTool][<name>] group, else the first
    [Libinput][v][p][<name>] group whose name ends in 'Pen'."""
    if live_pen_name:
        return live_pen_name
    recorded = get_device_ini().get("pen")
    if recorded:
        return recorded
    name = _only_group(r"^\[ButtonRebinds\]\[TabletTool\]\[(.+)\]\s*$")
    if name:
        return name
    try:
        text = KCMINPUTRC.read_text()
    except OSError:
        return None
    for _, _, group_name in re.findall(r"^\[Libinput\]\[(\d+)\]\[(\d+)\]\[(.+)\]\s*$", text, re.M):
        if group_name.endswith("Pen"):
            return group_name
    return None


# ── chunk: _only_group
def _only_group(pattern):
    try:
        text = KCMINPUTRC.read_text()
    except OSError:
        return None
    names = list(dict.fromkeys(re.findall(pattern, text, re.M)))
    return names[0] if len(names) == 1 else None


# ── chunk: _read_ini
def _read_ini():
    cp = configparser.RawConfigParser()
    cp.optionxform = str
    if PROFILES_INI.exists():
        cp.read(PROFILES_INI, encoding="utf-8")
    return cp


# ── chunk: _write_ini
def _write_ini(cp):
    buf = io.StringIO()
    cp.write(buf)
    DATA.mkdir(parents=True, exist_ok=True)
    atomic_write(PROFILES_INI, buf.getvalue())


# ── chunk: get_device_ini
def get_device_ini():
    cp = _read_ini()
    return dict(cp.items("Device")) if cp.has_section("Device") else {}


# ── chunk: save_device
def save_device(pad, pen):
    """Only a live KWin detection calls this - the asleep/disconnected
    fallback chains read profiles.ini but never write it."""
    cp = _read_ini()
    if not cp.has_section("Device"):
        cp.add_section("Device")
    if pad:
        cp.set("Device", "pad", pad)
    if pen:
        cp.set("Device", "pen", pen)
    _write_ini(cp)


# ── chunk: active_profile_name
def active_profile_name():
    cp = _read_ini()
    return cp.get("Active", "name", fallback=None) or None


# ── chunk: set_active_profile
def set_active_profile(name):
    cp = _read_ini()
    if not cp.has_section("Active"):
        cp.add_section("Active")
    cp.set("Active", "name", name)
    _write_ini(cp)


# ── chunk: get_grid
def get_grid():
    """The 9 grid cells (1..9), each a profile name or ''."""
    cp = _read_ini()
    if not cp.has_section("Grid"):
        return [""] * 9
    return [cp.get("Grid", str(n), fallback="") for n in range(1, 10)]


# ── chunk: set_grid_cell
def set_grid_cell(cell, name):
    cp = _read_ini()
    if not cp.has_section("Grid"):
        cp.add_section("Grid")
    cp.set("Grid", str(cell), name)
    _write_ini(cp)


# ── chunk: profile_path
def profile_path(name):
    return PROFILES_DIR / f"{name}.tcprofile"


# ── chunk: list_profiles
def list_profiles():
    if not PROFILES_DIR.exists():
        return []
    return sorted(p.stem for p in PROFILES_DIR.glob("*.tcprofile"))


# ── chunk: valid_name
def valid_name(name, warnings=None):
    """Trim spaces; raise ValueError for a name this store refuses outright
    (empty, '.', '..', '/', NUL, or one whose bytes plus '.tcprofile' pass
    255 - the file name could not be written, rule 5's exception). A warning
    is appended (when a list is given) above 200 bytes."""
    name = name.strip()
    if name in ("", ".", "..") or "/" in name or "\x00" in name:
        raise ValueError(f"{name!r} is not allowed as a profile name")
    encoded = name.encode("utf-8")
    if len(encoded) + len(".tcprofile") > 255:
        raise ValueError(f"{name!r} is too long for a file name")
    if warnings is not None and len(encoded) > 200:
        warnings.append(f"{name!r} is a very long profile name")
    return name


# ── chunk: unique_name
def unique_name(base):
    """base, or 'base (2)', 'base (3)', ... - the first that is not an
    existing profile (Keep Both)."""
    if not profile_path(base).exists():
        return base
    n = 2
    while profile_path(f"{base} ({n})").exists():
        n += 1
    return f"{base} ({n})"


# ── chunk: blank_sections
def blank_sections():
    return {section: {key: "" for key in keys} for section, keys in SECTION_KEYS.items()}


# ── chunk: _render_profile
def _render_profile(sections):
    lines = []
    for section in SECTION_ORDER:
        lines.append(f"[{section}]")
        for key in SECTION_KEYS[section]:
            lines.append(f"{key}={sections.get(section, {}).get(key, '')}")
    return "\n".join(lines) + "\n"


# ── chunk: write_profile
def write_profile(path, sections):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, _render_profile(sections))


# ── chunk: backup_now
def backup_now(name):
    """Copy profiles/<name>.tcprofile to backup/, once per window (for an
    explicit Replace flow; checkpoint() does its own, text-changed check)."""
    if name in _backed_up:
        return
    src = profile_path(name)
    if src.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(src, BACKUP_DIR / f"{name}.tcprofile")
        except OSError:
            pass
    _backed_up.add(name)


# ── chunk: _line_ok
def _line_ok(key, value):
    """A [Conf] line reaches tabprec.conf only through this gate - defense
    in depth behind the per-key validators below."""
    return bool(re.fullmatch(r"[A-Z][A-Z0-9_]*=[A-Za-z0-9.+]*", f"{key}={value}"))


# ── chunk: _as_float
def _as_float(text):
    try:
        return float(text.replace(",", "."))
    except (ValueError, AttributeError):
        return None


# ── chunk: _validate_pad_or_pen
def _validate_pad_or_pen(value, label):
    if value in ("", "Disabled", "Scroll"):      # Scroll: KDE's "act as a scroll wheel", written by its KCM
        return value, None
    if re.fullmatch(r"Key,[^,\x00-\x1f\x7f]+", value):
        seq = value.split(",", 1)[1]
        if len(seq) == 1 and seq.isalnum():
            return value, f"{label}: '{seq}' is a single letter or digit chord"
        return value, None
    if re.fullmatch(r"MouseButton,\d+(,\d+)?", value):
        return value, None
    if re.fullmatch(r"TabletToolButton,\d+", value):
        return value, None
    return "", f"{label}: {value!r} is not a recognized binding, dropped"


# ── chunk: _validate_curve
def _validate_curve(value):
    if not value:
        return "", None
    if not CURVE_RE.fullmatch(value):
        return "", "the pressure curve is malformed, dropped"
    try:
        floats = [float(n) for n in re.findall(r"[0-9.]+", value)]
    except ValueError:
        return "", "the pressure curve is malformed, dropped"
    if len(floats) != 4 or any(f < 0 or f > 1 for f in floats):
        return "", "the pressure curve has a value outside 0-1, dropped"
    return value, None


# ── chunk: _validate_range
def _validate_range(minv, maxv):
    """Dot-decimal numbers in 0-1 with min < max. A comma decimal is read AND
    returned as a dot: the value goes to kwriteconfig6 and busctl as given,
    where "0,5" is no number."""
    if not minv and not maxv:
        return "", "", None
    minv, maxv = minv.replace(",", "."), maxv.replace(",", ".")
    if any(v and not NUM_RE.fullmatch(v) for v in (minv, maxv)):
        return "", "", "the pressure range is invalid, reset to defaults"
    a = float(minv) if minv else 0.0
    b = float(maxv) if maxv else 1.0
    if not (0 <= a <= 1) or not (0 <= b <= 1) or not (a < b):
        return "", "", "the pressure range is invalid, reset to defaults"
    return minv, maxv, None


# ── chunk: _validate_png
def _validate_png(value):
    if not value:
        return "", None
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception:                    # noqa: BLE001 - any decode trouble = refuse the image
        return "", "the stored image is not valid base64, dropped"
    if len(raw) > 1024 * 1024:
        return "", "the stored image is over 1 MB, dropped"
    if raw[:8] != PNG_SIG or len(raw) < 24:
        return "", "the stored image is not a PNG, dropped"
    w, h = struct.unpack(">II", raw[16:24])
    if w > 16000 or h > 16000:
        return "", f"the stored image ({w}x{h} px) is too large, dropped"
    return value, None


# ── chunk: _apply_pie_rule
def _apply_pie_rule(sections, warnings):
    """Two rules. A CHORD_n (always a press pie) -> [Pad] key n-1 becomes
    Disabled, with a warning when it was not: the daemon presses the chord,
    KWin must swallow the key. A TOUCH_CHORD_n never changes its key: a
    touch pie is Disabled + the chord as the Pad tab wrote it, and a touch
    chord on a key that keeps its own press is just as valid. On the key of
    HOVER_MASK both entries are dropped and warned instead (the daemon never
    fires a chord there); that pad key's own binding stays untouched."""
    conf, pad = sections["Conf"], sections["Pad"]
    hover_idx = None
    mask = conf.get("HOVER_MASK", "")
    if mask:
        try:
            bits = int(mask, 16)
            for i in range(8):
                if bits & (1 << i):
                    hover_idx = i
                    break
        except ValueError:
            pass
    for n in range(1, 9):
        idx = n - 1
        has_press, has_touch = bool(conf.get(f"CHORD_{n}")), bool(conf.get(f"TOUCH_CHORD_{n}"))
        if idx == hover_idx:
            if has_press or has_touch:
                conf[f"CHORD_{n}"] = conf[f"TOUCH_CHORD_{n}"] = ""
                warnings.append(f"key {n}: a chord on the precision key is dropped, "
                                "that key's own binding stays untouched")
            continue
        if has_press and pad.get(str(idx), "") != "Disabled":
            warnings.append(f"Pad key {n}: has a pie chord, forced to Disabled")
            pad[str(idx)] = "Disabled"
    if hover_idx is not None:
        try:
            want = f"Key,{toggle_chord()}"
            have = pad.get(str(hover_idx), "")
            if have not in (want, ""):
                warnings.append(f"Pad key {hover_idx + 1}: HOVER_MASK expects the toggle "
                                f"chord, has {have!r}")
        except Exception:                # noqa: BLE001 - this cross-check is a nicety, never fatal
            pass


# ── chunk: read_profile
def read_profile(path):
    """The only reader of .tcprofile files. Returns (sections, warnings);
    drops a bad line and warns. Raises ProfileError for a file refused
    outright (over 2 MB, not UTF-8) - never partial values then."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as err:
        raise ProfileError(f"cannot read {path.name}: {err}") from err
    if len(raw) > 2 * 1024 * 1024:
        raise ProfileError(f"{path.name} is over 2 MB")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as err:
        raise ProfileError(f"{path.name} is not UTF-8") from err
    text = text.lstrip("﻿").replace("\r\n", "\n")

    warnings = []
    sections = blank_sections()
    section = None
    for lineno, line in enumerate(text.split("\n"), 1):
        if line.strip() == "":
            continue
        if line.startswith("[") and line.endswith("]"):
            candidate = line[1:-1]
            section = candidate if candidate in SECTION_KEYS else None
            if section is None:
                warnings.append(f"line {lineno}: unknown section [{candidate}], dropped")
            continue
        if section is None:
            warnings.append(f"line {lineno}: {line!r} is outside any section, dropped")
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep != "=" or key not in SECTION_KEYS[section]:
            warnings.append(f"line {lineno}: unknown key in [{section}], dropped")
            continue
        sections[section][key] = value

    parse_chord = load_parse_chord()
    for key in SECTION_KEYS["Conf"]:
        value = sections["Conf"][key]
        if value == "":
            continue
        if key == "HOVER_MASK":
            ok = bool(re.fullmatch(r"0x[0-9a-fA-F]{1,2}", value))
            if ok:
                try:
                    ok = int(value, 16) in (1, 2, 4, 8, 16, 32, 64, 128)
                except ValueError:
                    ok = False
            if not ok:
                warnings.append(f"HOVER_MASK={value!r} is invalid, dropped")
                sections["Conf"][key] = ""
            continue
        if key.startswith("CHORD_") or key.startswith("TOUCH_CHORD_"):
            # CHORD_n is always a press pie (needs a full chord); TOUCH_CHORD_n also
            # covers a plain touch chord (bare modifiers allowed, "Shift" for a sculpt
            # mode) and never changes its key (_apply_pie_rule below).
            need_key = key.startswith("CHORD_")
            cleaned = re.sub(r"\s*\+\s*", "+", value.strip())
            ok = parse_chord(cleaned, need_key) is not None if parse_chord else True
            if not ok:
                warnings.append(f"{key}={value!r} is not a valid chord, dropped")
                sections["Conf"][key] = ""
            else:
                sections["Conf"][key] = cleaned
            continue
        cleaned = value.replace(",", ".")
        if not NUM_RE.fullmatch(cleaned):
            warnings.append(f"{key}={value!r} is not a number, dropped")
            sections["Conf"][key] = ""
            continue
        sections["Conf"][key] = cleaned
        num = float(cleaned)
        if key == "SCALE" and not (0 < num <= 1):
            warnings.append(f"SCALE={cleaned} is outside 0-1 of the screen width")
        if key == "DIM" and num > 0.8:
            warnings.append(f"DIM={cleaned} is above the usual 0.8")

    for key, value in list(sections["Conf"].items()):
        if value and not _line_ok(key, value):
            warnings.append(f"{key}={value!r} cannot be written to the conf file, dropped")
            sections["Conf"][key] = ""

    for key in SECTION_KEYS["Pad"]:
        sections["Pad"][key], warn = _validate_pad_or_pen(sections["Pad"][key], f"Pad key {int(key) + 1}")
        if warn:
            warnings.append(warn)
    for key in SECTION_KEYS["Pen"]:
        sections["Pen"][key], warn = _validate_pad_or_pen(sections["Pen"][key], f"Pen key {key}")
        if warn:
            warnings.append(warn)
    for key in SECTION_KEYS["Ring"]:
        value = sections["Ring"][key]
        if value and value not in ("Scroll", "Disabled") \
                and not re.fullmatch(r"AxisKey,[^,\x00-\x1f\x7f]+,[^,\x00-\x1f\x7f]+,\d+", value):
            warnings.append(f"Ring mode {key}: {value!r} is invalid, dropped")
            sections["Ring"][key] = ""

    curve, warn = _validate_curve(sections["Pressure"]["TabletToolPressureCurve"])
    if warn:
        warnings.append(warn)
    sections["Pressure"]["TabletToolPressureCurve"] = curve
    rmin, rmax, warn = _validate_range(sections["Pressure"]["TabletToolPressureRangeMin"],
                                       sections["Pressure"]["TabletToolPressureRangeMax"])
    if warn:
        warnings.append(warn)
    sections["Pressure"]["TabletToolPressureRangeMin"] = rmin
    sections["Pressure"]["TabletToolPressureRangeMax"] = rmax

    png, warn = _validate_png(sections["Image"]["png"])
    if warn:
        warnings.append(warn)
    sections["Image"]["png"] = png

    _apply_pie_rule(sections, warnings)
    return sections, warnings


# ── chunk: _raw_conf_values
def _raw_conf_values():
    try:
        lines = CONF.read_text().splitlines()
    except OSError:
        lines = []
    values = {k: "" for k in CONF_KEYS}
    for line in lines:
        key, sep, val = line.partition("=")
        key = key.strip()
        if sep == "=" and key in values:
            values[key] = val.split("#", 1)[0].strip()
    return values


# ── chunk: _live_sections
def _live_sections(pad, pen, sysname):
    """The conf values raw; kreadconfig6 for [Pad]/[Ring]/[Pen]; [Pressure]
    from D-Bus when connected, else the first pen group that has the keys,
    else the defaults."""
    sections = blank_sections()
    sections["Conf"].update(_raw_conf_values())
    if pad:
        for i in range(8):
            sections["Pad"][str(i)] = kreadconfig6(["ButtonRebinds", "Tablet", pad], str(i))
        for mode in range(4):
            sections["Ring"][str(mode)] = kreadconfig6(
                ["ButtonRebinds", "TabletRing", pad, str(mode)], "0")
    if pen:
        for code in (331, 332, 329):
            sections["Pen"][str(code)] = kreadconfig6(["ButtonRebinds", "TabletTool", pen], str(code))
    curve = rmin = rmax = ""
    if sysname:
        curve = busctl_get(sysname, "pressureCurve")
        rmin = busctl_get(sysname, "pressureRangeMin")
        rmax = busctl_get(sysname, "pressureRangeMax")
    if not curve and pen:
        for vendor, product in pen_groups_in_kcminputrc(pen):
            value = kreadconfig6(["Libinput", vendor, product, pen], "TabletToolPressureCurve")
            if value:
                curve = value
                rmin = kreadconfig6(["Libinput", vendor, product, pen], "TabletToolPressureRangeMin")
                rmax = kreadconfig6(["Libinput", vendor, product, pen], "TabletToolPressureRangeMax")
                break
    sections["Pressure"]["TabletToolPressureCurve"] = curve or "0,0;1,1;"
    sections["Pressure"]["TabletToolPressureRangeMin"] = rmin or "0"
    sections["Pressure"]["TabletToolPressureRangeMax"] = rmax or "1"
    return sections


# ── chunk: ensure_first_start
def ensure_first_start(pad, pen, sysname=None):
    """First run (no profiles.ini): create 'My settings' from the live
    values, make it active, put it in grid cell 1. While the previous version's folder still waits to be
    copied (a failed move), the ini gets [Move] fallback = 1: the retry replaces it and 'My settings'."""
    if PROFILES_INI.exists():
        return
    pending = not ((OLD_DATA / MOVED_NOTE).exists() or (DATA / MOVE_MARK).exists()) and bool(_move_pairs(OLD_DATA, DATA))
    live = _live_sections(pad, pen, sysname)
    write_profile(profile_path("My settings"), live)
    cp = _read_ini()
    cp.add_section("Active")
    cp.set("Active", "name", "My settings")
    cp.add_section("Grid")
    for n in range(1, 10):
        cp.set("Grid", str(n), "My settings" if n == 1 else "")
    cp.add_section("Device")
    if pad:
        cp.set("Device", "pad", pad)
    if pen:
        cp.set("Device", "pen", pen)
    if pending:
        cp.add_section("Move")
        cp.set("Move", "fallback", "1")
    _write_ini(cp)


# ── chunk: _move_pairs
def _move_pairs(old, new):
    """(source, target) for every file of the previous version's folder; target = the new folder, new extension."""
    pairs = []
    if not old.is_dir():
        return pairs
    for src in sorted(old.rglob("*")):
        if src.is_file() and not src.is_symlink() and not src.name.endswith(".part"):
            rel = src.relative_to(old)
            if rel.suffix == OLD_EXT:
                rel = rel.with_suffix(".tcprofile")
            pairs.append((src, new / rel))
    return pairs


# ── chunk: migrate_old_data
def migrate_old_data(old=None, new=None):
    """The first start after the rename: COPY the previous version's data folder (profiles, backups,
    profiles.ini, the package-setup mark) into DATA, each old-extension file under the new extension,
    VERIFY every copied file byte for byte, write the done-mark in DATA, then a one-line note in the old
    folder (best effort: a read-only old folder keeps the copy). The old folder is never moved or deleted;
    a file the new folder already has is kept, except after a failed first move: then the new profiles.ini
    carries [Move] fallback = 1 and the old profiles.ini and 'My settings' REPLACE the fallback ones.
    Any failure removes what this call wrote (replaced files get their bytes back) and leaves the old
    folder untouched, so the next start tries again. Returns the notes to show ([] = nothing to do)."""
    old, new = Path(old or OLD_DATA), Path(new or DATA)
    if (old / MOVED_NOTE).exists() or (new / MOVE_MARK).exists():
        return []
    pairs = _move_pairs(old, new)
    if not pairs:
        return []
    ini = new / "profiles.ini"
    fallback = False
    if ini.exists():
        cp = configparser.RawConfigParser()
        cp.read(ini, encoding="utf-8")
        fallback = cp.has_option("Move", "fallback")
    swap = {"profiles.ini", "profiles/My settings.tcprofile"} if fallback else set()
    made, replaced, part = [], {}, None
    try:
        for src, dst in pairs:
            if dst.exists():
                if dst.relative_to(new).as_posix() not in swap:
                    continue
                replaced[dst] = dst.read_bytes()
            dst.parent.mkdir(parents=True, exist_ok=True)
            part = dst.with_name(dst.name + ".part")
            shutil.copy2(src, part)
            os.replace(part, dst)
            made.append((src, dst))
        part = None
        for src, dst in made:
            if src.read_bytes() != dst.read_bytes():
                raise OSError(f"{dst.name} differs from its source")
        if not all(dst.is_file() for _, dst in pairs):
            raise OSError("a file is missing after the copy")
        if fallback and ini.exists():             # the old folder had no profiles.ini: drop the mark itself
            cp = configparser.RawConfigParser()
            cp.optionxform = str
            cp.read(ini, encoding="utf-8")
            if cp.remove_section("Move"):
                replaced.setdefault(ini, ini.read_bytes())
                buf = io.StringIO()
                cp.write(buf)
                atomic_write(ini, buf.getvalue())
        (new / MOVE_MARK).write_text(f"Copied from {old} on {time.strftime('%Y-%m-%d')}.\n")
    except OSError as err:
        for _, dst in made:
            if dst in replaced:
                dst.write_bytes(replaced[dst])
            else:
                dst.unlink(missing_ok=True)
        if ini in replaced and ini not in [d for _, d in made]:
            ini.write_bytes(replaced[ini])
        if part:
            part.unlink(missing_ok=True)
        (new / MOVE_MARK).unlink(missing_ok=True)
        return [f"Your settings from the previous version could not be copied ({err}).",
                f"They are still in {old}; nothing was deleted. The next start tries again."]
    try:
        (old / MOVED_NOTE).write_text(f"These files were copied to {new} on {time.strftime('%Y-%m-%d')}.\n"
                                      "This folder is no longer used; you can delete it.\n")
    except OSError:
        pass                                      # the done-mark in the new folder already says it
    return [f"Your profiles from the previous version were copied to {new} ({len(made)} of {len(pairs)} files"
            + (f", {len(pairs) - len(made)} already there)." if len(made) < len(pairs) else ").")]


# ── chunk: package_setup
def package_setup(here=None):
    """The per-user steps install.sh did, for the packaged copy only (this
    file in PKG_DIR): register the three shortcuts with kglobalaccel, live
    at once, and start the pad helper's autostart unit now: no relogin.
    Runs at the first start (no mark) and again whenever kglobalaccel has
    lost the toggle (a purge, a login, a reinstall: VM, 2026-10-28); a key
    the user cleared stays cleared. An install.sh copy in the home shadows
    the package's entries: then nothing runs and the note says what to
    remove first. Returns (first, notes): first = the mark was written now."""
    here = Path(here or Path(__file__).resolve().parent)
    if here != PKG_DIR:
        return False, []
    old = [p for p in OLD_COPIES if (Path.home() / p).exists()]
    if old:
        return False, ["An older copy from install.sh is still in your home folder:",
                       *[f"  ~/{p}" for p in old],
                       "Run ./uninstall.sh from the folder you cloned, then start Tablet Center again."]

    def run(*cmd):                       # (ok, stdout)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            return r.returncode == 0, r.stdout
        except (OSError, subprocess.TimeoutExpired):
            return False, ""

    bus = ("busctl", "--user", "call", "org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel")
    first = not SETUP_MARK.exists()
    if not first and run(*bus, "getComponent", "s", SHORTCUTS[0][0])[0]:
        return False, []
    notes, failed, keyless = [], [], []
    for comp, friendly, key in SHORTCUTS:
        # setShortcut with flag 2 (SetPresent) makes the key live at once; install.sh's setForeignShortcut
        # left it inactive until the next login. No NoAutoloading flag: a key the user set before stays.
        ok, out = run(*bus, "doRegister", "as", "4", comp, "_launch", friendly, friendly)
        if ok:
            ok, out = run(*bus, "setShortcut", "asaiu", "4", comp, "_launch", friendly, friendly,
                          "1", str(key), "2")
        if not ok:
            failed.append(friendly)
        elif out.split()[:2] == ["ai", "0"]:   # no key: another action owns it, or the user cleared it
            keyless.append(friendly)
    if failed:
        notes.append("Could not register: " + ", ".join(failed) + ". Tablet Center tries again at its next start.")
    elif keyless:
        notes.append("No key for: " + ", ".join(keyless) +
                     " (taken by another shortcut?). Set one in System Settings > Keyboard > Shortcuts.")
    else:
        notes.append("Shortcuts registered: Meta+Shift+F12 toggles precision mode, "
                     "Meta+Shift+F10 / F9 make the area bigger / smaller.")
    if run("systemctl", "--user", "daemon-reload")[0] and run("systemctl", "--user", "start", HOVER_UNIT)[0]:
        notes.append("The pad helper is running; it starts at every login.")
    else:
        notes.append("The pad helper starts at your next login.")
    if failed or not first:              # a failed registration is tried again at the next start
        return False, notes
    DATA.mkdir(parents=True, exist_ok=True)
    SETUP_MARK.write_text("1\n")
    return True, notes


# ── chunk: checkpoint
def checkpoint(pad, pen, sysname=None, status=None):
    """Copy the live value of every key into the active profile ([Image]
    kept). Backs the profile up first, once per window, when the text
    changes. Never raises - a missing or unreadable active profile is
    recreated instead, with a status warning. First start creates the
    default profile (ensure_first_start)."""
    def note(msg):
        if status:
            status(msg)

    ensure_first_start(pad, pen, sysname)
    active = active_profile_name() or "My settings"
    path = profile_path(active)
    old_image = ""
    old_text = None
    if path.exists():
        try:
            old_sections, _ = read_profile(path)
            old_image = old_sections["Image"]["png"]
            old_text = _render_profile(old_sections)
        except ProfileError:
            note(f"{active}.tcprofile could not be read - recreated from the live values.")
    else:
        note(f"{active}.tcprofile is missing - recreated from the live values.")
    live = _live_sections(pad, pen, sysname)
    live["Image"]["png"] = old_image
    new_text = _render_profile(live)
    if old_text is not None and new_text != old_text:
        backup_now(active)
    write_profile(path, live)


# ── chunk: switch_to
def switch_to(name, checkpoint_=True, pad=None, pen=None, sysname=None, status=None):
    """Switch the live settings to profile `name`. checkpoint_=False only for
    Replace and Apply (step 6's import clash), which already wrote the
    target profile itself. Raises SwitchStopped when a step fails - nothing
    after that step ran. Returns the read_profile warnings."""
    def note(msg):
        if status:
            status(msg)

    if checkpoint_:
        checkpoint(pad, pen, sysname, status=status)
    try:
        target, warnings = read_profile(profile_path(name))
    except ProfileError as err:
        raise SwitchStopped(3, str(err)) from err
    for w in warnings:
        note(w)

    if pad:
        for i in range(8):
            if target["Pad"][str(i)] == "Disabled":
                live = kreadconfig6(["ButtonRebinds", "Tablet", pad], str(i))
                if live != "Disabled":
                    try:
                        kwrite_literal(["ButtonRebinds", "Tablet", pad], str(i), "Disabled")
                    except RuntimeError as err:
                        raise SwitchStopped(4, str(err)) from err

    pre_conf = _raw_conf_values()
    try:
        with conf_lock():
            try:
                lines = CONF.read_text().splitlines()
            except OSError:
                lines = []
            first_owned, kept = None, []
            for line in lines:
                key = line.split("=", 1)[0].strip()
                if key in CONF_KEYS:
                    if first_owned is None:
                        first_owned = len(kept)
                    continue
                kept.append(line)
            block = [f"{k}={target['Conf'][k]}" for k in CONF_KEYS
                    if target["Conf"][k] != "" and _line_ok(k, target["Conf"][k])]
            pos = first_owned if first_owned is not None else len(kept)
            new_lines = kept[:pos] + block + kept[pos:]
            atomic_write(CONF, "\n".join(new_lines) + "\n")
    except TimeoutError as err:
        raise SwitchStopped(5, str(err)) from err
    if not poke_daemon():
        note(DAEMON_DOWN)
    time.sleep(0.3)

    if pad:
        for i in range(8):
            want = target["Pad"][str(i)]
            if want == "Disabled":
                continue                  # step 4 already handled a Disabled target
            live = kreadconfig6(["ButtonRebinds", "Tablet", pad], str(i))
            if want != live:
                try:
                    kwrite_literal(["ButtonRebinds", "Tablet", pad], str(i), want)
                except RuntimeError as err:
                    raise SwitchStopped(6, str(err)) from err
        for mode in range(4):
            want = target["Ring"][str(mode)]
            live = kreadconfig6(["ButtonRebinds", "TabletRing", pad, str(mode)], "0")
            if want != live:
                try:
                    kwrite_literal(["ButtonRebinds", "TabletRing", pad, str(mode)], "0", want)
                except RuntimeError as err:
                    raise SwitchStopped(6, str(err)) from err
    if pen:
        for code in (331, 332, 329):
            want = target["Pen"][str(code)]
            live = kreadconfig6(["ButtonRebinds", "TabletTool", pen], str(code))
            if want != live:
                try:
                    kwrite_literal(["ButtonRebinds", "TabletTool", pen], str(code), want)
                except RuntimeError as err:
                    raise SwitchStopped(6, str(err)) from err
        curve = target["Pressure"]["TabletToolPressureCurve"]
        if curve:
            rmin = target["Pressure"]["TabletToolPressureRangeMin"] or "0"
            rmax = target["Pressure"]["TabletToolPressureRangeMax"] or "1"
            try:
                apply_pressure(curve, rmin, rmax, pen, sysname, status=status)
            except RuntimeError as err:
                raise SwitchStopped(6, str(err)) from err

    if (RD / "saved-area").exists():
        changed = False
        for k in ("SCALE", "DIM"):
            new = target["Conf"].get(k, "")
            if new and _as_float(pre_conf.get(k, "")) != _as_float(new):
                changed = True
        if changed:
            try:
                subprocess.run(["bash", str(TOGGLE), "resize"], timeout=5,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError):
                pass

    set_active_profile(name)
    return warnings


if __name__ == "__main__":
    sys.exit(f"{Path(__file__).name} is a library, imported by tablet_center.py")
