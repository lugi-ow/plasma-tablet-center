#!/usr/bin/env python3
# tablet-overlay.py X Y W H [DIM] [--waiting] [--follow | --fifo PATH] - the dim-around overlay of precision mode.
# Part of plasma-wacom-center (MIT). Structure and contracts: PROJECT_MAP.md; mechanisms: TECHNICAL.md.
import os
import stat
import sys

import PyQt6.QtQuick  # noqa: F401  (the root object is then seen as a QQuickWindow)
from PyQt6.QtCore import QSocketNotifier, QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlApplicationEngine

# ── chunk: args
USAGE = "usage: tablet-overlay.py X Y W H [DIM] [--waiting] [--follow | --fifo PATH]"
args = sys.argv[1:]
fifo = None
if "--fifo" in args:
    at = args.index("--fifo")
    if at + 1 >= len(args):
        sys.exit(USAGE)
    fifo = args[at + 1]
    del args[at:at + 2]
follow = "--follow" in args
waiting = "--waiting" in args
args = [a for a in args if a not in ("--follow", "--waiting")]
if len(args) not in (4, 5):
    sys.exit(USAGE)

# ── chunk: window
app = QGuiApplication(sys.argv[:1])
# app_id for a normal toplevel; inert here - tablet-overlay.qml is a layer-shell
# surface, which carries none, so KWin reports the interpreter name instead.
app.setDesktopFileName("wacom-center")
engine = QQmlApplicationEngine()
qml = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tablet-overlay.qml")
engine.load(QUrl.fromLocalFile(qml))
if not engine.rootObjects():
    sys.exit(1)
root = engine.rootObjects()[0]
NAMES = ("px", "py", "pw", "ph")


# ── chunk: place
def place(values, dim=None):
    """Move the clear rectangle to X Y W H (ints); DIM changes the bands' alpha."""
    for name, value in zip(NAMES, values):
        root.setProperty(name, value)
    if dim is not None:
        root.setProperty("dimval", dim)


place([int(a) for a in args[:4]], float(args[4]) if len(args) == 5 else None)   # no DIM: the QML default
root.setProperty("waiting", waiting)

# ── chunk: input-source
if follow or fifo:
    if fifo:
        try:
            if not stat.S_ISFIFO(os.stat(fifo).st_mode):
                os.remove(fifo)                # something else took the name
        except FileNotFoundError:
            pass
        if not os.path.exists(fifo):
            os.makedirs(os.path.dirname(fifo) or ".", exist_ok=True)
            os.mkfifo(fifo, 0o600)
        source = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)   # RDWR: no EOF when the last writer leaves
    else:
        source = 0
        os.set_blocking(0, False)
    pending = b""

    # ── chunk: on_input
    def on_input():
        global pending
        try:
            chunk = os.read(source, 4096)
        except BlockingIOError:
            return
        if not chunk:                      # stdin writer gone: the ghost goes with it
            app.quit()
            return
        pending += chunk
        *lines, pending = pending.split(b"\n")
        for line in lines:                 # in order: the last geometry and the last border word win
            parts = line.split()
            if parts in ([b"waiting"], [b"solid"]):
                root.setProperty("waiting", parts == [b"waiting"])
                continue
            if len(parts) not in (4, 5):
                continue                   # anything else is ignored
            try:
                values = [int(p) for p in parts[:4]]
                dim = float(parts[4]) if len(parts) == 5 else None
            except ValueError:
                continue
            place(values, dim)

    notifier = QSocketNotifier(source, QSocketNotifier.Type.Read)
    notifier.activated.connect(on_input)

sys.exit(app.exec())
