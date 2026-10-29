#!/usr/bin/env python3
# test_center.py <scratch dir> <repo dir> - Tablet Center offscreen: the Left-handed box turns the
# table with the tablet, every row keeps its own key, the ring's turn directions and default; the
# Precision tab's conf round trip (P); a pressure write that never reached the pen (B3-1).
# Scratch XDG_* dirs, a dead D-Bus address; the profiles rig's stubs run the real
# kreadconfig6/kwriteconfig6 on a scratch kcminputrc
# and the shared busctl stub plays KWin. Qt offscreen: no tablet, no window on the desktop.
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRATCH = Path(sys.argv[1]) / "center-test"
REPO = Path(sys.argv[2]).resolve()
shutil.rmtree(SCRATCH, ignore_errors=True)
for sub in ("config", "data", "runtime", "state", "libwacom"):
    (SCRATCH / sub).mkdir(parents=True)
STUB_LOG = SCRATCH / "stub.log"
os.environ.update({
    "QT_QPA_PLATFORM": "offscreen",
    "XDG_CONFIG_HOME": str(SCRATCH / "config"),
    "XDG_DATA_HOME": str(SCRATCH / "data"),
    "XDG_RUNTIME_DIR": str(SCRATCH / "runtime"),
    "XDG_STATE_HOME": str(SCRATCH / "state"),
    "WCC_LIBWACOM_DIR": str(SCRATCH / "libwacom"),
    "STUB_LOG": str(STUB_LOG),
    "PATH": f"{HERE / 'stubs' / 'profiles-only'}:{HERE / 'stubs'}:{os.environ['PATH']}",
    "DBUS_SESSION_BUS_ADDRESS": f"unix:path={SCRATCH}/no-bus",   # --notify reaches no live session
})
PAD, PEN, SYSNAME = "Test Pad", "Test Pen", "event42"
KCM = SCRATCH / "config" / "kcminputrc"
(SCRATCH / "config" / "tabprec.conf").write_text("SCALE=0.36\nDIM=0.10\nHOLD=0.15\n")
(SCRATCH / "libwacom" / "test-pro-m.tablet").write_text(        # the sibling: USB 0x357 = 855, BT 0x360 = 864
    "[Device]\nName=Test Pro M\nDeviceMatch=usb|056a|0357;bluetooth|056a|0360;\n")
sys.path.insert(0, str(REPO))

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond or not detail else f"\n{detail}"))
    fails += 0 if cond else 1


def attempt(call):
    """An old or broken copy must fail its check, not stop the rig."""
    try:
        return call()
    except Exception as err:                 # noqa: BLE001
        print(f"# {err!r}")
        return None


from PyQt6.QtWidgets import QApplication, QGridLayout, QMessageBox  # noqa: E402 - the environment comes first

app = QApplication([])
SAID = []                                    # every dialog the tab opened: modal, so never shown here
for _name in ("warning", "information", "question"):
    setattr(QMessageBox, _name, staticmethod(lambda *a, **k: SAID.append(a) or QMessageBox.StandardButton.Ok))
import tablet_center as wc  # noqa: E402


def grid_of(tab):
    """The tab's one table, found by its type: a copy without the turn has it too, so a pin there
    fails on the key it binds, not on a missing name."""
    return tab.findChild(QGridLayout)


def press_in_row(tab, row):
    """The Press box drawn in this grid row, found by its own placeholder - whatever its column."""
    for c in range(6):
        item = grid_of(tab).itemAtPosition(row, c)
        w = item.widget() if item else None
        if isinstance(w, wc.AntsLineEdit) and w.placeholderText() == "Meta+Shift+F10, or Shift":
            return w
    return None


def top_row_binding(chord):
    """A shortcut typed into the top key row's Press box, then Apply, as the user does it."""
    tab = wc.PadTab(PAD, pen=PEN, sysname=SYSNAME)
    press = press_in_row(tab, 2)
    press.setText(chord)
    tab.apply()
    return tab


# --- C: the turned table. Artem 2026-10-23: "We don't want people to flip the tablet, bind the key,
#        and find out that their bind is somewhere wrong because settings still work in right-hand way" ---
KCM.write_text(f"[Libinput][1386][855][{PEN}]\nLeftHanded=true\n")
tab = attempt(lambda: wc.PadTab(PAD, pen=PEN, sysname=SYSNAME))
pic = attempt(lambda: grid_of(tab).itemAtPosition(2, 5).widget().toolTip())
prm = attempt(lambda: grid_of(tab).itemAtPosition(2, 0).widget() is tab.prms[7])
check("C1 turned: the top row is key 8, its picture at the far right, the Precision tick at the far left",
      attempt(lambda: tab.left.isChecked()) is True and pic == "Key 8" and prm is True)

attempt(lambda: top_row_binding("Meta+F5"))
check("C2 turned: a shortcut typed in the top row lands on key 8 (libinput button 7) at Apply",
      wc.kread(PAD, 7) == "Meta+F5" and wc.kread(PAD, 0) != "Meta+F5")

KCM.write_text(f"[Libinput][1386][855][{PEN}]\n")
attempt(lambda: top_row_binding("Meta+F6"))
check("C3 right-handed: the same top-row shortcut lands on key 1 (button 0)",
      wc.kread(PAD, 0) == "Meta+F6" and wc.kread(PAD, 7) != "Meta+F6")

KCM.write_text(f"[Libinput][1386][855][{PEN}]\n")             # one group: the Bluetooth sibling must be made
flip = attempt(lambda: wc.PadTab(PAD, pen=PEN, sysname=SYSNAME))
moved = None
popens, real_popen = [], wc.subprocess.Popen
wc.subprocess.Popen = lambda argv, **k: (popens.append(argv) if str(wc.TOGGLE) in argv   # never the real toggle from a rig
                                         else real_popen(argv, **k))
if flip is not None:
    flip.touches[2].setText("Ctrl")                            # key 3's Touch box, not applied
    attempt(lambda: flip.left.setChecked(True))                # the user ticks the box
    moved = attempt(lambda: grid_of(flip).getItemPosition(grid_of(flip).indexOf(flip.touches[2]))[:2])
log = STUB_LOG.read_text() if STUB_LOG.exists() else ""
check("C4 the box turns the table in place (key 3's unapplied Touch edit moves with its row to 11, 4) "
      "and sets Left-handed mode for both connections and live",
      flip is not None and moved == (11, 4) and flip.touches[2].text() == "Ctrl"
      and wc.tablet_profiles.kreadconfig6(["Libinput", 1386, 855, PEN], "LeftHanded") == "true"
      and wc.tablet_profiles.kreadconfig6(["Libinput", 1386, 864, PEN], "LeftHanded") == "true"
      and f"InputDevice/{SYSNAME} org.kde.KWin.InputDevice leftHanded b true" in log)
with_pen = list(popens)
popens.clear()
os.environ["STUB_NO_PEN"] = "1"
if flip is not None:
    flip.sysname = None
    attempt(lambda: flip.left.setChecked(False))               # no pen anywhere: nothing to send
del os.environ["STUB_NO_PEN"]
wc.subprocess.Popen = real_popen
check("C4b R2-7: a tick with the pen connected re-sends the area (toggle resume); with no pen, nothing",
      with_pen == [["bash", str(wc.TOGGLE), "resume"]] and popens == [])


def amber(img, x, y):
    c = img.pixelColor(x, y)
    return c.red() > 180 and 120 < c.green() < 210 and c.blue() < 90


turned_img = attempt(lambda: wc.ring_icon(1, True).toImage())
plain_img = wc.ring_icon(1).toImage()
check("C5 turned: ring mode 1's light sits upper right, where the turned tablet shows it (lower left unturned)",
      turned_img is not None and amber(turned_img, 15, 6) and not amber(turned_img, 6, 15)
      and amber(plain_img, 6, 15))

# --- C: the ring's directions. KWin fires a mode's first chord (up) on a COUNTER-clockwise turn: Artem's
#        ring holds F9 (smaller) first, and a clockwise turn grows his area (2026-10-26, A17-4) ---
KCM.write_text(f"[Libinput][1386][855][{PEN}]\n")
ring_tab = attempt(lambda: wc.PadTab(PAD, pen=PEN, sysname=SYSNAME))
labelled = {b.placeholderText(): b for b in ring_tab.rings[1]} if ring_tab is not None else {}
if set(labelled) == {"clockwise", "counter-clockwise"}:
    labelled["clockwise"].setText("Meta+Shift+F7")
    labelled["counter-clockwise"].setText("Meta+Shift+F6")
    attempt(ring_tab.apply)
check("C6 a shortcut typed in ring mode 2's \"clockwise\" box lands in the second field, the one a "
      "clockwise turn fires",
      wc.tablet_profiles.kreadconfig6(["ButtonRebinds", "TabletRing", PAD, 1], "0")
      == "AxisKey,Meta+Shift+F6,Meta+Shift+F7,600")

KCM.write_text(f"[Libinput][1386][855][{PEN}]\n")             # no ring binding yet: a fresh install
fresh = attempt(lambda: wc.PrecisionTab(1.6, PAD))
fresh_ticked = attempt(lambda: fresh.ring_swap.isChecked())
attempt(lambda: fresh.apply_ring())
written = wc.tablet_profiles.kreadconfig6(["ButtonRebinds", "TabletRing", PAD, 0], "0")
KCM.write_text(f"[ButtonRebinds][TabletRing][{PAD}][0]\n0=AxisKey,Meta+Shift+F10,Meta+Shift+F9,600\n")
older = attempt(lambda: wc.PrecisionTab(1.6, PAD))
check("C7 by default a clockwise turn grows the area (the smaller chord first, Swap unticked), and an "
      "older install's order (the bigger chord first) reads as Swap ticked",
      fresh_ticked is False and written == "AxisKey,Meta+Shift+F9,Meta+Shift+F10,600"
      and attempt(lambda: older.ring_swap.isChecked()) is True)

# --- E1 the Profile specifics box (Profiles tab): no Save widget, a press is written at once into the SHOWN
#        (active) profile, an app another profile owns moves, the tick is the default, a switch re-lists
from PyQt6.QtWidgets import QPushButton as _QPB  # noqa: E402
wc.tablet_profiles.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
for _n in ("Desk", "Krita"):
    wc.tablet_profiles.write_profile(wc.tablet_profiles.profile_path(_n), wc.tablet_profiles.blank_sections())
notes = []
wc.tablet_profiles.RD.mkdir(parents=True, exist_ok=True)
(wc.tablet_profiles.RD / "appfocus.pid").write_text(f"{os.getpid()}\n")    # not a follower: the box must not start one
_popen = wc.subprocess.Popen
wc.subprocess.Popen = lambda *a, **k: None                                 # ...and a start would not run here
wc.tablet_profiles.set_active_profile("Krita")
box = attempt(lambda: wc.PerAppBox(notes.append))
e1 = {}
if box is not None and not isinstance(box, str):
    e1["no_save"] = not [b for b in box.findChildren(_QPB) if b.text().lower().startswith("save")]
    e1["title"] = box.title() == "Profile specifics" and box.profile == "Krita"
    box.pick_app = lambda: ("Krita", ["krita"])
    box.add_clicked()
    e1["added"] = wc.tablet_profiles.perapp_get() == ("", [("Krita", ["krita"])]) and len(box.row_widgets) == 1
    wc.tablet_profiles.set_active_profile("Desk")                            # a switch: the box shows Desk now
    box.reload()
    e1["switched"] = box.profile == "Desk" and not box.row_widgets
    box.pick_app = lambda: ("Krita", ["org.kde.krita"])                     # Desk takes the app Krita owned
    box.add_clicked()
    e1["moved"] = wc.tablet_profiles.perapp_get() == ("", [("Desk", ["org.kde.krita"])])
    box.default.click()
    e1["default"] = wc.tablet_profiles.perapp_get()[0] == "Desk" and box.default.isChecked()
    box.row_widgets[0][2].click()
    e1["removed"] = wc.tablet_profiles.perapp_get() == ("Desk", []) and not box.row_widgets
wc.subprocess.Popen = _popen          # subprocess.run goes through Popen: the stub must not outlive the box
check("E1 the Profile specifics box has no Save, writes at once into the shown profile, moves an app, "
      "sets the default, follows a switch", all(e1.get(k) for k in
      ("no_save", "title", "added", "switched", "moved", "default", "removed")) and len(e1) == 7)

# --- T: the Pen tab's pressure tooltip. The helper's record for the active profile is a file under the state
#        dir; the curve's tooltip is made from it when the pointer enters: numbers, no button ---
import json  # noqa: E402
from PyQt6.QtWidgets import QPushButton  # noqa: E402

KCM.write_text(f"[Libinput][1386][855][{PEN}]\n")
pen_tab = attempt(lambda: wc.PenTab(PAD, PEN, SYSNAME))
tip0 = attempt(lambda: pen_tab.graph.toolTip())
wc.tablet_profiles.set_active_profile("Inks")
rec_file = wc.tablet_profiles.pressure_file("Inks")
rec_file.parent.mkdir(parents=True, exist_ok=True)
bins = [0] * 101
for pct, count in ((8, 2), (30, 5), (45, 10), (61, 3)):          # 20 strokes: sorted, the 2nd = 8 (P10), the 19th = 61 (P95)
    bins[pct] = count
rec_file.write_text(json.dumps({"v": 1, "bucket_s": 900.0, "keep": 40, "active_s": 36000.0 * 2,
                                "strokes": 20, "buckets": {"7": bins}}))
attempt(lambda: pen_tab.graph.refresh_tip())                       # what enterEvent does
tip1 = attempt(lambda: pen_tab.graph.toolTip())
check("T the curve's tooltip is made from the record when the pointer enters",
      isinstance(tip0, str) and tip0.startswith("Pressure record: 0 strokes") and tip1 ==
      "Last 10 hours: your light strokes peak near 8 %, your hardest reach 61 % "
      "(20 strokes, raw pen pressure before the curve).")
check("T the Pen tab has no auto-fit button for the pressure curve",
      pen_tab is not None and not [b for b in pen_tab.findChildren(QPushButton)
                                   if any(w in b.text().lower() for w in ("fit", "auto", "learn"))])
# --- P: the Precision tab's widgets and the conf: what the file says reaches the widgets, what the widgets
#        write reaches the file, and every line the tab does not own stays exactly as the user wrote it ---
CONF = SCRATCH / "config" / "tabprec.conf"
HAND = ("# my own notes\nSCALE = 0.30 # spaces around the sign\nDIM=0.10\nCHORD_2=Meta+Shift+F8 # Blender pie\n"
        "WARP=0\nRING_STEP=1\nRECONNECT=\"30\"\n")
CONF.write_text(HAND)
KCM.write_text(f"[Libinput][1386][855][{PEN}]\n")
prec = attempt(lambda: wc.PrecisionTab(1.6, PAD))
check("P1 the file's values reach the widgets (spaces, a trailing comment, quotes): size 30, dim 10, "
      "ring step 1, reconnect 30",
      prec is not None and prec.size.value() == 30 and prec.dim.value() == 10
      and prec.ring_step.value() == 1.0 and prec.reconnect.value() == 30)
if prec is not None:
    prec.size.setValue(42)
    prec.dim.setValue(15)
    prec.long_press.setValue(1.25)
    prec.reconnect.setValue(45)
    prec.ring_step.setValue(0.75)
    prec.size_min.setValue(10)
    prec.size_max.setValue(60)
    prec.flush_pending()
text = CONF.read_text()
lines = text.splitlines()
check("P2 each widget writes its own key once: SCALE 0.4200, DIM 0.15, LONG 1.25, RECONNECT 45, RING_STEP 0.75, "
      "SCALE_MIN 0.10, SCALE_MAX 0.60",
      all(lines.count(l) == 1 for l in ("SCALE=0.4200", "DIM=0.15", "LONG=1.25", "RECONNECT=45", "RING_STEP=0.75",
                                         "SCALE_MIN=0.10", "SCALE_MAX=0.60"))
      and sum(1 for l in lines if l.replace(" ", "").startswith(("SCALE=", "DIM=", "RECONNECT=", "RING_STEP="))) == 4,
      text)
check("P3 the lines the tab does not own are kept as written (a comment, a pie chord with its comment, WARP=0)",
      all(l in lines for l in ("# my own notes", "CHORD_2=Meta+Shift+F8 # Blender pie", "WARP=0")), text)
back = attempt(lambda: wc.PrecisionTab(1.6, PAD))
check("P4 a new window reads it all back: size 42, dim 15, long 1.25, reconnect 45, step 0.75, limits 10 and 60",
      back is not None and back.size.value() == 42 and back.dim.value() == 15 and back.long_press.value() == 1.25
      and back.reconnect.value() == 45 and back.ring_step.value() == 0.75
      and back.size_min.value() == 10 and back.size_max.value() == 60)
check("P5 conf_value reads a raw key like the shell does: CHORD_2 without its comment, WARP, and '' for a missing key",
      wc.conf_value("CHORD_2") == "Meta+Shift+F8" and wc.conf_value("WARP") == "0"
      and wc.conf_value("NOPE") == "")
CONF.write_text("SCALE = 0,25\nDIM=\"0.2\"\nLONG=\n")
rc = wc.read_conf()
check("P6 read_conf: a comma decimal and quotes read, an empty value keeps the default (0.7)",
      rc["SCALE"] == 0.25 and rc["DIM"] == 0.2 and rc["LONG"] == 0.7)
CONF.write_text("SCALE=0.36\nDIM=0.10\nHOLD=0.15\n")

# --- B: B3-1 - a live pressure write that fails is no longer silent, and no write goes to a node the pen left ---
CURVE = "0.1000,0.2000;0.8000,0.9000;"
KCM.write_text(f"[Libinput][1386][855][{PEN}]\n")
os.environ["STUB_SET_FAIL"] = "1"
told = []
ret = attempt(lambda: wc.tablet_profiles.apply_pressure(CURVE, 0.0, 1.0, PEN, "event5", status=told.append))
del os.environ["STUB_SET_FAIL"]
check("B1 a refused live write: apply_pressure returns a warning, the status gets it, the saved keys stay",
      isinstance(ret, str) and told == [ret]
      and wc.tablet_profiles.kreadconfig6(["Libinput", 1386, 855, PEN], "TabletToolPressureCurve") == CURVE)
told.clear()
ret = attempt(lambda: wc.tablet_profiles.apply_pressure(CURVE, 0.0, 1.0, PEN, "event5", status=told.append))
check("B1 an accepted live write: no warning", ret is None and not any("did not take" in t for t in told))
os.environ["STUB_STALE"] = "event42"                      # the pen left event42 (a bus switch): event5 is its node now
check("B2 live_pen_sysname: a node the pen left gives the node KWin lists; a live one stays",
      attempt(lambda: wc.live_pen_sysname("event42")) == "event5" and attempt(lambda: wc.live_pen_sysname("event5")) == "event5")
STUB_LOG.write_text("")
pen_tab = attempt(lambda: wc.PenTab(PAD, PEN, "event42"))
if pen_tab is not None:
    pen_tab.p1x.setValue(0.1)
    attempt(pen_tab.apply)
log = STUB_LOG.read_text()
check("B3 PenTab.apply with a stale node: the live write goes to the pen's real node (event5), none to event42",
      "InputDevice/event5 org.kde.KWin.InputDevice pressureCurve" in log
      and "set-property org.kde.KWin /org/kde/KWin/InputDevice/event42" not in log)

pad_tab = wc.PadTab(PAD, pen=PEN, sysname="event5")
prof = attempt(lambda: wc.ProfilesTab(PAD, PEN, "event42", prec, pad_tab, wc.PenTab(PAD, PEN, "event5")))
if prof is not None:
    secs = wc.tablet_profiles.blank_sections()
    secs["Pressure"]["TabletToolPressureCurve"] = CURVE
    wc.tablet_profiles.write_profile(wc.tablet_profiles.profile_path("Soft"), secs)
    STUB_LOG.write_text("")
    attempt(lambda: prof.request_switch("Soft"))
    log = STUB_LOG.read_text()
    check("B4 a switch with a stale node: the live pressure write goes to event5, not event42",
          "InputDevice/event5 org.kde.KWin.InputDevice pressureCurve" in log
          and "set-property org.kde.KWin /org/kde/KWin/InputDevice/event42" not in log)
    os.environ["STUB_SET_FAIL"] = "1"
    STUB_LOG.write_text("")
    secs["Pressure"]["TabletToolPressureCurve"] = "0.3000,0.3000;0.7000,0.7000;"
    wc.tablet_profiles.write_profile(wc.tablet_profiles.profile_path("Firm"), secs)
    attempt(lambda: prof.request_switch("Firm"))
    del os.environ["STUB_SET_FAIL"]
    check("B5 a switch whose live write is refused SHOWS the warning on the Profiles tab",
          "did not take the pressure change" in prof.status.text())
else:
    check("B4 ProfilesTab builds", False)
    check("B5 ProfilesTab builds", False)
os.environ.pop("STUB_STALE", None)

# --- C: the chord guard. Ctrl+Alt+F1..F12 without Shift makes KWin switch to a text console: refused, the
#        key keeps its binding; F20 (microphone mute) only warns; Shift lifts the refusal ---
KCM.write_text(f"[Libinput][1386][855][{PEN}]\n")
wc.kwrite(PAD, 0, "Meta+F5")
guard = attempt(lambda: wc.PadTab(PAD, pen=PEN, sysname=SYSNAME))
del SAID[:]
if guard is not None:
    guard.presses[0].setText("Ctrl+Alt+F1")
    guard.presses[1].setText("Meta + Ctrl + Alt + F12")
    guard.presses[2].setText("Ctrl+Alt+Shift+F1")
    guard.presses[3].setText("F20")
    guard.touches[4].setText("Ctrl+Alt+F3")
    guard.rings[0][0].setText("Ctrl+Alt+F2")
    guard.rings[0][1].setText("Meta+Shift+F7")
    attempt(guard.apply)
said = " ".join(str(a) for call in SAID for a in call)
check("C8 Ctrl+Alt+F1 and Meta+Ctrl+Alt+F12 on a pad key are refused: the key keeps what it had",
      wc.kread(PAD, 0) == "Meta+F5" and wc.kread(PAD, 1) == "" and said.count("text console") >= 3)
check("C9 Ctrl+Alt+Shift+F1 is written as typed; F20 is written and warned about the microphone",
      wc.kread(PAD, 2) == "Ctrl+Alt+Shift+F1" and wc.kread(PAD, 3) == "F20" and "microphone" in said)
check("C10 a console chord in a Touch box or a ring mode is refused too (the ring box shows the disk again)",
      "TOUCH_CHORD_5" not in (SCRATCH / "config" / "tabprec.conf").read_text()
      and wc.tablet_profiles.kreadconfig6(["ButtonRebinds", "TabletRing", PAD, 0], "0") == ""
      and guard is not None and guard.rings[0][0].text() == "")
pen_guard = attempt(lambda: wc.PenTab(PAD, PEN, SYSNAME))
if pen_guard is not None:
    pen_guard.buttons[331].setText("Ctrl+Alt+F4")
    pen_guard.buttons[332].setText("Ctrl+Alt+Shift+F4")
    attempt(pen_guard.apply)
check("C11 a pen button: Ctrl+Alt+F4 refused, Ctrl+Alt+Shift+F4 written",
      wc.kread_group(["ButtonRebinds", "TabletTool", PEN], 331) == ""
      and wc.kread_group(["ButtonRebinds", "TabletTool", PEN], 332) == "Ctrl+Alt+Shift+F4")
# --- K: a typed-key chord (KEY-01) reaches tabprec.conf as letters only: the toggle script SOURCES that file ---
if guard is not None:
    del SAID[:]
    guard.presses[6].setText("Ctrl+[")
    guard.pp[6].setChecked(True)
    guard.touches[7].setText("Ctrl+'")
    guard.presses[5].setText("Print")                      # no key the device carries: warned, the old value stays
    guard.pp[5].setChecked(True)
    attempt(guard.apply)
import subprocess  # noqa: E402
conf_file = SCRATCH / "config" / "tabprec.conf"
sourced = subprocess.run(["env", "-i", "bash", "-c", '. "$1"; echo "$CHORD_7|$TOUCH_CHORD_8|$CHORD_6"', "x", str(conf_file)],
                         capture_output=True, text=True).stdout.strip()
said = " ".join(str(a) for call in SAID for a in call)
check("K1 Ctrl+[ on a pie press box and Ctrl+' on a touch box are written as words, and sourced by bash back whole",
      sourced == "Ctrl+BracketLeft|Ctrl+Apostrophe|" and "Key 6:" in said and "CHORD_6=" not in conf_file.read_text())

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
