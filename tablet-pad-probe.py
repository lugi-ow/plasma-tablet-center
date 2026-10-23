#!/usr/bin/env python3
# tablet-pad-probe.py [--all] - print every byte that changes in the tablet's raw HID reports (finds the touch and press bytes).
# Part of plasma-wacom-center (MIT). Structure and contracts: PROJECT_MAP.md; mechanisms: TECHNICAL.md.
import os
import re
import select
import sys
import time

# ── chunk: quiet-reports
BT_PEN_REGION_END = 270       # Bluetooth report 0x80: pen frames live below this
BT_QUIET_REPORTS = {0x81}     # Bluetooth touch report: skipped unless --all
USB_QUIET_REPORTS = {0x10}    # USB pen report (continuous in proximity): skipped unless --all
BUS = {"0003": "usb", "0005": "bt"}


# ── chunk: wacom_nodes
def wacom_nodes():
    """[(path, bus, name)] for every Wacom hidraw node (vendor 056A)."""
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
        fields = dict(line.split("=", 1) for line in uevent.splitlines() if "=" in line)
        match = re.match(r"^(\d{4}):0000056A:", fields.get("HID_ID", "").upper())
        if match:
            found.append((f"/dev/{name}", BUS.get(match.group(1), match.group(1)),
                          fields.get("HID_NAME", "?")))
    return found


# ── chunk: fmt
def fmt(value):
    return f"0x{value:02x} {value:08b}"


# ── chunk: main
def main():
    show_all = "--all" in sys.argv
    nodes = wacom_nodes()
    if not nodes:
        sys.exit("no Wacom hidraw device (tablet asleep or not connected)")
    fds = {}
    for path, bus, name in nodes:
        try:
            fds[os.open(path, os.O_RDONLY)] = (path, bus)
        except PermissionError:
            sys.exit(f"{path}: permission denied - add the udev rule install.sh prints")
        print(f"# {path}: {name} ({bus})", file=sys.stderr)
    # Protocol: the pen away from the tablet; rest a finger on ONE key, hold, lift; repeat per key; then press
    # a key. Each line: time, node, bus, report id, [byte] old -> new (hex + binary).
    print("# rest a finger on a key WITHOUT pressing, then lift; Ctrl+C ends", file=sys.stderr)
    last = {}
    t0 = time.monotonic()
    while fds:
        ready, _, _ = select.select(list(fds), [], [])
        for fd in ready:
            path, bus = fds[fd]
            try:
                report = os.read(fd, 4096)
            except OSError:
                report = b""
            if not report:
                print(f"# {path} gone", file=sys.stderr)
                os.close(fd)
                del fds[fd]
                continue
            rid = report[0]
            quiet = BT_QUIET_REPORTS if bus == "bt" else USB_QUIET_REPORTS
            if rid in quiet and not show_all:
                continue
            prev = last.get((fd, rid))
            last[(fd, rid)] = report
            stamp = f"{time.monotonic() - t0:8.3f}  {os.path.basename(path)} {bus}  id=0x{rid:02x}"
            if prev is None:
                print(f"{stamp}  first report, {len(report)} bytes")
                continue
            changes = []
            for i in range(min(len(prev), len(report))):
                if prev[i] == report[i]:
                    continue
                if not show_all and bus == "bt" and rid == 0x80 and 0 < i < BT_PEN_REGION_END:
                    continue
                changes.append(f"[{i}] {fmt(prev[i])} -> {fmt(report[i])}")
            if changes:
                print(f"{stamp}  " + "   ".join(changes))
                sys.stdout.flush()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
