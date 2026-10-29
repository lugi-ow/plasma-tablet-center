#!/usr/bin/env python3
# tablet-pointer-warp.py X Y [SW SH] - move the MOUSE pointer to a screen pixel through a virtual mouse on uinput; tablet-hover.py imports it.
# Part of plasma-tablet-center (MIT). Structure and contracts: PROJECT_MAP.md; mechanisms: TECHNICAL.md.
import ctypes as C
import fcntl
import os
import re
import struct
import subprocess
import sys
import time

debug = os.environ.get("DEBUG") == "1"


# ── chunk: trace
def trace(msg):
    if debug:
        print(f"# {msg}", file=sys.stderr)


# ── chunk: _IO
# ioctl numbers (asm-generic encoding: dir<<30 | size<<16 | type<<8 | nr)
def _IO(nr):
    return (ord("U") << 8) | nr


# ── chunk: _IOW
def _IOW(nr, size):
    return (1 << 30) | (size << 16) | (ord("U") << 8) | nr


# ── chunk: _IOR
def _IOR(nr, size):
    return (2 << 30) | (size << 16) | (ord("U") << 8) | nr


# ── chunk: uinput-constants
UI_DEV_CREATE, UI_DEV_DESTROY = _IO(1), _IO(2)
UI_DEV_SETUP = _IOW(3, 92)      # struct uinput_setup: input_id(8) + name[80] + u32
UI_ABS_SETUP = _IOW(4, 28)      # struct uinput_abs_setup: u16 code, pad, input_absinfo(24)
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT = _IOW(100, 4), _IOW(101, 4), _IOW(103, 4)
UI_GET_SYSNAME = _IOR(44, 64)
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
ABS_X, ABS_Y, BTN_LEFT, BUS_VIRTUAL = 0, 1, 0x110, 6
ABS_RANGE = 65536               # libinput: px = value * screen / (max - min + 1)

# ── chunk: chord-keys
# The chord alphabet: kcminputrc / QKeySequence key names -> kernel key codes
# at US positions (the helper presses PHYSICAL keys: under ru an app gets the
# key's Cyrillic letter, as from his keyboard). Modifiers, F1..F24, the
# letters, digits, punctuation and the editing / arrow / lock keys. NEVER
# registered: KEY_SYSRQ (Print) and the power keys - the kernel's magic-SysRq
# handler listens on any device that has KEY_LEFTALT, and the device's key set
# is the boundary (an unregistered code is dropped by the input core). No BTN_*
# code either: those flip the pointer / tablet / joystick class. KEY_* codes
# below 0x100 change no class beyond "keyboard", which the modifiers already set.
# CHORD_KEYS = every code, the set the daemon registers at create time.
KEYCODES = {"meta": 125, "super": 125, "win": 125, "ctrl": 29, "control": 29,
            "alt": 56, "shift": 42,
            **{f"f{n}": 58 + n for n in range(1, 11)},        # F1..F10 = 59..68
            "f11": 87, "f12": 88,
            **{f"f{n}": 170 + n for n in range(13, 25)}}      # F13..F24 = 183..194
TYPED_KEYS = {
    **{c: 2 + i for i, c in enumerate("1234567890")},                # 1..9, 0 = 2..11
    **{c: 16 + i for i, c in enumerate("qwertyuiop")},               # 16..25
    **{c: 30 + i for i, c in enumerate("asdfghjkl")},                # 30..38
    **{c: 44 + i for i, c in enumerate("zxcvbnm")},                  # 44..50
    "-": 12, "=": 13, "[": 26, "]": 27, ";": 39, "'": 40, "`": 41, "\\": 43,
    ",": 51, ".": 52, "/": 53,
    "esc": 1, "escape": 1, "backspace": 14, "tab": 15, "return": 28, "enter": 96,   # Enter = the keypad's
    "space": 57, "capslock": 58, "numlock": 69, "scrolllock": 70, "pause": 119, "menu": 139,
    "home": 102, "up": 103, "pgup": 104, "pageup": 104, "left": 105, "right": 106,
    "end": 107, "down": 108, "pgdown": 109, "pagedown": 109,
    "ins": 110, "insert": 110, "del": 111, "delete": 111}
SHIFT_OF = dict(zip("!@#$%^&*()_+{}|:\"~<>?", "1234567890-=[]\\;'`,./"))   # a shifted symbol -> its base key
SHIFTED = {c: TYPED_KEYS[base] for c, base in SHIFT_OF.items()}
# tabprec.conf is SOURCED by the toggle script, so a chord goes into it as letters, digits and '+' only
# (tablet_profiles._line_ok): a punctuation key is written as its Qt word, read back through the same table
CONF_WORDS = {"-": "Minus", "=": "Equal", "[": "BracketLeft", "]": "BracketRight", ";": "Semicolon",
              "'": "Apostrophe", "`": "QuoteLeft", "\\": "Backslash", ",": "Comma", ".": "Period", "/": "Slash"}
TYPED_KEYS.update({word.lower(): TYPED_KEYS[sym] for sym, word in CONF_WORDS.items()})
MODIFIER_ORDER = (42, 29, 56, 125)   # Shift, Ctrl, Alt, Meta: KWin's own emission order
MODIFIER_CODES = frozenset(MODIFIER_ORDER)
CHORD_KEYS = tuple(sorted(set(KEYCODES.values()) | set(TYPED_KEYS.values())))


# ── chunk: chord_parts
def chord_parts(text):
    """The '+'-separated names of a chord, with the '+' key itself kept
    ('Ctrl++' -> ['Ctrl', '+']); None when a '+' trails with no key after it."""
    parts = (text or "").split("+")
    if len(parts) >= 2 and parts[-1] == "":
        if parts[-2] != "":
            return None                              # 'Ctrl+': a separator with nothing after it
        parts = parts[:-2] + ["+"]
    return parts


# ── chunk: parse_chord
def parse_chord(text, need_key=True):
    """'Meta+Shift+F8' -> [42, 125, 66]: the modifiers in KWin's own order
    (Shift, Ctrl, Alt, Meta - what buttonrebindsfilter emits), then the
    other keys in the given order. A name that is a typed key ('[', 'Space',
    'q') gives its US-position code; a shifted symbol ('{') gives Shift plus
    its base key. need_key=True (a press or pie chord) demands exactly ONE
    non-modifier key; need_key=False (a touch chord, HELD while the finger
    rests) allows bare modifiers - except Meta alone, whose synthetic
    press-and-release is kglobalaccel's launcher tap. None when a name is no
    key the device carries, or on a broken shape."""
    parts = chord_parts(text)
    if parts is None:
        return None
    mods, keys = [], []
    for part in parts:
        name = part.strip().lower()
        code = KEYCODES.get(name)
        if code is None:
            code = TYPED_KEYS.get(name)
            if code is None and name in SHIFTED:
                code = SHIFTED[name]
                mods.append(42)
        (mods if code in MODIFIER_CODES else keys).append(code)
        if code is None:
            return None
    if need_key:
        if len(keys) != 1:
            return None
    elif not mods and not keys:
        return None
    ordered = sorted(set(mods), key=MODIFIER_ORDER.index) + keys
    if not need_key and ordered == [125]:
        return None                              # Meta alone = the launcher tap
    return ordered


# ── chunk: safe_chord
def safe_chord(text):
    """The chord as tabprec.conf may hold it: letters, digits and '+' only. A punctuation key becomes
    its Qt word ('Ctrl+[' -> 'Ctrl+BracketLeft'), a shifted symbol Shift plus its base key ('{' ->
    'Shift+BracketLeft'); spaces around the names go. The names are not judged (parse_chord does
    that); None on a trailing separator or a character that has no word."""
    parts = chord_parts(text)
    if parts is None:
        return None
    out = []
    for part in parts:
        name = part.strip()
        if name.isalnum():
            out.append(name)
        elif name in CONF_WORDS:
            out.append(CONF_WORDS[name])
        elif name in SHIFT_OF:
            out += ["Shift", CONF_WORDS.get(SHIFT_OF[name], SHIFT_OF[name])]
        else:
            return None
    return "+".join(out)


# ── chunk: chord
def chord(fd, codes, down):
    """Press (down=True) or release (reversed) the chord's keys, one frame
    per key like a keyboard. The caller sends any pointer motion FIRST: one
    device's events reach KWin in write order, so the warp is processed
    before the chord fires whatever the chord is bound to."""
    for code in (codes if down else reversed(codes)):
        emit(fd, EV_KEY, code, 1 if down else 0)
        emit(fd, EV_SYN, 0, 0)


# ── chunk: screen_size
def screen_size():
    x11 = C.CDLL("libX11.so.6")
    x11.XOpenDisplay.restype = C.c_void_p
    x11.XOpenDisplay.argtypes = [C.c_char_p]
    x11.XCloseDisplay.argtypes = [C.c_void_p]
    x11.XDefaultScreen.argtypes = [C.c_void_p]
    x11.XDisplayWidth.argtypes = [C.c_void_p, C.c_int]
    x11.XDisplayHeight.argtypes = [C.c_void_p, C.c_int]
    os.environ.setdefault("DISPLAY", ":0")
    dpy = x11.XOpenDisplay(None)
    if not dpy:
        sys.exit("no X display for the screen size - pass SW SH")
    screen = x11.XDefaultScreen(dpy)
    size = x11.XDisplayWidth(dpy, screen), x11.XDisplayHeight(dpy, screen)
    x11.XCloseDisplay(dpy)
    return size


# ── chunk: kwin_sees
def kwin_sees(event_node, timeout=1.5):
    """Poll KWin's input device list until event_node shows up, as a whole
    word of busctl's text ("as 2 "event5" ...") or JSON (--json=short) -
    quotes or none, so a changed quoting does not blind the probe. True =
    listed; False = still absent at the deadline, or no busctl/bus to ask."""
    deadline = time.monotonic() + timeout
    word = re.compile(rf"(?<![\w]){re.escape(event_node)}(?![\w])")
    while time.monotonic() < deadline:
        try:
            out = subprocess.run(
                ["busctl", "--user", "get-property", "org.kde.KWin",
                 "/org/kde/KWin/InputDevice", "org.kde.KWin.InputDeviceManager",
                 "devicesSysNames"], capture_output=True, text=True, timeout=0.5).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        if word.search(out):
            return True
        time.sleep(0.02)
    return False


# ── chunk: emit
def emit(fd, etype, code, value):
    os.write(fd, struct.pack("<qqHHi", 0, 0, etype, code, value))


# ── chunk: create
def create(keys=(), probe=True, strict=True, note=trace):
    """The virtual absolute mouse: (fd, its event node name), returned only
    once KWin lists the node. OSError = /dev/uinput cannot be opened;
    RuntimeError = KWin never picked the device up (it is gone again then).
    The caller keeps fd for as long as it wants to warp - one run here, the
    daemon's whole life in tablet-hover.py - and ends with destroy(fd).
    keys: extra keyboard codes to register (the daemon passes CHORD_KEYS so
    a CHORD_<n> added to the conf later needs no new device).
    probe=False skips the KWin look-up (the daemon with WARP=0 needs only
    the chords). strict=False keeps the device when KWin is not seen listing
    it (a changed busctl output must not kill every CHORD_<n>) and says so
    through note(); the one-shot warp stays strict."""
    fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    try:
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(fd, UI_SET_KEYBIT, BTN_LEFT)     # a button makes udev call it a mouse
        for code in keys:
            fcntl.ioctl(fd, UI_SET_KEYBIT, code)     # the daemon's chord alphabet
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
        for code in (ABS_X, ABS_Y):
            fcntl.ioctl(fd, UI_SET_ABSBIT, code)
            fcntl.ioctl(fd, UI_ABS_SETUP,
                        struct.pack("<H2x6i", code, 0, 0, ABS_RANGE - 1, 0, 0, 0))
        fcntl.ioctl(fd, UI_DEV_SETUP, struct.pack(
            "<HHHH80sI", BUS_VIRTUAL, 0x1209, 0x7ac0, 1, b"tablet pointer warp", 0))
        fcntl.ioctl(fd, UI_DEV_CREATE)
        buf = bytearray(64)
        fcntl.ioctl(fd, UI_GET_SYSNAME, buf)
        sysname = buf.split(b"\0", 1)[0].decode()
        nodes = [n for n in os.listdir(f"/sys/devices/virtual/input/{sysname}")
                 if n.startswith("event")]
        trace(f"virtual device {sysname} -> {nodes}")
        if not nodes:
            raise RuntimeError("the virtual pointer has no event node")
        if probe and not kwin_sees(nodes[0]):
            if strict:
                raise RuntimeError("KWin did not pick the virtual pointer up")
            note(f"KWin not seen listing {nodes[0]}: warp may land late")
    except BaseException:
        destroy(fd)
        raise
    return fd, nodes[0]


# ── chunk: destroy
def destroy(fd):
    """Remove the virtual mouse and close its descriptor."""
    try:
        fcntl.ioctl(fd, UI_DEV_DESTROY)
    except OSError:
        pass
    os.close(fd)


# ── chunk: warp
def warp(fd, fx, fy):
    """Move the pointer to the screen fraction fx, fy (0..1 each). Two
    frames: the kernel drops an ABS value equal to the axis's current one,
    so a second warp to the spot of the first would be lost - a frame one
    unit away first makes the real one a change every time. A fraction of
    the axis lands on the same physical spot on a scaled display too (a
    2560x1440 panel at 133% is 1920x1080 to KWin: cursorPos reads 0.75x)."""
    vx = max(0, min(ABS_RANGE - 1, int(fx * ABS_RANGE)))
    vy = max(0, min(ABS_RANGE - 1, int(fy * ABS_RANGE)))
    for x, y in ((vx ^ 1, vy ^ 1), (vx, vy)):
        emit(fd, EV_ABS, ABS_X, x)
        emit(fd, EV_ABS, ABS_Y, y)
        emit(fd, EV_SYN, 0, 0)


# ── chunk: main
def main():
    if len(sys.argv) not in (3, 5):
        sys.exit("usage: tablet-pointer-warp.py X Y [SW SH]")
    x, y = int(sys.argv[1]), int(sys.argv[2])
    sw, sh = (int(sys.argv[3]), int(sys.argv[4])) if len(sys.argv) == 5 else screen_size()
    x, y = max(0, min(sw - 1, x)), max(0, min(sh - 1, y))
    try:
        fd, _node = create()
    except OSError as err:
        sys.exit(f"/dev/uinput: {err}")
    except RuntimeError as err:
        sys.exit(str(err))
    try:
        warp(fd, (x + 0.5) / sw, (y + 0.5) / sh)
        time.sleep(0.05)                               # let KWin process it before the device goes
        trace(f"warped to {x} {y} on {sw}x{sh}")
    finally:
        destroy(fd)


if __name__ == "__main__":
    main()
