#!/usr/bin/env python3
# pen_standin.py --bus usb|bt --fifo PATH - VM ONLY: a uinput stand-in for the Intuos Pro M pen. Never run on the host.
# Commands, one per line, in the FIFO: frame X Y P | prox 0|1 | key CODE 0|1 | quit. Prints the node it made.
# Caps are the kernel driver's pen set as read from wacom_wac.c (the real pen was not connected tonight): X 0..44800
# Y 0..29600 res 200, pressure 0..8191, distance 0..63, tilt -64..63 res 57; USB adds BTN_STYLUS3 (329).
import argparse
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("pen_driver", ROOT / "tablet-pen-driver.py")
drv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drv)

BUS = {"usb": (0x03, 0x0357), "bt": (0x05, 0x0360)}
TOOL_PEN, BTN_TOUCH, STYLUS, STYLUS2, STYLUS3 = 0x140, 0x14A, 0x14B, 0x14C, 0x149
ABS_PRESSURE, ABS_DISTANCE, ABS_TILT_X, ABS_TILT_Y, ABS_MISC = 0x18, 0x19, 0x1A, 0x1B, 0x28


# ── chunk: standin_caps
def standin_caps(bus):
    """The (ident, name, types, absinfo, props) tuple make_copy() takes."""
    keys = [0x149 if bus == "usb" else None, TOOL_PEN, 0x141, BTN_TOUCH, STYLUS, STYLUS2]
    absinfo = {0: (0, 0, 44800, 4, 0, 200), 1: (0, 0, 29600, 4, 0, 200),
               ABS_PRESSURE: (0, 0, 8191, 0, 0, 0), ABS_DISTANCE: (0, 0, 63, 1, 0, 0),
               ABS_TILT_X: (0, -64, 63, 1, 0, 57), ABS_TILT_Y: (0, -64, 63, 1, 0, 57),
               ABS_MISC: (0, 0, 0, 0, 0, 0)}
    return ((*BUS[bus][:1], 0x056A, BUS[bus][1], 0x0100), b"Wacom Intuos Pro M Pen",
            {drv.EV_KEY: sorted(k for k in keys if k), drv.EV_ABS: sorted(absinfo), drv.EV_MSC: [0]},
            absinfo, [0])                      # INPUT_PROP_POINTER: an external tablet, not a screen


# ── chunk: standin_emit
def standin_emit(fd, events):
    """One frame: the (type, code, value) list plus SYN_REPORT."""
    data = b"".join(drv.EVENT.pack(0, 0, t, c, v) for t, c, v in events)
    os.write(fd, data + drv.EVENT.pack(0, 0, drv.EV_SYN, 0, 0))


# ── chunk: standin_main
def standin_main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bus", choices=BUS, required=True)
    ap.add_argument("--fifo", required=True)
    args = ap.parse_args()
    fd, sysname = drv.make_copy(*standin_caps(args.bus))
    node = [n for n in os.listdir(f"/sys/devices/virtual/input/{sysname}") if n.startswith("event")][0]
    print(f"/dev/input/{node}", flush=True)
    if not os.path.exists(args.fifo):
        os.mkfifo(args.fifo)
    while True:
        with open(args.fifo) as fifo:
            for line in fifo:
                cmd = line.split()
                if not cmd:
                    continue
                if cmd[0] == "frame":
                    x, y, p = (int(v) for v in cmd[1:4])
                    standin_emit(fd, [(drv.EV_ABS, 0, x), (drv.EV_ABS, 1, y), (drv.EV_ABS, ABS_PRESSURE, p)])
                elif cmd[0] == "prox":
                    standin_emit(fd, [(drv.EV_KEY, TOOL_PEN, int(cmd[1]))])
                elif cmd[0] == "key":
                    standin_emit(fd, [(drv.EV_KEY, int(cmd[1]), int(cmd[2]))])
                elif cmd[0] == "quit":
                    os.close(fd)
                    return 0


if __name__ == "__main__":
    sys.exit(standin_main())
