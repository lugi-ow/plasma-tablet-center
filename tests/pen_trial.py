#!/usr/bin/env python3
# pen_trial.py latency|crash|watchdog - VM ONLY (desktop user in group input, /dev/uinput open): numbers for the pen-driver trial.
# latency = area datagram to first changed copy event; crash = helper killed, then the stand-in node reads again; watchdog = helper stopped, systemd kills and restarts it.
import importlib.util
import os
import select
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN = Path(os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
SOCK = RUN / "tabprec" / "pen-driver.sock"


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


drv = load("pen_driver", "tablet-pen-driver.py")
standin = load("pen_standin", "tests/pen_standin.py")


# ── chunk: trial_standin
def trial_standin(bus="bt"):
    """The stand-in pen: (write fd, its event node path)."""
    fd, sysname = drv.make_copy(*standin.standin_caps(bus))
    node = [n for n in os.listdir(f"/sys/devices/virtual/input/{sysname}") if n.startswith("event")][0]
    time.sleep(0.5)
    return fd, f"/dev/input/{node}"


# ── chunk: trial_helper
def trial_helper(node):
    """Start the helper on the node, wait for its 'grabbed' line."""
    proc = subprocess.Popen([sys.executable, str(ROOT / "tablet-pen-driver.py"), "--force", "--source", node],
                            stdout=subprocess.PIPE, text=True)
    proc.stdout.readline()
    return proc


# ── chunk: trial_copy_node
def trial_copy_node(before):
    """The event node the helper's copy made: virtual input devices named like the pen, not in `before`."""
    for ev in sorted(Path("/sys/class/input").glob("event*")):
        path = "/dev/input/" + ev.name
        if path not in before and "/virtual/" in str((ev / "device").resolve()):
            return path
    return None


def event_nodes():
    return {"/dev/input/" + e.name for e in Path("/sys/class/input").glob("event*")}


# ── chunk: trial_reads
def trial_reads(node, out, wait=0.4):
    """Open a fresh descriptor on node, THEN make the stand-in emit one frame: True when the descriptor sees
    it within wait seconds (a grabbed node gives none)."""
    fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
    try:
        standin.standin_emit(out, [(drv.EV_ABS, 0, int(time.monotonic() * 1000) % 40000 + 1)])
        return bool(select.select([fd], [], [], wait)[0])
    finally:
        os.close(fd)


# ── chunk: trial_latency
def trial_latency(rounds=40):
    """Frames every 2 ms alternate x by one unit; at a random moment the area datagram goes out; the delay
    to the first copy event outside the old range is the mapping change time."""
    out, node = trial_standin()
    base = event_nodes()
    helper = trial_helper(node)
    time.sleep(1)
    copy = trial_copy_node(base)
    cfd = os.open(copy, os.O_RDONLY | os.O_NONBLOCK)
    tx = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    delays, x = [], 22400
    for n in range(rounds):
        tx.sendto(b"full", str(SOCK))
        time.sleep(0.05)
        drain(cfd)
        sent = None
        for tick in range(60):
            x = 22400 + 20 * (tick & 1)
            standin.standin_emit(out, [(drv.EV_ABS, 0, x), (drv.EV_ABS, 1, 14800 + 20 * (tick & 1))])
            if tick == 10:
                sent = time.monotonic()
                tx.sendto(b"0.5 0.5 0.1 0.1", str(SOCK))
            if select.select([cfd], [], [], 0.002)[0]:
                for _, _, t, c, v in drv.EVENT.iter_unpack(os.read(cfd, 4096)):
                    if sent and t == drv.EV_ABS and c == 0 and v > 23000:
                        delays.append((time.monotonic() - sent) * 1000)
                        sent = None
    delays.sort()
    print(f"latency ms n={len(delays)} p50={delays[len(delays)//2]:.2f} max={delays[-1]:.2f} min={delays[0]:.2f}")
    helper.terminate()
    return 0


def drain(fd):
    while select.select([fd], [], [], 0)[0]:
        os.read(fd, 4096)


# ── chunk: trial_crash
def trial_crash():
    """Grabbed: the stand-in node must read nothing; helper SIGKILLed: it must read again."""
    out, node = trial_standin()
    helper = trial_helper(node)
    time.sleep(0.5)
    print("while grabbed, a second reader sees events:", trial_reads(node, out))
    helper.kill()
    helper.wait()
    time.sleep(0.2)
    print("after SIGKILL, a second reader sees events:", trial_reads(node, out))
    return 0


# ── chunk: trial_watchdog
def trial_watchdog():
    """The helper as a transient user unit with a 2 s watchdog; SIGSTOP it; time until the node reads again
    (grab released by systemd's kill) and until it is grabbed again (restart)."""
    out, node = trial_standin()
    unit = "pen-driver-trial"
    subprocess.run(["systemd-run", "--user", f"--unit={unit}", "-p", "Type=notify", "-p", "WatchdogSec=2",
                    "-p", "Restart=on-failure", "-p", "RestartSec=1", "-p", "NotifyAccess=main",
                    sys.executable, str(ROOT / "tablet-pen-driver.py"), "--force", "--source", node], check=True)
    time.sleep(2)
    pid = int(subprocess.run(["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"],
                             capture_output=True, text=True).stdout)
    print("grabbed before the hang, a second reader sees events:", trial_reads(node, out))
    os.kill(pid, signal.SIGSTOP)
    t0 = time.monotonic()
    back = None
    while time.monotonic() - t0 < 15:
        if trial_reads(node, out, 0.05):
            back = time.monotonic() - t0
            break
    print("hang to pen back (s):", None if back is None else f"{back:.2f}")
    time.sleep(3)
    print("after the restart, grabbed again (reader sees nothing):", not trial_reads(node, out))
    subprocess.run(["systemctl", "--user", "stop", unit])
    print("after the one-line rescue (systemctl --user stop), pen reads:", trial_reads(node, out))
    return 0


# ── chunk: trial_hop
def trial_hop(seconds=10):
    """250 frames a second for `seconds`: the extra hop (stand-in write to copy read, ms) and the helper's CPU share."""
    out, node = trial_standin()
    base = event_nodes()
    helper = trial_helper(node)
    time.sleep(1)
    cfd = os.open(trial_copy_node(base), os.O_RDONLY | os.O_NONBLOCK)
    tick_path = f"/proc/{helper.pid}/stat"
    cpu0 = sum(int(v) for v in Path(tick_path).read_text().split()[13:15])
    hops, start, n = [], time.monotonic(), 0
    while time.monotonic() - start < seconds:
        sent = time.monotonic()
        standin.standin_emit(out, [(drv.EV_ABS, 0, 20000 + 20 * (n & 1)), (drv.EV_ABS, 1, 10000 + 20 * (n & 1))])
        n += 1
        if select.select([cfd], [], [], 0.05)[0]:
            os.read(cfd, 4096)
            hops.append((time.monotonic() - sent) * 1000)
        time.sleep(max(0, 0.004 - (time.monotonic() - sent)))
    cpu = sum(int(v) for v in Path(tick_path).read_text().split()[13:15]) - cpu0
    hops.sort()
    print(f"hop ms n={len(hops)} p50={hops[len(hops)//2]:.3f} p99={hops[int(len(hops)*.99)]:.3f} max={hops[-1]:.3f}; helper cpu {cpu / os.sysconf('SC_CLK_TCK') / seconds * 100:.2f} %")
    helper.terminate()
    return 0


# ── chunk: trial_unplug
def trial_unplug():
    """The stand-in vanishes (a Bluetooth hang-up) and comes back: the helper must say 'pen gone', then grab again."""
    out, node = trial_standin()
    helper = trial_helper(node)
    os.close(out)
    print("helper after the unplug:", helper.stdout.readline().strip())
    time.sleep(0.5)
    out, node2 = trial_standin()
    time.sleep(3)
    print("pen came back as", node2, "(was", node + "); a second reader sees events:", trial_reads(node2, out))
    helper.terminate()
    return 0


if __name__ == "__main__":
    sys.exit({"latency": trial_latency, "crash": trial_crash, "watchdog": trial_watchdog, "hop": trial_hop, "unplug": trial_unplug}[sys.argv[1]]())
