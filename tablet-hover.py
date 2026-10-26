#!/usr/bin/env python3
# tablet-hover.py [--simulate] - the ExpressKey daemon: touch preview (ghost), area drag, pad chords, mouse warp.
# Part of plasma-wacom-center (MIT). Structure and contracts: PROJECT_MAP.md; mechanisms: TECHNICAL.md.
import fcntl
import importlib.util
import os
import re
import select
import stat
import struct
import subprocess
import sys
import time
from pathlib import Path

CONF = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "tabprec.conf"
RD = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "tabprec"
STATE = RD / "saved-area"           # exists while precision mode is ON
AREA = RD / "area"                  # "X Y W H DIM SW SH FX FY FW FH" of the mapping precision mode has on (the toggle writes it)
FIFO = RD / "overlay.fifo"          # the live overlay's geometry pipe (tablet-overlay.py --fifo)
MARK = RD / "relocate"              # kept fresh while the overlay follows the pen: the toggle moves (< 3 s old) instead of switching off
CTL = RD / "hover.ctl"              # a line here = re-read the conf (Wacom Center pokes it on Apply)
KCMINPUTRC = CONF.with_name("kcminputrc")   # KWin writes the pen's Left-handed mode here: turn_poll watches its stamp
DIR = Path(__file__).resolve().parent
SHOW_DELAY = 0.06                    # touch sense must hold this long before the ghost (debounce)
HOLD_DEFAULT = 0.15                  # ...and this long before a relocation (precision on); conf key HOLD, Wacom Center field
LONG_DEFAULT = 0.7                   # a press that confirmed a drag, kept down this long, switches precision mode off; conf key LONG, 0 = off
RESCAN = 2.0                        # seconds between checks for new/lost hidraw nodes (hardware only)
GRACE = 10.0                        # seconds the tablet may be gone with precision mode on before the mode pauses: a cable swap reconnects inside it
TICK = 0.03                          # poll period while a rectangle follows the pen or a press is being judged
HEAL_TRIES = 5                      # heal runs per tablet appearance: a run that fails is tried again at the next rescan
HEAL = {"proc": None, "left": 0, "due": False}   # the heal run in flight, the tries left, a run owed
TURN = {"key": None, "on": False}   # KDE's Left-handed mode on the pen, and the (node, kcminputrc stamp) it was read for
BUS = {"0003": "usb", "0005": "bt"}

# ── chunk: FAMILIES
# Built-in layouts: family -> bus -> (report id, touch byte, press byte).
# One bit per key in both bytes, key N = bit N-1. Measured on the Intuos
# Pro M over USB and Bluetooth (2026-09). Bluetooth byte 282 is the key
# byte the kernel's wacom_intuos_pro2_bt_pad() reads; 283 is the touch
# sense it skips.
FAMILIES = {
    "intuos-pro-2017": {"usb": (0x11, 2, 1), "bt": (0x80, 283, 282)},
}
# ── chunk: MODELS
# (vendor, product) -> family. M measured; S and L share the firmware
# generation and the kernel's Bluetooth pad parser, so the same layout is
# expected - the probe confirms it in a minute if the ghost never shows.
MODELS = {
    ("056A", "0357"): "intuos-pro-2017",   # Intuos Pro M (PTH-660), USB
    ("056A", "0360"): "intuos-pro-2017",   # Intuos Pro M, Bluetooth
    ("056A", "0358"): "intuos-pro-2017",   # Intuos Pro L (PTH-860), USB
    ("056A", "0361"): "intuos-pro-2017",   # Intuos Pro L, Bluetooth
    ("056A", "0392"): "intuos-pro-2017",   # Intuos Pro S (PTH-460), USB
    ("056A", "0393"): "intuos-pro-2017",   # Intuos Pro S, Bluetooth
}


# ── chunk: log
def log(msg):
    print(f"tablet-hover: {msg}", file=sys.stderr, flush=True)


# ── chunk: read_conf
def read_conf():
    values = {}
    try:
        for line in CONF.read_text(errors="replace").splitlines():   # a stray byte must not end the daemon
            key, _, val = line.partition("=")
            values[key.strip()] = val.split("#", 1)[0].strip()
    except OSError:
        pass
    return values


# ── chunk: as_int
def as_int(text, default=None):
    try:
        return int(text, 0)
    except (TypeError, ValueError):
        return default


# ── chunk: touch_delay
def touch_delay(conf, n=None):
    """Key n's touch register (the Pad tab's per-key column): seconds a
    resting finger waits before its touch action starts - a touch chord,
    the area drag. Conf TOUCH_HOLD_<n>, falling back to HOLD, then the
    default. No floor: 0 = at the first touch report."""
    keys = (f"TOUCH_HOLD_{n}", "HOLD") if n else ("HOLD",)
    for key in keys:
        try:
            return max(0.0, float(conf[key]))
        except (KeyError, ValueError):
            continue
    return HOLD_DEFAULT


# ── chunk: hold_delay
def hold_delay(conf, n=None):
    """Seconds a finger must rest before a rectangle moves: the ghost
    debounce with precision mode off, the precision key's touch register
    with it on."""
    return touch_delay(conf, n) if STATE.exists() else SHOW_DELAY


# ── chunk: long_delay
def long_delay(conf):
    """Seconds a press that confirmed a drag must stay down to switch
    precision mode off (conf LONG); 0 or less = no long press."""
    try:
        return float(conf.get("LONG", LONG_DEFAULT))
    except ValueError:
        return LONG_DEFAULT


# ── chunk: layout_for
def layout_for(conf, bus, product):
    """Report layout for one node: conf keys over built-ins. None = unknown.
    Returns {report, touch, press, mask, press_mask}; mask None = not learned yet."""
    def pick(key, fallback=None):
        val = as_int(conf.get(f"{key}_{bus.upper()}", conf.get(key)))
        return fallback if val is None else val
    builtin = FAMILIES.get(MODELS.get(("056A", product.upper()), ""), {}).get(bus) or (None, None, None)
    touch = pick("HOVER_BYTE", builtin[1])
    if touch is None:
        return None
    mask = pick("HOVER_MASK")
    return {"report": pick("HOVER_REPORT", builtin[0] if builtin[0] is not None else 0x80),
            "touch": touch, "press": pick("PRESS_BYTE", builtin[2]),
            "mask": mask, "press_mask": pick("PRESS_MASK", mask)}


# ── chunk: wacom_nodes
def wacom_nodes():
    """[(path, bus, product)] for every Wacom hidraw node (vendor 056A)."""
    found = []
    base = "/sys/class/hidraw"
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return found
    for name in names:
        try:
            uevent = open(f"{base}/{name}/device/uevent", encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        match = re.search(r"^HID_ID=(\d{4}):0000056A:0000([0-9A-F]{4})", uevent, re.M | re.I)
        if match:
            found.append((f"/dev/{name}", BUS.get(match.group(1), match.group(1)),
                          match.group(2).upper()))
    return found


# ── chunk: pen_node
def pen_node():
    """The tablet's Pen evdev node name (eventNN), which is also the pen's KWin sysname; None = no pen."""
    try:
        blocks = open("/proc/bus/input/devices", encoding="utf-8", errors="replace").read().split("\n\n")
    except OSError:
        return None
    for block in blocks:
        if 'Name="Wacom' in block and 'Pen"' in block:
            match = re.search(r"Handlers=.*?(event\d+)", block)
            if match:
                return match.group(1)
    return None


# ── chunk: pen_open
def pen_open():
    """fd of the tablet's Pen evdev node (uaccess ACL), or None."""
    node = pen_node()
    try:
        return os.open(f"/dev/input/{node}", os.O_RDONLY | os.O_NONBLOCK) if node else None
    except OSError:
        return None


# ── chunk: left_handed
def left_handed(sysname):
    """KWin's leftHanded for the pen node `sysname`: True, False, or None when KWin gives no answer.
    KDE's Left-handed mode (System Settings > Drawing Tablet > Pen) makes libinput turn the tablet
    180 degrees before KWin maps it; the kernel's position, which pen_norm reads, does not turn."""
    try:
        out = subprocess.run(["busctl", "--user", "get-property", "org.kde.KWin",
                              f"/org/kde/KWin/InputDevice/{sysname}", "org.kde.KWin.InputDevice", "leftHanded"],
                             capture_output=True, text=True, timeout=1).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return {"b true": True, "b false": False}.get(out)


# ── chunk: turn_poll
def turn_poll():
    """TURN kept right off the hot path: run when a node set opens and at every rescan, it asks KWin
    only when the pen node or kcminputrc (KWin writes the setting there) changed, or the last ask got
    no answer. A change in System Settings reaches the pen within one rescan."""
    node = pen_node()
    if node is None:
        return                                # no pen node: nothing to ask about
    try:
        st = KCMINPUTRC.stat()
        stamp = (st.st_ino, st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    if (node, stamp) == TURN["key"]:
        return
    on = left_handed(node)
    if on is None:
        TURN["key"] = None                    # KWin does not list the pen yet: asked again at the next rescan
        return
    if on != TURN["on"]:
        log(f"left-handed mode {'on: pen positions turned 180 degrees' if on else 'off'} ({node})")
    TURN["on"], TURN["key"] = on, (node, stamp)


# ── chunk: pen_norm
def pen_norm(fd):
    """Normalized (0..1) pen position from the kernel's current ABS state, or None. The kernel zeroes
    X, Y and the tool key whenever the pen leaves proximity, so a lifted pen has no evdev position -
    same minimum-on-both-axes reading as a node that has not reported yet. Taken for a position, that
    sent the ghost, the drag and the mouse warp to the top-left corner. So out of proximity AND on the
    minimum together mean no position at all, never the corner. Turned 180 degrees while KDE's
    Left-handed mode is on (TURN): the frame KWin maps, for every caller."""
    axes = []
    for code in (0, 1):                      # ABS_X, ABS_Y
        buf = bytearray(24)
        try:
            fcntl.ioctl(fd, 0x80184540 + code, buf)   # EVIOCGABS(code)
        except OSError:
            return None
        value, lo, hi = struct.unpack("6i", buf)[:3]
        if hi <= lo:
            return None
        axes.append((value, lo, hi))
    keys = bytearray(96)
    try:
        fcntl.ioctl(fd, 0x80604518, keys)             # EVIOCGKEY(96): byte 40 bit 0 = BTN_TOOL_PEN (320), the pen in proximity
    except OSError:
        return None
    if not keys[40] & 1 and all(value == lo for value, lo, _ in axes):
        return None
    pos = tuple((value - lo) / (hi - lo) for value, lo, hi in axes)
    return (1 - pos[0], 1 - pos[1]) if TURN["on"] else pos   # libinput inverts both axes (invert_axis)


# ── chunk: toggle
def toggle(mode, *extra):
    """Run tablet-precision.sh MODE [ARGS] (suspend / resume / toggle / pause); False when it failed."""
    try:
        return subprocess.run([str(DIR / "tablet-precision.sh"), mode, *extra], timeout=5,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


# ── chunk: heal
def heal():
    """A tablet appeared: ask for `tablet-precision.sh heal` in the background, never waiting - the
    pen's mapping is put right after a connect, a wake or a bus switch. Its line, when it acts, lands
    in this journal. One run at a time: a request made while one runs is served when it ends."""
    HEAL["left"], HEAL["due"] = HEAL_TRIES, True
    heal_poll()


# ── chunk: heal_poll
def heal_poll():
    """Reap the heal run and start the one that is due. A run that exits non-zero lost its 2 s wait
    for KWin to list the pen (or the lock, or the mapping write): it is due again, HEAL_TRIES runs
    per request, so a rectangle is not left on the pen until the next bus change."""
    if HEAL["proc"] is not None:
        code = HEAL["proc"].poll()
        if code is None:
            return
        HEAL["proc"] = None
        if code != 0:
            HEAL["left"] -= 1
            HEAL["due"] = HEAL["due"] or HEAL["left"] > 0
            log(f"heal failed (exit {code}): " + (f"another try, {HEAL['left']} left" if HEAL["left"] > 0
                                                 else "no more tries until a tablet appears again"))
    if HEAL["due"]:
        HEAL["due"] = False
        try:
            HEAL["proc"] = subprocess.Popen([str(DIR / "tablet-precision.sh"), "heal"], stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, start_new_session=True)
        except OSError as err:
            log(f"heal not started: {err}")


# ── chunk: Follower
class Follower:
    """The rectangle that follows the pen. Precision OFF: the GHOST, an
    overlay of its own in the waiting look. Precision ON: the real overlay,
    dragged through its pipe for a RELOCATION with the waiting border -
    snapped back home and solid again on a lift, left where it is and
    solid for the toggle on a press."""

    def __init__(self):
        self.proc = None             # the ghost overlay process (its stdin is the geometry pipe)
        self.sink = None             # fd the geometry lines go to; None = nothing follows
        self.owner = None            # hidraw fd whose reports drive the current follow
        self.pen = None              # Pen evdev fd while following
        self.geom = None             # (w, h, sw, sh)
        self.last = None             # last (x, y) written
        self.home = None             # relocation only: the line that puts the overlay back

    @property
    def up(self):
        return self.sink is not None

    def show(self, owner):
        """Precision OFF: draw the ghost where a toggle would map right now."""
        if self.up or STATE.exists():
            return
        try:
            out = subprocess.run([str(DIR / "tablet-precision.sh"), "where"],
                                 capture_output=True, text=True, timeout=5).stdout.split()
            x, y, w, h, dim, sw, sh = out[:7]
            self.geom = tuple(map(int, (w, h, sw, sh)))
            self.last = (int(x), int(y))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return
        self.proc = subprocess.Popen(
            ["python3", str(DIR / "tablet-overlay.py"), x, y, w, h, dim, "--waiting", "--follow"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.sink = self.proc.stdin.fileno()
        self.home = None
        self.owner = owner
        self.pen = pen_open()

    def move(self, owner):
        """Precision ON: drag the real overlay after the pen until a press (the
        toggle re-maps there) or a lift (snap back)."""
        if self.up or not STATE.exists():
            return
        try:
            x, y, w, h, _dim, sw, sh = AREA.read_text().split()[:7]
            self.geom = tuple(map(int, (w, h, sw, sh)))
            self.sink = os.open(FIFO, os.O_WRONLY | os.O_NONBLOCK)   # ENXIO: no overlay reads the pipe
        except (OSError, ValueError) as err:
            log(f"cannot relocate ({err}): switch precision mode off and on again")
            return
        self.home = f"{x} {y} {w} {h}\n".encode()
        self._send(b"waiting\n")              # the flowing border: a preview until the press
        self.last = None                      # the first tick places the rectangle at the pen's aim
        self.owner = owner
        self.pen = pen_open()
        MARK.touch()
        if not toggle("suspend"):             # base mapping while aiming: the cursor roams, the area travels with it
            log("suspend failed: the cursor stays inside the old area while aiming")

    def follow(self):
        """Re-place the rectangle for the pen's current position (toggle formula)."""
        if not self.up or self.pen is None:
            return
        if self.home is not None:
            MARK.touch()                      # still relocating: the marker stays fresh for the toggle
        norm = pen_norm(self.pen)
        if norm is None:
            return
        w, h, sw, sh = self.geom
        cx, cy = int(norm[0] * sw), int(norm[1] * sh)
        x = int(cx / sw * (sw - w) + 0.5)
        y = int(cy / sh * (sh - h) + 0.5)
        if (x, y) == self.last:
            return
        self.last = (x, y)
        if not self._send(f"{x} {y} {w} {h}\n".encode()):
            self.hide()

    def _send(self, line):
        try:
            os.write(self.sink, line)
            return True
        except OSError:
            return False

    def hide(self, owner=None, confirmed=False):
        """Ghost: close it. Relocation: cancelled - the overlay goes home and
        the marker is dropped - unless confirmed, when the overlay stays put
        and the marker waits for the toggle."""
        if not self.up or owner not in (None, self.owner):
            return
        if self.home is not None and confirmed:
            log(f"relocation confirmed: overlay at {self.last}, the toggle maps there")
            self._send(b"solid\n")            # activated: the border stops flowing where it is
        elif self.home is not None:
            log("relocation cancelled: overlay back home")
            self._send(self.home + b"solid\n")
            MARK.unlink(missing_ok=True)
            if not toggle("resume"):          # the old area is mapped again
                log("resume failed: the pen stays on the whole screen with the mode on; "
                    "press the precision key to end it")
        if self.proc is not None:
            self.proc.stdin.close()           # closes the sink fd; the ghost quits on EOF
            self.proc.terminate()
            try:
                self.proc.wait(1)
            except subprocess.TimeoutExpired:
                pass
            self.proc = None
        else:
            os.close(self.sink)
        self.sink = self.owner = self.home = None
        if self.pen is not None:
            os.close(self.pen)
            self.pen = None


# ── chunk: Warper
class Warper:
    """The mouse pointer moved onto the pen when a finger lands on a pad key
    (and again at the press), so that whatever the key's chord opens - a
    Kando pie bound in Kando's own editor, anything that asks KWin where the
    pointer is - lands under the pen. One virtual absolute mouse
    (tablet-pointer-warp.py, imported from this file's folder), created once
    and kept for the daemon's life: KWin lists it a single time and a warp
    costs one motion event. The device also carries the chord alphabet's
    keyboard keys: chord_down presses a conf CHORD_<n> right after the warp
    on the same device (write order = KWin's processing order, so the warp
    is in place before the chord opens anything), chord_up releases it with
    the pad key, release_all sweeps up when a node goes away. Precision ON:
    the pen's tablet position goes through the area's fractions from the
    area file (the correction tablet-pen-pos.py --mapped makes); during a
    drag the base mapping is back and the plain position is right. No
    /dev/uinput, no KWin: the warper stays off with a line in the log,
    retried every 30 s, and chords are skipped with a line."""
    RETRY = 30.0

    def __init__(self):
        self.fd = None
        self.next_try = 0.0
        self.screen = None           # (sw, sh) for the log's pixels, when X answers
        self.held = {}               # ("press" | "touch", key number) -> (codes, label) currently down
        self.mod = None
        try:
            spec = importlib.util.spec_from_file_location(
                "tablet_pointer_warp", DIR / "tablet-pointer-warp.py")
            self.mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.mod)
        except Exception as err:                     # noqa: BLE001 - any import trouble = no warp, the rest runs
            log(f"no pointer warp: {err}")

    def ensure(self):
        """Create the virtual mouse when there is none; a failure waits RETRY s."""
        if self.fd is not None or self.mod is None or time.monotonic() < self.next_try:
            return
        self.next_try = time.monotonic() + self.RETRY
        try:
            self.fd, node = self.mod.create(self.mod.CHORD_KEYS)
        except (OSError, RuntimeError) as err:
            log(f"pointer warp off ({err}); next try in {self.RETRY:.0f} s")
            return
        try:
            self.screen = self.mod.screen_size()
        except (SystemExit, OSError):
            self.screen = None
        log(f"pointer warp ready: virtual mouse {node}")

    def close(self):
        if self.fd is not None:
            self.mod.destroy(self.fd)   # the kernel releases any keys still down
            self.fd = None
        self.held.clear()

    def chord_down(self, tag, text, label):
        """Press a conf chord on the virtual device - called right after the
        warp that triggered it, so the same fd carries the motion first and
        the chord second. tag = ("press" | "touch", key number); a press
        chord must carry one non-modifier key, a touch chord may be bare
        modifiers (it is held, not tapped)."""
        if tag in self.held:
            return
        codes = self.mod.parse_chord(text, tag[0] == "press") if self.mod else None
        if codes is None:
            log(f"chord ({label}) '{text}': outside the chord rules, ignored")
            return
        if self.fd is None:
            log(f"chord ({label}): no virtual device, skipped")
            return
        try:
            self.mod.chord(self.fd, codes, True)
        except OSError as err:
            log(f"chord failed ({err}); the virtual mouse is recreated")
            self.close()
            return
        self.held[tag] = (codes, label)
        log(f"chord ({label}): {text} down")

    def chord_up(self, tag):
        """Release a held chord: its pad key came up, the finger lifted, or
        its node went away."""
        entry = self.held.pop(tag, None)
        if entry is None or self.fd is None:
            return
        codes, label = entry
        try:
            self.mod.chord(self.fd, codes, False)
        except OSError as err:
            log(f"chord release failed ({err}); the virtual mouse is recreated")
            self.close()
            return
        log(f"chord ({label}): up")

    def release_all(self):
        """Every held chord up - a hidraw node vanished mid-press, or the
        watch loop is leaving."""
        for tag in list(self.held):
            self.chord_up(tag)

    def warp(self, mapped, why="press"):
        """Move the mouse onto the pen; mapped = through the area on record.
        why names the trigger in the log (touch / press)."""
        if self.fd is None:
            return
        pen = pen_open()
        if pen is None:
            return
        try:
            norm = pen_norm(pen)
        finally:
            os.close(pen)
        if norm is None:
            return
        fx, fy = norm
        if mapped:
            try:
                ax, ay, aw, ah = map(float, AREA.read_text().split()[7:11])
                fx, fy = ax + fx * aw, ay + fy * ah
            except (OSError, ValueError):
                pass                                 # no fractions on record: the plain stretch
        try:
            self.mod.warp(self.fd, fx, fy)
        except OSError as err:
            log(f"pointer warp failed ({err}); the virtual mouse is recreated")
            self.close()
            return
        where = (f"({int(fx * self.screen[0])}, {int(fy * self.screen[1])})" if self.screen
                 else f"({fx:.4f}, {fy:.4f}) of the screen")
        log(f"warp ({why}): mouse to {where}" + (" in the precision area" if mapped else ""))


# ── chunk: mask_text
def mask_text(mask):
    return "none (no Precision key ticked)" if mask is None else f"0x{mask:02x}"


# ── chunk: watch
def watch(conf, follower, warper=None):
    """Follow the current set of hidraw nodes until it changes or goes away.
    A line on the control pipe reloads the conf (deferred while a rectangle
    follows the pen). False = there was nothing to watch."""
    if warper is not None:
        warper.ensure()
    nodes = wacom_nodes()
    try:
        RD.mkdir(parents=True, exist_ok=True)   # the toggle makes this too, but the daemon starts first at a fresh login
        if CTL.exists() and not stat.S_ISFIFO(CTL.stat().st_mode):
            CTL.unlink()                        # a plain file left by a hand `echo reload >` is ALWAYS ready: select would spin
        os.mkfifo(CTL)
    except FileExistsError:
        pass
    except OSError as err:
        log(f"no control pipe ({err}): conf reloads need a daemon restart")
    try:
        ctl = os.open(CTL, os.O_RDWR | os.O_NONBLOCK)   # RDWR: pokers may come and go
    except OSError:
        ctl = None
    conf = read_conf()                # again, now that the pipe is open: a poke sent before this open is not lost
    fds = {}                          # fd -> [path, bus, product, layout]

    def attach(node, quiet=False):
        """Open one node into fds. False = it would not open: the caller keeps it for another try."""
        path, bus, product = node
        layout = layout_for(conf, bus, product)
        if layout is None:
            log(f"{path} ({bus}, product {product}): unknown layout - run tablet-pad-probe.py")
            return True
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError as err:
            if not quiet:
                log(f"{path}: {err}")
            return False
        fds[fd] = [path, bus, product, layout]
        log(f"{path}: {bus} report 0x{layout['report']:02x}, touch byte {layout['touch']}, "
            f"press byte {layout['press']}, precision key {mask_text(layout['mask'])}")
        return True

    # A USB plug-in makes the node before udev grants access to it (and the wacom driver replaces the
    # first node under the SAME name): an open that lands in that gap fails, and the node set never
    # changes again. Such a node is tried again at every rescan.
    missed = [node for node in nodes if not attach(node)]
    if not fds:
        if ctl is not None:
            os.close(ctl)
        return False
    heal()                            # a tablet just appeared: a precision rectangle KWin kept for its pen is put right
    TURN["key"] = None                # ...and its Left-handed mode is asked again (KDE keeps it per cable and Bluetooth)
    turn_poll()
    pending = {}                      # fd -> time the touch began; None once acted on
    held = set()                      # fds whose key was pressed: nothing more until the finger leaves it
    down = {}                         # fd -> last press-byte value
    rest = {}                         # fd -> last touch-byte value (a finger landing on any key warps the mouse)
    long_press = {}                   # fd -> deadline while a key pressed after a drag stays down: precision mode off at the deadline
    touch_since = {}                  # (fd, bit) -> when the finger landed on a TOUCH_CHORD key; gone once engaged, pressed or lifted
    reload_pending = False            # a control-pipe poke arrived; applied once nothing follows the pen
    last_scan = time.monotonic()
    try:
        while fds:
            now = time.monotonic()
            timeout = max(0.0, last_scan + RESCAN - now)
            for pfd, since in pending.items():
                if since is not None:
                    mask_n = (fds[pfd][3]["mask"] or 0).bit_length() or None
                    timeout = min(timeout, max(0.0, since + hold_delay(conf, mask_n) - now))
            for (tfd, bit), since in touch_since.items():
                timeout = min(timeout, max(0.0, since + touch_delay(conf, bit + 1) - now))
            for deadline in long_press.values():
                timeout = min(timeout, max(0.0, deadline - now))
            if follower.up:
                timeout = min(timeout, TICK)
            readable = list(fds) + ([ctl] if ctl is not None else [])
            ready, _, _ = select.select(readable, [], [], timeout)
            for fd in ready:
                if fd == ctl:
                    os.read(ctl, 4096)                        # drain; any content means "reload"
                    reload_pending = True
                    continue
                lay = fds[fd][3]
                try:
                    report = os.read(fd, 4096)
                except OSError:
                    report = b""
                if not report:
                    os.close(fd)
                    missed.append(tuple(fds[fd][:3]))   # replaced under the same name: the rescan opens it again
                    del fds[fd]
                    pending.pop(fd, None)
                    held.discard(fd)
                    down.pop(fd, None)
                    rest.pop(fd, None)        # the fd number comes back with the next node: no stale touch bits
                    long_press.pop(fd, None)
                    for key in [k for k in touch_since if k[0] == fd]:
                        del touch_since[key]
                    follower.hide(fd)
                    if warper is not None:
                        warper.release_all()      # a chord must not outlive its pad key's node
                    continue
                if report[0] != lay["report"] or len(report) <= lay["touch"]:
                    continue
                pb = lay["press"]
                press_bits = report[pb] if pb is not None and len(report) > pb else 0
                new_bits = press_bits & ~down.get(fd, 0)
                gone_bits = down.get(fd, 0) & ~press_bits
                down[fd] = press_bits
                touch_bits = report[lay["touch"]]
                new_touch = touch_bits & ~rest.get(fd, 0)
                gone_touch = rest.get(fd, 0) & ~touch_bits
                rest[fd] = touch_bits
                if (new_touch or new_bits) and warper is not None and conf.get("WARP", "1") != "0":
                    # the mouse onto the pen when a finger LANDS on any key, and again at the press.
                    # For a key kcminputrc owns this is best effort (KWin fires the chord inside its
                    # own handling of the pad button; the warp can lose). A CHORD_<n> key is exact:
                    # its chord goes out below, AFTER these motion frames, on the same device. Mapped
                    # through the area while precision mode is on, unless a drag has the base mapping back.
                    warper.warp(STATE.exists() and not (follower.up and follower.home is not None),
                                "press" if new_bits else "touch")
                if warper is not None and (new_bits or gone_bits or new_touch or gone_touch):
                    # conf CHORD_<n> / TOUCH_CHORD_<n>: the daemon presses the chords itself - such a
                    # key is Disabled in kcminputrc. Ups before downs (a report can swap keys); every
                    # release mirrors its trigger, so a held chord stays held (Kando's turbo mode).
                    # The precision key (the mask) is exempt: its touch is the ghost and the drag.
                    mask_bit = lay["mask"] or 0
                    for bit in range(8):
                        key = 1 << bit
                        if gone_touch & key:
                            warper.chord_up(("touch", bit + 1))
                            touch_since.pop((fd, bit), None)
                        if gone_bits & key:
                            warper.chord_up(("press", bit + 1))
                        if new_bits & key:
                            touch_since.pop((fd, bit), None)     # pressed before the delay: no touch chord this contact
                            text = conf.get(f"CHORD_{bit + 1}", "")
                            if text and not key & mask_bit:
                                warper.chord_down(("press", bit + 1), text, f"key {bit + 1}")
                        if (new_touch & key and not press_bits & key and not key & mask_bit
                                and conf.get(f"TOUCH_CHORD_{bit + 1}", "")):
                            touch_since[(fd, bit)] = time.monotonic()   # engages after the delay, below
                if lay["mask"] is None:
                    continue                                              # key not known yet
                touched = bool(report[lay["touch"]] & lay["mask"])
                pressed = bool(press_bits & (lay["press_mask"] or lay["mask"]))
                if pressed:                     # the toggle takes over: ghost closes, a relocation is confirmed
                    pending.pop(fd, None)
                    if fd not in held and follower.up and follower.owner == fd \
                            and follower.home is not None and long_delay(conf) > 0:
                        long_press[fd] = time.monotonic() + long_delay(conf)   # kept down: precision mode off
                    held.add(fd)
                    follower.hide(fd, confirmed=True)
                elif touched:
                    long_press.pop(fd, None)    # released, the finger still rests
                    if fd not in held:
                        pending.setdefault(fd, time.monotonic())
                else:                           # finger off the key: ghost closes, a relocation snaps back
                    long_press.pop(fd, None)
                    held.discard(fd)
                    pending.pop(fd, None)
                    follower.hide(fd)
            now = time.monotonic()
            for (tfd, bit), since in list(touch_since.items()):
                if now - since < touch_delay(conf, bit + 1):
                    continue                                              # not rested long enough yet
                del touch_since[(tfd, bit)]
                if tfd not in fds or down.get(tfd, 0) & (1 << bit):
                    continue                                              # pressed meanwhile: this contact is spent
                text = conf.get(f"TOUCH_CHORD_{bit + 1}", "")
                if not text or warper is None:
                    continue
                if conf.get("WARP", "1") != "0":
                    # a fresh warp right before the chord: the pen may have moved during the delay,
                    # and a pie bound to the touch must open where the pen is NOW
                    warper.warp(STATE.exists() and not (follower.up and follower.home is not None),
                                "touch")
                warper.chord_down(("touch", bit + 1), text, f"key {bit + 1} touch")
            for fd, since in list(pending.items()):
                mask_n = (fds[fd][3]["mask"] or 0).bit_length() or None
                if since is not None and now - since >= hold_delay(conf, mask_n):
                    (follower.move if STATE.exists() else follower.show)(fd)
                    pending[fd] = None          # up (or refused); no timer until the next touch
            for fd, deadline in list(long_press.items()):
                if now >= deadline:             # the key stayed down after the move: leave precision mode
                    del long_press[fd]
                    log("long press: precision mode off")
                    if not toggle("toggle"):
                        log("long press: the toggle failed, precision mode stays on")
            if follower.up:
                follower.follow()
            if reload_pending and not follower.up:
                reload_pending = False          # a Wacom Center Apply, a delay change, or a hand poke
                # Every chord goes UP here, while the finger is still down. The poke comes from a
                # profile switch that rewrites kcminputrc a moment later, and KWin rebuilds its whole
                # rebind table and its virtual device on that write: a release sent after it never
                # reaches the focused window, so a held Shift stayed logically pressed (hardware,
                # 2026-09-14; the journal shows the daemon DID send that late release). Released
                # before the rewrite it lands. The contact is then spent - a finger that rests through
                # a reload gets its chord back by lifting and landing again, never by itself, so no
                # chord is ever pressed across the rewrite.
                if warper is not None and warper.held:
                    log("conf reload: the held chords go up first")
                    warper.release_all()
                touch_since.clear()             # a touch register that spans a reload is spent too
                conf = read_conf()              # a finger already resting on a key stays known
                for node in fds.values():
                    node[3] = layout_for(conf, node[1], node[2]) or node[3]
                log("conf reloaded")
            if now - last_scan >= RESCAN:
                last_scan = now
                if warper is not None:
                    warper.ensure()                 # a virtual mouse lost or never created: another try
                heal_poll()                         # a heal that failed gets its next try
                turn_poll()                         # Left-handed mode changed in System Settings: one rescan
                if wacom_nodes() != nodes:
                    return True                 # bus switch or tablet gone: rescan
                missed = [node for node in missed if not attach(node, quiet=True)]
    finally:
        for fd in fds:
            os.close(fd)
        if ctl is not None:
            os.close(ctl)
        follower.hide()
        if warper is not None:
            warper.release_all()
    return True


# ── chunk: Absence
class Absence:
    """How long the tablet has been gone while precision mode is ON. Past GRACE the mode PAUSES
    (tablet-precision.sh pause): the rectangle leaves the screen, and the area is kept for the conf's
    RECONNECT seconds, so the tablet coming back resumes it (heal). Nothing is stranded either way,
    because kcminputrc already names the base: this is about the rectangle left drawn on the screen."""

    def __init__(self):
        self.since = None                    # monotonic time the tablet was first seen gone with the mode on
        self.wall = None                     # the same moment in epoch seconds, for the pause record

    def reset(self):
        self.since = self.wall = None

    def check(self):
        if not STATE.exists():               # the mode is off: nothing to pause
            self.reset()
            return
        if self.since is None:
            self.since, self.wall = time.monotonic(), int(time.time())
            return
        if time.monotonic() - self.since >= GRACE:
            log(f"tablet gone {GRACE:.0f} s with precision mode on: pausing it")
            toggle("pause", str(self.wall))
            self.reset()


# ── chunk: cycle
def cycle(follower, warper, gone, rescan=None):
    """One pass of the daemon: watch the tablet while it is here; while it is not, time its absence."""
    if watch(read_conf(), follower, warper):
        gone.reset()                         # it was here until just now
    else:
        gone.check()
        time.sleep(RESCAN if rescan is None else rescan)   # tablet asleep or not yet connected: poll


# ── chunk: main
def main():
    follower = Follower()
    if "--simulate" in sys.argv:          # the ghost for 3 s, following the pen, then exit
        follower.show(None)
        if not follower.up:
            sys.exit("no ghost: tablet-precision.sh where failed (tablet asleep?)")
        end = time.monotonic() + 3
        while time.monotonic() < end:
            follower.follow()
            time.sleep(TICK)
        follower.hide()
        return
    warper = Warper()
    gone = Absence()
    while True:
        cycle(follower, warper, gone)


if __name__ == "__main__":
    main()
