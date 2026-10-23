#!/usr/bin/env python3
# wacom_center.py - Wacom Center: one settings window (Precision, Pad buttons, Pen, Profiles).
# Part of plasma-wacom-center (MIT). Structure and contracts: PROJECT_MAP.md; mechanisms: TECHNICAL.md.
import base64
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

from PyQt6.QtCore import (QBuffer, QIODevice, QLocale, QPointF,
                          QRectF, QSize, Qt, QTimer, pyqtSignal)
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtGui import (QColor, QFontMetrics, QGuiApplication, QIcon, QImage, QImageReader,
                         QKeySequence, QPainter, QPainterPath, QPalette, QPen, QPixmap)
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QMenu, QMessageBox, QPushButton, QRadioButton, QSlider,
                             QSpinBox, QTabWidget, QToolButton, QVBoxLayout, QWidget,
                             QWidgetAction)

import wacom_profiles

# ── chunk: paths
CONF = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "tabprec.conf"
TOGGLE = Path.home() / ".local/bin/tablet-precision.sh"
MODIFIER_ONLY = {"Shift", "Control", "Ctrl", "Alt", "Meta"}
RING_DEFAULT = ("Meta+Shift+F10", "Meta+Shift+F9", 600)   # bigger, smaller, KWin threshold = degrees x 120: every 5-degree step
KW, MGR, IF = "org.kde.KWin", "/org/kde/KWin/InputDevice", "org.kde.KWin.InputDevice"
AMBER = "#e6a817"                        # the overlay border's amber: the waiting look
RED = "#da4453"                          # Breeze negative: a row consumed by precision mode
PIE_TIP = ("A pie key opens a Kando menu at the pen.\n"
           "1. In Kando, give the menu a shortcut.\n"
           "2. Type the same shortcut here, in Touch or in Press.\n"
           "3. Tick the matching side: left box = Touch, right = Press.\n"
           "4. Click Apply bindings.\n"
           "The toolkit moves the mouse to the pen, then presses the\n"
           "shortcut, so the menu opens at the pen. A touch pie opens on\n"
           "the rest; lift the finger on a slice to select it.\n"
           "A pie shortcut is F-keys plus modifiers; use each once.")
TP_TIP = ("Pie on the touch: the Touch shortcut opens a Kando menu.\n"
          "A full chord (modifiers plus one F-key). The key's raw\n"
          "press is swallowed.")
PP_TIP = ("Pie on the press: the Press shortcut opens a Kando menu.\n"
          "Stored as Disabled in kcminputrc plus CHORD in the conf.")
REG_TIP = ("Seconds a finger must rest on THIS key before its touch\n"
           "action starts - the touch chord, or the precision key's\n"
           "area drag. 0 = at once.")
SHORTCUT_TIP = ("The shortcut a pad key presses.\n"
                "An empty box gives the raw key to the focused program.\n"
                "Letters and digits fail when a non-Latin layout is active.\n"
                "F-keys and modifiers always work.")
PEN_TIP = ("The shortcut this pen button presses.\n"
           "An empty box gives the raw button to the focused program.\n"
           "Letters and digits fail when a non-Latin layout is active.\n"
           "F-keys and modifiers always work.")
TOUCH_TIP = ("A touch chord is held while a finger RESTS on the key.\n"
             "It engages after the key's Touch register and releases at\n"
             "the lift. Bare modifiers are allowed - Ctrl for Blender's\n"
             "sculpt - but not Meta alone (that is the launcher tap).\n"
             "A press within the register wins: no chord for that contact.\n"
             "Needs Wacom keys with a touch sensor (Intuos Pro and kin).")
RING_TIP = ("The shortcut for one turn direction.\n"
            "The mode with the two precision-size shortcuts controls the area size.\n"
            "The Precision tab sets that mode's tick angle and direction.\n"
            "Letters and digits fail when a non-Latin layout is active;\n"
            "F-keys and modifiers always work.")
PAD_TIP = ("The keys as printed on the tablet, top to bottom.\n"
           "On a tablet rotated 180 degrees the keys sit on the right\n"
           "and this table reads bottom-up; the key numbers do not move.")
PRM_TIP = ("One key toggles precision mode.\n"
           "Tick it: Apply gives the key the toggle shortcut, the ghost\n"
           "and the area drag. Red outlines = fields the mode consumes.\n"
           "Moving the tick moves all of it. No tick = no ghost, no drag;\n"
           "also clear the Press box to take the toggle off the pad.")


# ── chunk: busget
def busget(path, prop):
    try:
        return subprocess.run(
            ["busctl", "--user", "get-property", KW, path,
             IF if path != MGR else "org.kde.KWin.InputDeviceManager", prop],
            capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


# ── chunk: detect_devices
def detect_devices():
    """Return (pad_name, pen_name, pen_sysname, aspect) from KWin's live
    device list. Saves pad/pen into profiles.ini [Device] when found - the
    asleep-pad and disconnected-pen fallback chains (wacom_profiles) read
    that but never write it, so a stale recorded name never re-saves itself."""
    pad_name, pen_name, pen_sysname, aspect = None, None, None, 1.6
    for sysname in busget(MGR, "devicesSysNames").replace('"', " ").split()[2:]:
        path = f"{MGR}/{sysname}"
        if pad_name is None and "true" in busget(path, "tabletPad"):
            out = busget(path, "name").split('"')
            if len(out) >= 2:
                pad_name = out[1]
        if "true" in busget(path, "tabletTool"):
            size = busget(path, "size").split()
            if len(size) >= 3 and float(size[2]) > 0:
                aspect = float(size[1]) / float(size[2])
            if pen_name is None:
                out = busget(path, "name").split('"')
                if len(out) >= 2:
                    pen_name, pen_sysname = out[1], sysname
    if pad_name or pen_name:
        wacom_profiles.save_device(pad_name, pen_name)
    return pad_name, pen_name, pen_sysname, aspect


# ── chunk: read_conf
def read_conf():
    values = {"SCALE": 0.36, "SCALE_MIN": 0.05, "SCALE_MAX": 0.80, "DIM": 0.10, "HOLD": 0.15, "LONG": 0.7, "RING_STEP": 0.5, "RECONNECT": 60.0}
    try:
        lines = CONF.read_text().splitlines()
    except OSError:
        return values
    for line in lines:
        key, _, val = line.partition("=")
        if key.strip() not in values:
            continue
        try:                                       # per line, and comments stripped like conf_value:
            values[key.strip()] = float(val.split("#", 1)[0].strip())   # one bad line must not revert
        except ValueError:                                              # every other setting to default
            continue
    return values


# ── chunk: save_conf_key
def save_conf_key(key, value):
    """Set key=value in tabprec.conf under $RD/conf.lock, atomically (see
    wacom_profiles). A lock not taken within 5 s writes NOTHING and raises -
    the GUI shows "The settings file is busy. Try again." and a switch stops
    through its step-fails path."""
    try:
        wacom_profiles.save_conf_key(key, value)
    except TimeoutError:
        QMessageBox.warning(None, "Wacom Center", "The settings file is busy. Try again.")
        raise


# ── chunk: drop_conf_key
def drop_conf_key(key):
    """Remove key from tabprec.conf, keeping every other line (see wacom_profiles)."""
    try:
        wacom_profiles.drop_conf_key(key)
    except TimeoutError:
        QMessageBox.warning(None, "Wacom Center", "The settings file is busy. Try again.")
        raise


# ── chunk: conf_value
def conf_value(key):
    """One raw conf value (comments stripped), or '' when the key is absent."""
    try:
        for line in CONF.read_text().splitlines():
            k, _, val = line.partition("=")
            if k.strip() == key:
                return val.split("#", 1)[0].strip()
    except OSError:
        pass
    return ""


# ── chunk: poke_daemon
def poke_daemon():
    """One line into the daemon's control pipe: re-read the conf now. False
    when no process reads the pipe (no daemon, or not open yet)."""
    return wacom_profiles.poke_daemon()


# ── chunk: kread
def kread(pad, idx):
    """The raw kcminputrc value: Key,<seq> -> <seq> for the editable Press
    box, Disabled, empty, or the full raw text of any other form (MouseButton,
    TabletToolButton, Scroll, ...) - the caller shows those read-only, never
    silently drops them (that used to delete the binding on Apply). The
    toggle's shortcut and the daemon's chord alphabet come from wacom_profiles
    (toggle_chord, load_parse_chord): one copy of each."""
    out = wacom_profiles.kreadconfig6(["ButtonRebinds", "Tablet", pad], str(idx))
    return out.removeprefix("Key,") if out.startswith("Key,") else out


# ── chunk: is_raw_form
def is_raw_form(value):
    """A binding kread returns as-is (MouseButton,n[,n], TabletToolButton,n,
    Scroll, ...): the Pad tab's Press box shows it read-only - blanking an
    untouched box used to delete it on Apply."""
    return value not in ("", "Disabled") and ("," in value or value == "Scroll")


# ── chunk: kwrite
def kwrite(pad, idx, seq):
    """A chord, the literal Disabled (KWin swallows the key - what a
    CHORD_<n> key needs: a DELETED entry falls through to the focused
    app), or empty = delete."""
    value = (["--delete"] if not seq else
             ["Disabled"] if seq.lower() == "disabled" else [f"Key,{seq}"])
    subprocess.run(
        ["kwriteconfig6", "--notify", "--file", "kcminputrc", "--group",
         "ButtonRebinds", "--group", "Tablet", "--group", pad,
         "--key", str(idx)] + value,
        check=True)


# ── chunk: kread_group
def kread_group(groups, key):
    """Like kread, for an arbitrary kcminputrc group path (the Pen tab's
    [ButtonRebinds][TabletTool][<pen>])."""
    out = wacom_profiles.kreadconfig6(groups, str(key))
    return out.removeprefix("Key,") if out.startswith("Key,") else out


# ── chunk: kwrite_group
def kwrite_group(groups, key, seq):
    """Like kwrite, for an arbitrary kcminputrc group path."""
    value = (["--delete"] if not seq else
             ["Disabled"] if seq.lower() == "disabled" else [f"Key,{seq}"])
    args = ["kwriteconfig6", "--notify", "--file", "kcminputrc"]
    for g in groups:
        args += ["--group", str(g)]
    args += ["--key", str(key)] + value
    subprocess.run(args, check=True)


# ── chunk: ring_read
def ring_read(pad, mode=0):
    """(bigger chord, smaller chord, degrees per tick) of one ring mode's
    binding in kcminputrc (KWin groups 0-3), or None when that mode is
    unbound. KWin stores degrees x 120."""
    if pad:
        out = wacom_profiles.kreadconfig6(["ButtonRebinds", "TabletRing", pad, str(mode)], "0").split(",")
        if len(out) == 4 and out[0] == "AxisKey":
            try:
                return out[1], out[2], int(out[3]) / 120
            except ValueError:
                pass
    return None


# ── chunk: ring_write
def ring_write(pad, up, down, degrees, mode=0):
    """Bind one ring mode: UP fires every DEGREES of travel one way, DOWN the
    other way. Both chords empty = the mode unbound. KWin compares its
    threshold with degrees x 120 and the ring reports 5-degree steps, so 5
    is the finest tick."""
    if not pad:
        return
    if not up and not down:
        subprocess.run(
            ["kwriteconfig6", "--notify", "--file", "kcminputrc", "--group", "ButtonRebinds",
             "--group", "TabletRing", "--group", pad, "--group", str(mode), "--key", "0",
             "--delete"], check=True)
    else:
        subprocess.run(
            ["kwriteconfig6", "--notify", "--file", "kcminputrc", "--group", "ButtonRebinds",
             "--group", "TabletRing", "--group", pad, "--group", str(mode), "--key", "0",
             f"AxisKey,{up},{down},{max(1, round(degrees * 120))}"], check=True)


# ── chunk: precision_ring_mode
def precision_ring_mode(pad):
    """(mode, binding) of the ring mode that carries the two precision-size
    shortcuts, wherever the Pad tab moved them; (0, the defaults) when no
    mode has the pair. The Precision tab's angle and direction go there."""
    pair = {RING_DEFAULT[0], RING_DEFAULT[1]}
    for mode in range(4):
        bound = ring_read(pad, mode)
        if bound and {bound[0], bound[1]} == pair:
            return mode, bound
    return 0, (RING_DEFAULT[0], RING_DEFAULT[1], RING_DEFAULT[2] / 120)


# ── chunk: elided
def elided(widget, text, px):
    """text cut in the middle to px pixels in widget's font: a long profile
    name must not widen the window. The caller keeps the whole name in the
    tooltip."""
    return QFontMetrics(widget.font()).elidedText(text, Qt.TextElideMode.ElideMiddle, px)


# ── chunk: DotSpinBox
class DotSpinBox(QDoubleSpinBox):
    """A decimal box pinned to the C locale (the conf's dot decimal, whatever
    the desktop locale) that reads a typed comma as the decimal point: in
    that locale the comma is the GROUP separator, so 0,5 read as 5. The
    validator and the text-to-value step both see the comma as a dot, and
    the box shows the dot."""

    def __init__(self):
        super().__init__()
        self.setLocale(QLocale.c())

    def validate(self, text, pos):
        if text == self.specialValueText():     # "the default, 0.5 points": the comma there is prose
            return super().validate(text, pos)
        return super().validate(text.replace(",", "."), pos)

    def valueFromText(self, text):
        if text == self.specialValueText():
            return super().valueFromText(text)
        return super().valueFromText(text.replace(",", "."))


# ── chunk: PrecisionTab
class PrecisionTab(QWidget):
    def __init__(self, aspect, pad):
        super().__init__()
        self.aspect = aspect
        self.pad = pad
        conf = read_conf()
        screen = QGuiApplication.primaryScreen()
        self.sw = screen.size().width() if screen else 0
        self.sh = screen.size().height() if screen else 0
        if self.sw < 1 or self.sh < 1:      # no output (the monitor asleep): Qt hands out a 0x0 placeholder screen
            self.sw, self.sh = 1920, 1080   # a plausible desktop, so the labels still read; the conf keeps a fraction, never these pixels

        layout = QVBoxLayout(self)
        self.profile_label = QLabel()
        layout.addWidget(self.profile_label)
        self.size_label = QLabel()
        self.size = QSlider(Qt.Orientation.Horizontal)
        self.size_min = self.percent_field(conf["SCALE_MIN"])
        self.size_max = self.percent_field(conf["SCALE_MAX"])
        self.limit_note = QLabel(self)               # shown only while Smallest > Largest (limit_size); the parent
                                                     # now: shown parentless, it would open as its own window
        self.limit_note.setWordWrap(True)
        self.limit_size()
        self.size.setValue(round(conf["SCALE"] * 100))
        limit_row = QHBoxLayout()
        limit_row.addWidget(QLabel("Smallest size:"))
        limit_row.addWidget(self.size_min)
        limit_row.addWidget(QLabel("Largest size:"))
        limit_row.addWidget(self.size_max)
        limit_row.addWidget(QLabel("(of screen width, for the slider and the ring; 0 = no limit)"))
        limit_row.addStretch()
        self.dim_label = QLabel()
        self.dim = QSlider(Qt.Orientation.Horizontal)
        self.dim.setRange(0, 100)               # 100 % = the outside fully black (Artem, 2026-10-20)
        self.dim.setValue(round(conf["DIM"] * 100))
        self.long_press = self.seconds_field(conf["LONG"])
        long_row = QHBoxLayout()
        long_row.addWidget(QLabel("Keep it pressed this long after a move to leave precision "
                                  "mode (0 = never):"))
        long_row.addWidget(self.long_press)
        long_row.addStretch()
        self.reconnect = QSpinBox()                 # whole seconds, 0 = never; a day as the top is no cap in practice
        self.reconnect.setRange(0, 86400)
        self.reconnect.setSuffix(" s")
        self.reconnect.setLocale(QLocale.c())
        self.reconnect.setKeyboardTracking(False)   # save on Enter, focus-out or a step, not per keystroke
        self.reconnect.setValue(int(conf["RECONNECT"]))
        reconnect_row = QHBoxLayout()
        reconnect_row.addWidget(QLabel("If the tablet disconnects, resume precision mode when it is "
                                       "back within (0 = never):"))
        reconnect_row.addWidget(self.reconnect)
        reconnect_row.addStretch()
        _, (bound_up, _, degrees) = precision_ring_mode(pad)   # which chord sits on the up side now: the Swap box's state
        self.ring_step = DotSpinBox()
        self.ring_step.setRange(0.0, 100.0)          # no floor: the ring script takes any positive step, 0 = its default;
        self.ring_step.setSingleStep(0.25)           # 100 points is the whole width, the most a tick can mean
        self.ring_step.setDecimals(2)
        self.ring_step.setSuffix(" points of screen width")
        self.ring_step.setSpecialValueText("the default, 0.5 points of screen width")
        self.ring_step.setKeyboardTracking(False)
        self.ring_step.setValue(conf["RING_STEP"])
        self.ring_degrees = QSpinBox()
        self.ring_degrees.setRange(1, 180)
        self.ring_degrees.setSuffix("° of ring travel")
        self.ring_degrees.setKeyboardTracking(False)
        self.ring_degrees.setValue(max(1, round(degrees)))
        self.ring_degrees.setToolTip("The ring reports 5° steps, so 5 is the finest tick; 1 to 5 behave the same.")
        self.ring_swap = QCheckBox("Swap the ring direction")
        self.ring_swap.setChecked(bound_up == RING_DEFAULT[1])   # the smaller chord up = swapped; set BEFORE the signal
        ring_row = QHBoxLayout()                                   # is wired, or every window open rewrote the ring
        ring_row.addWidget(QLabel("Ring: each tick changes the size by"))
        ring_row.addWidget(self.ring_step)
        ring_row.addStretch()
        tick_row = QHBoxLayout()
        tick_row.addWidget(QLabel("A tick every"))
        tick_row.addWidget(self.ring_degrees)
        tick_row.addWidget(self.ring_swap)
        tick_row.addStretch()
        toggle = QPushButton("Toggle precision now")
        toggle.clicked.connect(lambda: subprocess.Popen([str(TOGGLE)]))
        toggle.setToolTip(f"The pad key bound to {wacom_profiles.toggle_chord()} does the same. Size and dim "
                          "apply on the next toggle or ring tick, the long-press time at once, the "
                          "reconnect time at the next disconnect, the ring step at the next tick.")

        layout.addWidget(self.size_label)
        layout.addWidget(self.size)
        layout.addLayout(limit_row)
        layout.addWidget(self.limit_note)
        layout.addWidget(self.dim_label)
        layout.addWidget(self.dim)
        layout.addLayout(long_row)
        layout.addLayout(reconnect_row)
        layout.addLayout(ring_row)
        layout.addLayout(tick_row)
        layout.addWidget(toggle)
        layout.addStretch()
        self._size_timer = self._debounce_timer(          # no daemon poke for these keys: the toggle and the ring
            lambda: save_conf_key("SCALE", f"{self.size.value() / 100:.4f}"))   # read them themselves, and a poke
        self._dim_timer = self._debounce_timer(           # releases every held chord for nothing
            lambda: save_conf_key("DIM", f"{self.dim.value() / 100:.2f}"))
        self._ring_timer = self._debounce_timer(self.apply_ring)   # each ring write makes KWin rebuild its
                                                                   # whole rebind table: one per pause, not per step
        self.size_min.valueChanged.connect(lambda v: (save_conf_key("SCALE_MIN", f"{v / 100:.2f}"), self.relimit()))
        self.size_max.valueChanged.connect(lambda v: (save_conf_key("SCALE_MAX", f"{v / 100:.2f}"), self.relimit()))
        self.size.valueChanged.connect(self.update_labels)
        self.dim.valueChanged.connect(self.update_labels)
        self.size.valueChanged.connect(lambda: self._size_timer.start())
        self.dim.valueChanged.connect(lambda: self._dim_timer.start())
        self.long_press.valueChanged.connect(lambda v: (save_conf_key("LONG", f"{v:.2f}"), poke_daemon()))
        self.reconnect.valueChanged.connect(lambda v: save_conf_key("RECONNECT", str(int(v))))
        self.ring_step.valueChanged.connect(lambda v: save_conf_key("RING_STEP", f"{v:.2f}"))
        self.ring_degrees.valueChanged.connect(lambda: self._ring_timer.start())
        self.ring_swap.toggled.connect(lambda: self._ring_timer.start())
        self.update_labels()

    def set_profile(self, name):
        self.profile_label.setText(f"Profile: {elided(self.profile_label, name, 300)}. "
                                   "Changes go into this profile.")
        self.profile_label.setToolTip(name)

    @staticmethod
    def _debounce_timer(slot):
        """A single-shot 300 ms QTimer wired to slot - one per slider and one
        for the ring's angle + swap, so the write follows the LAST change,
        never every tick."""
        timer = QTimer()
        timer.setSingleShot(True)
        timer.setInterval(300)
        timer.timeout.connect(slot)
        return timer

    def flush_pending(self):
        """Write a pending slider or ring save at once - before any switch and
        on window close, so it cannot land after the switch and stamp the OLD
        profile's value into the new one."""
        for timer in (self._size_timer, self._dim_timer, self._ring_timer):
            if timer.isActive():
                timer.stop()
                timer.timeout.emit()

    def confirm_switch(self):
        """No Apply button on this tab - just flush any pending slider save."""
        self.flush_pending()
        return True

    def apply_ring(self):
        """The tick angle and the direction go straight into the ring binding,
        on whichever mode CURRENTLY carries the precision-size pair - looked
        up fresh, since the Pad tab's Apply may have moved it since this tab
        opened. The pair is written in the order the Swap box states (ticked
        = the smaller chord on the up side), never as a flip of what is bound:
        that flipped on every angle change too."""
        mode, _ = precision_ring_mode(self.pad)
        up, down = ((RING_DEFAULT[1], RING_DEFAULT[0]) if self.ring_swap.isChecked()
                    else (RING_DEFAULT[0], RING_DEFAULT[1]))
        ring_write(self.pad, up, down, self.ring_degrees.value(), mode)

    @staticmethod
    def seconds_field(value):
        """A time in seconds: 2 decimals, from 0 (no floor - the conf takes any value), dot decimal."""
        box = DotSpinBox()                  # dot decimal like the conf file; a typed comma reads as one
        box.setRange(0.0, 60.0)
        box.setSingleStep(0.05)
        box.setDecimals(2)
        box.setSuffix(" s")
        box.setKeyboardTracking(False)      # save on Enter, focus-out or a step, not per keystroke
        box.setValue(value)
        return box

    @staticmethod
    def percent_field(value):
        """A size limit in whole percent of the screen width; 0 reads "no limit" (the conf keeps the 0)."""
        box = QSpinBox()
        box.setRange(0, 100)
        box.setSuffix(" %")
        box.setSpecialValueText("no limit")
        box.setLocale(QLocale.c())
        box.setKeyboardTracking(False)      # save on Enter, focus-out or a step, not per keystroke
        box.setValue(round(value * 100))
        return box

    def limit_size(self):
        """The size slider's ends = the soft cap (conf SCALE_MIN / SCALE_MAX, the limit the toggle and the
        ring use); no limit = 1% and the full width. A value now outside moves in and is saved.
        Smallest above Largest is a warning, never a refusal: the toggle and the ring then keep the
        smallest size, and so does the slider (Qt: a minimum above the maximum is the only value)."""
        lo, hi = self.size_min.value(), self.size_max.value()
        self.size.setRange(lo or 1, hi or 100)
        crossed = bool(lo and hi and lo > hi)
        self.limit_note.setText("Smallest size is above Largest size: the area stays at the smallest size."
                                if crossed else "")
        self.limit_note.setVisible(crossed)

    def relimit(self):
        """A limit field changed: the slider first takes the conf's CURRENT SCALE (the ring may have
        moved it since this tab was built), then the new ends - so a clamp saves the value the ring
        left only when it is really outside, never a stale slider over it."""
        self.flush_pending()
        self.size.blockSignals(True)
        self.size.setRange(1, 100)                   # the widest first: setValue clamps to the range in force
        self.size.setValue(round(read_conf()["SCALE"] * 100))
        self.size.blockSignals(False)
        self.limit_size()
        self.update_labels()

    def update_labels(self):
        pct = self.size.value()
        w = round(self.sw * pct / 100)
        h = round(w / self.aspect)
        if h > self.sh:
            h = self.sh
            w = round(h * self.aspect)
        area_pct = round(100 * w * h / (self.sw * self.sh))
        self.size_label.setText(
            f"Precision area: {pct}% of screen width -> {w} x {h} px "
            f"(tablet-shaped, {area_pct}% of the screen)")
        self.dim_label.setText(f"Dim strength outside the area: {self.dim.value()}%")


# ── chunk: pad_icon
def pad_icon(kind):
    """A 20 px key picture as printed on the pad: a box, plain or with the
    tactile dot or dash mark. Palette colors, so dark themes stay legible."""
    pal = QApplication.palette()
    ink, paper = pal.color(QPalette.ColorRole.Text), pal.color(QPalette.ColorRole.Base)
    pm = QPixmap(20, 20)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(ink, 1.5))
    p.setBrush(paper)
    p.drawRoundedRect(QRectF(2, 4, 16, 12), 2, 2)
    if kind == "dot":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink)
        p.drawEllipse(QPointF(10, 10), 2.4, 2.4)
    elif kind == "dash":
        p.setPen(QPen(ink, 2))
        p.drawLine(QPointF(7, 10), QPointF(13, 10))
    p.end()
    return pm


# ── chunk: ring_icon
def ring_icon(mode):
    """The pad ring as a 22 px doughnut with mode n's light lit: mode 1 sits
    at 7:30 on the clock face, the next modes continue clockwise."""
    pal = QApplication.palette()
    ink, paper = pal.color(QPalette.ColorRole.Text), pal.color(QPalette.ColorRole.Base)
    pm = QPixmap(22, 22)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(ink, 1.5))
    p.setBrush(paper)
    p.drawEllipse(QPointF(11, 11), 8, 8)
    p.drawEllipse(QPointF(11, 11), 3.4, 3.4)
    dx, dy = {1: (-1, 1), 2: (-1, -1), 3: (1, -1), 4: (1, 1)}[mode]
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(AMBER))
    p.drawEllipse(QPointF(11 + dx * 4.07, 11 + dy * 4.07), 2, 2)
    p.end()
    return pm


# ── chunk: draw_ants
def draw_ants(widget, color, offset):
    """The flowing dashed outline - the overlay's waiting border in
    miniature - painted over a widget: amber marks a pie key's box, red a
    field consumed by precision mode. Shared by the two Ants widgets."""
    p = QPainter(widget)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 2)
    pen.setStyle(Qt.PenStyle.CustomDashLine)
    pen.setDashPattern([3, 3])
    pen.setDashOffset(offset)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(widget.rect()).adjusted(1, 1, -1, -1), 4, 4)
    p.end()


# ── chunk: AntsLineEdit
class AntsLineEdit(QLineEdit):
    """A shortcut box that can wear the waiting look: dashes flowing
    clockwise, amber for a pie key, red when precision mode consumes it."""

    def __init__(self):
        super().__init__()
        self._offset = 0.0
        self._color = AMBER
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._advance)

    def set_ants(self, on, color=None):
        if color:
            self._color = color
        if on and not self._timer.isActive():
            self._timer.start()
        elif not on and self._timer.isActive():
            self._timer.stop()
        self.update()

    def _advance(self):
        self._offset -= 0.4              # a shrinking offset flows the dashes forward (the overlay's trick)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._timer.isActive():
            draw_ants(self, self._color, self._offset)


# ── chunk: AntsCheckBox
class AntsCheckBox(QCheckBox):
    """A checkbox wearing the same flowing outline: red when its row is
    consumed by precision mode."""

    def __init__(self):
        super().__init__()
        self._offset = 0.0
        self._color = RED
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._advance)

    def set_ants(self, on, color=None):
        if color:
            self._color = color
        if on and not self._timer.isActive():
            self._timer.start()
        elif not on and self._timer.isActive():
            self._timer.stop()
        self.update()

    def _advance(self):
        self._offset -= 0.4
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._timer.isActive():
            draw_ants(self, self._color, self._offset)


# ── chunk: AntsRadio
class AntsRadio(QRadioButton):
    """The Precision column's round tick (one option at most), wearing the
    red outline while its row is the precision key. Exclusivity and
    click-again-to-clear live in PadTab, so no key at all is a valid
    state - autoExclusive is off."""

    def __init__(self):
        super().__init__()
        self.setAutoExclusive(False)
        self._offset = 0.0
        self._color = RED
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._advance)

    def set_ants(self, on, color=None):
        if color:
            self._color = color
        if on and not self._timer.isActive():
            self._timer.start()
        elif not on and self._timer.isActive():
            self._timer.stop()
        self.update()

    def _advance(self):
        self._offset -= 0.4
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._timer.isActive():
            draw_ants(self, self._color, self._offset)


# ── chunk: PadTab
class PadTab(QWidget):
    """The pad as a table: the keys pictured as printed on the pad, a Touch
    and a Press box per key, the per-key Touch register, the ring's four
    modes between keys 4 and 5, the Pie keys column (a tick per side) and
    the Precision column (a round tick, one at most). A ticked pie side is
    Disabled in kcminputrc + its conf chord (amber outline); the Precision
    key gets the toggle chord + HOVER_MASK, its chord fields consumed (red
    outline, read-only Press). Apply and a register change poke the
    daemon's control pipe."""
    MARKS = ("plain", "dash", "dot", "plain", "plain", "dot", "dash", "plain")

    def __init__(self, pad, asleep=False, pen=None, sysname=None):
        super().__init__()
        self.pad = pad
        self.pen, self.sysname = pen, sysname     # only for the post-Apply checkpoint()
        self.parse = wacom_profiles.load_parse_chord()
        self._prm_last = None
        self.dirty = False          # a switch asks Apply/Discard/Cancel while this is set
        outer = QVBoxLayout(self)
        self.profile_label = QLabel()
        outer.addWidget(self.profile_label)
        if not pad:
            note = QLabel("No tablet pad detected. Wake the tablet and reopen "
                          "this window.")
            note.setWordWrap(True)
            outer.addWidget(note)
            outer.addStretch()                       # the lines stay at the top of the page
            return
        if asleep:
            asleep_note = QLabel("The tablet is asleep. These settings apply when it wakes.")
            asleep_note.setWordWrap(True)
            outer.addWidget(asleep_note)
        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        outer.addWidget(grid_widget)
        outer.addStretch()                           # extra height goes below the table, not into its rows
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 3)
        for col, span, text, tip in ((0, 1, "| Pad keys |", PAD_TIP),
                                     (1, 2, "| Shortcuts |", SHORTCUT_TIP),
                                     (3, 1, "| Touch register |", REG_TIP),
                                     (4, 1, "| Pie keys |", PIE_TIP),
                                     (5, 1, "| Precision |", PRM_TIP)):
            head = QLabel(text)
            head.setToolTip(tip)
            grid.addWidget(head, 0, col, 1, span, Qt.AlignmentFlag.AlignHCenter)
        side = QLabel("(on the left side)")
        side.setToolTip(PAD_TIP)
        grid.addWidget(side, 1, 0, Qt.AlignmentFlag.AlignHCenter)
        for col, text, tip in ((1, "Touch", TOUCH_TIP), (2, "Press", SHORTCUT_TIP)):
            sub = QLabel(text)
            sub.setToolTip(tip)
            grid.addWidget(sub, 1, col, Qt.AlignmentFlag.AlignHCenter)
        info = QLabel()
        theme = QIcon.fromTheme("dialog-information")
        if theme.isNull():
            info.setText("(i)")
        else:
            info.setPixmap(theme.pixmap(16, 16))
        info.setToolTip(PIE_TIP)
        grid.addWidget(info, 1, 4, Qt.AlignmentFlag.AlignHCenter)
        self.touches, self.presses, self.regs = {}, {}, {}
        self._saved_press = {}      # a row the Precision tick turned red: what its Press box said before
        self._raw = {}              # idx -> True: a raw-form binding, read-only, Apply never touches it
        self.tp, self.pp, self.prms, self.rings = {}, {}, {}, {}
        self._ring_raw = {}         # mode -> True: Scroll or Disabled, set in System Settings; read-only, Apply skips it
        hold_default = read_conf()["HOLD"]
        for idx in range(8):
            row = idx + 2 if idx < 4 else idx + 6    # the ring sits between keys 4 and 5, as on the pad
            picture = QLabel()
            picture.setPixmap(pad_icon(self.MARKS[idx]))
            picture.setToolTip(f"Key {idx + 1}")
            grid.addWidget(picture, row, 0, Qt.AlignmentFlag.AlignCenter)
            touch = AntsLineEdit()
            touch.setPlaceholderText("Ctrl, or Alt+F5")
            touch.setToolTip(TOUCH_TIP)
            press = AntsLineEdit()
            press.setPlaceholderText("Meta+Shift+F10, or Shift")
            try:
                reg_value = float(conf_value(f"TOUCH_HOLD_{idx + 1}"))
            except ValueError:
                reg_value = hold_default
            reg = PrecisionTab.seconds_field(reg_value)
            reg.setToolTip(REG_TIP)
            reg.valueChanged.connect(lambda v, n=idx + 1: self.save_register(n, v))
            tp = AntsCheckBox()
            tp.setToolTip(TP_TIP)
            pp = AntsCheckBox()
            pp.setToolTip(PP_TIP)
            prm = AntsRadio()
            prm.setToolTip(PRM_TIP)
            self.touches[idx], self.presses[idx], self.regs[idx] = touch, press, reg
            self.tp[idx], self.pp[idx], self.prms[idx] = tp, pp, prm
            self._load_row(idx)
            tp.toggled.connect(self.restyle)
            pp.toggled.connect(self.restyle)
            prm.clicked.connect(lambda _, i=idx: self.pick_prm(i))
            grid.addWidget(touch, row, 1)
            grid.addWidget(press, row, 2)
            grid.addWidget(reg, row, 3)
            pies = QWidget()
            pair = QHBoxLayout(pies)
            pair.setContentsMargins(0, 0, 0, 0)
            pair.addWidget(tp)
            pair.addWidget(pp)
            grid.addWidget(pies, row, 4, Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(prm, row, 5, Qt.AlignmentFlag.AlignCenter)
        try:
            mask = int(conf_value("HOVER_MASK"), 0)
        except (TypeError, ValueError):
            mask = 0
        for idx in range(8):
            if mask & (1 << idx):
                self.prms[idx].setChecked(True)
                self._prm_last = idx
                break
        for mode in range(4):
            picture = QLabel()
            picture.setPixmap(ring_icon(mode + 1))
            picture.setToolTip(f"Pad ring, mode {mode + 1}. The light on the tablet shows "
                               "the active mode. Press the ring button to change the mode.")
            grid.addWidget(picture, mode + 6, 0, Qt.AlignmentFlag.AlignCenter)
            pair = QHBoxLayout()
            boxes = []
            for way in ("clockwise", "counter-clockwise"):
                turn = QLineEdit()
                turn.setPlaceholderText(way)
                turn.setToolTip(RING_TIP)
                pair.addWidget(turn)
                boxes.append(turn)
            bound = ring_read(pad, mode)
            raw = wacom_profiles.kreadconfig6(["ButtonRebinds", "TabletRing", pad, str(mode)], "0")
            if bound:
                boxes[0].setText(bound[0])
                boxes[1].setText(bound[1])
            elif raw:                                # Scroll (a scroll wheel) or Disabled, from System Settings
                boxes[0].setText(raw)
                for turn in boxes:
                    turn.setReadOnly(True)
                    turn.setToolTip("Set in System Settings > Drawing Tablet.")
                self._ring_raw[mode] = True
            self.rings[mode] = boxes
            grid.addLayout(pair, mode + 6, 1, 1, 2)
        note = QLabel("Precision mode consumes <b>both</b> the Touch and Press fields, "
                      "and cannot be a pie key.")
        grid.addWidget(note, 14, 0, 1, 6)
        apply_btn = QPushButton("Apply bindings")
        apply_btn.clicked.connect(self.apply)
        grid.addWidget(apply_btn, 15, 2)
        self.restyle()
        key = lambda i: (self.touches[i], self.presses[i], self.regs[i], self.tp[i], self.pp[i], self.prms[i])
        chain = ([w for i in range(4) for w in key(i)] + [w for m in range(4) for w in self.rings[m]]
                 + [w for i in range(4, 8) for w in key(i)] + [apply_btn])
        for a, b in zip(chain, chain[1:]):           # Tab follows the rows as drawn: the ring modes sit
            QWidget.setTabOrder(a, b)                # between keys 4 and 5, but were built after key 8
        for w in list(self.touches.values()) + list(self.presses.values()):
            w.textEdited.connect(lambda: setattr(self, "dirty", True))
        for w in list(self.tp.values()) + list(self.pp.values()) + list(self.prms.values()):
            w.toggled.connect(lambda: setattr(self, "dirty", True))
        for pair in self.rings.values():
            for w in pair:
                w.textEdited.connect(lambda: setattr(self, "dirty", True))

    def confirm_switch(self):
        """A switch asks Apply, Discard or Cancel when this tab has edits not
        applied; True = go on with the switch."""
        if not self.dirty:
            return True
        box = QMessageBox(QMessageBox.Icon.Question, "Wacom Center",
                          "The Pad buttons tab has changes that have not been applied.",
                          QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Discard
                          | QMessageBox.StandardButton.Cancel, self)
        choice = box.exec()
        if choice == QMessageBox.StandardButton.Cancel:
            return False
        if choice == QMessageBox.StandardButton.Apply:
            self.apply()
        else:
            self.dirty = False
        return True

    def set_profile(self, name):
        self.profile_label.setText(f"Profile: {elided(self.profile_label, name, 300)}. "
                                   "Changes go into this profile.")
        self.profile_label.setToolTip(name)

    def pick_prm(self, idx):
        """The round ticks: one key at most; a click on the active one
        clears it, so no precision key at all is a valid state."""
        if self._prm_last == idx and not self.prms[idx].isChecked():
            self._prm_last = None                    # Qt already cleared it? keep in step
        elif self._prm_last == idx:
            self.prms[idx].setChecked(False)         # click on the active tick = clear
            self._prm_last = None
        else:
            for other, prm in self.prms.items():
                if other != idx and prm.isChecked():
                    prm.setChecked(False)
            self.prms[idx].setChecked(True)
            self._prm_last = idx
        self.restyle()

    def prm_row(self):
        """The ticked Precision row, or None."""
        for idx, prm in self.prms.items():
            if prm.isChecked():
                return idx
        return None

    def _load_row(self, idx):
        """Key idx's Touch and Press boxes, pie ticks and raw flag from
        kcminputrc and the conf: at the build, and again after an Apply, so
        the table shows what is on disk (a refused side kept its old state)."""
        touch, press, tp, pp = self.touches[idx], self.presses[idx], self.tp[idx], self.pp[idx]
        touch.setText(conf_value(f"TOUCH_CHORD_{idx + 1}"))
        bound, chord = kread(self.pad, idx), conf_value(f"CHORD_{idx + 1}")
        self._raw.pop(idx, None)
        press.setReadOnly(False)
        press.setToolTip("")
        if chord and bound in ("Disabled", "", chord):
            press.setText(chord)
            pp.setChecked(True)
        elif is_raw_form(bound):
            press.setText(bound)
            press.setReadOnly(True)
            press.setToolTip("Set in System Settings > Drawing Tablet.")
            self._raw[idx] = True
            pp.setChecked(False)
        else:
            press.setText(bound)
            pp.setChecked(False)
        tp.setChecked(bound == "Disabled" and bool(touch.text()) and self.parse is not None
                      and self.parse(touch.text(), True) is not None)   # a touch pie: Disabled + a full touch chord

    def restyle(self):
        """The outlines follow the state: red across the consumed precision
        row (its Press read-only, showing the toggle chord), amber on a
        ticked pie side's box. The register field stays plain - the
        precision key uses it too (the drag hold)."""
        m = self.prm_row()
        tc = wacom_profiles.toggle_chord()           # one read per restyle, not one per row
        for idx in range(8):
            red = idx == m
            self.touches[idx].set_ants(red or self.tp[idx].isChecked(),
                                       RED if red else AMBER)
            press = self.presses[idx]
            press.setReadOnly(red or self._raw.get(idx, False))
            if red:
                if idx not in self._saved_press:     # remember the user's own chord ONCE, before
                    self._saved_press[idx] = press.text()   # the toggle chord covers it
                press.setText(tc)
                press.set_ants(True, RED)
            else:
                if idx in self._saved_press:         # the tick moved or was cleared: give the chord back,
                    saved = self._saved_press.pop(idx)      # or Apply would commit the toggle chord.
                    # The saved text IS the toggle chord when this key was the precision key at the
                    # build: the tick moved to another key, so the chord moves with it, never doubled
                    press.setText("" if m is not None and saved == tc else saved)
                press.set_ants(self.pp[idx].isChecked(), AMBER)
            self.tp[idx].set_ants(red, RED)
            self.pp[idx].set_ants(red, RED)
            self.prms[idx].set_ants(red, RED)

    def save_register(self, n, value):
        """Key n's touch register; the daemon hears about it at once."""
        save_conf_key(f"TOUCH_HOLD_{n}", f"{value:.2f}")
        poke_daemon()

    def apply(self):
        """Every row onto disk. A shortcut the daemon cannot press is not
        written and the key keeps what it had - the sides are checked BEFORE
        the key is set Disabled (a refused pie used to leave a dead key:
        Disabled, no chord); a raw-form key is never Disabled. The table then
        reloads each row from disk."""
        problems = []
        m = self.prm_row()
        if m is not None:
            save_conf_key("HOVER_MASK", f"0x{1 << m:02x}")
        else:
            drop_conf_key("HOVER_MASK")
        for idx in range(8):
            n = idx + 1
            touch = re.sub(r"\s*\+\s*", "+", self.touches[idx].text().strip())   # "Meta + F5": the daemon
            press = re.sub(r"\s*\+\s*", "+", self.presses[idx].text().strip())   # takes it, bash sourcing the conf does not
            tp, pp = self.tp[idx].isChecked(), self.pp[idx].isChecked()
            if idx == m:                             # consumed: the toggle chord, nothing else
                if touch or tp or pp:
                    problems.append(f"Key {n}: the precision key has no Touch shortcut and no pie; cleared.")
                    self.touches[idx].setText("")
                    self.tp[idx].setChecked(False)
                    self.pp[idx].setChecked(False)
                kwrite(self.pad, idx, wacom_profiles.toggle_chord())
                drop_conf_key(f"CHORD_{n}")
                drop_conf_key(f"TOUCH_CHORD_{n}")
                continue
            touch_ok = bool(touch) and (self.parse is None or self.parse(touch, tp) is not None)
            if touch and not touch_ok:
                problems.append(f"Key {n}: '{touch}' is not a "
                                + ("pie touch chord (modifiers plus one F-key)" if tp else
                                   "touch chord (modifiers and F-keys; not Meta alone)")
                                + "; the key keeps its previous Touch shortcut.")
            elif tp and not touch:
                problems.append(f"Key {n}: Pie on the touch needs a Touch shortcut; the key keeps "
                                "its previous binding.")
            if touch_ok:
                save_conf_key(f"TOUCH_CHORD_{n}", touch)
            elif not touch:
                drop_conf_key(f"TOUCH_CHORD_{n}")    # an emptied box: the chord goes (a typo keeps the old one)
            if self._raw.get(idx):                   # a raw-form binding (MouseButton, Scroll, ...): never Disabled
                if tp or pp:
                    problems.append(f"Key {n}: its binding is set in System Settings > Drawing Tablet. "
                                    "Clear it there to use a pie on this key.")
                if kread(self.pad, idx) != press:    # the precision tick left this row: its binding back
                    wacom_profiles.kwrite_literal(["ButtonRebinds", "Tablet", self.pad], str(idx), press)
                drop_conf_key(f"CHORD_{n}")
                continue
            press_ok = bool(press) and (self.parse is None or self.parse(press) is not None)
            if pp and not press:
                problems.append(f"Key {n}: Pie on the press needs a Press shortcut; the key keeps "
                                "its previous binding.")
            elif pp and not press_ok:
                problems.append(f"Key {n}: a pie press chord is modifiers plus one F-key; the key "
                                "keeps its previous binding.")
            if (tp and touch_ok) or (pp and press_ok):   # the key belongs to the daemon
                kwrite(self.pad, idx, "Disabled")        # never a deleted line: KWin would leak the raw button
                if pp and press_ok:
                    save_conf_key(f"CHORD_{n}", press)
                elif not pp:
                    drop_conf_key(f"CHORD_{n}")
                    if press:
                        problems.append(f"Key {n}: the Press shortcut needs its Pie tick, or clear it")
                continue                                 # a refused press pie: CHORD stays as it was
            if tp or pp:
                continue                                 # every ticked side refused: the key stays as it was
            # isEmpty() is False for ANY non-empty text, so it never rejected anything; toString()
            # is empty exactly for Qt's Key_unknown. A WARNING, never a refusal: a chord Qt cannot
            # parse can still be what someone wants under another keyboard layout.
            if press and press.lower() != "disabled" and press not in MODIFIER_ONLY \
                    and not QKeySequence(press).toString():
                problems.append(f"Key {n}: '{press}' does not look like a valid key "
                                "combination. It might not work.")
            kwrite(self.pad, idx, press)
            drop_conf_key(f"CHORD_{n}")
        for mode, (up, down) in self.rings.items():
            if self._ring_raw.get(mode):
                continue                             # Scroll or Disabled: System Settings owns that mode
            chords = (up.text().strip(), down.text().strip())
            if any(t and t not in MODIFIER_ONLY and not QKeySequence(t).toString() for t in chords):
                problems.append(f"Ring mode {mode + 1}: that does not look like a valid key "
                                "combination. It might not work.")
            kept = ring_read(self.pad, mode)
            ring_write(self.pad, chords[0], chords[1], kept[2] if kept else 5, mode)
        if not poke_daemon():
            problems.append(wacom_profiles.DAEMON_DOWN)
        wacom_profiles.checkpoint(self.pad, self.pen, self.sysname)
        for idx in range(8):
            if idx != m:
                self._load_row(idx)                  # what is on disk now, kept sides included
        self.dirty = False
        self.restyle()
        if problems:
            QMessageBox.warning(self, "Wacom Center", "\n".join(problems))


# ── chunk: _parse_curve
def _parse_curve(text):
    """(x1, y1, x2, y2) floats from a 'x1,y1;x2,y2;' TabletToolPressureCurve
    value, or None."""
    m = re.fullmatch(r"([0-9.]+),([0-9.]+);([0-9.]+),([0-9.]+);", text or "")
    if not m:
        return None
    try:
        return tuple(float(g) for g in m.groups())
    except ValueError:
        return None


# ── chunk: _read_pressure_group
def _read_pressure_group(pen):
    """(curve, rmin, rmax) raw strings from the first kcminputrc [Libinput]
    group for this pen, or (None, None, None)."""
    for vendor, product in wacom_profiles.pen_groups_in_kcminputrc(pen):
        curve = wacom_profiles.kreadconfig6(["Libinput", vendor, product, pen], "TabletToolPressureCurve")
        if curve:
            rmin = wacom_profiles.kreadconfig6(["Libinput", vendor, product, pen], "TabletToolPressureRangeMin")
            rmax = wacom_profiles.kreadconfig6(["Libinput", vendor, product, pen], "TabletToolPressureRangeMax")
            return curve, rmin, rmax
    return None, None, None


# ── chunk: CurveGraph
class CurveGraph(QWidget):
    """A 160x160 px pressure-curve editor: the cubic Bezier from 0,0 to 1,1
    with its two inner control points as draggable handles (grab radius >=
    12 px; a drag clamps to 0-1) - like the KDE System Settings pressure-
    curve editor. changed(x1, y1, x2, y2) fires on every drag; set_points
    updates the picture without re-emitting (no feedback loop with the spin
    boxes - the caller blocks signals during a mutual update)."""
    changed = pyqtSignal(float, float, float, float)
    SIZE = 160
    MARGIN = 8          # a handle at 0 or 1 sits inside the widget, whole, not half off its clipped edge

    def __init__(self):
        super().__init__()
        self.setFixedSize(self.SIZE, self.SIZE)
        self.p1 = QPointF(0.0, 0.0)
        self.p2 = QPointF(1.0, 1.0)
        self._drag = None

    def set_points(self, x1, y1, x2, y2):
        self.p1, self.p2 = QPointF(x1, y1), QPointF(x2, y2)
        self.update()

    def _to_widget(self, pt):
        inner = self.SIZE - 2 * self.MARGIN
        return QPointF(self.MARGIN + pt.x() * inner, self.MARGIN + (1 - pt.y()) * inner)

    def _to_curve(self, pos):
        inner = self.SIZE - 2 * self.MARGIN
        x = min(1.0, max(0.0, (pos.x() - self.MARGIN) / inner))
        y = min(1.0, max(0.0, 1 - (pos.y() - self.MARGIN) / inner))
        return x, y

    def paintEvent(self, event):
        pal = QApplication.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), pal.color(QPalette.ColorRole.Base))
        p.setPen(QPen(pal.color(QPalette.ColorRole.Mid), 1))
        inner = self.SIZE - 2 * self.MARGIN
        p.drawRect(QRectF(self.MARGIN, self.MARGIN, inner, inner))   # the 0-1 box the curve lives in
        start, end = self._to_widget(QPointF(0, 0)), self._to_widget(QPointF(1, 1))
        h1, h2 = self._to_widget(self.p1), self._to_widget(self.p2)
        path = QPainterPath(start)
        path.cubicTo(h1, h2, end)
        p.setPen(QPen(pal.color(QPalette.ColorRole.Text), 2))
        p.drawPath(path)
        p.setPen(QPen(QColor(AMBER), 1))
        p.drawLine(start, h1)
        p.drawLine(end, h2)
        p.setBrush(QColor(AMBER))
        for h in (h1, h2):
            p.drawEllipse(h, 4, 4)
        p.end()

    def _handle_at(self, pos):
        for name, pt in (("p1", self.p1), ("p2", self.p2)):
            d = self._to_widget(pt) - pos
            if d.x() ** 2 + d.y() ** 2 <= 12 ** 2:
                return name
        return None

    def mousePressEvent(self, event):
        self._drag = self._handle_at(event.position())

    def mouseMoveEvent(self, event):
        if self._drag:
            x, y = self._to_curve(event.position())
            setattr(self, self._drag, QPointF(x, y))
            self.update()
            self.changed.emit(self.p1.x(), self.p1.y(), self.p2.x(), self.p2.y())

    def mouseReleaseEvent(self, event):
        self._drag = None


# ── chunk: PenTab
class PenTab(QWidget):
    """Pen buttons (evdev codes 331/332/329) in kcminputrc
    [ButtonRebinds][TabletTool][<pen>], the pressure curve (the interactive
    graph plus 4 synced spin boxes) and the pressure range, all through
    wacom_profiles.apply_pressure - one Qt-free write path the switch
    reuses too."""
    CODES = (331, 332, 329)
    LABELS = ("Pen button 1", "Pen button 2", "Pen button 3")

    def __init__(self, pad, pen, sysname):
        super().__init__()
        self.pad, self.pen, self.sysname = pad, pen, sysname
        self.dirty = False           # a switch asks Apply/Discard/Cancel while this is set
        layout = QVBoxLayout(self)
        self.profile_label = QLabel()
        layout.addWidget(self.profile_label)
        if not pen:
            note = QLabel("No pen detected. Bring the pen near the tablet and reopen this window.")
            note.setWordWrap(True)
            layout.addWidget(note)
            layout.addStretch()                      # the lines stay at the top of the page
            return
        if not sysname:
            note = QLabel("The pen is not connected. These settings apply when it connects.")
            note.setWordWrap(True)
            layout.addWidget(note)

        self.buttons = {}
        btn_grid = QGridLayout()
        for row, (code, label) in enumerate(zip(self.CODES, self.LABELS)):
            box = AntsLineEdit()
            box.setPlaceholderText("Meta+Shift+F10, or Shift")
            box.setToolTip(PEN_TIP)
            bound = kread_group(["ButtonRebinds", "TabletTool", pen], code)
            box.setText(bound)
            if is_raw_form(bound):
                box.setReadOnly(True)
                box.setToolTip("Set in System Settings > Drawing Tablet.")
            lbl = QLabel(label)
            if code == 329:
                lbl.setToolTip("Both side buttons pressed together (USB only). The pen has no "
                               "third button.")
            btn_grid.addWidget(lbl, row, 0)
            btn_grid.addWidget(box, row, 1)
            self.buttons[code] = box
        layout.addLayout(btn_grid)

        curve_row = QHBoxLayout()
        self.graph = CurveGraph()
        curve_row.addWidget(self.graph)
        spins = QGridLayout()
        self.p1x, self.p1y, self.p2x, self.p2y = (self._curve_spin() for _ in range(4))
        for col, (label, box) in enumerate((("Point 1 input", self.p1x), ("Point 1 output", self.p1y),
                                            ("Point 2 input", self.p2x), ("Point 2 output", self.p2y))):
            spins.addWidget(QLabel(label), 0, col)
            spins.addWidget(box, 1, col)
        curve_row.addLayout(spins)
        layout.addLayout(curve_row)

        curve = rmin_v = rmax_v = None
        if sysname:
            out = busget(f"{MGR}/{sysname}", "pressureCurve").split(None, 1)
            if len(out) == 2:
                curve = out[1].strip().strip('"')
            rmin_out = busget(f"{MGR}/{sysname}", "pressureRangeMin").split()
            if len(rmin_out) >= 2:
                rmin_v = rmin_out[1]
            rmax_out = busget(f"{MGR}/{sysname}", "pressureRangeMax").split()
            if len(rmax_out) >= 2:
                rmax_v = rmax_out[1]
        if not curve:
            g_curve, g_rmin, g_rmax = _read_pressure_group(pen)
            curve = curve or g_curve
            if rmin_v is None and g_rmin:
                rmin_v = g_rmin
            if rmax_v is None and g_rmax:
                rmax_v = g_rmax
        x1, y1, x2, y2 = _parse_curve(curve) or (0.0, 0.0, 1.0, 1.0)
        self.p1x.setValue(x1)
        self.p1y.setValue(y1)
        self.p2x.setValue(x2)
        self.p2y.setValue(y2)
        self.graph.set_points(x1, y1, x2, y2)

        range_row = QHBoxLayout()
        self.rmin = self._curve_spin()
        self.rmax = self._curve_spin()
        self.rmax.setValue(1.0)
        range_row.addWidget(QLabel("Pressure range minimum"))
        range_row.addWidget(self.rmin)
        range_row.addWidget(QLabel("Pressure range maximum"))
        range_row.addWidget(self.rmax)
        layout.addLayout(range_row)
        try:
            if rmin_v is not None:
                self.rmin.setValue(float(rmin_v))
            if rmax_v is not None:
                self.rmax.setValue(float(rmax_v))
        except (TypeError, ValueError):
            pass

        self.range_note = QLabel()
        self.range_note.setWordWrap(True)
        layout.addWidget(self.range_note)
        if sysname and "true" not in busget(f"{MGR}/{sysname}", "supportsPressureRange"):
            self.range_note.setText("The pen has not reported yet. The range applies when it does.")

        btn_row = QHBoxLayout()
        reset = QPushButton("Reset Pressure")
        reset.clicked.connect(self.reset_pressure)
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self.apply)
        btn_row.addWidget(reset)
        btn_row.addWidget(apply_btn)
        layout.addLayout(btn_row)
        layout.addStretch()

        self._syncing = False
        for box in (self.p1x, self.p1y, self.p2x, self.p2y):
            box.valueChanged.connect(self._spins_to_graph)
        self.graph.changed.connect(self._graph_to_spins)

        for box in self.buttons.values():
            box.textEdited.connect(lambda: setattr(self, "dirty", True))
        for box in (self.p1x, self.p1y, self.p2x, self.p2y, self.rmin, self.rmax):
            box.valueChanged.connect(lambda: setattr(self, "dirty", True))

    def confirm_switch(self):
        """A switch asks Apply, Discard or Cancel when this tab has edits not
        applied; True = go on with the switch."""
        if not self.dirty:
            return True
        box = QMessageBox(QMessageBox.Icon.Question, "Wacom Center",
                          "The Pen tab has changes that have not been applied.",
                          QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Discard
                          | QMessageBox.StandardButton.Cancel, self)
        choice = box.exec()
        if choice == QMessageBox.StandardButton.Cancel:
            return False
        if choice == QMessageBox.StandardButton.Apply:
            self.apply()
        else:
            self.dirty = False
        return True

    def set_profile(self, name):
        self.profile_label.setText(f"Profile: {elided(self.profile_label, name, 300)}. "
                                   "Changes go into this profile.")
        self.profile_label.setToolTip(name)

    @staticmethod
    def _curve_spin():
        box = DotSpinBox()
        box.setRange(0.0, 1.0)
        box.setSingleStep(0.05)
        box.setDecimals(2)
        box.setKeyboardTracking(False)
        return box

    def _spins_to_graph(self):
        if self._syncing:
            return
        self._syncing = True
        self.graph.set_points(self.p1x.value(), self.p1y.value(), self.p2x.value(), self.p2y.value())
        self._syncing = False

    def _graph_to_spins(self, x1, y1, x2, y2):
        if self._syncing:
            return
        self._syncing = True
        self.p1x.setValue(x1)
        self.p1y.setValue(y1)
        self.p2x.setValue(x2)
        self.p2y.setValue(y2)
        self._syncing = False

    def reset_pressure(self):
        self.p1x.setValue(0.0)
        self.p1y.setValue(0.0)
        self.p2x.setValue(1.0)
        self.p2y.setValue(1.0)
        self.rmin.setValue(0.0)
        self.rmax.setValue(1.0)
        self.graph.set_points(0.0, 0.0, 1.0, 1.0)

    def apply(self):
        problems = []
        for i, (code, box) in enumerate(self.buttons.items()):
            seq = box.text().strip()
            if is_raw_form(seq):
                continue                       # a raw-form binding: never touched by Apply
            if seq and seq.lower() != "disabled" and seq not in MODIFIER_ONLY \
                    and not QKeySequence(seq).toString():
                problems.append(f"{self.LABELS[i]}: '{seq}' does not look like a valid key "
                                "combination. It might not work.")
            elif len(seq) == 1 and seq.isalnum():      # the profile reader warns on the same shape
                problems.append(f"{self.LABELS[i]}: '{seq}' is a single letter or digit. It fails "
                                "when a non-Latin layout is active; F-keys and modifiers always work.")
            kwrite_group(["ButtonRebinds", "TabletTool", self.pen], code, seq)
        if self.rmin.value() >= self.rmax.value():     # applied as set: a warning, never a refusal
            problems.append(f"Pressure range: the minimum ({self.rmin.value():.2f}) is not below the "
                            f"maximum ({self.rmax.value():.2f}). It is applied as set, but a later "
                            "switch to this profile resets the range to 0.00-1.00.")
        curve = (f"{self.p1x.value():.4f},{self.p1y.value():.4f};"
                f"{self.p2x.value():.4f},{self.p2y.value():.4f};")
        notes = []
        wacom_profiles.apply_pressure(curve, self.rmin.value(), self.rmax.value(), self.pen,
                                      self.sysname, status=notes.append)
        wacom_profiles.checkpoint(self.pad, self.pen, self.sysname)
        self.dirty = False
        problems.extend(notes)
        if problems:
            QMessageBox.information(self, "Wacom Center", "\n".join(problems))


# ── chunk: ProfilesTab
class ProfilesTab(QWidget):
    """The profile menu + `+` import, the 3x3 grid (Edit Grid opens the cell
    dialog), Export All, and the status line. Every switch goes through
    wacom_profiles.switch_to after asking the other tabs to confirm."""
    switched = pyqtSignal(str)

    def __init__(self, pad, pen, sysname, precision_tab, pad_tab, pen_tab):
        super().__init__()
        self.pad, self.pen, self.sysname = pad, pen, sysname
        self.precision_tab, self.pad_tab, self.pen_tab = precision_tab, pad_tab, pen_tab
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.menu_btn = QToolButton()
        self.menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        top.addWidget(self.menu_btn)
        add_btn = QPushButton("+")
        add_btn.setToolTip("Add Profile from File...")
        add_btn.clicked.connect(self.import_from_file)
        top.addWidget(add_btn)
        top.addStretch()
        layout.addLayout(top)

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        self.grid_cells = []
        for i in range(9):
            btn = QToolButton()
            btn.setFixedSize(96, 96)
            btn.setIconSize(QSize(64, 64))
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            btn.clicked.connect(lambda _, n=i + 1: self.cell_clicked(n))
            grid.addWidget(btn, i // 3, i % 3)
            self.grid_cells.append(btn)
        layout.addWidget(grid_widget)

        self.edit_grid = QPushButton("Edit Grid")
        self.edit_grid.setCheckable(True)
        self.edit_grid.toggled.connect(
            lambda on: self.edit_grid.setText("Done" if on else "Edit Grid"))
        layout.addWidget(self.edit_grid)

        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet("border-top: 1px dashed gray;")
        layout.addWidget(sep)

        export_btn = QPushButton("Export All Current Settings as Profile...")
        export_btn.setToolTip("Saves every setting on the Precision, Pad buttons and Pen tabs. "
                              "The pie menus themselves stay in Kando.")
        export_btn.clicked.connect(self.export_all)
        layout.addWidget(export_btn)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()
        self.refresh()

    def note(self, msg):
        self.status.setText(msg)

    @staticmethod
    def _icon_for(name, size):
        if not name:
            return QIcon()
        try:
            sections, _ = wacom_profiles.read_profile(wacom_profiles.profile_path(name))
        except wacom_profiles.ProfileError:
            return QIcon()
        png = sections["Image"]["png"]
        if not png:
            return QIcon()
        img = QImage()
        img.loadFromData(base64.b64decode(png))
        pix = QPixmap.fromImage(img).scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation)
        return QIcon(pix)

    def refresh(self):
        active = wacom_profiles.active_profile_name() or ""
        names = wacom_profiles.list_profiles()
        self.menu_btn.setText(elided(self.menu_btn, active, 240) if active else "(no profile)")
        self.menu_btn.setToolTip(active)
        self.menu_btn.setIcon(self._icon_for(active, 24))

        old_menu = self.menu_btn.menu()                 # replaced below and freed, not kept for the window's life
        menu = QMenu(self.menu_btn)
        search = QLineEdit()
        search.setPlaceholderText("Search profiles...")
        search_action = QWidgetAction(menu)
        search_action.setDefaultWidget(search)
        menu.addAction(search_action)
        rows = []
        for name in sorted(names):
            act = menu.addAction(self._icon_for(name, 24), name)
            act.triggered.connect(lambda _, n=name: self.request_switch(n))
            rows.append((name, act))

        def filter_rows(text):
            needle = text.lower()
            for name, act in rows:
                act.setVisible(needle in name.lower())

        def pick_first():
            for name, act in rows:
                if act.isVisible():
                    menu.close()
                    self.request_switch(name)
                    return

        search.textChanged.connect(filter_rows)
        search.returnPressed.connect(pick_first)
        menu.aboutToShow.connect(lambda: QTimer.singleShot(0, search.setFocus))   # type to search at once
        self.menu_btn.setMenu(menu)
        if old_menu is not None:
            old_menu.deleteLater()

        cell_names = wacom_profiles.get_grid()
        for i, btn in enumerate(self.grid_cells):
            name = cell_names[i] if i < len(cell_names) else ""
            btn.setText(elided(btn, name, 84))          # a 96 px cell: centred text lost both ends
            btn.setToolTip(name)
            btn.setIcon(self._icon_for(name, 64) if name else QIcon())
            # A border in a style sheet hides the style's focus frame, and offscreen neither Breeze
            # nor Fusion drew one on a plain cell: every kind of cell draws its own focus.
            if name and name == active:
                btn.setStyleSheet(f"QToolButton {{ border: 2px solid {AMBER}; }} "
                                  f"QToolButton:focus {{ border: 4px double {AMBER}; }}")
            elif not name:
                btn.setStyleSheet("QToolButton { border: 1px dashed gray; } "
                                  "QToolButton:focus { border: 2px dashed palette(highlight); }")
            else:
                btn.setStyleSheet("QToolButton:focus { border: 2px solid palette(highlight); }")

    def cell_clicked(self, n):
        cell_names = wacom_profiles.get_grid()
        name = cell_names[n - 1] if n - 1 < len(cell_names) else ""
        if self.edit_grid.isChecked() or not name:
            self.open_cell_dialog(n, name)
            return
        if name == wacom_profiles.active_profile_name():
            return                          # the active cell does nothing
        self.request_switch(name)

    def open_cell_dialog(self, n, current_name):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Grid Cell {n}")
        v = QVBoxLayout(dlg)
        combo = QComboBox()
        combo.addItem("Empty")
        names = wacom_profiles.list_profiles()
        combo.addItems(names)
        if current_name and current_name in names:
            combo.setCurrentText(current_name)
        v.addWidget(combo)
        preview = QLabel()
        preview.setFixedSize(128, 128)
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(preview)

        def load_preview(name):
            preview.clear()
            if not name or name == "Empty":
                return
            try:
                sections, _ = wacom_profiles.read_profile(wacom_profiles.profile_path(name))
            except wacom_profiles.ProfileError:
                return
            png = sections["Image"]["png"]
            if png:
                img = QImage()
                img.loadFromData(base64.b64decode(png))
                preview.setPixmap(QPixmap.fromImage(img).scaled(
                    128, 128, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

        combo.currentTextChanged.connect(load_preview)
        load_preview(combo.currentText())

        def choose_image():
            name = combo.currentText()
            if name == "Empty":
                return
            path, _ = QFileDialog.getOpenFileName(dlg, "Choose Image")
            if not path:
                return
            reader = QImageReader(path)
            size = reader.size()
            if size.width() > 16000 or size.height() > 16000:
                QMessageBox.warning(dlg, "Wacom Center", "That image is too large.")
                return
            img = reader.read()
            if img.isNull():
                QMessageBox.warning(dlg, "Wacom Center", "That file could not be read as an image.")
                return
            scaled = img.scaled(256, 256, Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            scaled.save(buf, "PNG")
            png_b64 = base64.b64encode(bytes(buf.data())).decode("ascii")
            wacom_profiles.backup_now(name)
            sections, _ = wacom_profiles.read_profile(wacom_profiles.profile_path(name))
            sections["Image"]["png"] = png_b64
            wacom_profiles.write_profile(wacom_profiles.profile_path(name), sections)
            load_preview(name)

        def remove_image():
            name = combo.currentText()
            if name == "Empty":
                return
            wacom_profiles.backup_now(name)
            sections, _ = wacom_profiles.read_profile(wacom_profiles.profile_path(name))
            sections["Image"]["png"] = ""
            wacom_profiles.write_profile(wacom_profiles.profile_path(name), sections)
            load_preview(name)

        btn_row = QHBoxLayout()
        choose = QPushButton("Choose Image...")
        choose.clicked.connect(choose_image)
        remove = QPushButton("Remove Image")
        remove.clicked.connect(remove_image)
        btn_row.addWidget(choose)
        btn_row.addWidget(remove)
        v.addLayout(btn_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        v.addWidget(buttons)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            chosen = combo.currentText()
            wacom_profiles.set_grid_cell(n, "" if chosen == "Empty" else chosen)
        self.refresh()                                  # an image chosen, then Cancel: the profile on disk changed anyway

    def _confirm_unapplied(self):
        """Step 5 item 1: the Pad buttons or Pen tab asks Apply/Discard/Cancel
        for its own unapplied edits; the Precision tab just flushes."""
        self.precision_tab.confirm_switch()
        return self.pad_tab.confirm_switch() and self.pen_tab.confirm_switch()

    def request_switch(self, name):
        if not self._confirm_unapplied():
            return
        msgs = []                                       # every note of the switch, shown together at the end:
        try:                                            # a final "Switched" line used to cover the daemon warning
            wacom_profiles.switch_to(name, pad=self.pad, pen=self.pen,
                                     sysname=self.sysname, status=msgs.append)
        except wacom_profiles.SwitchStopped as err:
            self.note("\n".join(msgs + [str(err)]))
            return
        self.note("\n".join([f"Switched to {name}."] + msgs))
        self.refresh()
        self.switched.emit(name)

    def _ask_clash(self, name, replace_label, keep_both=True):
        box = QMessageBox(self)
        box.setWindowTitle("Wacom Center")
        box.setText(f"'{name}' already exists.")
        replace_btn = box.addButton(replace_label, QMessageBox.ButtonRole.AcceptRole)
        keep_btn = box.addButton("Keep Both", QMessageBox.ButtonRole.ActionRole) if keep_both else None
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is replace_btn:
            return "replace"
        if keep_btn is not None and clicked is keep_btn:
            return "keep_both"
        return "cancel"

    def import_from_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Add Profile from File", str(Path.home() / "Documents"),
                                              "Wacom Center profiles (*.wcprofile)")
        if not path:
            return
        try:
            sections, warnings = wacom_profiles.read_profile(path)
        except wacom_profiles.ProfileError as err:
            self.note(str(err))
            return
        if warnings:
            self.note("\n".join(warnings))
        try:
            name = wacom_profiles.valid_name(Path(path).stem)
        except ValueError as err:
            self.note(str(err))
            return
        existing = name in wacom_profiles.list_profiles()
        active = wacom_profiles.active_profile_name()
        if not existing:
            wacom_profiles.write_profile(wacom_profiles.profile_path(name), sections)
            self.refresh()
            return
        choice = self._ask_clash(name, "Replace and Apply" if name == active else "Replace")
        if choice == "cancel":
            return
        if choice == "keep_both":
            name = wacom_profiles.unique_name(name)
            wacom_profiles.write_profile(wacom_profiles.profile_path(name), sections)
        else:
            if name == active and not self._confirm_unapplied():
                return                                  # asked BEFORE the file is replaced: Cancel leaves it as it was
            wacom_profiles.backup_now(name)
            wacom_profiles.write_profile(wacom_profiles.profile_path(name), sections)
            if name == active:
                msgs = []
                try:
                    wacom_profiles.switch_to(name, checkpoint_=False, pad=self.pad, pen=self.pen,
                                             sysname=self.sysname, status=msgs.append)
                except wacom_profiles.SwitchStopped as err:
                    self.note("\n".join(msgs + [str(err)]))
                    self.refresh()
                    return
                self.note("\n".join([f"Replaced and applied {name}."] + msgs))
                self.switched.emit(name)
        self.refresh()

    def export_all(self):
        if not self._confirm_unapplied():
            return                                      # the export is the LIVE values: edits not applied would be missing
        wacom_profiles.checkpoint(self.pad, self.pen, self.sysname)
        active = wacom_profiles.active_profile_name() or "My settings"
        default = str(Path.home() / "Documents" / f"{active}.wcprofile")
        path, _ = QFileDialog.getSaveFileName(self, "Export All Current Settings as Profile", default,
                                              "Wacom Center profiles (*.wcprofile)")
        if not path:
            return
        try:
            name = wacom_profiles.valid_name(Path(path).stem)
        except ValueError as err:
            self.note(str(err))
            return
        sections, _ = wacom_profiles.read_profile(wacom_profiles.profile_path(active))
        if name != active and name in wacom_profiles.list_profiles():
            choice = self._ask_clash(name, "Replace", keep_both=False)
            if choice == "cancel":
                return
            wacom_profiles.backup_now(name)
        wacom_profiles.write_profile(Path(path), sections)
        wacom_profiles.write_profile(wacom_profiles.profile_path(name), sections)
        if name == active:
            self.note(f"Exported and updated the library copy of {name!r}.")
        else:
            wacom_profiles.set_active_profile(name)
            self.note(f"Exported and made {name!r} the active profile.")
            self.switched.emit(name)
        self.refresh()


# ── chunk: show_exception
def show_exception(kind, value, tb):
    """sys.excepthook while the window runs. On a box with Apport the hook
    prints the traceback and skips an unpackaged script, so an error inside
    a slot left a click half done with no sign: the traceback still goes to
    stderr, and a dialog says what stopped."""
    traceback.print_exception(kind, value, tb)
    QMessageBox.warning(None, "Wacom Center", f"{kind.__name__}: {value}")


# ── chunk: CenterWindow
class CenterWindow(QWidget):
    """The main window. Closing it asks the Pad buttons and Pen tabs about
    edits not applied (Apply, Discard, Cancel), as a profile switch does;
    the override must sit on a subclass to reach Qt's virtual closeEvent."""

    def __init__(self, state):
        super().__init__()
        self.state = state            # the live tabs, filled by main's build_tabs

    def closeEvent(self, event):
        for key in ("pad_tab", "pen_tab"):
            tab = self.state.get(key)
            if tab is not None and not tab.confirm_switch():
                event.ignore()
                return
        event.accept()


# ── chunk: main
def main():
    app = QApplication(sys.argv)
    app.setDesktopFileName("wacom-center")   # the Wayland app_id KWin reports; without it, the interpreter name
    app.setWindowIcon(QIcon.fromTheme("input-tablet"))

    # ── chunk: single_instance
    probe = QLocalSocket()
    probe.connectToServer("wacom-center")
    if probe.waitForConnected(200):          # one Wacom Center at a time: raise the first window and exit
        probe.write(b"raise")
        probe.waitForBytesWritten(200)
        sys.exit(0)
    QLocalServer.removeServer("wacom-center")   # a stale socket from a crashed run
    server = QLocalServer()
    server.listen("wacom-center")

    live_pad, live_pen, sysname, aspect = detect_devices()
    pad = wacom_profiles.resolve_pad_name(live_pad)
    pen = wacom_profiles.resolve_pen_name(live_pen)
    wacom_profiles.checkpoint(pad, pen, sysname)     # runs at window open
    active = wacom_profiles.active_profile_name() or "My settings"

    state = {}          # precision_tab, pad_tab, pen_tab, profiles_tab - filled by build_tabs()
    win = CenterWindow(state)
    win.setWindowTitle(f"Wacom Center — {active}")
    server.newConnection.connect(
        lambda: (win.showNormal(), win.raise_(), win.activateWindow(),
                 server.nextPendingConnection().disconnectFromServer()))

    tabs = QTabWidget()

    def build_tabs(select=0):
        old_tabs = [state[k] for k in ("precision_tab", "pad_tab", "pen_tab") if k in state]
        if "precision_tab" in state:
            state["precision_tab"].flush_pending()   # its debounce timers must not fire into a freed widget
        d_pad, d_pen, d_sysname, d_aspect = detect_devices()
        r_pad = wacom_profiles.resolve_pad_name(d_pad)
        r_pen = wacom_profiles.resolve_pen_name(d_pen)
        precision_tab = PrecisionTab(d_aspect, r_pad)
        pad_tab = PadTab(r_pad, asleep=bool(r_pad and not d_pad), pen=r_pen, sysname=d_sysname)
        pen_tab = PenTab(r_pad, r_pen, d_sysname)
        if "profiles_tab" in state:
            profiles_tab = state["profiles_tab"]
            profiles_tab.pad, profiles_tab.pen, profiles_tab.sysname = r_pad, r_pen, d_sysname
            profiles_tab.precision_tab, profiles_tab.pad_tab, profiles_tab.pen_tab = (
                precision_tab, pad_tab, pen_tab)
            profiles_tab.refresh()
        else:
            profiles_tab = ProfilesTab(r_pad, r_pen, d_sysname, precision_tab, pad_tab, pen_tab)
            profiles_tab.switched.connect(lambda _name: build_tabs(select=tabs.currentIndex()))
        name = wacom_profiles.active_profile_name() or "My settings"
        for tab in (precision_tab, pad_tab, pen_tab):
            tab.set_profile(name)
        while tabs.count():
            tabs.removeTab(0)
        for old in old_tabs:
            old.deleteLater()                        # removeTab only detaches: the widgets lived on, a set per switch
        tabs.addTab(precision_tab, "Precision")
        tabs.addTab(pad_tab, "Pad buttons")
        tabs.addTab(pen_tab, "Pen")
        tabs.addTab(profiles_tab, "Profiles")
        tabs.setCurrentIndex(select)
        win.setWindowTitle(f"Wacom Center — {name}")
        state.update(precision_tab=precision_tab, pad_tab=pad_tab, pen_tab=pen_tab,
                     profiles_tab=profiles_tab)

    build_tabs(select=1 if "pad" in sys.argv[1:] else 0)

    links = QHBoxLayout()
    for label, cmd in (("Pie editor (Kando)", ["kando", "--settings"]),
                       ("System tablet page", ["systemsettings", "kcm_tablet"])):
        btn = QPushButton(label)
        btn.clicked.connect(lambda _, c=cmd: subprocess.Popen(c))
        links.addWidget(btn)
    layout = QVBoxLayout(win)
    layout.addWidget(tabs)
    layout.addLayout(links)
    win.resize(760, 720)                   # the Pad tab is a 6-column table now

    def on_quit():
        state["precision_tab"].flush_pending()
        wacom_profiles.checkpoint(state["pad_tab"].pad, state["pen_tab"].pen, state["pen_tab"].sysname)

    app.aboutToQuit.connect(on_quit)         # fires on window close and at logout; the close question
    sys.excepthook = show_exception          # itself lives in CenterWindow.closeEvent
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
