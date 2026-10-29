#!/usr/bin/env python3
# test_pen_driver.py - rig for tablet-pen-driver.py: the arithmetic, the area parser, the node filter on a fake sysfs. No device, no uinput, no grab.
import importlib.util
import os
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append(label)
        print(f"FAIL {label}: got {got!r}, want {want!r}")


def load(sys_root):
    os.environ["PEN_DRIVER_SYS"] = str(sys_root)
    spec = importlib.util.spec_from_file_location("pen_driver", ROOT / "tablet-pen-driver.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_node(base, event, real_dir, name, vendor):
    dev = real_dir / event
    (dev / "id").mkdir(parents=True)
    (dev / "name").write_text(name + "\n")
    (dev / "id" / "vendor").write_text(vendor + "\n")
    (base / event).mkdir(parents=True)
    (base / event / "device").symlink_to(dev)


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    cls, phys, virt = tmp / "class", tmp / "devices" / "pci", tmp / "devices" / "virtual" / "input"
    fake_node(cls, "event3", phys, "Wacom Intuos Pro M Pen", "056a")
    fake_node(cls, "event4", virt, "Wacom Intuos Pro M Pen", "056a")      # our own copy: same name + ids
    fake_node(cls, "event5", phys, "Wacom Intuos Pro M Pad", "056a")
    fake_node(cls, "event6", phys, "Other Pen", "1234")
    uhid = tmp / "devices" / "virtual" / "misc" / "uhid" / "0005:056A:0360.0011" / "input"
    fake_node(cls, "event7", uhid, "Wacom Intuos Pro M Pen", "056a")      # the Bluetooth pen (BlueZ uhid)
    d = load(cls)

    check("node filter: the real pens (USB, Bluetooth uhid), never the virtual copy",
          [p.name for p in d.pen_nodes()], ["event3", "event7"])
    check("bits_of", d.bits_of(bytes([0b101, 0b10])), [0, 2, 9])
    check("full area is untouched", d.parse_area("0 0 1 1"), None)
    check("'full' keyword", d.parse_area("full"), None)
    check("small area", d.parse_area("0.25 0.5 0.36 0.36"), (0.25, 0.5, 0.36, 0.36))
    check("area clamped inside the screen", d.parse_area("0.9 0.9 0.36 0.36"), (0.64, 0.64, 0.36, 0.36))
    check("oversize area is full", d.parse_area("0 0 2 2"), None)
    try:
        d.parse_area("junk")
        check("junk raises ValueError", False, True)
    except ValueError:
        pass
    for bad in ("nan 0 0.3 0.3", "0 0 inf 0.3", "0.1 0.1 0 0.3", "0.1 0.1 0.3 -1"):   # Low 5b: a NaN crashed the helper at the next pen event
        try:
            check(f"{bad!r} raises ValueError", d.parse_area(bad), "ValueError")
        except ValueError:
            pass
    absinfo = {0: (0, 0, 44800, 4, 0, 200), 1: (0, 0, 29600, 4, 0, 200)}
    area = (0.25, 0.5, 0.36, 0.36)
    check("corner 0 lands on the area start", d.remap(0, 0, 44800, 0.25, 0.36), 11200)
    check("far corner lands on start+size", d.remap(44800, 0, 44800, 0.25, 0.36), round(0.61 * 44800))
    check("middle", d.remap(22400, 0, 44800, 0.25, 0.36), round(0.43 * 44800))
    check("y axis uses the y range", d.remap(29600, 0, 29600, 0.5, 0.36), round(0.86 * 29600))
    pack = lambda *ev: b"".join(d.EVENT.pack(0, 0, t, c, v) for t, c, v in ev)
    frame = pack((3, 0, 44800), (3, 1, 0), (3, 0x18, 4000), (0, 0, 0))
    check("area None is byte-identical", d.transform(frame, None, absinfo), frame)
    out = list(d.EVENT.iter_unpack(d.transform(frame, area, absinfo)))
    check("x remapped", out[0][4], round(0.61 * 44800))
    check("y remapped", out[1][4], round(0.5 * 29600))
    check("pressure untouched", out[2][4], 4000)
    check("SYN untouched", out[3][2:], (0, 0, 0))
    check("event count kept", len(out), 4)
    # worst case over a sweep: every output stays inside the copy's own range
    worst = max(d.remap(v, 0, 44800, 0.64, 0.36) for v in range(0, 44801, 7))
    check("sweep never leaves the range", worst <= 44800, True)
    # a pen leaving proximity reads 0,0: it maps to the area origin, a valid value
    check("zero at proximity out is valid", d.remap(0, 0, 44800, 0.25, 0.36) >= 0, True)
    # M3: a SYN_DROPPED drops up to the next SYN_REPORT, then the pen's whole state stands in (also across two reads)
    STATE_EV = pack((1, 0x140, 1), (3, 0, 777), (0, 0, 0))
    first, rest = pack((3, 0, 1), (0, 3, 0), (3, 1, 2)), pack((3, 1, 5), (0, 0, 0), (3, 0, 3), (0, 0, 0))
    out1, drop1 = d.splice(first, False, lambda: STATE_EV)
    out2, drop2 = d.splice(rest, drop1, lambda: STATE_EV)
    check("M3 SYN_DROPPED: the lost packet goes, the state stands in, the next packet follows",
          (out1, drop1, out2, drop2), (pack((3, 0, 1)), True, STATE_EV + pack((3, 0, 3), (0, 0, 0)), False))
    check("M3 a read with no drop passes byte for byte", d.splice(frame, False, lambda: b"x"), (frame, False))
    # Low 4: WATCHDOG=1 at most every PING/2, not once per pen report
    sent = []
    d.notify = sent.append
    for _ in range(200):
        d.beat()
    check("Low 4: 200 loop turns in a burst send one watchdog ping", len(sent), 1)

# checker (GATED dda420b): a pen that is present but cannot be taken must not keep the socket (the toggle would think the
# helper maps it, and KWin would never get the area). main() in-process, every outside effect stubbed: no socket, no device.
import signal  # noqa: E402
with tempfile.TemporaryDirectory() as td:
    d = load(Path(td) / "no-sys")
    d.RD, d.SOCK, d.LOCK = Path(td), Path(td) / "pen-driver.sock", Path(td) / "pen-driver.lock"
    node = Path(td) / "event3"
    node.write_text("")
    seen = []

    class FakeSock:
        def close(self):
            pass

    def busy(_source):
        raise OSError(16, "Device or resource busy")

    def one_idle(_seconds):
        seen.append(d.LIVE["sk"])
        d.STOP.append(1)

    d.control_socket, d.run, d.idle, d.notify = FakeSock, busy, one_idle, lambda _text: None
    argv, sys.argv = sys.argv, ["tablet-pen-driver.py", "--force", "--source", str(node)]
    try:
        d.main()
    finally:
        sys.argv = argv
        signal.set_wakeup_fd(-1)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
    check("a grab that fails closes the socket at once (the toggle maps the free pen through KWin)", seen, [None])

# the unit's ExecCondition is `tablet-pen-driver.py --check`: the same conf reader (THE line shape) as the helper itself
import subprocess  # noqa: E402
CASES = [("PEN_DRIVER=1\n", 0), ("PEN_DRIVER = 1  # trial\n", 0), ('PEN_DRIVER="1"\r\n', 0), ("# PEN_DRIVER=1\n", 1),
         ("PEN_DRIVER=1\nPEN_DRIVER=0\n", 1), ("PEN_DRIVER=0\n", 1), ("SCALE=0.3\n", 1), ("", 1)]
with tempfile.TemporaryDirectory() as td:
    for text, want in CASES:
        (Path(td) / "tabprec.conf").write_bytes(text.encode())
        rc = subprocess.run([sys.executable, str(ROOT / "tablet-pen-driver.py"), "--check"], capture_output=True, timeout=10,
                            env=dict(os.environ, XDG_CONFIG_HOME=td, XDG_RUNTIME_DIR=td, PEN_DRIVER_SYS=td + "/no-sys")).returncode   # never the live /sys
        check(f"--check on {text!r}", rc, want)
check("unit ExecCondition is --check", (ROOT / "tablet-pen-driver.service").read_text().count("ExecCondition=%h/.local/bin/tablet-pen-driver.py --check"), 1)

print(f"failures {len(FAILS)}")
sys.exit(1 if FAILS else 0)
