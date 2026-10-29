#!/usr/bin/env python3
# tablet-pen-pos.py [--mapped] - print "X Y SCREEN_W SCREEN_H" (physical px) for the tablet pen; exit 1 = no position.
# Part of plasma-tablet-center (MIT). Structure and contracts: PROJECT_MAP.md; mechanisms: TECHNICAL.md.
import ctypes as C
import fcntl
import os
import re
import struct
import subprocess
import sys

os.environ.setdefault("DISPLAY", ":0")
debug = os.environ.get("DEBUG") == "1"
BTN_TOOL_PEN = 0x140                   # the key code of a pen tool: the pen node is found by it, never by its name
EVIOCGKEY_96 = 0x80604518              # EVIOCGKEY(96): the state of key codes 0 to 767
LONG_BITS = struct.calcsize("L") * 8   # /proc/bus/input/devices prints its bitmaps as unsigned longs
OUT_OF_RANGE = "out of range"          # evdev_pen_norm: the pen has no evdev position (out of proximity zeroes it)


# ── chunk: trace
def trace(msg):
    if debug:
        print(f"# {msg}", file=sys.stderr)


# ── chunk: screen_size
def screen_size():
    x11 = C.CDLL("libX11.so.6")
    x11.XOpenDisplay.restype = C.c_void_p
    x11.XOpenDisplay.argtypes = [C.c_char_p]
    x11.XDefaultScreen.argtypes = [C.c_void_p]            # a Display* passed as an int would be truncated to 32 bits
    x11.XDisplayWidth.argtypes = [C.c_void_p, C.c_int]    # on a build where the pointer sits above 4 GiB
    x11.XDisplayHeight.argtypes = [C.c_void_p, C.c_int]
    dpy = x11.XOpenDisplay(None)
    if not dpy:
        return None
    screen = x11.XDefaultScreen(dpy)
    return x11.XDisplayWidth(dpy, screen), x11.XDisplayHeight(dpy, screen), x11, dpy


# ── chunk: left_handed
def left_handed(sysname):
    """KDE's Left-handed mode on the pen node `sysname` (eventNN, also its KWin sysname). With it on,
    libinput turns the tablet 180 degrees before KWin maps it; the kernel's position does not turn.
    No answer from KWin = not turned."""
    try:
        out = subprocess.run(
            ["busctl", "--user", "get-property", "org.kde.KWin", f"/org/kde/KWin/InputDevice/{sysname}",
             "org.kde.KWin.InputDevice", "leftHanded"],
            capture_output=True, text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError) as err:
        trace(f"leftHanded: busctl: {err}")
        return False
    trace(f"leftHanded: {sysname} says {out!r}")
    return out == "b true"


# ── chunk: evdev_pen_norm
def evdev_pen_norm(devices="/proc/bus/input/devices", dev_dir="/dev/input"):
    """Normalized (0..1) pen position from the kernel, turned 180 degrees when KDE's Left-handed mode is
    on: the frame KWin maps. None: no pen node, or no access to it. OUT_OF_RANGE: the pen has no evdev
    position right now. The arguments are for the rig."""
    try:
        blocks = open(devices, encoding="utf-8", errors="replace").read().split("\n\n")
    except OSError:
        return None
    node = None
    for block in blocks:
        caps = re.search(r"^B: KEY=([0-9a-f ]+?)[ \t]*$", block, re.M)
        handler = re.search(r"Handlers=.*?(event\d+)", block)
        if not caps or not handler:
            continue
        bits = 0
        for word in caps.group(1).split():           # unsigned longs, the highest first
            bits = bits << LONG_BITS | int(word, 16)
        if bits >> BTN_TOOL_PEN & 1:                 # a capability, not a name: "pen" is also in "Suspend"
            node = os.path.join(dev_dir, handler.group(1))
            break
    if not node:
        trace(f"evdev: no device in {devices} can report BTN_TOOL_PEN")
        return None
    try:
        fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
    except PermissionError:
        trace(f"evdev: no read access to {node} (udev uaccess rule not applied)")
        return None
    except OSError as err:
        trace(f"evdev: {err}")
        return None
    try:
        axes = []
        for code in (0, 1):  # ABS_X, ABS_Y
            buf = bytearray(24)
            fcntl.ioctl(fd, 0x80184540 + code, buf)  # EVIOCGABS(code)
            value, lo, hi, _, _, _ = struct.unpack("6i", buf)
            trace(f"evdev: abs{code} value={value} range={lo}..{hi}")
            if hi <= lo:
                return None
            axes.append((value, lo, hi))
        keys = bytearray(96)
        fcntl.ioctl(fd, EVIOCGKEY_96, keys)
        if not keys[BTN_TOOL_PEN // 8] >> BTN_TOOL_PEN % 8 & 1 and all(value == lo for value, lo, _ in axes):
            trace("evdev: out of proximity and on the minimum of both axes: the kernel zeroed it on exit")
            return OUT_OF_RANGE                      # a lifted pen has no evdev position - the kernel zeroes X, Y and the tool key
        pos = tuple((value - lo) / (hi - lo) for value, lo, hi in axes)
    except OSError as err:
        trace(f"evdev ioctl: {err}")
        return None
    finally:
        os.close(fd)
    if left_handed(os.path.basename(node)):          # libinput inverts both axes (evdev-tablet.c invert_axis)
        pos = (1 - pos[0], 1 - pos[1])
        trace(f"evdev: left-handed mode, turned -> {pos}")
    return pos


# ── chunk: XIAnyClassInfo
class XIAnyClassInfo(C.Structure):
    _fields_ = [("type", C.c_int), ("sourceid", C.c_int)]


# ── chunk: XIValuatorClassInfo
class XIValuatorClassInfo(C.Structure):
    _fields_ = [("type", C.c_int), ("sourceid", C.c_int), ("number", C.c_int),
                ("label", C.c_ulong), ("min", C.c_double), ("max", C.c_double),
                ("value", C.c_double), ("resolution", C.c_int), ("mode", C.c_int)]


# ── chunk: XIDeviceInfo
class XIDeviceInfo(C.Structure):
    _fields_ = [("deviceid", C.c_int), ("name", C.c_char_p), ("use", C.c_int),
                ("attachment", C.c_int), ("enabled", C.c_int),
                ("num_classes", C.c_int),
                ("classes", C.POINTER(C.POINTER(XIAnyClassInfo)))]


# ── chunk: xwayland_stylus_norm
def xwayland_stylus_norm(dpy):
    """Normalized (0..1) stylus position from XWayland, or None (may be stale)."""
    xi = C.CDLL("libXi.so.6")
    xi.XIQueryDevice.restype = C.POINTER(XIDeviceInfo)
    xi.XIQueryDevice.argtypes = [C.c_void_p, C.c_int, C.POINTER(C.c_int)]
    xi.XIQueryVersion.argtypes = [C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_int)]
    xi.XIFreeDeviceInfo.argtypes = [C.POINTER(XIDeviceInfo)]
    major, minor = C.c_int(2), C.c_int(2)
    xi.XIQueryVersion(dpy, C.byref(major), C.byref(minor))
    count = C.c_int()
    devs = xi.XIQueryDevice(dpy, 0, C.byref(count))  # 0 = XIAllDevices
    pos = None
    for i in range(count.value):
        dev = devs[i]
        name = (dev.name or b"").decode("utf-8", "replace")
        if "stylus" not in name.lower() or not dev.enabled:
            continue
        axes = {}
        for j in range(dev.num_classes):
            if dev.classes[j].contents.type != 2:  # XIValuatorClass
                continue
            val = C.cast(dev.classes[j], C.POINTER(XIValuatorClassInfo)).contents
            if val.max > val.min:
                axes[val.number] = (val.value - val.min) / (val.max - val.min)
        if 0 in axes and 1 in axes:
            pos = (axes[0], axes[1])
            trace(f"xwayland: {name} -> {pos}")
            break
    xi.XIFreeDeviceInfo(devs)
    return pos


# ── chunk: output_area
def output_area():
    """The pen's KWin outputArea as (fx, fy, fw, fh) fractions, or None.

    An inputArea other than 0,0,1,1 (a cropped tablet) is not handled.
    Precision mode maps the pen to a rectangle of the screen and keeps it on
    the device as outputArea, so a tablet position n lands at fx + n * fw. The
    sysname comes from the cache tablet-precision.sh writes on every run that
    needs the pen: no cache (or one that is not the pen any more) means there
    is no precision mapping to undo, and the caller keeps the plain stretch.
    """
    cache = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "tabprec", "pen")
    try:
        with open(cache) as fh:
            sysname = fh.read().strip()
    except OSError:
        trace(f"outputArea: no {cache} - the pen is mapped to the whole screen")
        return None
    try:
        out = subprocess.run(
            ["busctl", "--user", "get-property", "org.kde.KWin",
             f"/org/kde/KWin/InputDevice/{sysname}", "org.kde.KWin.InputDevice",
             "tabletTool", "outputArea"],
            capture_output=True, text=True, timeout=2).stdout.splitlines()
    except (OSError, subprocess.SubprocessError) as err:
        trace(f"outputArea: busctl: {err}")
        return None
    if len(out) < 2:
        trace(f"outputArea: KWin said nothing about {sysname}")
        return None
    if out[0].strip() != "b true":
        trace(f"outputArea: {sysname} is not a tablet tool any more")
        return None
    fields = out[1].split()
    if len(fields) != 5 or fields[0] != "(dddd)":
        trace(f"outputArea: cannot read {out[1]!r}")
        return None
    try:
        fx, fy, fw, fh = (float(value) for value in fields[1:])
    except ValueError:
        trace(f"outputArea: cannot read {out[1]!r}")
        return None
    if fw <= 0 or fh <= 0:
        return None
    trace(f"outputArea: {fx} {fy} {fw} {fh}")
    return fx, fy, fw, fh


# ── chunk: main
def main(argv=None):
    """evdev, then XWayland, then exit 1. OUT_OF_RANGE exits 1 at once: XWayland cannot know anything newer."""
    argv = sys.argv[1:] if argv is None else argv
    scr = screen_size()
    if not scr:
        sys.exit(1)
    sw, sh = scr[0], scr[1]
    if "--screen" in argv:
        print(f"{sw} {sh}")                              # the screen size now (UNU-03): no pen read
        return

    pos = evdev_pen_norm()
    source = "evdev"
    if pos == OUT_OF_RANGE:
        trace("out of range: exit 1, the caller uses the mouse")
        sys.exit(1)
    if pos is None:
        pos = xwayland_stylus_norm(scr[3])           # comes through KWin: already turned for left-handed mode
        source = "xwayland"
    if pos is None:
        sys.exit(1)
    trace(f"source={source}")
    if "--mapped" in argv and source == "evdev":
        rect = output_area()
        if rect:
            pos = (rect[0] + pos[0] * rect[2], rect[1] + pos[1] * rect[3])
            trace(f"mapped -> {pos}")
    print(f"{int(pos[0] * sw)} {int(pos[1] * sh)} {sw} {sh}")


if __name__ == "__main__":
    main()
