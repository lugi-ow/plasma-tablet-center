#!/usr/bin/env python3
# tablet-pointer-warp.py X Y [SW SH] - move the MOUSE pointer to a screen pixel through a virtual mouse on uinput; tablet-hover.py imports it.
# Part of plasma-tablet-center (MIT). Structure and contracts: PROJECT_MAP.md; mechanisms: TECHNICAL.md.
import ctypes as C
import fcntl
import os
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
# The chord alphabet: kcminputrc's key names -> kernel key codes, for the
# modifiers and F1..F24 only (the project rule: letters resolve through the
# ACTIVE layout, F-keys and modifiers do not - and ONLY these codes may go
# on the device: other button classes flip libinput's classification).
# CHORD_KEYS = every code, the set the daemon registers at create time.
KEYCODES = {"meta": 125, "super": 125, "win": 125, "ctrl": 29, "control": 29,
            "alt": 56, "shift": 42,
            **{f"f{n}": 58 + n for n in range(1, 11)},        # F1..F10 = 59..68
            "f11": 87, "f12": 88,
            **{f"f{n}": 170 + n for n in range(13, 25)}}      # F13..F24 = 183..194
MODIFIER_ORDER = (42, 29, 56, 125)   # Shift, Ctrl, Alt, Meta: KWin's own emission order
MODIFIER_CODES = frozenset(MODIFIER_ORDER)
CHORD_KEYS = tuple(sorted(set(KEYCODES.values())))


# ── chunk: parse_chord
def parse_chord(text, need_key=True):
    """'Meta+Shift+F8' -> [42, 125, 66]: the modifiers in KWin's own order
    (Shift, Ctrl, Alt, Meta - what buttonrebindsfilter emits), then the
    other keys in the given order. need_key=True (a press or pie chord)
    demands exactly ONE non-modifier key; need_key=False (a touch chord,
    HELD while the finger rests) allows bare modifiers - except Meta
    alone, whose synthetic press-and-release is kglobalaccel's launcher
    tap. None outside the alphabet or on a broken shape."""
    mods, keys = [], []
    for part in (text or "").split("+"):
        code = KEYCODES.get(part.strip().lower())
        if code is None:
            return None
        (mods if code in MODIFIER_CODES else keys).append(code)
    if need_key:
        if len(keys) != 1:
            return None
    elif not mods and not keys:
        return None
    ordered = sorted(set(mods), key=MODIFIER_ORDER.index) + keys
    if not need_key and ordered == [125]:
        return None                              # Meta alone = the launcher tap
    return ordered


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
    """Poll KWin's input device list until event_node shows up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        out = subprocess.run(
            ["busctl", "--user", "get-property", "org.kde.KWin",
             "/org/kde/KWin/InputDevice", "org.kde.KWin.InputDeviceManager",
             "devicesSysNames"], capture_output=True, text=True).stdout
        if f'"{event_node}"' in out:
            return True
        time.sleep(0.02)
    return False


# ── chunk: emit
def emit(fd, etype, code, value):
    os.write(fd, struct.pack("<qqHHi", 0, 0, etype, code, value))


# ── chunk: create
def create(keys=()):
    """The virtual absolute mouse: (fd, its event node name), returned only
    once KWin lists the node. OSError = /dev/uinput cannot be opened;
    RuntimeError = KWin never picked the device up (it is gone again then).
    The caller keeps fd for as long as it wants to warp - one run here, the
    daemon's whole life in tablet-hover.py - and ends with destroy(fd).
    keys: extra keyboard codes to register (the daemon passes CHORD_KEYS so
    a CHORD_<n> added to the conf later needs no new device)."""
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
        if not nodes or not kwin_sees(nodes[0]):
            raise RuntimeError("KWin did not pick the virtual pointer up")
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
