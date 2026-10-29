#!/usr/bin/env python3
# test_pressure.py <scratch dir> <repo dir> - rig for the pen pressure record (tablet-hover.py PressureRecord
# and the watch() wiring) and its reader (tablet_profiles.pressure_stats / pressure_tip). Synthetic evdev
# events through pipes; scratch XDG folders; no tablet node is opened, no KWin, no window.
# Also the child of itself: `test_pressure.py --driver MODE ...` is a daemon process for the SIGTERM and CPU checks.
import importlib.util
import os
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

DRIVER = len(sys.argv) > 1 and sys.argv[1] == "--driver"
if DRIVER:
    _, _, MODE, SCR, REPO_S, EVFIFO = sys.argv[:6]
    SCRATCH = Path(SCR)
    REPO = Path(REPO_S)
else:
    SCRATCH = Path(sys.argv[1]) / "pressure-test"
    REPO = Path(sys.argv[2]).resolve()
    shutil.rmtree(SCRATCH, ignore_errors=True)
for sub in ("config", "data", "runtime", "state"):
    (SCRATCH / sub).mkdir(parents=True, exist_ok=True)
os.environ.update({"XDG_CONFIG_HOME": str(SCRATCH / "config"), "XDG_DATA_HOME": str(SCRATCH / "data"),
                   "XDG_RUNTIME_DIR": str(SCRATCH / "runtime"), "XDG_STATE_HOME": str(SCRATCH / "state")})
sys.path.insert(0, str(REPO))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hover = load("hover_pressure" if not DRIVER else "hover_driver", REPO / "tablet-hover.py")
CONF = SCRATCH / "config" / "tabprec.conf"
TOP = 8191
ABS, KEY, SYN = 3, 1, 0
P_ABS, TOUCH, PEN, RUBBER = 0x18, 0x14A, 0x140, 0x141


def ev(ms, kind, code, value):
    return struct.pack("llHHi", ms // 1000, (ms % 1000) * 1000, kind, code, value)


def frame(ms, *changes):
    """changes: (kind, code, value) triples, then the SYN_REPORT, all at ms."""
    return b"".join(ev(ms, k, c, v) for k, c, v in changes) + ev(ms, SYN, 0, 0)


def raw_for(pct):
    return TOP if pct >= 100 else -(-pct * TOP // 100)       # the smallest raw value that is pct whole percent


def session(start, pct, tool=PEN, dur=150):
    """The pen comes in, touches for `dur` ms with a peak of pct %, lifts, leaves: 0.25 s of pen-in-range time."""
    raw = raw_for(pct)
    assert raw * 100 // TOP == pct, (pct, raw)
    return (frame(start, (KEY, tool, 1))
            + frame(start + 100, (KEY, TOUCH, 1), (ABS, P_ABS, raw // 4))
            + frame(start + 100 + dur // 3, (ABS, P_ABS, raw))
            + frame(start + 100 + 2 * dur // 3, (ABS, P_ABS, raw // 2))
            + frame(start + 100 + dur, (KEY, TOUCH, 0), (ABS, P_ABS, 0))
            + frame(start + 300, (KEY, tool, 0)))


if DRIVER:
    # a daemon process whose pen node is a pipe: MODE term = SIGTERM check, cpu-on / cpu-off = the CPU check
    pad = SCRATCH / "pad.fifo"
    if not pad.exists():
        os.mkfifo(pad)
    pad_w = os.open(pad, os.O_RDWR)
    if MODE.startswith("cpu"):
        hover.RESCAN = 1.0
    else:
        hover.RESCAN = 0.2
    hover.heal = lambda: None
    hover.turn_poll = lambda: None
    hover.pen_node = lambda: None
    hover.tablet_nodes = lambda: [(str(pad), "bt", "0360")] if NODES_UP[0] else []
    NODES_UP = [True]
    hover.log = lambda text: print("log:", text, flush=True)
    if MODE in ("term", "off", "late"):
        pr, pw = os.pipe()
        os.set_blocking(pr, False)
        hover.pressure_open = lambda: (pr, TOP)
    else:
        evfd = os.open(EVFIFO, os.O_RDWR | os.O_NONBLOCK)      # RDWR: no EOF while the rig has not written yet
        hover.pressure_open = lambda: (evfd, TOP)
    hover.read_conf.__globals__["CONF"] = CONF
    FOLLOWS = [0]
    F = type("F", (), {"up": MODE.endswith("drag"), "hide": lambda *a, **k: None,       # -drag: a rectangle follows the pen
                       "follow": lambda self: FOLLOWS.__setitem__(0, FOLLOWS[0] + 1)})

    if MODE.startswith("cpu"):
        start = time.process_time()
        threading.Timer(float(sys.argv[6]), lambda: NODES_UP.__setitem__(0, False)).start()
        hover.watch(hover.read_conf(), F())
        print(f"cpu_ms {(time.process_time() - start) * 1000:.1f} follows {FOLLOWS[0]}")
        sys.exit(0)

    def feeder():
        time.sleep(0.3)
        if MODE == "late":                                # started with the switch 0; the reload turns the record on
            CONF.write_text("SCALE=0.3\n")
            os.write(os.open(hover.CTL, os.O_RDWR | os.O_NONBLOCK), b"reload\n")
            time.sleep(0.5)
        os.write(pw, b"".join(session(k * 1000, 40) for k in range(25)))
        time.sleep(0.5)                                   # the first write has happened (RESCAN 0.2); the cadence holds the next
        os.write(pw, b"".join(session((30 + k) * 1000, 40) for k in range(5)))
        time.sleep(0.4)
        print("handler:", "default" if signal.getsignal(signal.SIGTERM) == signal.SIG_DFL else "set", flush=True)
        if MODE in ("term", "late"):
            os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(2)
        os._exit(7)                                       # SIGTERM did not end the daemon

    hover.Follower = F
    hover.Warper = lambda: None
    hover.cycle = lambda follower, warper, gone: hover.watch(hover.read_conf(), follower, None)
    threading.Thread(target=feeder, daemon=True).start()
    hover.main()
    sys.exit(0)

wp = load("wp_pressure", REPO / "tablet_profiles.py")
fails = 0


def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    fails += 0 if cond else 1


def fresh(profile="Test", clock=None):
    return hover.PressureRecord(TOP, profile, clock or time.monotonic)


# --- S: the statistics equal a hand computation. Strokes at 1..30 % and 100 %; a 20 ms touch and an eraser
#        stroke at 99 % must not count. Sorted peaks: 1..30, 100 (31 strokes). Nearest rank: P10 = the
#        ceil(3.1) = 4th = 4; P95 = the ceil(29.45) = 30th = 30. 31 strokes at 0.25 s = 7.75 s of pen-in-range time ---
rec = fresh("Hand")
order = [(k * 7) % 31 for k in range(31)]                 # 0..30 in a scrambled order (7 is coprime to 31)
stream = b""
t = 0
for k in order:
    pct = 100 if k == 0 else k
    stream += session(t, pct)
    t += 1000
short = (frame(t, (KEY, PEN, 1)) + frame(t + 100, (KEY, TOUCH, 1), (ABS, P_ABS, raw_for(99)))
         + frame(t + 120, (KEY, TOUCH, 0), (ABS, P_ABS, 0)) + frame(t + 300, (KEY, PEN, 0)))
eraser = session(t + 1000, 99, tool=RUBBER)
rec.feed(stream + short + eraser)
rec.flush(True)
got = wp.pressure_stats("Hand")
check("S31 strokes counted, the 20 ms touch and the eraser not", rec.data["strokes"] == 31 and got and got[0] == 31)
check("S light = 4 %, hardest = 30 % (hand: 4th and 30th of 31 sorted peaks)", got and got[2] == 4 and got[3] == 30)
check("S pen-in-range time = 31 x 0.25 s + the 20 ms touch session's 0.12 s", abs(rec.data["active_s"] - (7.75 + 0.12)) < 1e-6)
check("S tooltip text is the numbers", wp.pressure_tip("Hand") ==
      "Last 1 minute: your light strokes peak near 4 %, your hardest reach 30 % (31 strokes, raw pen pressure before the curve).")
check("S too few strokes: the tooltip says how many, no percentages",
      "%" not in wp.pressure_tip("nobody") and wp.pressure_tip("nobody").startswith("Pressure record: 0 strokes"))

# --- R: a SYN_DROPPED between touch-down and touch-up (the client buffer overran): no stroke is invented, the
#        next full stroke counts ---
rec = fresh("Dropped")
rec.feed(frame(0, (KEY, PEN, 1)) + frame(100, (KEY, TOUCH, 1), (ABS, P_ABS, raw_for(50))) + ev(150, SYN, 3, 0))
rec.feed(frame(200, (ABS, P_ABS, 0)) + frame(250) + frame(300))        # the lost events held the touch-up
check("R after a drop nothing is counted until the pen and touch are seen again", rec.data["strokes"] == 0 and rec.stroke is None)
rec.feed(session(1000, 25))
check("R ...then a whole stroke counts", rec.data["strokes"] == 1)

# --- I: an idle gap counts at most IDLE_CAP (1 s): 0 ms in range, a report 10 s later, one 0.3 s after that ---
rec = fresh("Idle")
rec.feed(frame(0, (KEY, PEN, 1)) + frame(10_000) + frame(10_300))
check("I a 10 s silence with the pen in range counts 1.0 s, then 0.3 s", abs(rec.data["active_s"] - 1.3) < 1e-9)

# --- W: the window is 40 buckets of 900 s ACTIVE time. 25 strokes at 80 %, then 10 h of hovering, then
#        25 strokes at 20 %: the old habit has faded (P95 of everything would be 80) ---
rec = fresh("Window")
rec.feed(b"".join(session(k * 1000, 80) for k in range(25)))
hover_ms = 25_000
rec.feed(frame(hover_ms, (KEY, PEN, 1)) + b"".join(frame(hover_ms + 1000 * i) for i in range(1, 36_050)))
rec.feed(frame(hover_ms + 36_100_000, (KEY, PEN, 0)))
rec.feed(b"".join(session(hover_ms + 37_000_000 + k * 1000, 20) for k in range(25)))
rec.flush(True)
got = wp.pressure_stats("Window")
check("W the 80 % strokes of bucket 0 are gone, 25 strokes at 20 % remain", got and got[0] == 25 and got[2] == 20 and got[3] == 20)
check("W the window holds 10 hours, the tooltip says so", got and abs(got[1] - 10.0) < 1e-9
      and wp.pressure_tip("Window").startswith("Last 10 hours: your light strokes peak near 20 %"))

# --- C: the write cadence. A fake clock; os.replace is the write ---
writes = []
real_replace = os.replace
os.replace = lambda a, b: (writes.append(str(b)), real_replace(a, b))[1]
now = [1000.0]
rec = fresh("Cadence", lambda: now[0])


def one():
    rec.feed(session(int(now[0] * 1000) % 10**7, 50))


one()
rec.flush()                                               # first dirty flush: written at once
n1 = len(writes)
one(); now[0] += 59; rec.flush()
n2 = len(writes)
now[0] += 1; rec.flush()
n3 = len(writes)
one(); now[0] += 1; rec.flush()
n4 = len(writes)
rec.flush(True)
n5 = len(writes)
rec.flush(True)                                           # nothing changed: nothing written
n6 = len(writes)
check("C one write at the first stroke, none 59 s later, one at 60 s, none 1 s after that",
      (n1, n2, n3, n4) == (1, 1, 2, 2))
check("C a forced flush (exit) writes the change; with nothing changed it writes nothing", (n5, n6) == (3, 3))
os.replace = real_replace

# a write that fails is not fatal and is retried after FLUSH s
blocker = SCRATCH / "state" / "tabprec" / "pressure"
shutil.rmtree(blocker, ignore_errors=True)
blocker.write_text("a file where the folder should be")
hover.log = lambda text: None
rec = fresh("Blocked", lambda: now[0])
one()
check("C a failed write returns False and keeps the strokes for the next try", rec.flush(True) is False and rec.dirty)
blocker.unlink()
check("C ...which writes once the folder can be made", rec.flush(True) is True and not rec.dirty)

# a folder that stays unwritable is one journal line per run of failures, not one a minute for ever
shutil.rmtree(blocker, ignore_errors=True)
blocker.write_text("a file where the folder should be")
logged = []
hover.log = logged.append
rec = fresh("Blocked", lambda: now[0])
one()
for _ in range(3):
    now[0] += 60
    rec.flush()
check("C three failed tries 60 s apart: ONE journal line", len(logged) == 1)
blocker.unlink()
now[0] += 60
rec.flush()
shutil.rmtree(blocker, ignore_errors=True)
blocker.write_text("a file where the folder should be")
one()
now[0] += 60
rec.flush()
check("C ...after a success the next failure is told again (2 lines)", len(logged) == 2)
blocker.unlink()
hover.log = lambda text: None

# two helpers on one profile: B writes and replaces between A's write and A's replace; A's write must still land whole
a, b = fresh("Twin"), fresh("Twin")
a.feed(session(0, 30))
b.feed(session(0, 30))


def interleave(src, dst):
    os.replace = real_replace
    b.flush(True)
    return real_replace(src, dst)


os.replace = interleave
ok = a.flush(True)
os.replace = real_replace
check("C two helpers: A's write survives B's write in between, the file is whole, no .tmp is left",
      ok is True and wp.pressure_stats("Twin") and wp.pressure_stats("Twin")[0] == 1
      and not list(hover.pressure_file("Twin").parent.glob("Twin*.tmp")))

# --- F: file names agree between the helper and the reader; a profile switch writes the old record, loads the new ---
names = ["Plain", "a/b", "Ünï cödé", "x" * 300, "x" * 300 + "y", "", ".", "..", "a b"]
agree = all(hover.pressure_file(n) == wp.pressure_file(n) for n in names)
ok_len = all(len(hover.pressure_file(n).name.encode()) < 255 and "/" not in hover.pressure_file(n).name for n in names)
check("F helper and reader name every profile's file alike; no slash, under 255 bytes", agree and ok_len)
check("F two long names with the same start get two files", hover.pressure_file(names[3]) != hover.pressure_file(names[4]))
rec = fresh("A")
rec.feed(session(0, 30))
rec.set_profile("B")
check("F a switch writes A's record and starts B empty", wp.pressure_stats("A")[0] == 1 and rec.data["strokes"] == 0)
rec.feed(session(1000, 60)); rec.set_profile("A")
check("F ...and back: A's strokes come back, B's were kept", rec.data["strokes"] == 1 and wp.pressure_stats("B")[0] == 1)
hover.pressure_file("Damaged").parent.mkdir(parents=True, exist_ok=True)
hover.pressure_file("Damaged").write_text("{not json")
hover.pressure_file("Future").write_text('{"v": 2, "buckets": {}, "active_s": 1, "strokes": 0}')
check("F a damaged or newer-format file starts a fresh record, the reader says none",
      fresh("Damaged").data["strokes"] == 0 and fresh("Future").data["strokes"] == 0
      and wp.pressure_stats("Damaged") is None and wp.pressure_stats("Future") is None)

# --- D: the daemon's watch() with a pipe as the pen node ---
hd = load("hover_daemon", REPO / "tablet-hover.py")
hd.RESCAN = 0.2
hd.CTL = SCRATCH / "runtime" / "hd.ctl"
hd.heal = lambda: None
hd.turn_poll = lambda: None
hd.pen_node = lambda: None
said = []
hd.log = said.append
pad = SCRATCH / "pad-d.fifo"
os.mkfifo(pad)
pad_w = os.open(pad, os.O_RDWR)
up = [True]
hd.tablet_nodes = lambda: [(str(pad), "bt", "0360")] if up[0] else []
pipes, asked = [], [0]


def stub_open():
    asked[0] += 1
    r, w = os.pipe()
    os.set_blocking(r, False)
    pipes.append((r, w))
    return r, TOP


hd.pressure_open = stub_open
ini = SCRATCH / "data" / "tablet-center" / "profiles.ini"
ini.parent.mkdir(parents=True, exist_ok=True)
ini.write_text("[Active]\nname = Alpha\n")
CONF.write_text("SCALE=0.3\n")
F = type("F", (), {"up": False, "hide": lambda *a, **k: None})
done = threading.Event()
threading.Thread(target=lambda: (hd.watch(hd.read_conf(), F()), done.set()), daemon=True).start()
time.sleep(0.5)
check("D switch absent = on: the pen was opened and the record named", asked[0] == 1 and any("pressure record on" in s for s in said))
os.write(pipes[0][1], b"".join(session(k * 1000, 40) for k in range(25)))
time.sleep(0.5)
alpha = hd.pressure_file("Alpha")
a1 = wp.pressure_stats("Alpha")
check("D the first strokes reach Alpha's file at a rescan", a1 is not None and a1[0] == 25)
os.write(pipes[0][1], b"".join(session((30 + k) * 1000, 40) for k in range(5)))
time.sleep(0.5)
check("D inside the minute the file is not rewritten (still 25)", wp.pressure_stats("Alpha")[0] == 25)
ini.write_text("[Active]\nname = Beta\n")                    # a profile switch (a new stamp)
time.sleep(0.5)
check("D the switch wrote Alpha's 30 strokes", wp.pressure_stats("Alpha")[0] == 30)
os.write(pipes[0][1], b"".join(session((40 + k) * 1000, 70) for k in range(3)))
time.sleep(0.4)
CONF.write_text("PRESSURE_RECORD=0\n")                       # the switch off, by a conf reload
fd_ctl = os.open(hd.CTL, os.O_RDWR | os.O_NONBLOCK)
os.write(fd_ctl, b"reload\n")
time.sleep(0.5)
check("D switch 0 on a reload: the record is closed and written (Beta has 3)",
      any("pressure record off" in s for s in said) and wp.pressure_stats("Beta") and wp.pressure_stats("Beta")[0] == 3)
n_asked = asked[0]
time.sleep(0.5)
check("D switch 0: the pen is not opened again at a rescan", asked[0] == n_asked)
CONF.write_text("SCALE=0.3\n")
os.write(fd_ctl, b"reload\n")
time.sleep(0.6)
check("D switch back on: the pen is opened again, Beta's strokes are kept", asked[0] == n_asked + 1)
os.write(pipes[-1][1], session(60_000, 70))
time.sleep(0.1)
up[0] = False                                                # the tablet goes: watch ends at its next rescan
check("D watch ends", done.wait(1.5))
check("D the exit write has the stroke fed just before the tablet went (Beta 4)", wp.pressure_stats("Beta")[0] == 4)
os.close(fd_ctl)

# --- H: the pen node goes mid-stroke (a Bluetooth hang-up) and comes back: the stroke under way is not carried over.
#        Touch down at 90 %, EOF, the next node: a pen in, a 30 % stroke. One stroke, at 30 %, not the 90 % of before ---
ini.write_text("[Active]\nname = Gamma\n")
n_pipes = len(pipes)
up[0] = True
done.clear()
threading.Thread(target=lambda: (hd.watch(hd.read_conf(), F()), done.set()), daemon=True).start()
time.sleep(0.5)
os.write(pipes[n_pipes][1], frame(0, (KEY, PEN, 1)) + frame(100, (KEY, TOUCH, 1), (ABS, P_ABS, raw_for(90))))
time.sleep(0.2)
os.close(pipes[n_pipes][1])                                  # EOF: the node is gone
time.sleep(0.7)                                              # ...and opened again at a rescan
os.write(pipes[-1][1], session(5000, 30))
time.sleep(0.2)
up[0] = False
check("H watch ends", done.wait(1.5))
got = wp.pressure_stats("Gamma")
check("H after a node loss mid-stroke: a new node was opened, one stroke, at 30 % (not the 90 % from before)",
      len(pipes) == n_pipes + 2 and got and got[0] == 1 and got[2] == 30 and got[3] == 30)

# --- H2: the pen driver comes (the record's source moves from the pen's own node to the driver's copy) mid-stroke:
#         the same rule, a stroke on the old node is not carried over ---
via = [None]
hd.pen_copy_node = lambda: via[0]
ini.write_text("[Active]\nname = Delta\n")
n_pipes = len(pipes)
up[0] = True
done.clear()
threading.Thread(target=lambda: (hd.watch(hd.read_conf(), F()), done.set()), daemon=True).start()
time.sleep(0.5)
os.write(pipes[n_pipes][1], frame(0, (KEY, PEN, 1)) + frame(100, (KEY, TOUCH, 1), (ABS, P_ABS, raw_for(90))))
time.sleep(0.2)
via[0] = "event9"                                            # the driver started
time.sleep(0.7)
os.write(pipes[-1][1], session(5000, 30))
time.sleep(0.2)
up[0] = False
check("H2 watch ends", done.wait(1.5))
got = wp.pressure_stats("Delta")
check("H2 the node swap mid-stroke: a new node was opened, one stroke, at 30 % (not the 90 % from the old node)",
      len(pipes) == n_pipes + 2 and got and got[0] == 1 and got[2] == 30 and got[3] == 30)
hd.pen_copy_node = lambda: None

# --- X: the switch OFF opens nothing and writes nothing: a fresh daemon copy, conf 0 from the start ---
shutil.rmtree(SCRATCH / "state" / "tabprec", ignore_errors=True)
CONF.write_text("PRESSURE_RECORD=0\n")
hx = load("hover_off", REPO / "tablet-hover.py")
hx.RESCAN = 0.2
hx.CTL = SCRATCH / "runtime" / "hx.ctl"
hx.heal = lambda: None
hx.turn_poll = lambda: None
hx.pen_node = lambda: None
hx.log = lambda text: None
opened = []
hx.pressure_open = lambda: opened.append(1)
hx.pen_open = lambda: opened.append(1)
up2 = [True]
hx.tablet_nodes = lambda: [(str(pad), "bt", "0360")] if up2[0] else []
done2 = threading.Event()
threading.Thread(target=lambda: (hx.watch(hx.read_conf(), F()), done2.set()), daemon=True).start()
time.sleep(0.8)
up2[0] = False
done2.wait(1.5)
check("X switch 0: the pen is never opened, no profiles.ini read for it, no file or folder made",
      not opened and not (SCRATCH / "state" / "tabprec").exists())

# --- T: a real daemon process (main()) and SIGTERM: the exit write; switch 0 keeps the default handler ---
CONF.write_text("SCALE=0.3\n")
shutil.rmtree(SCRATCH / "state" / "tabprec", ignore_errors=True)
ini.write_text("[Active]\nname = Term\n")
run = subprocess.run([sys.executable, __file__, "--driver", "term", str(SCRATCH), str(REPO), "-"],
                     capture_output=True, text=True, timeout=30)
check("T SIGTERM ends the daemon with status 0 and the record holds all 30 strokes (the cadence held the last 5 until the exit)",
      run.returncode == 0 and "handler: set" in run.stdout and wp.pressure_stats("Term") and wp.pressure_stats("Term")[0] == 30)
CONF.write_text("PRESSURE_RECORD=0\n")
run = subprocess.run([sys.executable, __file__, "--driver", "off", str(SCRATCH), str(REPO), "-"],
                     capture_output=True, text=True, timeout=30)
check("T switch 0: SIGTERM keeps its default handler (today's code path)", "handler: default" in run.stdout)

# a record switched on LATER (conf 0 at the start, a reload turns it on) is written at SIGTERM too
shutil.rmtree(SCRATCH / "state" / "tabprec", ignore_errors=True)
CONF.write_text("PRESSURE_RECORD=0\n")
run = subprocess.run([sys.executable, __file__, "--driver", "late", str(SCRATCH), str(REPO), "-"],
                     capture_output=True, text=True, timeout=30)
check("T switched on by a reload: SIGTERM ends the daemon with status 0 and the record holds all 30 strokes",
      run.returncode == 0 and "handler: set" in run.stdout and wp.pressure_stats("Term") and wp.pressure_stats("Term")[0] == 30)

# --- U: the CPU the daemon spends, with the record and without: the same 200-report/s pen stream (6 events
#        a report, the pen touching), 5 s, the daemon a process of its own; cpu_ms is its process time ---
cpu = {}
FOLLOWS = {}
for mode, text in (("cpu-off", "PRESSURE_RECORD=0\n"), ("cpu-on", "SCALE=0.3\n"),
                   ("cpu-off-drag", "PRESSURE_RECORD=0\n"), ("cpu-on-drag", "SCALE=0.3\n")):
    CONF.write_text(text)
    fifo = SCRATCH / f"{mode}.fifo"
    os.mkfifo(fifo)
    child = subprocess.Popen([sys.executable, __file__, "--driver", mode, str(SCRATCH), str(REPO), str(fifo), "5"],
                             stdout=subprocess.PIPE, text=True)
    time.sleep(0.8)
    w = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    ms, end = 0, time.monotonic() + 4.5
    os.write(w, frame(0, (KEY, PEN, 1), (KEY, TOUCH, 1)))
    while time.monotonic() < end:
        ms += 5
        try:
            os.write(w, frame(ms, (ABS, 0, 1000 + ms % 5000), (ABS, 1, 2000), (ABS, P_ABS, 1000 + ms % 4000),
                              (ABS, 0x1A, 3), (ABS, 0x1B, 4)))
        except BlockingIOError:
            pass
        time.sleep(0.005)
    out = child.communicate(timeout=30)[0]
    os.close(w)
    last = [l for l in out.splitlines() if l.startswith("cpu_ms")][-1].split()
    cpu[mode], FOLLOWS[mode] = float(last[1]), int(last[3])
print(f"cpu over ~5 s of a pen at 200 reports/s: record off {cpu['cpu-off']:.1f} ms, record on {cpu['cpu-on']:.1f} ms "
      f"(extra {cpu['cpu-on'] - cpu['cpu-off']:.1f} ms = {(cpu['cpu-on'] - cpu['cpu-off']) / 50:.2f} % of one core)")
check("U the record costs under 5 % of one core at 200 reports/s", cpu["cpu-on"] - cpu["cpu-off"] < 250)
print(f"drag follow() calls in ~5 s of the same stream: record off {FOLLOWS['cpu-off-drag']}, record on {FOLLOWS['cpu-on-drag']}")
check("U a drag's follow() rate is the same with the record on (a pen report must not wake it 200 times a second)",
      FOLLOWS["cpu-off-drag"] > 50 and abs(FOLLOWS["cpu-on-drag"] - FOLLOWS["cpu-off-drag"]) <= 0.2 * FOLLOWS["cpu-off-drag"])

# --- N (v2-all): with the pen driver on, the record reads the driver's copy. The driver grabs the pen's own node: its
#     ABS / KEY state still updates (pen_norm, pen_axes read that), but no event reaches a second reader. The copy is
#     the node named like the pen under /devices/virtual/. Without the driver's socket, or with a stale one (no copy),
#     it is the pen's own node, as before ---
hn = load("hover_node", REPO / "tablet-hover.py")
DEVS = SCRATCH / "proc-devices"
PEN_BLOCK = ('I: Bus=0003 Vendor=056a Product=0357 Version=0110\nN: Name="Wacom Intuos Pro M Pen"\n'
             'P: Phys=\nS: Sysfs=%s\nU: Uniq=\nH: Handlers=mouse2 event%d \nB: PROP=1\n')
ORIG = PEN_BLOCK % ("/devices/pci0000:00/usb1/1-1/input/input10", 5)
COPY = PEN_BLOCK % ("/devices/virtual/input/input99", 9)
OTHER = 'I: Bus=0011 Vendor=0001 Product=0001 Version=ab41\nN: Name="AT Translated Set 2 keyboard"\nH: Handlers=sysrq kbd event3 \n'
hn.DEVICES = DEVS
SOCK = hn.RD / "pen-driver.sock"
hn.RD.mkdir(parents=True, exist_ok=True)
DEVS.write_text("\n".join([OTHER, ORIG, COPY]) + "\n")
if SOCK.exists():
    SOCK.unlink()
check("N no pen driver socket: no copy is named, the pen's own node is used", hn.pen_copy_node() is None)
SOCK.write_text("")
check("N the pen driver's socket and its virtual copy listed (the original comes first in the list): the copy, event9",
      hn.pen_copy_node() == "event9")
DEVS.write_text("\n".join([OTHER, ORIG]) + "\n")
check("N a stale socket and no copy in the list: none (the pen's own node is used again)", hn.pen_copy_node() is None)
DEVS.write_text("\n".join([OTHER, COPY, ORIG]) + "\n")
opened = []
real_open = os.open
hn.os.open = lambda path, *a, **k: (opened.append(str(path)), real_open("/dev/null", os.O_RDONLY))[1]
hn.pen_open = lambda: (opened.append("pen_open"), None)[1]
got = hn.pressure_open()
hn.os.open = real_open
check("N the record opens the copy's node while the driver runs (/dev/input/event9), not the pen's own", opened[:1] == ["/dev/input/event9"]
      and "pen_open" not in opened)
DEVS.write_text("\n".join([OTHER, COPY.replace("event9", "event4"), COPY]) + "\n")
check("N two virtual pens in the list (a VM stand-in, then the copy): the later one, event9", hn.pen_copy_node() == "event9")
def _deny(path, *a, **k):
    raise PermissionError(path)
opened.clear()
hn.os.open = _deny
got = hn.pressure_open()
hn.os.open = real_open
check("N the copy exists but cannot be opened yet: None, no fallback to the silent grabbed original", got is None and "pen_open" not in opened)
SOCK.unlink()
opened.clear()
hn.pressure_open()
check("N the driver gone: the record is back on the pen's own node (pen_open)", opened == ["pen_open"])

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
