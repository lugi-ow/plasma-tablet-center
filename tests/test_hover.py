#!/usr/bin/env python3
# Offline harness for tablet-hover.py: a named pipe plays the pad's hidraw
# node (Bluetooth layout: report 0x80, touch byte 283, press byte 282), the
# pen is faked, the toggle and both overlays are fakes in a scratch DIR, the
# live overlay's pipe is read by a thread. No GUI, no real devices.
# Usage: test_hover.py <scratch dir> <path to tablet-hover.py>
import importlib.util
import os
import select
import shutil
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRATCH = Path(sys.argv[1]) / "hover-test"
SRC = Path(sys.argv[2])
shutil.rmtree(SCRATCH, ignore_errors=True)
RD, CONFD, FAKE = SCRATCH / "rd", SCRATCH / "conf", SCRATCH / "fake"
for d in (RD, CONFD, FAKE):           # NOT RD/tabprec: the daemon must make its own (check A0)
    d.mkdir(parents=True)
os.environ["XDG_RUNTIME_DIR"] = str(RD)
os.environ["XDG_CONFIG_HOME"] = str(CONFD)
CONF = CONFD / "tabprec.conf"
CONF.write_text("SCALE=0.29\nDIM=0.10\nHOVER_MASK=0x80\nHOLD=0.6\nLONG=1.0\n")   # the rig's timings assume these, not the defaults

spec = importlib.util.spec_from_file_location("hover", SRC)
hover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hover)
assert hover.RD == RD / "tabprec", hover.RD

# fakes the daemon runs from DIR: the toggle's "where" and the ghost overlay
GHOST_LOG = SCRATCH / "ghost.log"
os.environ["FAKE_LOG"] = str(GHOST_LOG)
(FAKE / "tablet-precision.sh").write_text(
    "#!/bin/bash\ncase \"$1\" in where) echo '909 475 742 490 0.10 2560 1440';; "
    "*) echo \"CALL $1\" >> \"$FAKE_LOG\";; esac\n")
shutil.copy(HERE / "fake" / "tablet-overlay.py", FAKE / "tablet-overlay.py")
shutil.copy(SRC.parent / "tablet-pointer-warp.py", FAKE / "tablet-pointer-warp.py")   # real Warper loads it (K)
for f in FAKE.iterdir():
    f.chmod(0o755)
hover.DIR = FAKE

# A0, measured here because it must happen before anything else makes the runtime dir:
# at a fresh login nothing has run the toggle, so the daemon has to make $XDG_RUNTIME_DIR/tabprec
# itself or its control pipe never exists and every conf reload is silently dead for the session.
# watch() with no nodes returns as soon as it has made the pipe, so it is a safe probe.
hover.tablet_nodes = lambda: []
hover.watch(hover.read_conf(), None, None)
FRESH_LOGIN_PIPE = hover.CTL.is_fifo()
hover.RD.mkdir(parents=True, exist_ok=True)   # the second probe needs the dir whatever the first one found
if hover.CTL.exists():
    hover.CTL.unlink()
hover.CTL.write_text("what a hand `echo reload > hover.ctl` leaves behind\n")
hover.watch(hover.read_conf(), None, None)
STALE_FILE_REPLACED = hover.CTL.is_fifo()   # a plain file is ALWAYS ready: select would spin on it forever
if not STALE_FILE_REPLACED:
    hover.CTL.unlink()                        # let the rest of the rig run, so both probes report as checks
    os.mkfifo(hover.CTL)

# A poke that races ahead of watch() opening the pipe must not be lost: watch() re-reads the conf
# a second time, once the pipe is open, rather than trusting the value cycle() read before either
# the node set or the pipe existed.
_READ_CALLS = []
_ORIG_READ_CONF = hover.read_conf
hover.read_conf = lambda: (_READ_CALLS.append(1), _ORIG_READ_CONF())[1]
hover.watch(hover.read_conf(), None, None)    # mirrors cycle()'s own call shape
hover.read_conf = _ORIG_READ_CONF
WATCH_REREADS_CONF = len(_READ_CALLS) >= 2

PEN = [(0.5, 0.5)]
hover.pen_open = lambda: os.open("/dev/null", os.O_RDONLY)
hover.pen_norm = lambda fd: PEN[0]
hover.pen_node = lambda: None             # turn_poll never asks the live KWin (section L fakes it)
WARPS = []                                # (pen position, mapped) per warp the daemon asked for
EVENTS = []                               # warps and chords in daemon order: the warp must precede the chord


class FakeWarper:                         # never the real one: it would move the desktop's mouse
    def __init__(self):
        self.held = {}

    def ensure(self):
        pass

    def warp(self, mapped, why="press"):
        WARPS.append((PEN[0], mapped))
        EVENTS.append(("warp", PEN[0]))

    def chord_down(self, tag, text, label):   # records what the daemon asked; validation is the real Warper's
        if tag not in self.held:
            self.held[tag] = text
            EVENTS.append(("down", tag, text))

    def chord_up(self, tag):
        if self.held.pop(tag, None) is not None:
            EVENTS.append(("up", tag))

    def release_all(self):
        for tag in list(self.held):
            self.chord_up(tag)


PAD = SCRATCH / "pad.fifo"
os.mkfifo(PAD)
NODES = [(str(PAD), "bt", "0360")]        # section X empties it (the tablet goes away), then fills it again
hover.tablet_nodes = lambda: list(NODES)

# the live overlay's pipe, read like the real overlay does (RDWR, no EOF)
FIFO = hover.FIFO
os.mkfifo(FIFO)
ffd = os.open(FIFO, os.O_RDWR | os.O_NONBLOCK)
lines = []
reading = True


def reader():
    buf = b""
    while reading:
        ready, _, _ = select.select([ffd], [], [], 0.05)
        if not ready:
            continue
        try:
            buf += os.read(ffd, 4096)
        except BlockingIOError:
            continue
        *done, buf = buf.split(b"\n")
        lines.extend(l.decode().strip() for l in done)


threading.Thread(target=reader, daemon=True).start()

writer = os.open(PAD, os.O_RDWR)          # a writer must exist before the daemon's blocking open
follower = hover.Follower()


def daemon():
    gone = hover.Absence()
    while True:
        hover.cycle(follower, FakeWarper(), gone, rescan=0.2)   # the daemon's own loop body, so the rig runs what main runs


threading.Thread(target=daemon, daemon=True).start()
time.sleep(0.3)


def report(touch=0, press=0):
    r = bytearray(300)
    r[0], r[283], r[282] = 0x80, touch, press
    os.write(writer, bytes(r))
    time.sleep(0.05)                      # hidraw never merges reports; a pipe would


def poke():
    """A conf edit reaches the daemon through the control pipe, not a poll."""
    for _ in range(100):
        if hover.CTL.exists():
            break
        time.sleep(0.02)
    fd = os.open(hover.CTL, os.O_WRONLY | os.O_NONBLOCK)
    os.write(fd, b"reload\n")
    os.close(fd)
    time.sleep(0.15)                      # the select loop wakes at once; margin for the reload


fails = 0


def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    fails += 0 if cond else 1


STATE, AREA, MARK = hover.STATE, hover.AREA, hover.MARK
HOME = "909 475 742 490"
ghost = lambda: GHOST_LOG.read_text() if GHOST_LOG.exists() else ""

# --- A0: the daemon makes its own runtime dir and control pipe (probed before the rig made either) ---
check("A0 fresh login: the daemon makes its own runtime dir and control pipe", FRESH_LOGIN_PIPE)
check("A0 a plain file at hover.ctl is replaced by a pipe", STALE_FILE_REPLACED)
check("A0 a conf change poked before watch opens the pipe is applied", WATCH_REREADS_CONF)

# --- S: the node set opened: the daemon starts the toggle's heal, in the background ---
check("S the pad node opened: heal started once", ghost().count("CALL heal") == 1)

# --- A: precision OFF: the ghost, in the waiting look of the mode itself ---
report(touch=0x80)
time.sleep(0.25)
check("A ghost spawned 0.25 s after the touch, full dim, --waiting",
      "SPAWN 909 475 742 490 0.10 --waiting --follow\n" in ghost())
ghost_pid = follower.proc.pid if follower.proc else None
PEN[0] = (0.25, 0.25)
time.sleep(0.15)
check("A ghost follows the pen: 455 238 742 490", "455 238 742 490" in ghost())
report(touch=0)
time.sleep(0.25)
check("A lift ends the ghost process", ghost_pid and not os.path.exists(f"/proc/{ghost_pid}") and not follower.up)
check("A no marker, no pipe lines", not MARK.exists() and not lines)
check("A the finger landing warped once, at the touch, plain (precision off)", WARPS == [((0.5, 0.5), False)])

# --- W: a finger landing on ANY key warps the mouse onto the pen, the press again ---
PEN[0] = (0.25, 0.25)
report(touch=0x03)                         # keys 1+2 together, away from the precision key
time.sleep(0.05)
check("W finger lands on keys 1+2: one warp, plain", WARPS[1:] == [((0.25, 0.25), False)])
PEN[0] = (0.3, 0.3)
report(touch=0x03, press=0x03)
report(touch=0x03, press=0)
report(touch=0)
time.sleep(0.1)
check("W the press warps again at the pen's new spot; the release and lift do not", WARPS[2:] == [((0.3, 0.3), False)])
check("W keys 1+2: no ghost, no marker, nothing else", ghost().count("SPAWN") == 1 and not MARK.exists() and not lines)
report(press=0x03)                         # a press the touch sense never saw: still one warp
report(press=0)
time.sleep(0.1)
check("W a press without a touch warps too", len(WARPS) == 4)

# --- K: CHORD_<n> from the conf: the daemon presses the chord AFTER the warp ---
wspec = importlib.util.spec_from_file_location("warpmod", FAKE / "tablet-pointer-warp.py")
warpmod = importlib.util.module_from_spec(wspec)
wspec.loader.exec_module(warpmod)
check("K parse: Meta+Shift+F8 -> Shift, Meta, F8 (KWin's modifier order)",
      warpmod.parse_chord("Meta+Shift+F8") == [42, 125, 66])
check("K parse: case-blind, Super = Meta, the F13 block", warpmod.parse_chord("super+f13") == [125, 183])
check("K parse: a letter or an empty chord is refused",
      warpmod.parse_chord("Q+F1") is None and warpmod.parse_chord("") is None)
check("K parse: modifier-only refused for a press chord, two F-keys too",
      warpmod.parse_chord("Meta") is None and warpmod.parse_chord("Ctrl+Shift") is None
      and warpmod.parse_chord("F1+F2") is None)
check("K parse: touch chords allow bare modifiers, in KWin's order",
      warpmod.parse_chord("Ctrl", False) == [29] and warpmod.parse_chord("Ctrl+Shift", False) == [42, 29])
check("K parse: Meta alone refused even for a touch (the launcher tap)",
      warpmod.parse_chord("Meta", False) is None and warpmod.parse_chord("", False) is None)
rw = hover.Warper()                        # the real one: mod loads from FAKE, fd stays None - no device touched
rw.chord_down(("press", 2), "Meta+Shift+F8", "key 2")
rw.chord_down(("press", 3), "Q+F1", "key 3")
check("K real Warper without a device: nothing held, no crash", rw.held == {})
CONF.write_text("SCALE=0.29\nDIM=0.10\nHOVER_MASK=0x80\nHOLD=0.6\nLONG=1.0\nCHORD_2=Meta+Shift+F8\nCHORD_3=Q+F1\n")
poke()
n_ev = len(EVENTS)
PEN[0] = (0.4, 0.6)
report(press=0x06)                         # keys 2+3 down in one report, no touch first
time.sleep(0.05)
check("K press: one warp first, then the chords in key order",
      EVENTS[n_ev:] == [("warp", (0.4, 0.6)), ("down", ("press", 2), "Meta+Shift+F8"),
                        ("down", ("press", 3), "Q+F1")])
report(press=0x04)                         # key 2 up, key 3 still down
time.sleep(0.05)
check("K key 2's release lifts its chord only", EVENTS[n_ev + 3:] == [("up", ("press", 2))])
report(press=0)
time.sleep(0.05)
check("K key 3's release lifts the rest", EVENTS[n_ev + 4:] == [("up", ("press", 3))])
CONF.write_text("SCALE=0.29\nDIM=0.10\nHOVER_MASK=0x80\nHOLD=0.6\nLONG=1.0\nWARP=0\nCHORD_2=Meta+Shift+F8\n")
poke()
n_ev = len(EVENTS)
report(press=0x02)
report(press=0)
time.sleep(0.05)
check("K WARP=0: no warp, the chord still fires",
      EVENTS[n_ev:] == [("down", ("press", 2), "Meta+Shift+F8"), ("up", ("press", 2))])

# --- T: TOUCH_CHORD_<n>: held after the delay, released at the lift ---
CONF.write_text("SCALE=0.29\nDIM=0.10\nHOVER_MASK=0x80\nHOLD=0.3\nLONG=1.0\n"
                "TOUCH_CHORD_2=Ctrl\nTOUCH_CHORD_3=Shift\nCHORD_3=Meta+Shift+F5\n")
poke()
n_ev = len(EVENTS)
PEN[0] = (0.6, 0.4)
report(touch=0x02)                         # finger lands on key 2: warp now, the chord only after HOLD
time.sleep(0.15)
check("T no touch chord before the delay (0.15 s into 0.3)",
      EVENTS[n_ev:] == [("warp", (0.6, 0.4))])
PEN[0] = (0.7, 0.5)                        # the pen moves during the delay
time.sleep(0.3)
check("T engaged after the delay: a FRESH warp, then the chord held",
      EVENTS[n_ev + 1:] == [("warp", (0.7, 0.5)), ("down", ("touch", 2), "Ctrl")])
report(touch=0)
time.sleep(0.05)
check("T lift releases the touch chord", EVENTS[n_ev + 3:] == [("up", ("touch", 2))])
n_ev = len(EVENTS)
report(touch=0x02)                         # a press before the delay wins: no touch chord this contact
report(touch=0x02, press=0x02)
report(touch=0x02, press=0)
report(touch=0)
time.sleep(0.5)                            # well past the delay: nothing may engage late either
check("T press before the delay: warps only, no chord this contact",
      [e for e in EVENTS[n_ev:] if e[0] != "warp"] == [])
n_ev = len(EVENTS)
report(touch=0x04)                         # key 3: engage, then press on top of the held chord
time.sleep(0.45)
report(touch=0x04, press=0x04)
time.sleep(0.05)
check("T press after the engage: the touch chord stays held, the press chord joins",
      EVENTS[n_ev + 1:] == [("warp", (0.7, 0.5)), ("down", ("touch", 3), "Shift"),
                            ("warp", (0.7, 0.5)), ("down", ("press", 3), "Meta+Shift+F5")])
report(touch=0x04, press=0)
report(touch=0)
time.sleep(0.05)
check("T releases mirror their triggers, the press first",
      EVENTS[n_ev + 5:] == [("up", ("press", 3)), ("up", ("touch", 3))])
n_ev = len(EVENTS)
CONF.write_text("SCALE=0.29\nDIM=0.10\nHOVER_MASK=0x80\nHOLD=1.0\nLONG=1.0\n"
                "TOUCH_CHORD_2=Ctrl\nTOUCH_HOLD_2=0.1\n")
poke()
report(touch=0x02)                         # key 2's own register (0.1) beats the HOLD fallback (1.0)
time.sleep(0.35)
check("T per-key register: TOUCH_HOLD_2 overrides HOLD",
      ("down", ("touch", 2), "Ctrl") in EVENTS[n_ev:])
report(touch=0)
time.sleep(0.05)
CONF.write_text("SCALE=0.29\nDIM=0.10\nHOVER_MASK=0x80\nHOLD=0.6\nLONG=1.0\n")
poke()

# --- B: precision ON: hold -> relocation, lift -> snap back ---
STATE.write_text("0 0 1 1\n")
AREA.write_text("909 475 742 490 0.10 2560 1440 0.355078 0.329861 0.289844 0.340278\n")
PEN[0] = (0.5, 0.5)
report(touch=0x80)
time.sleep(0.35)
check("B nothing 0.35 s into the touch (hold is 0.6)", not MARK.exists() and not lines
      and ghost().count("CALL") == ghost().count("CALL heal"))
time.sleep(0.6)
check("B marker fresh after the hold", MARK.exists() and time.time() - MARK.stat().st_mtime < 0.5)
check("B waiting border first, then the overlay at the aim: 909 475 742 490", lines == ["waiting", HOME])
check("B suspend called once (cursor freed)", ghost().count("CALL suspend") == 1 and "CALL resume" not in ghost())
PEN[0] = (0.1, 0.9)
time.sleep(0.15)
check("B follows the pen: 182 855 742 490", lines[-1] == "182 855 742 490")
n = len(lines)
report(touch=0)
time.sleep(0.25)
check("B lift: home line 909 475 742 490, then solid", len(lines) == n + 2 and lines[-2:] == [HOME, "solid"])
check("B lift: marker removed", not MARK.exists())
check("B lift: resume called (old area mapped again)", ghost().count("CALL resume") == 1)
check("B no ghost while ON", ghost().count("SPAWN") == 1)

# --- C: hold -> press confirms: border solid where it is, marker kept, no snap back, the resting finger after it is ignored ---
PEN[0] = (0.5, 0.5)
report(touch=0x80)
time.sleep(0.9)
check("C relocating again: waiting, then the aim", MARK.exists() and lines[-2:] == ["waiting", HOME])
PEN[0] = (0.9, 0.1)
time.sleep(0.15)
check("C aimed: 1636 95 742 490", lines[-1] == "1636 95 742 490")
n = len(lines)
t_press = time.time()
report(touch=0x80, press=0x80)
time.sleep(0.25)
check("C press: marker kept for the toggle", MARK.exists())
check("C press: border solid, no snap back", lines[n:] == ["solid"])
check("C press mid-drag warps to the plain position (base mapping while aiming)", WARPS[-1] == ((0.9, 0.1), False))
n = len(lines)
report(touch=0x80, press=0)               # key released, finger still resting
PEN[0] = (0.2, 0.2)
time.sleep(1.4)
check("C resting 1.4 s after the press: no new relocation, marker not refreshed",
      len(lines) == n and MARK.exists() and MARK.stat().st_mtime <= t_press + 0.05)
report(touch=0)
time.sleep(0.25)
check("C lift after a press: no snap back, marker still there", len(lines) == n and MARK.exists())
check("C confirm: no resume (the toggle maps the new area), no long press; suspend 2, resume 1",
      ghost().count("CALL suspend") == 2 and ghost().count("CALL resume") == 1
      and "CALL toggle" not in ghost())
MARK.unlink()                              # the toggle would have consumed it

# --- D: a quick press while ON (toggle off) never relocates ---
n = len(lines)
report(touch=0x80)
report(touch=0x80, press=0x80)
time.sleep(0.1)
report(touch=0x80)
report(touch=0)
time.sleep(0.9)
check("D quick press: no marker, no pipe lines, no suspend", not MARK.exists() and len(lines) == n
      and ghost().count("CALL suspend") == 2)
check("D quick press while ON warps through the area (mapped)", WARPS[-1] == ((0.2, 0.2), True))

# --- E: HOLD from the conf, picked up through the control pipe ---
CONF.write_text("SCALE=0.29\nDIM=0.10\nHOVER_MASK=0x80\nHOLD=1.2\nWARP=0\n")
poke()
n_warps = len(WARPS)
report(press=0x03)
report(press=0)
time.sleep(0.1)
check("E WARP=0 reloaded: a press warps nothing", len(WARPS) == n_warps)
n = len(lines)
report(touch=0x80)
time.sleep(0.9)
check("E HOLD=1.2 reloaded: nothing 0.9 s into the touch", not MARK.exists() and len(lines) == n)
time.sleep(0.5)
check("E HOLD=1.2 reloaded: relocating 1.4 s into the touch", MARK.exists() and len(lines) == n + 2)
report(touch=0)
time.sleep(0.25)
check("E lift snaps home", lines[-2:] == [HOME, "solid"] and not MARK.exists())

# --- G: the conf reloaded while the finger already rests: the hold still ends in a drag ---
CONF.write_text("SCALE=0.29\nDIM=0.10\nHOVER_MASK=0x80\nHOLD=2.5\n")
poke()
n = len(lines)
report(touch=0x80)
time.sleep(0.3)
CONF.write_text("SCALE=0.31\nDIM=0.10\nHOVER_MASK=0x80\nHOLD=2.5\n")   # a poke mid-rest reloads before the hold ends
poke()
time.sleep(2.6)
check("G conf reloaded mid-rest: the drag still starts", MARK.exists() and len(lines) == n + 2 and lines[-2] == "waiting")
report(touch=0)
time.sleep(0.25)
check("G lift snaps home", lines[-2:] == [HOME, "solid"] and not MARK.exists())
CONF.write_text("SCALE=0.29\nDIM=0.10\nHOVER_MASK=0x80\nHOLD=0.6\nLONG=1.0\n")
poke()

# --- H: no floor on HOLD, and the LONG PRESS out of precision mode ---
calls = lambda: ghost().count("CALL toggle")
CONF.write_text("SCALE=0.29\nDIM=0.10\nHOVER_MASK=0x80\nHOLD=0\nLONG=1.0\n")
poke()
n = len(lines)
report(touch=0x80)
time.sleep(0.1)
check("H HOLD=0: the drag starts at the first touch report", MARK.exists() and len(lines) == n + 2)
report(touch=0)
time.sleep(0.25)
CONF.write_text("SCALE=0.29\nDIM=0.10\nHOVER_MASK=0x80\nHOLD=0.6\nLONG=1.0\n")
poke()
report(touch=0x80)
time.sleep(0.9)
check("H drag on, no toggle call so far", MARK.exists() and calls() == 0)
report(touch=0x80, press=0x80)            # the press lands the area (KWin's toggle, not modelled) and stays down
time.sleep(0.5)
check("H pressed 0.5 s: no exit yet", calls() == 0)
time.sleep(0.7)
check("H pressed 1.2 s: the daemon toggles precision mode off, once", calls() == 1)
report(touch=0x80, press=0)               # released, finger resting
time.sleep(1.3)
check("H after the release: no further toggle", calls() == 1)
report(touch=0)
time.sleep(0.25)
MARK.unlink(missing_ok=True)
report(touch=0x80)                        # a short press after a drag: a move, no exit
time.sleep(0.9)
report(touch=0x80, press=0x80)
time.sleep(0.3)
report(touch=0x80, press=0)
time.sleep(1.2)
check("H short press after a drag: no exit", calls() == 1)
report(touch=0)
time.sleep(0.25)
MARK.unlink(missing_ok=True)
report(touch=0x80)                        # a press before any drag (the toggle itself switches off): never armed
report(touch=0x80, press=0x80)
time.sleep(1.4)
check("H press before a drag, held 1.4 s: not armed", calls() == 1 and not MARK.exists())
report(touch=0)
time.sleep(0.25)
CONF.write_text("SCALE=0.29\nDIM=0.10\nHOVER_MASK=0x80\nHOLD=0.6\nLONG=0\n")
poke()
report(touch=0x80)
time.sleep(0.9)
report(touch=0x80, press=0x80)
time.sleep(1.4)
check("H LONG=0: a press held 1.4 s after a drag does not exit", calls() == 1)
report(touch=0)
time.sleep(0.25)
MARK.unlink(missing_ok=True)
CONF.write_text("SCALE=0.29\nDIM=0.10\nHOVER_MASK=0x80\nHOLD=0.6\nLONG=1.0\n")
poke()

# --- M: the precision key's touch and press belong to the mode, never to a chord ---
CONF.write_text("SCALE=0.29\nDIM=0.10\nHOVER_MASK=0x80\nHOLD=0.2\nLONG=1.0\n"
                "TOUCH_CHORD_8=Alt\nCHORD_8=Meta+Shift+F6\n")
poke()
n_ev = len(EVENTS)
report(touch=0x80)                         # resting on the precision key: the drag's, not a chord's
time.sleep(0.5)
report(touch=0x80, press=0x80)             # pressing it: the toggle's, not a chord's
time.sleep(0.05)
report(touch=0)
time.sleep(0.25)
check("M no chord ever for the mask key, warps only",
      [e for e in EVENTS[n_ev:] if e[0] != "warp"] == [])
MARK.unlink(missing_ok=True)               # the confirmed press left it for the (absent) toggle
CONF.write_text("SCALE=0.29\nDIM=0.10\nHOVER_MASK=0x80\nHOLD=0.6\nLONG=1.0\n")
poke()

# --- F: no live overlay reading the pipe -> the hold does nothing ---
reading = False
time.sleep(0.1)
os.close(ffd)
report(touch=0x80)
time.sleep(0.8)
check("F no overlay: no marker", not MARK.exists())
report(touch=0)
time.sleep(0.2)

# --- P: the tablet gone with precision mode on pauses the mode past GRACE. A second copy of the module,
#        so the daemon thread this rig is running is not touched ---
spec_p = importlib.util.spec_from_file_location("hover_p", SRC)
hp = importlib.util.module_from_spec(spec_p)
spec_p.loader.exec_module(hp)
hp.GRACE = 0.3
calls = []
hp.toggle = lambda mode, *extra: calls.append((mode,) + extra) or True
STATE.write_text("0 0 1 1" + chr(10))
g = hp.Absence()
g.check(); time.sleep(0.1); g.check()
check("P gone for less than GRACE: no pause", calls == [])
time.sleep(0.3); g.check()
check("P gone past GRACE with the mode on: paused, with the moment it went",
      len(calls) == 1 and calls[0][0] == "pause" and calls[0][1].isdigit())
calls.clear(); STATE.unlink()
g.check(); time.sleep(0.4); g.check()
check("P gone past GRACE with the mode off: nothing to pause", calls == [])
STATE.write_text("0 0 1 1" + chr(10))
hp.watch = lambda *a: False
hp.cycle(None, None, g, rescan=0); time.sleep(0.4); hp.cycle(None, None, g, rescan=0)
check("P the daemon loop times the absence and pauses", len(calls) == 1 and calls[0][0] == "pause")
calls.clear(); g.check()
hp.watch = lambda *a: True
hp.cycle(None, None, g, rescan=0)
check("P the tablet back resets the timer", g.since is None and calls == [])
STATE.unlink()

# --- U: a node that refuses its first open (a USB plug-in: the node exists before udev grants access)
#        is opened by a later rescan, while the node that did open keeps working. A third copy (P stubbed watch) ---
hn = importlib.util.module_from_spec(spec_p)
spec_p.loader.exec_module(hn)
hn.RESCAN = 0.2
hn.CTL = SCRATCH / "hover_n.ctl"
hn.heal = lambda: None
hn.pen_node = lambda: None
said = []
hn.log = said.append
node_a, node_b = SCRATCH / "n_a.fifo", SCRATCH / "n_b.fifo"
os.mkfifo(node_a); os.mkfifo(node_b)
wa, wb = os.open(node_a, os.O_RDWR), os.open(node_b, os.O_RDWR)
os.chmod(node_b, 0)
n_nodes = [(str(node_a), "usb", "0357"), (str(node_b), "usb", "0357")]
hn.tablet_nodes = lambda: list(n_nodes)
threading.Thread(target=lambda: hn.watch(hn.read_conf(), type("F", (), {"up": False, "hide": lambda *a, **k: None})()),
                 daemon=True).start()
time.sleep(0.5)
check("U a refused node: said once, the other node opened",
      sum("Permission denied" in s for s in said) == 1 and sum("usb report" in s for s in said) == 1)
os.chmod(node_b, 0o600)
time.sleep(0.5)
check("U access granted later: the rescan opens the node", sum(f"{node_b}: usb report" in s for s in said) == 1)
os.close(wa)                               # the node's reads end while its name stays (the driver replaced it)
time.sleep(0.05)
wa = os.open(node_a, os.O_RDWR)            # a writer again: the rescan's open would block on a pipe without one
time.sleep(0.5)
check("U a node whose reads end under the same name is opened again",
      sum(f"{node_a}: usb report" in s for s in said) == 2)
n_nodes[:] = []                            # the watch ends at its next rescan
time.sleep(0.4)
os.close(wa); os.close(wb)

# --- Y: heal runs one at a time, and a run that fails is tried again (REVIEW HLA-02, HLA-03): a heal
#        that lost its wait for KWin left the pen on the rectangle until the next bus change. The fake
#        heal takes 0.2 s and fails until its run count reaches HEAL_OK_AT. A fourth copy ---
hy = importlib.util.module_from_spec(spec_p)
spec_p.loader.exec_module(hy)
HEALD = SCRATCH / "heal"
HEALD.mkdir()
HEAL_LOG = HEALD / "runs"
(HEALD / "tablet-precision.sh").write_text(
    '#!/bin/bash\necho run >> "$HEAL_LOG"\nsleep 0.2\n[ "$(wc -l < "$HEAL_LOG")" -ge "$HEAL_OK_AT" ]\n')
(HEALD / "tablet-precision.sh").chmod(0o755)
os.environ["HEAL_LOG"], os.environ["HEAL_OK_AT"] = str(HEAL_LOG), "4"
hy.DIR = HEALD
hy.log = lambda text: None
runs = lambda: len(HEAL_LOG.read_text().splitlines()) if HEAL_LOG.exists() else 0


def rescans(seconds):
    """What the watch loop does at every rescan, for this long."""
    end = time.time() + seconds
    while time.time() < end:
        getattr(hy, "heal_poll", lambda: None)()
        time.sleep(0.1)


hy.heal(); hy.heal(); hy.heal()            # the node set changed three times while the first run is going
time.sleep(0.12)
check("Y three requests while a heal runs: one run", runs() == 1)
rescans(2.0)
check("Y a failed heal is tried again until one succeeds: 4 runs, then quiet", runs() == 4)
HEAL_LOG.unlink(missing_ok=True)
os.environ["HEAL_OK_AT"] = "99"
hy.heal()
rescans(2.5)
check("Y a heal that always fails stops after HEAL_TRIES runs", runs() == getattr(hy, "HEAL_TRIES", 0))

# --- X: the tablet goes away while a touch chord is held and the ghost is up (a lost Bluetooth link).
#        Nothing may stay pressed or drawn: a latched Ctrl turns every click into a Ctrl+click. When the
#        tablet comes back, the node set opens again and starts a new heal ---
CONF.write_text("SCALE=0.29\nDIM=0.10\nHOVER_MASK=0x80\nHOLD=0.6\nLONG=1.0\n"
                "TOUCH_CHORD_2=Ctrl\nTOUCH_HOLD_2=0.1\n")
poke()
heals = ghost().count("CALL heal")
n_ev = len(EVENTS)
report(touch=0x82)                         # one finger on the precision key (the ghost), one on key 2 (Ctrl)
time.sleep(0.35)
ghost_pid = follower.proc.pid if follower.proc else None
check("X before: the touch chord is held and the ghost is up",
      ("down", ("touch", 2), "Ctrl") in EVENTS[n_ev:] and follower.up and ghost_pid is not None)
NODES[:] = []                              # the node leaves the list, and its reads end
os.close(writer)
time.sleep(0.4)
check("X gone: the touch chord is released", EVENTS[n_ev:].count(("up", ("touch", 2))) == 1)
check("X gone: the ghost closes",
      not follower.up and ghost_pid is not None and not os.path.exists(f"/proc/{ghost_pid}"))
writer = os.open(PAD, os.O_RDWR)           # the tablet is back
NODES[:] = [(str(PAD), "bt", "0360")]
time.sleep(0.6)
check("X back: the node set opens again and starts a new heal", ghost().count("CALL heal") == heals + 1)

# --- R: a touch chord held through a conf reload. A profile switch pokes the daemon and rewrites
#        kcminputrc a moment later; KWin drops its whole rebind table and its virtual device on that
#        write, so a release sent afterwards never reaches the focused window - Shift stayed logically
#        pressed and Kate typed uppercase (hardware, 2026-09-14; the journal shows the daemon did send
#        that late release). The reload itself must release the chord, while the finger still rests ---
CONF.write_text("SCALE=0.29\nDIM=0.10\nHOVER_MASK=0x80\nHOLD=0.6\n"
                "TOUCH_CHORD_5=Shift\nTOUCH_HOLD_5=0.1\n")
poke()
n_ev = len(EVENTS)
report(touch=0x10)                         # a finger rests on key 5: Shift engages after its register
time.sleep(0.35)
engaged = ("down", ("touch", 5), "Shift") in EVENTS[n_ev:]
poke()                                     # the profile switch reloads the conf mid-hold
at_reload = EVENTS[n_ev:].count(("up", ("touch", 5)))
report(touch=0x00)                         # the finger lifts at last
time.sleep(0.2)
check("R a chord held through a conf reload goes up AT the reload, exactly once",
      engaged and at_reload == 1 and EVENTS[n_ev:].count(("up", ("touch", 5))) == 1)

# --- N: out of range. The kernel zeroes X, Y and the tool key whenever the pen leaves proximity, so a
#        lifted pen reads the minimum on both axes - same as a node that has not reported yet. The daemon
#        took that for the top-left corner (the ghost, the drag and the warp jumped there), and
#        tablet-pen-pos.py printed the corner instead of exiting 1 so the toggle could use the mouse. The
#        module copy from P runs pen_norm, never the daemon thread's ---
import struct


class FakeEvdev:                           # stands in for the fcntl module inside a module copy
    def __init__(self, x, y, in_prox, node=None, lo=0, hi=1000):
        self.x, self.y, self.in_prox, self.node, self.lo, self.hi = x, y, in_prox, node, lo, hi

    def ioctl(self, fd, request, buf):
        if self.node and os.readlink(f"/proc/self/fd/{fd}") != str(self.node):
            raise OSError(25, "not the pen node")          # proves which node the reader opened
        if request == 0x80604518:          # EVIOCGKEY(96): byte 40 bit 0 = BTN_TOOL_PEN, the pen in proximity
            buf[40] = 1 if self.in_prox else 0
        else:                              # EVIOCGABS(ABS_X) or EVIOCGABS(ABS_Y)
            value = (self.x, self.y)[request - 0x80184540]
            buf[:24] = struct.pack("6i", value, self.lo, self.hi, 0, 0, 0)


nfd = os.open("/dev/null", os.O_RDONLY)
hp.fcntl = FakeEvdev(0, 0, False)
check("N daemon: out of proximity on the minimum = no position, not the corner", hp.pen_norm(nfd) is None)
hp.fcntl = FakeEvdev(500, 250, False)
check("N daemon: out of proximity but not on the minimum still reads as a position", hp.pen_norm(nfd) == (0.5, 0.25))
hp.fcntl = FakeEvdev(0, 0, True)
check("N daemon: the real corner, in proximity, is a position", hp.pen_norm(nfd) == (0.0, 0.0))
os.close(nfd)

DEVICES = SCRATCH / "input-devices"        # a name with "pen" in it that is no pen comes first, then the tablet's nodes
DEVICES.write_text(
    'N: Name="Suspend Button"\nH: Handlers=kbd event0 \nB: KEY=4000 0 0\n\n'
    'N: Name="Wacom Intuos Pro M Pen"\nH: Handlers=mouse3 event23 \nB: KEY=1c03 0 0 0 0 0\nB: ABS=3000003\n\n'
    'N: Name="Wacom Intuos Pro M Finger"\nH: Handlers=mouse4 event24 \nB: KEY=2c08 0 0 0 0 0\n\n'
    'N: Name="Wacom Intuos Pro M Pad"\nH: Handlers=event25 \nB: KEY=800 3ff 0 0 0 0\n')
for name in ("event0", "event23", "event24", "event25"):
    (SCRATCH / name).write_text("")
try:
    spec_n = importlib.util.spec_from_file_location("pen_pos", SRC.parent / "tablet-pen-pos.py")
    pen_pos = importlib.util.module_from_spec(spec_n)
    spec_n.loader.exec_module(pen_pos)       # must not run its main flow on import
except BaseException as err:                 # noqa: BLE001 - a copy that runs or crashes on import fails every check below
    print(f"# tablet-pen-pos.py import: {err!r}")
    pen_pos = None
def attempt(call):
    """An old or broken copy must fail its check, not stop the rig."""
    try:
        return call()
    except SystemExit as stop:
        return ("exit", stop.code)
    except Exception as err:                 # noqa: BLE001
        return ("error", repr(err))


got = asked = None
if pen_pos is not None:
    pen_pos.left_handed = lambda sysname: False   # never the live KWin; section L turns the tablet
    pen_pos.fcntl = FakeEvdev(600, 300, False, node=SCRATCH / "event23")
    got = attempt(lambda: pen_pos.evdev_pen_norm(str(DEVICES), str(SCRATCH)))
check("N pen-pos: the node that can report BTN_TOOL_PEN, not the first name with 'pen' in it", got == (0.6, 0.3))
if pen_pos is not None:
    pen_pos.fcntl = FakeEvdev(0, 0, False, node=SCRATCH / "event23")
    got = attempt(lambda: pen_pos.evdev_pen_norm(str(DEVICES), str(SCRATCH)))
check("N pen-pos: out of range (the kernel zeroed it) = OUT_OF_RANGE, never the corner",
      pen_pos is not None and got == getattr(pen_pos, "OUT_OF_RANGE", "absent"))
if pen_pos is not None:
    asked = []
    pen_pos.screen_size = lambda: (2560, 1440, None, None)
    pen_pos.evdev_pen_norm = lambda: getattr(pen_pos, "OUT_OF_RANGE", None)
    pen_pos.xwayland_stylus_norm = lambda dpy: asked.append(dpy) or (0.1, 0.1)
    got = attempt(lambda: pen_pos.main([]))
check("N pen-pos: OUT_OF_RANGE exits 1 at once, XWayland unasked (its data is older), so the toggle uses the mouse",
      got == ("exit", 1) and asked == [])

# --- L: left-handed mode. KDE's Left-handed mode (System Settings > Drawing Tablet > Pen) makes libinput
#        turn the tablet 180 degrees (evdev-tablet.c invert_axis) before KWin maps it; the kernel's
#        position does not turn. Read raw, the area, the ghost, the drag and the warp landed on the
#        opposite side of the screen. Fresh module copies; KWin is always a fake here. The raw values
#        are binary fractions, and no turned value equals the same point with x and y swapped ---
import contextlib
import io


def near(a, b):
    try:
        return len(a) == len(b) and all(abs(x - y) < 1e-9 for x, y in zip(a, b))
    except TypeError:
        return False


def load(name, path):
    try:
        s = importlib.util.spec_from_file_location(name, path)
        m = importlib.util.module_from_spec(s)
        s.loader.exec_module(m)
        return m
    except BaseException as err:             # noqa: BLE001 - a broken copy fails its checks below
        print(f"# {path.name} import: {err!r}")
        return None


def printed(mod, argv):
    """What mod.main(argv) printed, or how it ended."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ended = attempt(lambda: mod.main(argv))
    return buf.getvalue().strip() if ended is None else ended


ASKED = []
pl = load("pen_pos_l", SRC.parent / "tablet-pen-pos.py")
if pl is not None:
    pl.fcntl = FakeEvdev(640, 256, True, node=SCRATCH / "event23", lo=0, hi=1024)   # raw (0.625, 0.25)
    real_evdev = pl.evdev_pen_norm
    pl.evdev_pen_norm = lambda: real_evdev(str(DEVICES), str(SCRATCH))
    pl.screen_size = lambda: (2560, 1440, None, None)
    pl.left_handed = lambda sysname: ASKED.append(sysname) or sysname == "event23"
    pl.output_area = lambda: (0.5, 0.5, 0.25, 0.25)
check("L pen-pos: left-handed mode turns evdev 180 degrees, asked for the node read: (0.625, 0.25) prints 960 1080",
      pl is not None and printed(pl, []) == "960 1080 2560 1440" and ASKED == ["event23"])
check("L pen-pos: turned BEFORE --mapped: in outputArea 0.5 0.5 0.25 0.25 it prints 1520 990",
      pl is not None and printed(pl, ["--mapped"]) == "1520 990 2560 1440")
if pl is not None:
    pl.evdev_pen_norm = lambda: None         # no evdev access: the XWayland fallback
    pl.xwayland_stylus_norm = lambda dpy: (0.25, 0.75)
check("L pen-pos: XWayland's position comes through KWin, never turned again",
      pl is not None and printed(pl, []) == "640 1080 2560 1440")

hl = load("hover_l", SRC)
lfd = os.open("/dev/null", os.O_RDONLY)
turned = lifted = None
if hl is not None:
    if hasattr(hl, "TURN"):
        hl.TURN["on"] = True
    hl.fcntl = FakeEvdev(256, 384, True, lo=0, hi=1024)        # raw (0.25, 0.375)
    turned = attempt(lambda: hl.pen_norm(lfd))
    hl.fcntl = FakeEvdev(0, 0, False, lo=0, hi=1024)
    lifted = attempt(lambda: hl.pen_norm(lfd))
os.close(lfd)
check("L daemon: pen_norm turns with the mode, (0.25, 0.375) -> (0.75, 0.625); a zeroed pen stays no position",
      near(turned, (0.75, 0.625)) and lifted is None)

warped = []
if hl is not None:
    hl.fcntl = FakeEvdev(256, 384, True, lo=0, hi=1024)
    hl.pen_open = lambda: os.open("/dev/null", os.O_RDONLY)
    hl.AREA = SCRATCH / "area-l"
    hl.AREA.write_text("1280 720 640 360 0.10 2560 1440 0.5 0.5 0.25 0.25\n")
    hl.log = lambda text: None
    lw = attempt(hl.Warper)                  # loads tablet-pointer-warp.py; no device is made
    if not isinstance(lw, tuple):
        lw.fd, lw.screen = 99, (2560, 1440)
        lw.mod = type("Mod", (), {"warp": staticmethod(lambda fd, fx, fy: warped.append((fx, fy)))})()
        attempt(lambda: lw.warp(True, "press"))
check("L daemon: the warp lands on the turned pen, then goes through the area: (0.6875, 0.65625)",
      len(warped) == 1 and near(warped[0], (0.6875, 0.65625)))

ASKED_D, seen, answer = [], [], [None]
if hl is not None and hasattr(hl, "turn_poll"):
    hl.TURN.update(key=None, on=False)
    hl.KCMINPUTRC = SCRATCH / "kcminputrc-l"
    hl.KCMINPUTRC.write_text("[Libinput]\n")
    hl.pen_node = lambda: "event23"
    hl.left_handed = lambda sysname: ASKED_D.append(sysname) or answer[0]

    def poll(said):
        answer[0] = said
        hl.turn_poll()
        seen.append(hl.TURN["on"])

    poll(True); poll(True); poll(True)       # three rescans, nothing changed: one ask
    hl.KCMINPUTRC.write_text("[Libinput]\nOutputArea=0,0,1,1\n")   # KWin rewrote the file
    poll(None)                               # no answer from KWin: the last value stays...
    poll(None)                               # ...and KWin is asked again at the next rescan
    hl.pen_node = lambda: "event24"          # another pen node: the tablet on the other bus
    poll(False); poll(False)
check("L daemon: KWin asked once per pen node and kcminputrc change, not per rescan; no answer = asked again",
      ASKED_D == ["event23", "event23", "event23", "event24"] and seen == [True] * 5 + [False] * 2)

BIN_L = SCRATCH / "bin-l"                    # a busctl that says what the rig wants, and logs what it was asked
BIN_L.mkdir()
(BIN_L / "busctl").write_text('#!/bin/bash\necho "$*" >> "$LH_LOG"\nprintf "%s\\n" "$LH_SAY"\nexit "${LH_EXIT:-0}"\n')
(BIN_L / "busctl").chmod(0o755)
os.environ["LH_LOG"] = str(SCRATCH / "busctl-l.log")
path_before = os.environ["PATH"]
os.environ["PATH"] = f"{BIN_L}:{path_before}"
real_d, real_p = load("hover_l2", SRC), load("pen_pos_l2", SRC.parent / "tablet-pen-pos.py")
said_back = []
for text, code in (("b true", "0"), ("b false", "0"), ("", "1")):
    os.environ["LH_SAY"], os.environ["LH_EXIT"] = text, code
    said_back.append((attempt(lambda: real_d.left_handed("event23")), attempt(lambda: real_p.left_handed("event23"))))
os.environ["PATH"] = path_before
LH_ASK = "get-property org.kde.KWin /org/kde/KWin/InputDevice/event23 org.kde.KWin.InputDevice leftHanded"
LH_LOG = SCRATCH / "busctl-l.log"
check("L both readers: KWin's 'b true' = turned, 'b false' or no answer = not; the pen's own node is asked",
      said_back == [(True, True), (False, False), (None, False)]
      and LH_LOG.exists() and LH_LOG.read_text().count(LH_ASK) == 6)

# --- Q: the control pipe has a reader for the helper's whole life. Hardware, 2026-10-29: the Intuos asleep, every Apply
#        said "pad helper not running" because watch() closed the pipe and cycle() slept with no reader (ENXIO).
#        A fifth copy; no tablet node at first ---
hq = importlib.util.module_from_spec(spec_p)
spec_p.loader.exec_module(hq)
hq.RESCAN = 0.1
hq.RD = SCRATCH / "rdq"
hq.CTL = hq.RD / "hover.ctl"
hq.heal = lambda: None
hq.pen_node = lambda: None
hq.tablet_nodes = lambda: []
q_reads, q_marks, q_watches = [], [], []
_q_conf, _q_watch = hq.read_conf, hq.watch
hq.read_conf = lambda: (q_reads.append(1), _q_conf())[1]
hq.watch = lambda *a: (q_watches.append(1), _q_watch(*a))[1]
hq.log = lambda m: q_marks.append(len(q_reads)) if "usb report" in m else None
q_follower = type("F", (), {"up": False, "hide": lambda *a, **k: None})()
q_stop = threading.Event()


def q_daemon():
    gone = hq.Absence()
    getattr(hq, "open_ctl", lambda: None)()    # main() does this before its loop
    while not q_stop.is_set():
        hq.cycle(q_follower, None, gone, rescan=0.1)


def q_poke():
    try:
        fd = os.open(hq.CTL, os.O_WRONLY | os.O_NONBLOCK)   # poke_daemon's own open: ENXIO without a reader
    except OSError:
        return False
    os.write(fd, b"reload\n")
    os.close(fd)
    return True


threading.Thread(target=q_daemon, daemon=True).start()
time.sleep(0.4)
q_ok = []
for _ in range(10):
    q_ok.append(q_poke())
    time.sleep(0.07)
check("Q no tablet node: all 10 pokes over 0.7 s are accepted (the Center never says 'helper down')", all(q_ok))
check("Q no tablet node: the helper rescans, it does not spin", 3 <= len(q_watches) < 20)
time.sleep(0.25)
check("Q no tablet node: pokes made meanwhile are drained, none pile up",
      getattr(hq, "CTL_FD", None) is not None and not select.select([hq.CTL_FD], [], [], 0)[0])
q_poke()
node_q = SCRATCH / "q_node.fifo"
os.mkfifo(node_q)
wq = os.open(node_q, os.O_RDWR)
hq.tablet_nodes = lambda: [(str(node_q), "usb", "0357")]
time.sleep(0.5)
check("Q a poke made while absent, then the tablet appears: one normal start", len(q_marks) == 1)
check("Q ...and no stale reload follows it", len(q_marks) == 1 and len(q_reads) == q_marks[0])
q_poke()
time.sleep(0.3)
check("Q the pipe still reloads the conf while the tablet is here", q_marks and len(q_reads) == q_marks[0] + 1)
hq.tablet_nodes = lambda: []
time.sleep(0.3)
q_stop.set()
check("Q the tablet gone again: the pipe is still held and answers", q_poke())
time.sleep(0.2)
os.close(wq)

print(f"failures: {fails}")
os.close(writer)
sys.exit(1 if fails else 0)
