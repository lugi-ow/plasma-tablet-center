#!/usr/bin/env python3
# tests/test_warp.py SCRATCH SRCDIR - FUT-01: the daemon's virtual mouse survives a KWin probe that
# cannot answer. The real Warper + tablet-pointer-warp.py from SRCDIR run with /dev/uinput redirected
# to /dev/null, the ioctls no-ops, and a stub busctl whose answer the rig sets. No desktop is touched.
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

SCRATCH, SRC = Path(sys.argv[1]) / "warp", Path(sys.argv[2])
shutil.rmtree(SCRATCH, ignore_errors=True)
BIN, FAKE, RD = SCRATCH / "bin", SCRATCH / "fake", SCRATCH / "rd"
for d in (BIN, FAKE, RD):
    d.mkdir(parents=True)
os.environ["XDG_RUNTIME_DIR"] = str(RD)
os.environ["XDG_CONFIG_HOME"] = str(SCRATCH / "conf")
LOG, ANSWER = SCRATCH / "busctl.log", SCRATCH / "busctl.answer"
(BIN / "busctl").write_text(f'#!/bin/bash\necho "$*" >> "{LOG}"\ncat "{ANSWER}" 2>/dev/null\n')
(BIN / "busctl").chmod(0o755)
os.environ["PATH"] = f"{BIN}:{os.environ['PATH']}"
shutil.copy(SRC / "tablet-pointer-warp.py", FAKE / "tablet-pointer-warp.py")
shutil.copy(SRC / "tablet_profiles.py", FAKE / "tablet_profiles.py")   # the daemon takes chord_hazard from it

spec = importlib.util.spec_from_file_location("hover", SRC / "tablet-hover.py")
hover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hover)
hover.DIR = FAKE
hover.log = lambda msg: LINES.append(msg)
LINES = []
fails = 0


def check(name, ok):
    global fails
    print(("ok   " if ok else "FAIL ") + name)
    fails += not ok


def make_warper():
    w = hover.Warper()
    m = w.mod
    m.screen_size = lambda: (2560, 1440)
    m.kwin_sees.__defaults__ = (0.3,)              # a probe that never answers waits 0.3 s, not 1.5 s
    return w


def ensure(w, probe):
    """The daemon's call: ensure(WARP != 0) since FUT-01; the old Warper takes no argument."""
    try:
        w.ensure(probe)
    except TypeError:
        w.ensure()


real_open, real_ioctl, real_listdir = os.open, __import__("fcntl").ioctl, os.listdir
import fcntl  # noqa: E402

os.open = lambda p, *a, **k: real_open("/dev/null" if p == "/dev/uinput" else p, *a, **k)
fcntl.ioctl = lambda fd, req, arg=0: 0
os.listdir = lambda p: ["event99"] if str(p).startswith("/sys/devices/virtual/input/") else real_listdir(p)

# F1: busctl prints nothing we can match (a changed output, a bus that does not answer): the device stays
ANSWER.write_text('as 0\n')
w = make_warper()
ensure(w, True)
check("F1 KWin never listed the node: the virtual device is kept (chords stay alive)", w.fd is not None)
check("F1 the log says the warp may land late", any("may land late" in l for l in LINES))
w.close()

# F2: WARP=0 needs no probe: no busctl call at all, the device exists
LOG.write_text("")
w = make_warper()
ensure(w, False)
check("F2 WARP=0: the device exists", w.fd is not None)
check("F2 WARP=0: busctl was not called", LOG.read_text() == "")
w.close()

# F3: the node named without quotes, and in JSON, is found at once
m = w.mod
ANSWER.write_text('as 2 event7 event99\n')
check("F3 unquoted node found", m.kwin_sees("event99", 0.3) is True)
ANSWER.write_text('{"type":"as","data":[["event7","event99"]]}\n')
check("F3 --json=short shape found", m.kwin_sees("event99", 0.3) is True)
ANSWER.write_text('as 1 "event990"\n')
check("F3 event990 is not event99 (whole words)", m.kwin_sees("event99", 0.3) is False)

# F4: the one-shot warp stays strict: a node KWin never lists is an error there
ANSWER.write_text('as 0\n')
try:
    fd, _ = m.create()
    strict_raised = False
    m.destroy(fd)
except RuntimeError:
    strict_raised = True
check("F4 create() default stays strict (the one-shot warp)", strict_raised)

# F5: a listed node still works and logs nothing late
ANSWER.write_text('as 1 "event99"\n')
LINES.clear()
w = make_warper()
ensure(w, True)
check("F5 a listed node: device up, no 'late' line", w.fd is not None and not any("late" in l for l in LINES))
w.close()

# G: the console-key guard in chord_down (R3-3): the keys already held count with the new chord
LINES.clear()
w = make_warper()
ensure(w, True)
SENT = []
real_chord = w.mod.chord
w.mod.chord = lambda fd, codes, down: SENT.append((tuple(codes), down)) or real_chord(fd, codes, down)
w.chord_down(("touch", 1), "Ctrl+Alt", "key 1 touch")
w.chord_down(("press", 2), "F3", "key 2")
check("G1 a held touch chord Ctrl+Alt plus a press chord F3 is refused: only Ctrl+Alt went out",
      list(w.held) == [("touch", 1)] and SENT == [((29, 56), True)])
check("G1 ...with one journal line naming the chord",
      sum("'F3': refused" in l for l in LINES) == 1)
w.release_all()
SENT.clear()
LINES.clear()
w.chord_down(("press", 3), "Ctrl+Alt+F1", "key 3")
check("G2 a hand-edited CHORD_n=Ctrl+Alt+F1 is refused: nothing sent, nothing held",
      w.held == {} and SENT == [] and sum("'Ctrl+Alt+F1': refused" in l for l in LINES) == 1)
for tag, text in ((("press", 4), "Ctrl+Alt+Shift+F3"), (("press", 5), "Ctrl+F3"), (("press", 6), "Alt+F3")):
    SENT.clear()
    w.chord_down(tag, text, "k")
    check(f"G3 {text} still passes", tag in w.held and SENT and SENT[0][1] is True)
    w.release_all()
check("G4 a passing chord is released as before (Alt+F3 down, then up)",
      SENT == [((56, 61), True), ((56, 61), False)] and w.held == {})
SENT.clear()
w.chord_down(("touch", 1), "Ctrl+Alt", "key 1 touch")
w.chord_down(("press", 2), "F3", "key 2")
w.release_all()
check("G4 release_all lifts the one chord that went out, none other",
      SENT == [((29, 56), True), ((29, 56), False)] and w.held == {})
w.close()

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
