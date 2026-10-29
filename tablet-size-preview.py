#!/usr/bin/env python3
# tablet-size-preview.py - the centred, fading preview of the precision area size (a ring tick with precision mode off).
# Part of plasma-tablet-center (MIT). Structure and contracts: PROJECT_MAP.md; mechanisms: TECHNICAL.md.
import os
import subprocess
import sys
from pathlib import Path

import PyQt6.QtQuick  # noqa: F401  (the root object is then seen as a QQuickWindow)
from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlApplicationEngine

CONF = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "tabprec.conf"
STATE = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "tabprec" / "saved-area"   # exists while precision mode is ON
HOLD_MS = 1200
FADE_MS = 450


# ── chunk: tablet_aspect
def tablet_aspect():
    """Width/height of the pen tablet from KWin's device list; 1.6 fallback."""
    kw, mgr, iface = "org.kde.KWin", "/org/kde/KWin/InputDevice", "org.kde.KWin.InputDevice"
    try:
        out = subprocess.run(
            ["busctl", "--user", "get-property", kw, mgr,
             "org.kde.KWin.InputDeviceManager", "devicesSysNames"],
            capture_output=True, text=True, timeout=3).stdout
        for sysname in out.replace('"', " ").split()[2:]:
            tool = subprocess.run(
                ["busctl", "--user", "get-property", kw, f"{mgr}/{sysname}",
                 iface, "tabletTool"], capture_output=True, text=True, timeout=3).stdout
            if "true" not in tool:
                continue
            size = subprocess.run(
                ["busctl", "--user", "get-property", kw, f"{mgr}/{sysname}",
                 iface, "size"], capture_output=True, text=True, timeout=3).stdout.split()
            if len(size) >= 3 and float(size[2]) > 0:
                return float(size[1]) / float(size[2])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return 1.6


# ── chunk: read_conf
def read_conf():
    """(SCALE, DIM) from the conf, clamped the way the toggle clamps them: SCALE to the soft cap
    (conf SCALE_MIN / SCALE_MAX, 0 = none on that side), then to the screen width."""
    scale, dim, lo, hi = 0.36, 0.10, 0.05, 0.80
    try:
        for line in CONF.read_text().splitlines():
            key, _, val = line.partition("=")
            try:
                if key.strip() == "SCALE":
                    scale = float(val)
                elif key.strip() == "SCALE_MIN":
                    lo = float(val)
                elif key.strip() == "SCALE_MAX":
                    hi = float(val)
                elif key.strip() == "DIM":
                    dim = max(0.0, min(1.0, float(val)))
            except ValueError:
                pass
    except OSError:
        pass
    if hi > 0:
        scale = min(scale, hi)
    if lo > 0:
        scale = max(scale, lo)
    return min(scale, 1.0), dim


# ── chunk: window
app = QGuiApplication(sys.argv)
# app_id for a normal toplevel; inert here - tablet-overlay.qml is a layer-shell
# surface, which carries none, so KWin reports the interpreter name instead.
app.setDesktopFileName("tablet-center")
screen = app.primaryScreen().size()
ASPECT = tablet_aspect()
engine = QQmlApplicationEngine()
engine.load(QUrl.fromLocalFile(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "tablet-overlay.qml")))
if not engine.rootObjects():
    sys.exit(1)
root = engine.rootObjects()[0]
root.setProperty("waiting", True)

last_mtime = 0.0


# ── chunk: apply_scale
def apply_scale():
    """Size the rectangle from the conf's SCALE, centre it on the screen, show it."""
    scale, dim = read_conf()
    w = round(screen.width() * scale)
    h = round(w / ASPECT)
    if h > screen.height():
        h = screen.height()
        w = round(h * ASPECT)
    for name, value in (("px", (screen.width() - w) // 2), ("py", (screen.height() - h) // 2),
                        ("pw", w), ("ph", h), ("dimval", dim), ("shown", True)):
        root.setProperty(name, value)


# ── chunk: tick
def tick():
    global last_mtime
    if STATE.exists():                 # precision mode came on: its own overlay shows the size from now on
        hold.stop()
        if root.property("shown"):
            start_fade()
        return
    try:
        mtime = CONF.stat().st_mtime
    except OSError:
        mtime = last_mtime
    if mtime != last_mtime:
        last_mtime = mtime
        quit_timer.stop()  # a tick mid-fade cancels the pending exit
        apply_scale()
        hold.start(HOLD_MS)


# ── chunk: start_fade
def start_fade():
    root.setProperty("shown", False)
    quit_timer.start(FADE_MS + 200)


# ── chunk: timers
quit_timer = QTimer()
quit_timer.setSingleShot(True)
quit_timer.timeout.connect(lambda: app.quit())
hold = QTimer()
hold.setSingleShot(True)
hold.timeout.connect(start_fade)
poll = QTimer()
poll.timeout.connect(tick)
poll.start(100)

# ── chunk: first-show
try:
    last_mtime = CONF.stat().st_mtime
except OSError:
    pass
apply_scale()
hold.start(HOLD_MS)
sys.exit(app.exec())
