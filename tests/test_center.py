#!/usr/bin/env python3
# test_center.py <scratch dir> <repo dir> - Wacom Center offscreen: the Left-handed box turns the
# table with the tablet, every row keeps its own key, the ring's turn directions and default.
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


def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
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
import wacom_center as wc  # noqa: E402


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
if flip is not None:
    flip.touches[2].setText("Ctrl")                            # key 3's Touch box, not applied
    attempt(lambda: flip.left.setChecked(True))                # the user ticks the box
    moved = attempt(lambda: grid_of(flip).getItemPosition(grid_of(flip).indexOf(flip.touches[2]))[:2])
log = STUB_LOG.read_text() if STUB_LOG.exists() else ""
check("C4 the box turns the table in place (key 3's unapplied Touch edit moves with its row to 11, 4) "
      "and sets Left-handed mode for both connections and live",
      flip is not None and moved == (11, 4) and flip.touches[2].text() == "Ctrl"
      and wc.wacom_profiles.kreadconfig6(["Libinput", 1386, 855, PEN], "LeftHanded") == "true"
      and wc.wacom_profiles.kreadconfig6(["Libinput", 1386, 864, PEN], "LeftHanded") == "true"
      and f"InputDevice/{SYSNAME} org.kde.KWin.InputDevice leftHanded b true" in log)


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
      wc.wacom_profiles.kreadconfig6(["ButtonRebinds", "TabletRing", PAD, 1], "0")
      == "AxisKey,Meta+Shift+F6,Meta+Shift+F7,600")

KCM.write_text(f"[Libinput][1386][855][{PEN}]\n")             # no ring binding yet: a fresh install
fresh = attempt(lambda: wc.PrecisionTab(1.6, PAD))
fresh_ticked = attempt(lambda: fresh.ring_swap.isChecked())
attempt(lambda: fresh.apply_ring())
written = wc.wacom_profiles.kreadconfig6(["ButtonRebinds", "TabletRing", PAD, 0], "0")
KCM.write_text(f"[ButtonRebinds][TabletRing][{PAD}][0]\n0=AxisKey,Meta+Shift+F10,Meta+Shift+F9,600\n")
older = attempt(lambda: wc.PrecisionTab(1.6, PAD))
check("C7 by default a clockwise turn grows the area (the smaller chord first, Swap unticked), and an "
      "older install's order (the bigger chord first) reads as Swap ticked",
      fresh_ticked is False and written == "AxisKey,Meta+Shift+F9,Meta+Shift+F10,600"
      and attempt(lambda: older.ring_swap.isChecked()) is True)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
