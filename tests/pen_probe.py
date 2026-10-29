#!/usr/bin/env python3
# pen_probe.py OUTFILE [SECONDS] - VM ONLY: a fullscreen Qt window that logs what KWin hands a client: tablet events (screen fraction, pressure) and key presses.
# One line each: "T x y pressure" (x, y = fractions of the window) or "K name". Needs a Wayland session; run it as the desktop user.
import sys
import time

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QWidget


# ── chunk: probe_window
class Probe(QWidget):
    def __init__(self, out):
        super().__init__()
        self.out = open(out, "w", buffering=1)
        self.setWindowTitle("pen-probe")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def event(self, ev):
        if ev.type() in (QEvent.Type.TabletMove, QEvent.Type.TabletPress, QEvent.Type.TabletRelease):
            pos = ev.position()
            self.out.write(f"T {pos.x() / self.width():.5f} {pos.y() / self.height():.5f} {ev.pressure():.5f} {time.monotonic():.4f}\n")
            return True
        if ev.type() == QEvent.Type.KeyPress:
            self.out.write(f"K {ev.key():#x} {time.monotonic():.4f}\n")
            return True
        return super().event(ev)


# ── chunk: probe_main
def probe_main():
    app = QApplication(sys.argv)
    win = Probe(sys.argv[1])
    win.showFullScreen()
    QTimer.singleShot(int(float(sys.argv[2] if len(sys.argv) > 2 else 120) * 1000), app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(probe_main())
