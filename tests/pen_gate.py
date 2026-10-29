#!/usr/bin/env python3
# pen_gate.py --bus usb|bt [--grab] [--out FILE] - VM ONLY (desktop user, session bus, Wayland): the pen-driver gate. One line per count.
# Writes the Intuos kcminputrc group BEFORE the stand-in exists, then reads KWin back. --grab runs the helper on the stand-in too.
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
PIDS = {"usb": 855, "bt": 864}
NAME, VENDOR = "Wacom Intuos Pro M Pen", 1386
CURVE = "0.9,0;1,0.3;"                      # a strong bow: the default is 0,0;1,1;
AREA = (0.25, 0.25, 0.5, 0.5)               # KWin's OutputArea: fractions of the screen
KEY_F9 = 0x01000038
RES = {}


def run(*cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# ── chunk: gate_rc
def gate_rc(product):
    """The Intuos's settings, written to kcminputrc before any device exists."""
    grp = ["--group", "Libinput", "--group", str(VENDOR), "--group", str(product), "--group", NAME]
    for key, val in (("TabletToolPressureCurve", CURVE), ("LeftHanded", "true"),
                     ("OutputArea", ",".join(str(v) for v in AREA))):
        RES[f"write {key}"] = run("kwriteconfig6", "--notify", "--file", "kcminputrc", *grp, "--key", key, val).returncode
    run("kwriteconfig6", "--notify", "--file", "kcminputrc", "--group", "ButtonRebinds", "--group", "TabletTool",
        "--group", NAME, "--key", "331", "Key,F9")


# ── chunk: gate_kwin
def gate_kwin(node):
    """KWin's InputDevice properties for an event node, as a dict of busctl text."""
    path = f"/org/kde/KWin/InputDevice/{node}"
    props = {}
    for prop in ("name", "vendor", "product", "tabletTool", "leftHanded", "pressureCurve", "outputArea",
                 "supportsPressureRange", "pressureRangeMin", "pressureRangeMax"):
        out = run("busctl", "--user", "get-property", "org.kde.KWin", path, "org.kde.KWin.InputDevice", prop)
        props[prop] = out.stdout.strip() or out.stderr.strip()[:80]
    return props


# ── chunk: gate_named
def gate_named():
    """sysnames of every KWin device that carries the pen's name."""
    out = run("busctl", "--user", "get-property", "org.kde.KWin", "/org/kde/KWin/InputDevice",
              "org.kde.KWin.InputDeviceManager", "devicesSysNames").stdout
    names = re.findall(r'"(event\d+)"', out)
    return [n for n in names if NAME in gate_kwin(n)["name"]]


# ── chunk: gate_expect
def gate_expect(fx, fy, area=None):
    """Where KWin must put tablet fraction (fx, fy): helper area first (copy range), then Left-handed
    (1 - g on both axes) and the OutputArea."""
    if area:
        fx, fy = area[0] + fx * area[2], area[1] + fy * area[3]
    gx, gy = 1 - fx, 1 - fy
    return AREA[0] + AREA[2] * gx, AREA[1] + AREA[3] * gy


# ── chunk: gate_drive
def gate_drive(fifo, probe_log, frames, wait=0.4):
    """Feed stand-in frames (x, y, pressure), return the probe's T lines that came after."""
    before = len(Path(probe_log).read_text().splitlines())
    with open(fifo, "w") as f:
        f.write("frame 22400 14800 0\nprox 1\nkey 330 1\n")   # position first, then tool in (BTN_TOOL_PEN), then touch (0x14a = 330)
        for x, y, p in frames:
            f.write(f"frame {x} {y} {p}\n")
    time.sleep(wait)
    return [ln.split() for ln in Path(probe_log).read_text().splitlines()[before:] if ln.startswith("T")]


# ── chunk: gate_main
def gate_main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bus", choices=PIDS, required=True)
    ap.add_argument("--grab", action="store_true")
    ap.add_argument("--out", default="gate-result.json")
    args = ap.parse_args()
    product, work = PIDS[args.bus], Path(f"/tmp/pen-gate-{args.bus}")
    work.mkdir(exist_ok=True)
    fifo, log = str(work / "cmd.fifo"), str(work / "probe.log")
    gate_rc(product)
    probe = subprocess.Popen([PY, str(ROOT / "tests/pen_probe.py"), log, "90"])
    time.sleep(4)
    stand = subprocess.Popen([PY, str(ROOT / "tests/pen_standin.py"), "--bus", args.bus, "--fifo", fifo],
                             stdout=subprocess.PIPE, text=True)
    node = stand.stdout.readline().strip().rsplit("/", 1)[-1]
    time.sleep(2)
    time.sleep(1)
    RES["standin node"] = node
    tag = run("udevadm", "info", "--query=property", f"/dev/input/{node}").stdout
    RES["udev ID_INPUT_TABLET"] = "ID_INPUT_TABLET=1" in tag
    RES["libwacom lists the stand-in"] = NAME.replace(" Pen", "") in run("libwacom-list-local-devices").stdout
    RES["kcminputrc groups with the name"] = Path("~/.config/kcminputrc").expanduser().read_text().count(f"[{NAME}]")
    RES["kwin standin"] = gate_kwin(node)
    pts = gate_drive(fifo, log, [(0, 0, 0), (44800, 29600, 0), (22400, 14800, 0), (22400, 14800, 4096)])
    RES["probe T lines"] = pts
    RES["expected corners"] = [gate_expect(0, 0), gate_expect(1, 1), gate_expect(0.5, 0.5)]
    stand_keys = gate_drive(fifo, log, [])               # touch is down; now the side button
    with open(fifo, "w") as f:
        f.write("key 331 1\nkey 331 0\n")
    time.sleep(0.6)
    RES["F9 key presses"] = sum(1 for ln in Path(log).read_text().splitlines() if ln.startswith(f"K {KEY_F9:#x}"))
    if args.grab:
        helper = subprocess.Popen([PY, str(ROOT / "tablet-pen-driver.py"), "--force", "--source", f"/dev/input/{node}"],
                                  stdout=subprocess.PIPE, text=True)
        RES["helper line"] = helper.stdout.readline().strip()
        time.sleep(3)
        named = gate_named()
        RES["kwin devices with the name"] = {n: gate_kwin(n) for n in named}
        RES["kcminputrc groups after copy"] = Path("~/.config/kcminputrc").expanduser().read_text().count(f"[{NAME}]")
        pts = gate_drive(fifo, log, [(0, 0, 0), (44800, 29600, 0), (22400, 14800, 4096)])
        RES["probe T lines via copy"] = pts
        harea = (0.2, 0.3, 0.4, 0.4)                       # precision mode as arithmetic in the helper
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.sendto(" ".join(str(v) for v in harea).encode(), str(Path(os.environ["XDG_RUNTIME_DIR"]) / "tabprec/pen-driver.sock"))
        time.sleep(0.3)
        RES["probe T lines, helper area"] = gate_drive(fifo, log, [(0, 0, 0), (44800, 29600, 0), (22400, 14800, 0)])
        RES["expected, helper area"] = [gate_expect(0, 0, harea), gate_expect(1, 1, harea), gate_expect(0.5, 0.5, harea)]
        helper.terminate()
    with open(fifo, "w") as f:
        f.write("quit\n")
    probe.wait(timeout=100)
    Path(args.out).write_text(json.dumps(RES, indent=1, default=str))
    print(json.dumps(RES, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(gate_main())
