#!/usr/bin/env python3
# tablet-pen-driver.py [--source EVENTNODE] [--force] [--check] - TRIAL, off unless conf PEN_DRIVER=1: grab the pen's evdev node, re-emit it through a uinput copy.
# Part of plasma-tablet-center (MIT). Structure and contracts: PROJECT_MAP.md; mechanisms: TECHNICAL.md.
import fcntl
import math
import os
import errno
import select
import signal
import socket
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

CONF = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "tabprec.conf"
RD = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "tabprec"
SOCK = RD / "pen-driver.sock"       # datagram control: "FX FY FW FH" (fractions of the screen) or "full"
LOCK = RD / "pen-driver.lock"       # held while this helper takes or holds the pen: only then does the toggle wait for SOCK
SYS = Path(os.environ.get("PEN_DRIVER_SYS", "/sys/class/input"))   # the rig points this at a fake tree
VENDOR = 0x056A                   # the pen maker's USB vendor id
EV_SYN, EV_KEY, EV_ABS, EV_MSC = 0, 1, 3, 4
ABS_X, ABS_Y = 0, 1
SYN_REPORT, SYN_DROPPED = 0, 3
EVENT = struct.Struct("<qqHHi")
PING = 0.5                          # seconds between loop turns while idle: the watchdog is pinged on each
TEST_VIRTUAL = os.environ.get("PEN_DRIVER_TEST_VIRTUAL") == "1"   # VM tests only: a uinput stand-in counts as the real pen
STOP = []                           # filled by SIGTERM
GONE = 2.0                          # seconds between looks for a pen that was unplugged (Bluetooth hangs up an idle link)
NOPEN = 5.0                         # seconds with no pen before SOCK goes: the toggle then maps through KWin, nothing is swallowed
LIVE = {"sk": None, "area": None, "lock": None, "wake": None, "beat": 0.0}   # the socket and area outlive a run(); the area dies with the socket


# ── chunk: ioc
def ioc(direction, kind, nr, size):
    """asm-generic ioctl number: dir<<30 | size<<16 | type<<8 | nr (1 = write, 2 = read)."""
    return (direction << 30) | (size << 16) | (ord(kind) << 8) | nr


# ── chunk: bits_of
def bits_of(raw):
    """The set bit numbers of a kernel bitmask (little-endian bytes)."""
    return [i * 8 + b for i, byte in enumerate(raw) for b in range(8) if byte >> b & 1]


# ── chunk: read_conf
def read_conf():
    values = {}
    try:
        for line in CONF.read_text(errors="replace").splitlines():
            m = re.fullmatch(r"[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*=(.*)", line.split("#", 1)[0])   # THE line shape (PROJECT_MAP conf contract)
            if m:
                val = m.group(2).strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                values[m.group(1)] = val
    except OSError:
        pass
    return values


# ── chunk: pen_nodes
def pen_nodes():
    """Event nodes of a real Wacom pen: vendor 056a, name ends in Pen, NOT a uinput
    device under /sys/devices/virtual/input (our own copy carries the same name and ids).
    A Bluetooth pen comes through BlueZ's uhid, under /sys/devices/virtual/misc, and counts."""
    found = []
    for ev in sorted(SYS.glob("event*")):
        try:
            dev = (ev / "device").resolve()
            if "/devices/virtual/input/" in str(dev) and not TEST_VIRTUAL:
                continue
            name = (dev / "name").read_text().strip()
            vendor = int((dev / "id" / "vendor").read_text(), 16)
        except (OSError, ValueError):
            continue
        if vendor == VENDOR and name.endswith("Pen"):
            found.append(Path("/dev/input") / ev.name)
    return found


# ── chunk: read_caps
def read_caps(fd):
    """Everything the copy must declare: (bus, vendor, product, version), name,
    {event type: [codes]}, {abs code: absinfo tuple}, [input props]."""
    ident = struct.unpack("<4H", fcntl.ioctl(fd, ioc(2, "E", 2, 8), bytes(8)))
    buf = bytearray(256)
    fcntl.ioctl(fd, ioc(2, "E", 6, 256), buf)
    name = bytes(buf).split(b"\0", 1)[0]
    types, absinfo = {}, {}
    for kind in (EV_KEY, EV_ABS, EV_MSC):
        size = 96 if kind == EV_KEY else 8
        types[kind] = bits_of(fcntl.ioctl(fd, ioc(2, "E", 0x20 + kind, size), bytes(size)))
    for code in types[EV_ABS]:
        absinfo[code] = struct.unpack("<6i", fcntl.ioctl(fd, ioc(2, "E", 0x40 + code, 24), bytes(24)))
    props = bits_of(fcntl.ioctl(fd, ioc(2, "E", 9, 8), bytes(8)))
    return ident, name, types, absinfo, props


# ── chunk: make_copy
def make_copy(ident, name, types, absinfo, props):
    """The uinput copy of the pen: same ids, name, bits and ranges (+ resolution),
    so KWin finds the same kcminputrc group. Returns (fd, sysname)."""
    fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    try:
        for kind, nr in ((EV_KEY, 101), (EV_ABS, 103), (EV_MSC, 104)):
            if types[kind]:
                fcntl.ioctl(fd, ioc(1, "U", 100, 4), kind)
                for code in types[kind]:
                    fcntl.ioctl(fd, ioc(1, "U", nr, 4), code)
        for prop in props:
            fcntl.ioctl(fd, ioc(1, "U", 110, 4), prop)
        for code, info in absinfo.items():
            fcntl.ioctl(fd, ioc(1, "U", 4, 28), struct.pack("<H2x6i", code, *info))
        fcntl.ioctl(fd, ioc(1, "U", 3, 92), struct.pack("<4H80sI", *ident[:4], name, 0))
        fcntl.ioctl(fd, ioc(0, "U", 1, 0))
        buf = bytearray(64)
        fcntl.ioctl(fd, ioc(2, "U", 44, 64), buf)
    except BaseException:
        os.close(fd)
        raise
    return fd, bytes(buf).split(b"\0", 1)[0].decode()


# ── chunk: parse_area
def parse_area(text):
    """'FX FY FW FH' or 'full' -> (fx, fy, fw, fh) or None for the whole screen;
    ValueError on junk, a non-finite number or a size not above 0 (a NaN would crash the copy's
    arithmetic at the next pen event). A rectangle is clamped inside 0..1 (it cannot leave the screen)."""
    text = text.strip()
    if text in ("full", ""):
        return None
    fx, fy, fw, fh = (float(p) for p in text.split())
    if not all(map(math.isfinite, (fx, fy, fw, fh))) or fw <= 0 or fh <= 0:
        raise ValueError(text)
    fw, fh = min(max(fw, 0.0), 1.0), min(max(fh, 0.0), 1.0)
    fx, fy = min(max(fx, 0.0), 1.0 - fw), min(max(fy, 0.0), 1.0 - fh)
    if fw == 1.0 and fh == 1.0:
        return None
    return fx, fy, fw, fh


# ── chunk: remap
def remap(value, lo, hi, start, size):
    """One axis of the precision arithmetic: the tablet's lo..hi lands on the
    screen fraction start..start+size of the copy's lo..hi range."""
    frac = (value - lo) / (hi - lo)
    return lo + round((start + frac * size) * (hi - lo))


# ── chunk: transform
def transform(data, area, absinfo):
    """Re-emit one read() of input_event structs, ABS_X/ABS_Y remapped into area
    (None = untouched, byte for byte)."""
    if area is None:
        return data
    out = bytearray()
    for sec, usec, etype, code, value in EVENT.iter_unpack(data):
        if etype == EV_ABS and code in (ABS_X, ABS_Y):
            lo, hi = absinfo[code][1:3]
            axis = 0 if code == ABS_X else 1
            value = remap(value, lo, hi, area[axis], area[2 + axis])
        out += EVENT.pack(sec, usec, etype, code, value)
    return bytes(out)


# ── chunk: notify
def notify(text):
    """sd_notify: READY=1 / WATCHDOG=1 to $NOTIFY_SOCKET (a no-op outside systemd)."""
    path = os.environ.get("NOTIFY_SOCKET")
    if not path:
        return
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sk:
        sk.sendto(text.encode(), "\0" + path[1:] if path[0] == "@" else path)




# ── chunk: beat
def beat():
    """WATCHDOG=1 at most every PING/2: a pen at 200 reports a second must not send 200 pings (worst gap 0.75 s)."""
    now = time.monotonic()
    if now - LIVE["beat"] >= PING / 2:
        LIVE["beat"] = now
        notify("WATCHDOG=1")


# ── chunk: syn_at
def syn_at(data, code, start=0):
    """Byte offset of the first EV_SYN `code` event in a read() at or after start, else -1."""
    tag = struct.pack("<HH", EV_SYN, code)
    for i in range(start, len(data), EVENT.size):
        if data[i + 16:i + 20] == tag:
            return i
    return -1


# ── chunk: splice
def splice(data, dropping, full):
    """A SYN_DROPPED (the kernel lost events) drops everything up to the next SYN_REPORT, then full() - the
    pen's whole state - stands in for it. Returns (bytes to forward, still dropping)."""
    if not dropping:
        i = syn_at(data, SYN_DROPPED)
        if i < 0:
            return data, False
        head, data = data[:i], data[i:]
    else:
        head = b""
    j = syn_at(data, SYN_REPORT)
    if j < 0:
        return head, True
    return head + full() + data[j + EVENT.size:], False


# ── chunk: state_frame
def state_frame(fd, types, absinfo, area, keys_only=False):
    """The pen's state read from its node, as events: every key (keys_only: the held ones, for the head of the
    first packet after a grab - a key frame alone makes a serial-0 tool), else every key, every axis (X/Y into
    area) and a SYN_REPORT."""
    down = set(bits_of(fcntl.ioctl(fd, ioc(2, "E", 0x18, 96), bytes(96))))   # EVIOCGKEY
    out = bytearray()
    for code in types[EV_KEY]:
        if code in down or not keys_only:
            out += EVENT.pack(0, 0, EV_KEY, code, int(code in down))
    if keys_only:
        return bytes(out)
    for code in types[EV_ABS]:
        value = struct.unpack("<6i", fcntl.ioctl(fd, ioc(2, "E", 0x40 + code, 24), bytes(24)))[0]   # EVIOCGABS
        if area is not None and code in (ABS_X, ABS_Y):
            axis = 0 if code == ABS_X else 1
            value = remap(value, *absinfo[code][1:3], area[axis], area[2 + axis])
        out += EVENT.pack(0, 0, EV_ABS, code, value)
    return bytes(out + EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))


# ── chunk: control_socket
def control_socket():
    """The datagram socket the toggle writes the mapping to (a stale file from a crashed helper goes first)."""
    RD.mkdir(parents=True, exist_ok=True)
    SOCK.unlink(missing_ok=True)
    sk = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_NONBLOCK)
    sk.bind(str(SOCK))
    return sk


# ── chunk: ctl_open
def ctl_open():
    """SOCK bound, once: it stays across run() calls, so a toggle during a reconnect reaches this helper."""
    if LIVE["sk"] is None:
        LIVE["sk"] = control_socket()
    return LIVE["sk"]


# ── chunk: ctl_close
def ctl_close():
    """SOCK gone and the area forgotten with it: from now on only the grabbed hook can tell a new grab the area."""
    if LIVE["sk"] is not None:
        LIVE["sk"].close()
        LIVE["sk"] = None
        SOCK.unlink(missing_ok=True)
    LIVE["area"] = None


# ── chunk: ctl_read
def ctl_read():
    """Every waiting datagram, the newest wins; junk keeps the old mapping."""
    while LIVE["sk"] is not None:
        try:
            text = LIVE["sk"].recv(256).decode(errors="replace")
        except (BlockingIOError, InterruptedError):
            return
        try:
            LIVE["area"] = parse_area(text)
        except ValueError:
            pass


# ── chunk: hold
def hold(on):
    """LOCK while this helper takes or holds the pen (the toggle waits for SOCK only then); False lets go.
    Returns False when it stays taken (only a toggle's look holds it, and only for a moment)."""
    if LIVE["lock"] is None:
        LIVE["lock"] = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    if not on:
        fcntl.flock(LIVE["lock"], fcntl.LOCK_UN)
        return True
    for _ in range(20):                                  # a toggle's look takes the lock shared for a moment
        try:
            fcntl.flock(LIVE["lock"], fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            time.sleep(0.05)
    return False


# ── chunk: wake_pipe
def wake_pipe():
    """A SIGTERM wakes every select at once (signal.set_wakeup_fd): the rescue line ends a wait now, not 2 s later."""
    r, w = os.pipe()
    for fd in (r, w):
        os.set_blocking(fd, False)
    signal.set_wakeup_fd(w)
    LIVE["wake"] = r


# ── chunk: idle
def idle(seconds):
    """Wait with no pen: the watchdog fed, the socket read, a SIGTERM ends it at once."""
    end = time.monotonic() + seconds
    while not STOP and time.monotonic() < end:
        beat()
        watch = [x for x in (LIVE["wake"], LIVE["sk"]) if x is not None]
        ready, _, _ = select.select(watch, [], [], min(PING, max(0.0, end - time.monotonic())))
        if LIVE["sk"] in ready:
            ctl_read()
        if LIVE["wake"] is not None and LIVE["wake"] in ready:
            os.read(LIVE["wake"], 64)


# ── chunk: grabbed_hook
def grabbed_hook():
    """After every grab, detached: `tablet-precision.sh grabbed` decides under its lock (never pre-check saved-area
    here: an ON in flight writes it after its KWin-path rectangle)."""
    try:
        subprocess.Popen([str(Path(__file__).resolve().parent / "tablet-precision.sh"), "grabbed"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, start_new_session=True)
    except OSError as err:
        print(f"grabbed hook not started: {err}", flush=True)


# ── chunk: run
def run(source):
    """Open + read the pen, make the copy, THEN grab (a failed copy never costs the
    pen); loop until SIGTERM (0) or the pen vanishes (1). Closing the fd (any exit,
    a crash too) ends the grab; an OSError leaves both fds closed."""
    fd = os.open(source, os.O_RDONLY | os.O_NONBLOCK)
    try:
        ident, name, types, absinfo, props = read_caps(fd)
        out, sysname = make_copy(ident, name, types, absinfo, props)
        try:
            fcntl.ioctl(fd, ioc(1, "E", 0x90, 4), 1)         # EVIOCGRAB
            sk = ctl_open()
            print(f"grabbed {source} {name.decode()} {ident[1]:04x}:{ident[2]:04x} -> copy {sysname}", flush=True)
            notify("READY=1")
            grabbed_hook()
            head, dropping = state_frame(fd, types, absinfo, None, keys_only=True), False   # keys held at the grab
            while not STOP:
                beat()
                ready, _, _ = select.select([x for x in (fd, sk, LIVE["wake"]) if x is not None], [], [], PING)
                if sk in ready:
                    ctl_read()
                if LIVE["wake"] is not None and LIVE["wake"] in ready:
                    os.read(LIVE["wake"], 64)
                if fd in ready:
                    try:
                        data = os.read(fd, EVENT.size * 256)
                    except BlockingIOError:
                        continue
                    except OSError as err:
                        if err.errno == errno.ENODEV:         # unplugged or hung up: the copy goes too
                            print("pen gone", flush=True)
                            return 1
                        raise
                    data, dropping = splice(data, dropping, lambda: state_frame(fd, types, absinfo, None))
                    if head and data:
                        data, head = head + data, b""
                    if data:
                        os.write(out, transform(data, LIVE["area"], absinfo))
            return 0
        finally:
            os.close(out)
    finally:
        os.close(fd)                                         # ends the grab


# ── chunk: main
def main():
    args = sys.argv[1:]
    if "--check" in args:                                  # the unit's ExecCondition: one reader of the conf, THE line shape, for the switch
        return 0 if read_conf().get("PEN_DRIVER") == "1" else 1
    if read_conf().get("PEN_DRIVER") != "1" and "--force" not in args:
        print("PEN_DRIVER is not 1 in tabprec.conf: the trial stays off", file=sys.stderr)
        return 0
    fixed = args[args.index("--source") + 1] if "--source" in args else None
    signal.signal(signal.SIGTERM, lambda *_: STOP.append(1))
    wake_pipe()
    RD.mkdir(parents=True, exist_ok=True)
    ctl_open()                                             # bound once for the helper's life (until NOPEN s without a pen)
    notify("READY=1")                                      # alive; a sleeping tablet is not a failure
    lost = time.monotonic()
    try:
        while not STOP:
            nodes = [Path(fixed)] if fixed else pen_nodes()
            if nodes and nodes[0].exists() and hold(True):
                try:
                    if run(str(nodes[0])) == 0:
                        break
                    lost = time.monotonic()                # the pen went: its socket and area wait NOPEN s for it
                except OSError as err:                     # a busy node, no uinput, a pen gone mid-setup: wait and look again
                    print(f"pen not taken: {err}", flush=True)
                    ctl_close()                            # a pen it cannot hold is free: the toggle maps it through KWin at once
                hold(False)
            if LIVE["sk"] is not None and time.monotonic() - lost >= NOPEN:
                ctl_close()
            idle(GONE)
    finally:
        ctl_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
