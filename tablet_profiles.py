#!/usr/bin/env python3
# tablet_profiles.py - profile files (read/write/validate), checkpoint() and switch_to() - Qt-free, imported by tablet_center.py. Part of plasma-tablet-center (MIT).
import base64
import configparser
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

# ── chunk: paths
CONF = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "tabprec.conf"
KCMINPUTRC = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kcminputrc"
RD = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "tabprec"
CTL = RD / "hover.ctl"
DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "tablet-center"
PROFILES_DIR = DATA / "profiles"
BACKUP_DIR = DATA / "backup"
PRESSURE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "tabprec" / "pressure"   # the helper's records; main never reads them
PRESSURE_LIGHT, PRESSURE_HARD, PRESSURE_MIN_STROKES = 10, 95, 20   # percentile ranks of the stroke peaks the tooltip names; strokes needed before it names any
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


CONF_LINE = re.compile(r"[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*=(.*)")


# ── chunk: parse_conf_line
def parse_conf_line(line):
    """THE conf line shape (PROJECT_MAP conf contract; the daemon, the size preview and both
    shell scripts read the same one): KEY [spaces] = [spaces] VALUE, a # starts a comment, a
    value in one pair of quotes loses them. (key, value), or None for any other line."""
    m = CONF_LINE.fullmatch(line.split("#", 1)[0])
    if not m:
        return None
    val = m.group(2).strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        val = val[1:-1]
    return m.group(1), val


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
            if (parse_conf_line(line) or (None,))[0] == key:
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
        out = [line for line in lines if (parse_conf_line(line) or (None,))[0] != key]
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
    (KWin's own setter carries no notify and skips an unchanged value).
    True = busctl exited 0; False = it failed or could not run (a stale node)."""
    try:
        return subprocess.run(["busctl", "--user", "set-property", "org.kde.KWin",
                              f"/org/kde/KWin/InputDevice/{sysname}", "org.kde.KWin.InputDevice",
                              prop, sig, value], capture_output=True, text=True, timeout=3).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ── chunk: pen_twins
def pen_twins(sysname):
    """sysname plus every other KWin tablet tool with its name, vendor and product, in KWin's order: while the pen
    driver runs, its copy (what the artist draws with) and the grabbed original (what the daemon, the toggle and the
    pen position read) both take a live write. One walk over the device list per apply; no driver = [sysname]."""
    def ident(node):
        return tuple(busctl_get(node, prop) for prop in ("name", "vendor", "product"))
    want = ident(sysname)
    if not all(want):
        return [sysname]
    try:
        out = subprocess.run(["busctl", "--user", "get-property", "org.kde.KWin", "/org/kde/KWin/InputDevice",
                              "org.kde.KWin.InputDeviceManager", "devicesSysNames"],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    nodes = out.replace('"', " ").split()[2:]
    return [sysname] + [n for n in nodes if n != sysname and busctl_get(n, "tabletTool") == "true" and ident(n) == want]


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


# ── chunk: conf_chord
def conf_chord(text):
    """A chord as tabprec.conf may hold it (tablet-pointer-warp.py safe_chord: the toggle script
    SOURCES the conf, so no quote, bracket or '#' may reach it). The text unchanged when the helper's
    module cannot be loaded or has no word for it - _line_ok still stands behind."""
    try:
        spec = importlib.util.spec_from_file_location(
            "tablet_pointer_warp", Path(__file__).resolve().parent / "tablet-pointer-warp.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.safe_chord(text) or text
    except Exception:                    # noqa: BLE001 - any import trouble = the text as typed
        return text


# ── chunk: chord_hazard
def chord_hazard(text):
    """'console' for Ctrl+Alt+F1..F12 without Shift (KWin's virtual-terminal filter fires on the
    xkb level of Ctrl+Alt; Meta changes nothing, Shift moves the key to level 2), 'mic' for any
    F20 chord, else None. Case and the Ctrl/Control spellings do not matter."""
    parts = {p.strip().lower() for p in (text or "").split("+")}
    if parts & {"ctrl", "control"} and "alt" in parts and "shift" not in parts \
            and parts & {f"f{n}" for n in range(1, 13)}:
        return "console"
    return "mic" if "f20" in parts else None


HAZARD_TEXT = {
    "console": "'{chord}' switches the screen to a text console (KWin does that on Ctrl+Alt+F1 to F12 "
               "without Shift, Meta or not). Add Shift to use it",
    "mic": "'{chord}' is the microphone mute key on most keyboards. It works as a shortcut, "
           "but it mutes the microphone",
}


# ── chunk: hazard_note
def hazard_note(label, text):
    """(kind, "<label>: <sentence>") for a chord chord_hazard flags, else (None, None). The
    caller adds what happens next: a console chord is refused, an F20 chord only warned."""
    kind = chord_hazard(text)
    if not kind:
        return None, None
    return kind, f"{label}: " + HAZARD_TEXT[kind].format(chord=text)


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
    so there is one pressure write path. Returns the warning when a live
    write failed (the node is stale: a bus switch renumbered the pen; the
    saved values apply when the pen reconnects), else None; status gets it too."""
    def note(msg):
        if status:
            status(msg)

    pairs = pen_groups_in_kcminputrc(pen_name)
    if not pairs and not sysname:
        note("Pressure settings apply after the pen connects once.")
        return None
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
        twins = pen_twins(sysname)
        failed = [prop for prop, sig, value in (("pressureCurve", "s", curve),
                                                ("pressureRangeMin", "d", str(rmin)),
                                                ("pressureRangeMax", "d", str(rmax)))
                  if not all([busctl_set(node, prop, sig, value) for node in twins])]   # a list: every twin is written
        if failed:
            warning = ("The pen did not take the pressure change right now (its connection may have changed). "
                       "The settings are saved and apply when the pen reconnects.")
            note(warning)
            return warning
    return None


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
        for node in pen_twins(sysname):
            busctl_set(node, "leftHanded", "b", value)


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


# ── chunk: ini_lock
class ini_lock:
    """$RD/profiles.lock around one read-modify-write of profiles.ini. wait=None waits for as long as
    it takes (a holder keeps it for well under a millisecond); a number of seconds raises TimeoutError
    when another writer holds it that long - the caller must write NOTHING then."""

    def __init__(self, wait=5):
        self.wait = wait

    def __enter__(self):
        RD.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(RD / "profiles.lock", os.O_CREAT | os.O_RDWR, 0o600)
        deadline = None if self.wait is None else time.monotonic() + self.wait
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if deadline is not None and time.monotonic() >= deadline:
                    os.close(self._fd)
                    raise TimeoutError("profiles.ini is busy")
                time.sleep(0.01)

    def __exit__(self, *exc):
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        return False


# ── chunk: update_ini
def update_ini(change, wait=5):
    """The only way to change profiles.ini: read it INSIDE the lock, let change(cp) edit it, write it.
    A stale copy held elsewhere can never overwrite a newer write. TimeoutError: nothing was written."""
    with ini_lock(wait):
        cp = _read_ini()
        change(cp)
        _write_ini(cp)


# ── chunk: get_device_ini
def get_device_ini():
    cp = _read_ini()
    return dict(cp.items("Device")) if cp.has_section("Device") else {}


# ── chunk: save_device
def save_device(pad, pen):
    """Only a live KWin detection calls this - the asleep/disconnected
    fallback chains read profiles.ini but never write it."""
    def change(cp):
        if not cp.has_section("Device"):
            cp.add_section("Device")
        if pad:
            cp.set("Device", "pad", pad)
        if pen:
            cp.set("Device", "pen", pen)
    try:
        update_ini(change)
    except TimeoutError:
        pass                                    # the next detection writes it again


# ── chunk: pressure_file
def pressure_file(profile):
    """The helper's record file of one profile (tablet-hover.py pressure_file gives the same path)."""
    name = quote(profile, safe=" ") if profile else "(none)"
    if len(name) > 200:
        name = name[:150] + "-" + hashlib.sha1(profile.encode("utf-8")).hexdigest()[:10]
    return PRESSURE_DIR / (name + ".json")


# ── chunk: pressure_stats
def pressure_stats(profile):
    """(strokes, hours, light, hard) over the helper's record of this profile: the strokes in its window,
    the drawing hours that window holds, and the PRESSURE_LIGHT / PRESSURE_HARD percentile (nearest rank)
    of the stroke peaks in whole percent. None = no readable record."""
    try:
        doc = json.loads(pressure_file(profile).read_text(encoding="utf-8"))
        if doc["v"] != 1:
            return None
        hist = [0] * 101
        for bins in doc["buckets"].values():
            for pct, count in enumerate(bins[:101]):
                hist[pct] += int(count)
        hours = min(float(doc["active_s"]), doc["keep"] * doc["bucket_s"]) / 3600
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    total = sum(hist)
    if not total:
        return (0, hours, None, None)

    def rank(percent):
        need, seen = -(-percent * total // 100), 0
        for pct, count in enumerate(hist):
            seen += count
            if seen >= need:
                return pct
    return (total, hours, rank(PRESSURE_LIGHT), rank(PRESSURE_HARD))


# ── chunk: pressure_tip
def pressure_tip(profile):
    """The Pen tab curve's tooltip: numbers from the helper's record of the active profile."""
    got = pressure_stats(profile)
    if got is None or got[0] < PRESSURE_MIN_STROKES:
        return (f"Pressure record: {got[0] if got else 0} strokes so far, "
                f"{PRESSURE_MIN_STROKES} are needed. It fills while the pad helper runs and you draw.")
    total, hours, light, hard = got
    minutes = max(1, round(hours * 60))
    span = f"{round(hours)} hours" if hours >= 1.5 else f"{minutes} minute{'s' if minutes != 1 else ''}"
    return (f"Last {span}: your light strokes peak near {light} %, your hardest reach {hard} % "
            f"({total} strokes, raw pen pressure before the curve).")


# ── chunk: active_profile_name
def active_profile_name():
    cp = _read_ini()
    return cp.get("Active", "name", fallback=None) or None


# ── chunk: set_active_profile
def set_active_profile(name):
    """Waits for the lock as long as it takes: it is the last step of a switch and must not be lost."""
    def change(cp):
        if not cp.has_section("Active"):
            cp.add_section("Active")
        cp.set("Active", "name", name)
    update_ini(change, wait=None)


# ── chunk: get_grid
def get_grid():
    """The 9 grid cells (1..9), each a profile name or ''."""
    cp = _read_ini()
    if not cp.has_section("Grid"):
        return [""] * 9
    return [cp.get("Grid", str(n), fallback="") for n in range(1, 10)]


# ── chunk: set_grid_cell
def set_grid_cell(cell, name):
    def change(cp):
        if not cp.has_section("Grid"):
            cp.add_section("Grid")
        cp.set("Grid", str(cell), name)
    update_ini(change)


# ── chunk: _perapp_read
def _perapp_read(cp):
    default = cp.get("PerApp", "default", fallback="").strip()
    rules = []
    if cp.has_section("PerAppProfile"):
        for idx, name in cp.items("PerAppProfile"):
            apps = perapp_split(cp.get("PerAppApps", idx, fallback=""))
            if name.strip() and apps:
                rules.append((name.strip(), apps))
    return default, rules


# ── chunk: perapp_get
def perapp_get():
    """(default profile name or '', [(profile, [app, ...]), ...]) from
    profiles.ini [PerApp] / [PerAppProfile] / [PerAppApps]. profiles.ini keeps
    unknown sections, so 'main' ignores these and leaves them in place."""
    return _perapp_read(_read_ini())


# ── chunk: _perapp_write
def _perapp_write(cp, default, rules):
    for sec in ("PerApp", "PerAppProfile", "PerAppApps"):
        cp.remove_section(sec)
    rules = [(name, perapp_split(apps) if isinstance(apps, str) else apps) for name, apps in rules]
    rules = [(name, apps) for name, apps in rules if name and apps]
    if default or rules:
        cp.add_section("PerApp")
        cp.set("PerApp", "default", default)
        cp.add_section("PerAppProfile")
        cp.add_section("PerAppApps")
        for n, (name, apps) in enumerate(rules, 1):
            cp.set("PerAppProfile", str(n), name)
            cp.set("PerAppApps", str(n), ";".join(apps))


# ── chunk: perapp_set
def perapp_set(default, rules):
    """Replace the whole per-app block; rules = [(profile, [app, ...])]. Empty
    default and no rules removes the three sections (the feature is off)."""
    update_ini(lambda cp: _perapp_write(cp, default, rules))


# ── chunk: perapp_edit
def perapp_edit(change):
    """Change the per-app block inside the lock: change(default, rules) -> (default, rules), both read
    from the file as it is NOW. Returns what was written. TimeoutError: nothing was written."""
    done = []

    def run(cp):
        default, rules = change(*_perapp_read(cp))
        _perapp_write(cp, default, rules)
        done[:] = _perapp_read(cp)
    update_ini(run)
    return tuple(done)


# ── chunk: perapp_related
def perapp_related(a, b):
    """The engine's relation (perapp_decide): equal, or one ends with '.' + the other."""
    return a == b or a.endswith("." + b) or b.endswith("." + a)


# ── chunk: perapp_split
def perapp_split(text):
    """The apps of one rule from typed text: comma, semicolon or newline
    separated, lower case, a trailing '.desktop' dropped, duplicates removed."""
    out = []
    for part in re.split(r"[,;\n]", text or ""):
        part = part.strip().lower()
        if part.endswith(".desktop"):
            part = part[:-len(".desktop")]
        if part and part not in out:
            out.append(part)
    return out


PERAPP_IGNORE = {"kando", "tablet-center", "tablet_center", "plasmashell", "krunner", "kwin_wayland",
                 "tablet_center.py", "xdg-desktop-portal-kde", "org.freedesktop.impl.portal.desktop.kde",
                 "org.kde.polkit-kde-authentication-agent-1", "org.kde.kdialog", "ksmserver-logout-greeter",
                 "org.kde.kscreenlocker_greet", "ksplashqml", "kded6", "org.kde.spectacle"}
PERAPP_TRANSIENT = {"dialog", "utility", "splash", "notification", "criticalnotification", "osd",
                    "onscreendisplay", "popupmenu", "dropdownmenu", "tooltip", "combobox", "dnd",
                    "menu", "toolbar", "dock", "desktop", "appletpopup", "lowestlayer", "specialwindow"}


DESKTOP_GENERIC = {"desktop", "app", "client", "studio", "editor", "viewer", "player", "browser", "settings",
                   "manager", "gui", "home", "sms"}


# ── chunk: desktop_keys
def desktop_keys(stem, wmclass="", stems=(), snap=False):
    """The window names one installed app answers to, lower case, for the per-app rules: its
    StartupWMClass (X11 windows), the whole desktop-file stem (Wayland windows) and the stem's part after the
    last dot (a snap file loses its '<snap>_' first), unless that part is generic or ends 2+ installed stems."""
    stem = stem.lower()
    base = stem.split("_", 1)[1] if snap and "_" in stem else stem
    last = base.rsplit(".", 1)[-1]
    if last in DESKTOP_GENERIC or sum(1 for t in stems if t == last or t.endswith("." + last)) > 1:
        last = ""
    out = []
    for key in (str(wmclass).strip().lower(), stem, last):
        if key and key not in out:
            out.append(key)
    return out


# ── chunk: desktop_apps
def desktop_apps():
    """[{'id', 'name', 'icon', 'keys'}] of the installed apps a person launches (Type=Application; NoDisplay,
    Hidden, NotShowIn=KDE and an OnlyShowIn without KDE left out), XDG_DATA_HOME then XDG_DATA_DIRS, the first
    folder wins per id. Sorted by name."""
    dirs = [Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")]
    dirs += [Path(d) for d in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":") if d]
    seen, found = set(), []
    for base in dirs:
        folder = base / "applications"
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.desktop")):
            ident = str(path.relative_to(folder)).replace("/", "-")
            if ident in seen:
                continue
            cp = configparser.RawConfigParser(strict=False, interpolation=None)
            try:
                cp.read(path, encoding="utf-8")
                entry = cp["Desktop Entry"]
            except (OSError, configparser.Error, KeyError, UnicodeDecodeError):
                continue
            seen.add(ident)
            if entry.get("Type", "") != "Application" or entry.get("NoDisplay", "").lower() == "true" \
                    or entry.get("Hidden", "").lower() == "true" or "KDE" in entry.get("NotShowIn", "") \
                    or (entry.get("OnlyShowIn") and "KDE" not in entry.get("OnlyShowIn", "")):
                continue
            found.append((ident[:-len(".desktop")], entry, "snapd" in path.parts))
    stems = [ident.lower() for ident, _, _ in found]
    apps = [{"id": ident, "name": entry.get("Name", "").strip() or ident, "icon": entry.get("Icon", "").strip(),
             "keys": desktop_keys(ident, entry.get("StartupWMClass", ""), stems, snap)}
            for ident, entry, snap in found]
    return sorted(apps, key=lambda a: a["name"].lower())


# ── chunk: seen_apps
def seen_apps():
    """The window classes the follower saw with focus since login, newest first."""
    try:
        return list(reversed((RD / "seen-apps").read_text().split()))
    except OSError:
        return []


# ── chunk: perapp_rows
def perapp_rows(keys, apps):
    """The keys of one profile as rows for a person: [{'name', 'icon', 'keys'}]. An installed app claims the
    keys that relate to one of its own (the engine's relation); a key no app claims is a row of its own."""
    rows, left = [], list(keys)
    for app in apps:
        mine = [k for k in left if any(perapp_related(k, a) for a in app["keys"])]
        if mine:
            rows.append({"name": app["name"], "icon": app["icon"], "keys": mine})
            left = [k for k in left if k not in mine]
    rows += [{"name": k, "icon": "", "keys": [k]} for k in left]
    return sorted(rows, key=lambda r: r["name"].lower())


# ── chunk: perapp_add
def perapp_add(profile, keys, apps=()):
    """Give the app named by keys to profile, inside the lock. Another profile's keys that relate to them go
    (with every key of the installed app they belong to): one owner per app. Returns (default, rules)."""
    keys = perapp_split(";".join(keys))

    def change(default, rules):
        gone = {k for _, ks in rules for k in ks if any(perapp_related(k, n) for n in keys)}
        for app in apps:
            if any(perapp_related(k, a) for k in gone for a in app["keys"]):
                gone |= {k for _, ks in rules for k in ks if any(perapp_related(k, a) for a in app["keys"])}
        out = []
        for name, ks in rules:
            ks = [k for k in ks if name == profile or k not in gone]
            if name == profile:
                ks = ks + [k for k in keys if k not in ks]
            out.append((name, ks))
        if profile not in [n for n, _ in out]:
            out.append((profile, keys))
        return default, out
    return perapp_edit(change)


# ── chunk: perapp_remove
def perapp_remove(profile, keys):
    """Take these keys off profile's row, inside the lock. Returns (default, rules)."""
    drop = set(keys)
    return perapp_edit(lambda default, rules: (default, [(n, [k for k in ks if n != profile or k not in drop])
                                                         for n, ks in rules]))


# ── chunk: perapp_default
def perapp_default(profile, on):
    """Make profile the default (on) or, when it is the default, none (off), inside the lock."""
    return perapp_edit(lambda default, rules: ((profile if on else ("" if default == profile else default)), rules))


# ── chunk: perapp_ignored
def perapp_ignored(name):
    """True when a class or desktop-file name is on the ignore list: the whole name or the part after
    the last dot (Kando's pie reads `menu.kando.kando`, the list holds `kando`)."""
    name = str(name).strip().lower()
    return bool(name) and (name in PERAPP_IGNORE or name.rsplit(".", 1)[-1] in PERAPP_IGNORE)


# ── chunk: perapp_decide
def perapp_decide(event, default, rules):
    """What one focus event means: ('ignore', why) for a window that never
    switches (none, transient, a listed class); ('want', profile) for the
    profile that app asks for, else the default's; ('keep', why) when no
    default is marked and no rule matches. `event` is the KWin script's dict:
    c = resourceClass, d = desktopFileName, t = window type, nw = a normal
    window, dlg / tr / sp = dialog / has a parent window / special window. The caller has dropped
    the rules and the default that name a profile which no longer exists."""
    if not event:
        return "ignore", "no window"
    cls = str(event.get("c", "")).strip().lower()
    dfn = str(event.get("d", "")).strip().lower()
    if dfn.endswith(".desktop"):
        dfn = dfn[:-len(".desktop")]
    if event.get("dlg") or event.get("tr") or event.get("sp") or event.get("nw") is False \
            or str(event.get("t", "")).lower() in PERAPP_TRANSIENT:
        return "ignore", "transient or dialog window"
    if perapp_ignored(cls) or perapp_ignored(dfn):
        return "ignore", "listed window"
    if not cls and not dfn:
        return "ignore", "no class"
    for name, apps in rules:
        if any(app in (cls, dfn) or cls.endswith("." + app) or dfn.endswith("." + app) for app in apps):
            return "want", name
    if default:
        return "want", default
    return "keep", "no default profile"


# ── chunk: follower_running
def follower_running():
    """True while tablet-appfocus.py runs: its pid file names a live process of that script."""
    try:
        pid = int((RD / "appfocus.pid").read_text().split()[0])
        return b"tablet-appfocus" in Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, ValueError, IndexError):
        return False


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
        kind, note = hazard_note(label, seq)
        if kind == "console":
            return "", note + "; dropped"
        if kind:
            return value, note
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
                kind, note = hazard_note(key, cleaned)
                if kind == "console":
                    warnings.append(note + "; dropped")
                    sections["Conf"][key] = ""
                    continue
                if kind:
                    warnings.append(note)
                sections["Conf"][key] = conf_chord(cleaned)
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
        elif value.startswith("AxisKey,"):
            for chord_ in value.split(",")[1:3]:
                kind, note = hazard_note(f"Ring mode {key}", chord_)
                if kind == "console":
                    warnings.append(note + "; the mode is dropped")
                    sections["Ring"][key] = ""
                    break
                if kind:
                    warnings.append(note)

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
        kv = parse_conf_line(line)
        if kv and kv[0] in values:
            values[kv[0]] = kv[1]
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

    def change(cp):
        if PROFILES_INI.exists():
            return                              # another start was first
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
    try:
        update_ini(change)
    except TimeoutError:
        pass                                    # the next start tries again


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
def switch_to(name, checkpoint_=True, pad=None, pen=None, sysname=None, status=None, wait_ready=None):
    """Switch the live settings to profile `name`. checkpoint_=False only for
    Replace and Apply (step 6's import clash), which already wrote the
    target profile itself. Raises SwitchStopped when a step fails - nothing
    after that step ran. Returns the read_profile warnings. wait_ready, when
    given, is called (it may block) right before the first live write of the
    pad rebinds and again before the second round: the per-app follower holds
    it until no key is down."""
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

    if wait_ready:
        wait_ready()
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
                key = (parse_conf_line(line) or (None,))[0]
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

    if wait_ready:
        wait_ready()
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
