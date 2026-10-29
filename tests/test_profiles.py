#!/usr/bin/env python3
# test_profiles.py <scratch dir> <repo dir> - Qt-free rig for tablet_profiles.py.
# Scratch XDG_* dirs; stubs/profiles-only/{kreadconfig6,kwriteconfig6,qdbus6}
# wrap the real binaries with a call log (a scratch kcminputrc reads/writes
# exactly like the live one); the shared stubs/busctl already logs every
# call unconditionally, reused here for pressure. No Qt, no tablet, no KWin.
import base64
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRATCH = Path(sys.argv[1]) / "profiles-test"
REPO = Path(sys.argv[2]).resolve()
shutil.rmtree(SCRATCH, ignore_errors=True)
for sub in ("config", "data", "runtime", "state", "libwacom"):
    (SCRATCH / sub).mkdir(parents=True)

STUB_LOG = SCRATCH / "stub.log"
os.environ.update({
    "XDG_CONFIG_HOME": str(SCRATCH / "config"),
    "XDG_DATA_HOME": str(SCRATCH / "data"),
    "XDG_RUNTIME_DIR": str(SCRATCH / "runtime"),
    "XDG_STATE_HOME": str(SCRATCH / "state"),
    "WCC_LIBWACOM_DIR": str(SCRATCH / "libwacom"),
    "STUB_LOG": str(STUB_LOG),
    "PATH": f"{HERE / 'stubs' / 'profiles-only'}:{HERE / 'stubs'}:{os.environ['PATH']}",
    "DBUS_SESSION_BUS_ADDRESS": f"unix:path={SCRATCH}/no-bus",   # kwriteconfig6 --notify writes the file and exits 0
})                                                               # on a dead bus: no broadcasts on the live session
sys.path.insert(0, str(REPO))
import tablet_profiles as wp  # noqa: E402 - env vars must be set first: its paths read them at import

_orig_atomic_write = wp.atomic_write

def _traced_atomic_write(path, text):
    """switch_to's conf block replace has no external command to log (it is
    a plain Python file write) - mark it in the SAME log so switch-order can
    place step 4's and step 6's real subprocess calls relative to it."""
    if str(path) == str(wp.CONF):
        with open(os.environ["STUB_LOG"], "a") as f:
            f.write("CONF-REPLACE\n")
    return _orig_atomic_write(path, text)

wp.atomic_write = _traced_atomic_write

fails = 0

def check(label, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        fails += 1

PAD, PEN, SYSNAME = "Test Pad", "Test Pen", "event42"
VENDOR, PROD_USB, PROD_BT = 1386, 855, 864

def reset_kcminputrc(text=""): wp.KCMINPUTRC.write_text(text)
def reset_data(): shutil.rmtree(wp.DATA, ignore_errors=True); wp._backed_up.clear()
def log_lines_from(pos): return (STUB_LOG.read_text() if STUB_LOG.exists() else "")[pos:].splitlines()
def log_pos(): return len(STUB_LOG.read_text()) if STUB_LOG.exists() else 0
def seed_pad(idx, value): wp.kwrite_literal(["ButtonRebinds", "Tablet", PAD], str(idx), value)
def find_log(lines, prefix, needle): return next((i for i, l in enumerate(lines) if l.startswith(prefix) and needle in l), None)
def before(a, b): return a is not None and b is not None and a < b

def libwacom_tablet_file():
    (SCRATCH / "libwacom" / "wacom-intuos-pro-2-m.tablet").write_text(
        "[Device]\nName=Wacom Intuos Pro M\n"
        "DeviceMatch=usb|056a|0357;bluetooth|056a|0360;\n")

def make_png(width, height, size_bytes):
    body = wp.PNG_SIG + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)
    body += b"\x08\x06\x00\x00\x00" + b"\x00" * 4
    body += b"\x00" * max(0, size_bytes - len(body))
    return base64.b64encode(body).decode("ascii")

# ============================================================ hostile-import
reset_data()
reset_kcminputrc()
seed_pad(2, "Key,Meta+Shift+F12")   # the precision key's own binding: must stay untouched
oversized_png = make_png(30000, 30000, 900 * 1024)
hostile = ("﻿" + "\r\n".join([
    "[Conf]",
    "SCALE=$(touch pwned)",
    "DIM=0.10", "HOLD=0.15", "LONG=0.7", "RING_STEP=0.5", "RECONNECT=60",
    "HOVER_MASK=0x04",                  # bit 2 -> the precision key is pad key 2 (0-based)
    "CHORD_2=Meta + Shift + F8",        # survives -> disables pad key 1
    "CHORD_4=zzz",                      # invalid -> dropped, pad key 3 left alone (key 2 is guarded anyway: the precision key)
    "TOUCH_CHORD_3=Meta+Shift+F7",      # a full chord ON the precision key -> dropped, key untouched
    "OutputArea=1",                     # unknown key -> dropped
    "[Pad]",
    "2=Key,Meta+Shift+F12",             # the precision key's own binding, as checkpoint would capture it
    "\tGARBAGE=IGNORED",                # a tab before an unknown key -> dropped
    "BADLINE\x1cNOEQUALS",              # a stray control char, no '=' -> dropped
    "[Image]",
    f"png={oversized_png}",
]) + "\r\n")
wp.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
(wp.PROFILES_DIR / "Hostile.tcprofile").write_bytes(hostile.encode("utf-8"))
warns_h = []
wp.switch_to("Hostile", pad=PAD, pen=None, sysname=None, status=warns_h.append)
declared = subprocess.run(["env", "-i", "bash", "-c", f'. "{wp.CONF}"; declare -p'],      # sourced in the scratch:
                          capture_output=True, text=True, cwd=SCRATCH).stdout             # a slipped payload lands there
check("hostile-import: pwned was never created", not (Path.cwd() / "pwned").exists() and not (SCRATCH / "pwned").exists())
check("hostile-import: CHORD_2 reads Meta+Shift+F8", 'CHORD_2="Meta+Shift+F8"' in declared)
check("hostile-import: pad key 1 disabled", wp.kreadconfig6(["ButtonRebinds", "Tablet", PAD], "1") == "Disabled")
check("hostile-import: pad key 3 NOT disabled (invalid chord dropped, left alone)", wp.kreadconfig6(["ButtonRebinds", "Tablet", PAD], "3") != "Disabled")
check("hostile-import: the HOVER_MASK key's own binding is untouched", wp.kreadconfig6(["ButtonRebinds", "Tablet", PAD], "2") == "Key,Meta+Shift+F12")
check("hostile-import: the oversized PNG was dropped with a warning", any("too large" in w for w in warns_h))

big = "[Conf]\nSCALE=0.30\n" + "#" * (2 * 1024 * 1024 + 100)
(wp.PROFILES_DIR / "HostileBig.tcprofile").write_text(big)
conf_before, kcm_before = wp.CONF.read_bytes(), wp.KCMINPUTRC.read_bytes()
stopped_at = []
try:
    wp.switch_to("HostileBig", pad=PAD, pen=None, sysname=None)
except wp.SwitchStopped as err:
    stopped_at.append(err.step)
check("hostile-import: a 3 MB profile stops the switch at step 3", stopped_at == [3])
check("hostile-import: conf byte-unchanged after the refused switch", wp.CONF.read_bytes() == conf_before)
check("hostile-import: kcminputrc byte-unchanged after the refused switch", wp.KCMINPUTRC.read_bytes() == kcm_before)

# ================================================================= pie-rule
reset_data()
reset_kcminputrc()
seed_pad(3, "Key,Space")
wp.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
sec = wp.blank_sections()
sec["Conf"]["CHORD_4"] = "Meta+Shift+F11"
sec["Pad"]["3"] = "Key,Space"
wp.write_profile(wp.profile_path("PieRule"), sec)
wp.switch_to("PieRule", pad=PAD, pen=None, sysname=None)
check("pie-rule: CHORD_4 disables live pad key 3", wp.kreadconfig6(["ButtonRebinds", "Tablet", PAD], "3") == "Disabled")

# ============================================================= switch-order
reset_data()
reset_kcminputrc()
libwacom_tablet_file()
kcm_pen_seed = (f"[Libinput][{VENDOR}][{PROD_USB}][{PEN}]\nOutputArea=0,0,1,1\n")
wp.KCMINPUTRC.write_text(kcm_pen_seed)
seed_pad(0, "")           # absent -> target wants Disabled -> a step-4 write
sec = wp.blank_sections()
sec["Pad"]["0"] = "Disabled"
sec["Pad"]["1"] = "Key,F5"
sec["Pressure"]["TabletToolPressureCurve"] = "0.1,0.2;0.8,0.9;"
sec["Pressure"]["TabletToolPressureRangeMin"] = "0.05"
sec["Pressure"]["TabletToolPressureRangeMax"] = "0.95"
wp.write_profile(wp.profile_path("SwitchOrder"), sec)
pos = log_pos()
wp.switch_to("SwitchOrder", pad=PAD, pen=PEN, sysname=SYSNAME)
lines = log_lines_from(pos)
idx_step4 = find_log(lines, "kwriteconfig6", "--key 0 Disabled")
idx_conf = next((i for i, l in enumerate(lines) if l == "CONF-REPLACE"), None)
idx_step6_pad = find_log(lines, "kwriteconfig6", "--key 1 Key,F5")
idx_step6_pressure = find_log(lines, "kwriteconfig6", "TabletToolPressureCurve")
check("switch-order: step 4 write happened", idx_step4 is not None)
check("switch-order: conf replace happened", idx_conf is not None)
check("switch-order: step 4 before the conf replace", before(idx_step4, idx_conf))
check("switch-order: conf replace before the step 6 pad write", before(idx_conf, idx_step6_pad))
check("switch-order: conf replace before the step 6 pressure write", before(idx_conf, idx_step6_pressure))
check("switch-order: no reconfigure anywhere", not any("reconfigure" in l for l in lines))   # the call, whatever the binary

# =========================================================== owned-and-kept
reset_data()
reset_kcminputrc()
wp.CONF.write_text("WARP=0\n# a comment\nSCALE=0.50\n")
sec = wp.blank_sections()
sec["Conf"]["DIM"] = "0.20"       # SCALE stays absent/empty in the target -> removed from the conf
wp.write_profile(wp.profile_path("OwnedKept"), sec)
wp.switch_to("OwnedKept", pad=None, pen=None, sysname=None)
conf_lines = wp.CONF.read_text().splitlines()
check("owned-and-kept: an owned key empty in the profile is removed", not any(l.startswith("SCALE=") for l in conf_lines))
check("owned-and-kept: WARP=0 kept", "WARP=0" in conf_lines)
check("owned-and-kept: the comment line kept", "# a comment" in conf_lines)
check("owned-and-kept: DIM written", "DIM=0.20" in conf_lines)

# ============================================================ live-roundtrip
FIXTURES = HERE / "fixtures" / "live-2026-09"
reset_data()
shutil.copy(FIXTURES / "tabprec.conf", wp.CONF)
shutil.copy(FIXTURES / "kcminputrc", wp.KCMINPUTRC)
LIVE_PAD, LIVE_PEN = "Wacom Intuos Pro M Pad", "Wacom Intuos Pro M Pen"
fixture_conf = wp.CONF.read_bytes()
wp.checkpoint(LIVE_PAD, LIVE_PEN, None)               # creates "My settings" from the fixture
sec = wp.blank_sections()
sec["Conf"]["SCALE"] = "0.5000"
wp.write_profile(wp.profile_path("Away"), sec)
wp.switch_to("Away", pad=LIVE_PAD, pen=LIVE_PEN, sysname=None)
wp.switch_to("My settings", pad=LIVE_PAD, pen=LIVE_PEN, sysname=None)
def conf_lines(data): return sorted(l for l in data.decode().splitlines() if l.strip())   # the lines, not their order:
check("live-roundtrip: the conf holds the fixture's lines", conf_lines(wp.CONF.read_bytes()) == conf_lines(fixture_conf))   # the switch writes CONF_KEYS order
for idx, want in (("1", "Disabled"), ("2", "Key,Space"), ("3", "Disabled"), ("4", "Key,Shift"),
                  ("5", "Key,Control"), ("6", "Key,Alt"), ("7", "Key,Meta+Shift+F12")):
    check(f"live-roundtrip: pad key {idx} equal", wp.kreadconfig6(["ButtonRebinds", "Tablet", LIVE_PAD], idx) == want)

# ============================================================ pressure-writes
reset_data()
reset_kcminputrc()
libwacom_tablet_file()
wp.KCMINPUTRC.write_text(f"[Libinput][{VENDOR}][{PROD_USB}][{PEN}]\nOutputArea=0,0,1,1\n")
sec = wp.blank_sections()
sec["Pressure"]["TabletToolPressureCurve"] = "0.1,0.2;0.8,0.9;"
sec["Pressure"]["TabletToolPressureRangeMin"] = "0.05"
sec["Pressure"]["TabletToolPressureRangeMax"] = "0.95"
wp.write_profile(wp.profile_path("Pressure"), sec)
pos = log_pos()
wp.switch_to("Pressure", pad=None, pen=PEN, sysname=None)     # not connected
lines_disc = log_lines_from(pos)
check("pressure-writes: 855 group written while disconnected", wp.kreadconfig6(["Libinput", VENDOR, PROD_USB, PEN], "TabletToolPressureCurve") == "0.1,0.2;0.8,0.9;")
check("pressure-writes: the 864 sibling group created", wp.kreadconfig6(["Libinput", VENDOR, PROD_BT, PEN], "TabletToolPressureCurve") == "0.1,0.2;0.8,0.9;")
check("pressure-writes: no busctl set-property while disconnected", not any("busctl" in l and "set-property" in l for l in lines_disc))
reset_kcminputrc()
wp.KCMINPUTRC.write_text(f"[Libinput][{VENDOR}][{PROD_USB}][{PEN}]\nOutputArea=0,0,1,1\n")
wp.set_active_profile("Pressure")     # already active from before; switch to itself, now connected
pos = log_pos()
wp.switch_to("Pressure", checkpoint_=False, pad=None, pen=PEN, sysname=SYSNAME)
lines_conn = log_lines_from(pos)
check("pressure-writes: busctl set-property logged once connected",
      any("busctl" in l and "set-property" in l and "pressureCurve" in l for l in lines_conn))

# ======================================================= left-handed-writes
reset_kcminputrc(f"[Libinput][{VENDOR}][{PROD_USB}][{PEN}]\nOutputArea=0,0,1,1\n")
libwacom_tablet_file()
pos = log_pos()
getattr(wp, "apply_left_handed", lambda *a, **k: None)(True, PEN, None)     # the tablet asleep: no live pen
lines_lh = log_lines_from(pos)
check("left-handed-writes: one tick holds on both connections (855 written, the 864 sibling made), "
      "and nothing live while disconnected",
      wp.kreadconfig6(["Libinput", VENDOR, PROD_USB, PEN], "LeftHanded") == "true"
      and wp.kreadconfig6(["Libinput", VENDOR, PROD_BT, PEN], "LeftHanded") == "true"
      and not any("set-property" in l for l in lines_lh))
state = getattr(wp, "left_handed_state", lambda *a: None)
os.environ["STUB_LEFT"] = "false"            # KWin says otherwise: it is what the pen does now
said_live = state(PEN, SYSNAME)
del os.environ["STUB_LEFT"]
check("left-handed-state: KWin's live answer wins; with none, any pen group's LeftHanded=true",
      said_live is False and state(PEN, SYSNAME) is True and state(PEN, None) is True)

# ============================================================== lock-timeout
reset_data()
reset_kcminputrc()
wp.CONF.write_text("SCALE=0.36\n")
wp.RD.mkdir(parents=True, exist_ok=True)
holder = subprocess.Popen([sys.executable, "-c",
    "import fcntl,time;f=open(%r,'w');fcntl.flock(f,fcntl.LOCK_EX);time.sleep(6)" % str(wp.RD / "conf.lock")])
time.sleep(0.4)
lock_conf_before = wp.CONF.read_bytes()
t0 = time.monotonic()
raised = False
try:
    wp.save_conf_key("SCALE", "0.77")
except TimeoutError:
    raised = True
elapsed = time.monotonic() - t0
lock_conf_after = wp.CONF.read_bytes()
holder.wait(timeout=10)
check("lock-timeout: TimeoutError raised after ~5 s", raised and 4.5 <= elapsed <= 8.0)
check("lock-timeout: conf byte-unchanged", lock_conf_before == lock_conf_after)

# ============================================================= active-missing
reset_data()
reset_kcminputrc()
wp.CONF.write_text("SCALE=0.36\n")
wp.checkpoint(None, None, None)
active = wp.active_profile_name()
wp.profile_path(active).unlink()
notes = []
wp.checkpoint(None, None, None, status=notes.append)
check("active-missing: the file is recreated", wp.profile_path(active).exists())
check("active-missing: a warning noted", any("recreated" in n for n in notes))

# ============================================================ precision-literal
reset_data()
reset_kcminputrc()
sec = wp.blank_sections()
sec["Pad"]["7"] = "Key,Meta+Shift+F12"
sec["Conf"]["HOVER_MASK"] = "0x80"
wp.write_profile(wp.profile_path("Literal"), sec)
wp.switch_to("Literal", pad=PAD, pen=None, sysname=None)
check("precision-literal: [Pad] 7 written as written",
      wp.kreadconfig6(["ButtonRebinds", "Tablet", PAD], "7") == "Key,Meta+Shift+F12")
check("precision-literal: HOVER_MASK written as written", "HOVER_MASK=0x80" in wp.CONF.read_text().splitlines())

# ========================================================= ring-scale-survives
reset_kcminputrc()
wp.CONF.write_text("SCALE=0.30\nDIM=0.10\n")
RING_BIN = SCRATCH / "bin"                 # the ring script beside a no-op size preview and toggle: the real preview
RING_BIN.mkdir(exist_ok=True)              # is a Qt window on the desktop, and the gate stays headless
shutil.copy(REPO / "tablet-precision-size.sh", RING_BIN / "tablet-precision-size.sh")
(RING_BIN / "tablet-size-preview.py").write_text("#!/usr/bin/env python3\n")
(RING_BIN / "tablet-precision.sh").write_text("#!/bin/bash\n")
for f in RING_BIN.iterdir():
    f.chmod(0o755)
subprocess.run(["bash", str(RING_BIN / "tablet-precision-size.sh"), "up"], env=os.environ, timeout=5,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
mid_lines = wp.CONF.read_text().splitlines()
scale_line = next((l for l in mid_lines if l.startswith("SCALE=")), None)
wp.save_conf_key("DIM", "0.20")
final_lines = wp.CONF.read_text().splitlines()
check("ring-scale-survives: the ring changed SCALE", scale_line is not None and scale_line != "SCALE=0.30")
check("ring-scale-survives: SCALE kept after the DIM save", scale_line in final_lines)
check("ring-scale-survives: DIM updated", "DIM=0.20" in final_lines)

# ==================================================================== names
def _refused(name):
    try:
        wp.valid_name(name)
        return False
    except ValueError:
        return True

cyr = "Профиль"
check("names: valid_name keeps a Cyrillic name as is", wp.valid_name(cyr) == cyr)
for bad in ("", "..", "a/b"):
    check(f"names: {bad!r} refused", _refused(bad))
check("names: a 250-byte name refused", _refused("x" * 250))

# ==================================================================== package setup
# The .deb copy's per-user steps (install.sh's 3/5 and the helper's start): a scratch HOME,
# the logging busctl and systemctl stubs; never the live kglobalaccel or user manager.
os.environ["HOME"] = str(SCRATCH / "home")
reset_data()
pos = log_pos()
check("package: a copy outside the package folder does nothing",
      wp.package_setup(here=REPO) == (False, []) and not log_lines_from(pos))
first, notes = wp.package_setup(here=wp.PKG_DIR)
lines = log_lines_from(pos)
regs = [l for l in lines if "setShortcut asaiu" in l]
check("package: the first start runs", first and wp.SETUP_MARK.exists())
check("package: 3 doRegister calls", sum("doRegister" in l for l in lines) == 3)
check("package: F12 / F10 / F9 to toggle / bigger / smaller",
      [l.split()[-2:] for l in regs] == [["318767163", "2"], ["318767161", "2"], ["318767160", "2"]]
      and all(c in r for c, r in zip(("toggle", "bigger", "smaller"), regs)))
check("package: the helper unit started after a daemon-reload",
      lines[-2:] == ["systemctl --user daemon-reload", f"systemctl --user start {wp.HOVER_UNIT}"])
pos = log_pos()
check("package: a second start only asks kglobalaccel for the toggle",
      wp.package_setup(here=wp.PKG_DIR) == (False, [])
      and [l.split()[6] for l in log_lines_from(pos)] == ["getComponent"])
pos = log_pos()
os.environ["STUB_NO_COMPONENT"] = "1"          # a purge and a login made kglobalaccel forget it
first, notes = wp.package_setup(here=wp.PKG_DIR)
del os.environ["STUB_NO_COMPONENT"]
check("package: a lost toggle is registered again, no first-start question",
      not first and sum("setShortcut asaiu" in l for l in log_lines_from(pos)) == 3
      and notes[0].startswith("Shortcuts registered"))
wp.SETUP_MARK.unlink()
old = SCRATCH / "home/.config/autostart/tablet-hover.desktop"
old.parent.mkdir(parents=True)
old.write_text("[Desktop Entry]\n")
pos = log_pos()
first, notes = wp.package_setup(here=wp.PKG_DIR)
check("package: an install.sh copy stops it and is named",
      not first and not log_lines_from(pos) and "  ~/.config/autostart/tablet-hover.desktop" in notes)
old.unlink()
os.environ["STUB_CALL_FAIL"] = "1"
first, notes = wp.package_setup(here=wp.PKG_DIR)
del os.environ["STUB_CALL_FAIL"]
check("package: a refused registration leaves no mark and no first-start question",
      not first and not wp.SETUP_MARK.exists() and notes[0].startswith("Could not register"))
os.environ["STUB_KEY_TAKEN"] = "1"
first, notes = wp.package_setup(here=wp.PKG_DIR)
del os.environ["STUB_KEY_TAKEN"]
check("package: a key another action owns is named, not reported as registered",
      first and wp.SETUP_MARK.exists() and notes[0].startswith("No key for: Precision mode toggle"))

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
