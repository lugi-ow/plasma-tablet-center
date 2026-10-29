#!/usr/bin/env python3
# test_perapp.py <scratch dir> <repo dir> - rig for per-app profiles: the decision table, the storage, and tablet-appfocus.py
# run with --stdin (scratch XDG dirs, the profiles rig's logging stubs, a scratch kcminputrc, a fake HELD file). No KWin, no bus.
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRATCH = Path(sys.argv[1]) / "perapp-test"
REPO = Path(sys.argv[2]).resolve()
shutil.rmtree(SCRATCH, ignore_errors=True)
for sub in ("config", "data", "runtime", "state"):
    (SCRATCH / sub).mkdir(parents=True)
STUB_LOG = SCRATCH / "stub.log"
for stub in list((HERE / "stubs").glob("*")) + list((HERE / "stubs" / "profiles-only").glob("*")):
    if stub.is_file():
        stub.chmod(0o755)                        # a fresh checkout on D has them without the x bit
ENV = dict(os.environ)
ENV.update({
    "XDG_CONFIG_HOME": str(SCRATCH / "config"), "XDG_DATA_HOME": str(SCRATCH / "data"),
    "XDG_RUNTIME_DIR": str(SCRATCH / "runtime"), "XDG_STATE_HOME": str(SCRATCH / "state"),
    "STUB_LOG": str(STUB_LOG),
    "PATH": f"{HERE / 'stubs' / 'profiles-only'}:{HERE / 'stubs'}:{os.environ['PATH']}",
    "DBUS_SESSION_BUS_ADDRESS": f"unix:path={SCRATCH}/no-bus",
})
os.environ.update(ENV)
sys.path.insert(0, str(REPO))
import tablet_profiles as wp  # noqa: E402 - env first: its paths read it at import

fails = 0


def check(label, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        fails += 1


PAD, PEN = "Test Pad", "Wacom Intuos Pro M Pen"
RD = wp.RD
RD.mkdir(parents=True, exist_ok=True)


def writes_from(pos):
    text = STUB_LOG.read_text()[pos:] if STUB_LOG.exists() else ""
    return [l for l in text.splitlines() if l.startswith("kwriteconfig6") and "kcminputrc" in l]


def log_pos():
    return len(STUB_LOG.read_text()) if STUB_LOG.exists() else 0


def ev(cls, **kw):
    d = {"ms": time.time() * 1000, "c": cls, "d": cls, "nw": True, "dlg": False, "tr": False, "sp": False}
    d.update(kw)
    return d


# ================================================================ decision
RULES = [("Krita", ["krita", "org.kde.krita"]), ("Blender", ["blender"])]
dec = lambda e, default="Desk", rules=RULES: wp.perapp_decide(e, default, rules)   # noqa: E731
check("decide: a listed app wants its profile", dec(ev("krita")) == ("want", "Krita"))
check("decide: the desktop file name matches too", dec(ev("x", d="org.kde.krita.desktop")) == ("want", "Krita"))
check("decide: a Wayland class org.kde.krita matches the short name krita", dec(ev("org.kde.krita", d="org.kde.krita"), rules=[("Krita", ["krita"])]) == ("want", "Krita"))
check("decide: ...but a name that only ends the same does not (nokrita)", dec(ev("nokrita"), rules=[("Krita", ["krita"])]) == ("want", "Desk"))
check("decide: kdialog's stand-alone file dialog never switches", dec(ev("org.kde.kdialog"))[0] == "ignore")
check("decide: case does not matter", dec(ev("Blender")) == ("want", "Blender"))
check("decide: an unlisted app wants the default", dec(ev("firefox")) == ("want", "Desk"))
check("decide: no default marked and no match = keep", dec(ev("firefox"), default="")[0] == "keep")
check("decide: Kando's pie window never switches", dec(ev("kando"))[0] == "ignore" and dec(ev("Kando", d="kando"))[0] == "ignore")
check("decide: Kando 3.0's pie (menu.kando.kando, class and desktop file) never switches, but a name that only ends like it does",
      dec(ev("menu.kando.kando", d="menu.kando.Kando.desktop"))[0] == "ignore" and dec(ev("menu.kando.kando"))[0] == "ignore"
      and dec(ev("notkando"))[0] == "want")
check("decide: Tablet Center never switches", dec(ev("tablet-center"))[0] == "ignore" and dec(ev("tablet_center.py"))[0] == "ignore")
check("decide: a dialog never switches, even of a listed app", dec(ev("krita", dlg=True))[0] == "ignore")
check("decide: a transient window never switches", dec(ev("krita", tr=True))[0] == "ignore")
check("decide: a window that is not a normal window never switches", dec(ev("krita", nw=False))[0] == "ignore")
check("decide: the portal file chooser class never switches", dec(ev("xdg-desktop-portal-kde"))[0] == "ignore")
check("decide: no window (focus on the desktop) never switches", dec(None)[0] == "ignore")
check("decide: an empty class never switches", dec(ev("", d=""))[0] == "ignore")
check("split: normalised", wp.perapp_split(" Krita, org.kde.krita.desktop;\nKRITA ,, ") == ["krita", "org.kde.krita"])

# ================================================================ storage
wp.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
for name in ("Desk", "Krita", "Blender"):
    wp.write_profile(wp.profile_path(name), wp.blank_sections())
wp.set_active_profile("Desk")
wp.save_device(PAD, PEN)
check("storage: nothing set = feature off", wp.perapp_get() == ("", []))
wp.perapp_set("Desk", [("Krita", "krita, org.kde.krita"), ("Blender", ["blender"]), ("Gone", [])])
check("storage: round trip, an empty rule is dropped", wp.perapp_get() == ("Desk", RULES))
# 'main' (the commit this branch was cut from) reads and rewrites profiles.ini: the block must survive it
main_src = subprocess.run(["git", "-C", str(REPO), "show", "6ab0707:tablet_profiles.py"], capture_output=True, text=True).stdout
if main_src:
    (SCRATCH / "main_profiles.py").write_text(main_src)
    out = subprocess.run([sys.executable, "-c",
                          "import sys; sys.path.insert(0, %r); import main_profiles as m;"
                          "print(m.active_profile_name()); m.set_active_profile('Krita'); m.set_grid_cell(1, 'Desk')"
                          % str(SCRATCH)], capture_output=True, text=True, env=ENV)
    check("storage: main reads the active profile with the block present", out.stdout.strip() == "Desk")
    check("storage: main rewrites profiles.ini and the per-app block survives", wp.perapp_get() == ("Desk", RULES))
    wp.set_active_profile("Desk")
else:
    print("SKIP main compatibility: no git history here")
wp.perapp_set("", [])
check("storage: set empty removes the block", wp.perapp_get() == ("", []) and "PerApp" not in wp.PROFILES_INI.read_text())
wp.perapp_set("Desk", RULES)
wp.perapp_set("Ghost", [("Krita", ["krita"]), ("Vanished", ["x"])])
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location("appfocus", REPO / "tablet-appfocus.py")
appfocus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(appfocus)
check("storage: rules_now drops a missing profile and a missing default", appfocus.rules_now() == ("", [("Krita", ["krita"])]))

# ==================================================== the follower, --stdin
def seed(profile_pad_key0):
    sec = wp.blank_sections()
    sec["Pad"]["0"] = profile_pad_key0
    return sec


wp.perapp_set("Desk", RULES)
wp.write_profile(wp.profile_path("Desk"), seed("Key,F6"))
wp.write_profile(wp.profile_path("Krita"), seed("Key,F7"))     # differs from Desk in one pad key
wp.write_profile(wp.profile_path("Blender"), seed("Key,F7"))   # same bindings as Krita: Krita <-> Blender costs no write
wp.kwrite_literal(["ButtonRebinds", "Tablet", PAD], "0", "Key,F6")
wp.set_active_profile("Desk")
HELD = RD / "held"


def start():
    return subprocess.Popen([sys.executable, str(REPO / "tablet-appfocus.py"), "--stdin"], stdin=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=ENV)


def send(proc, e):
    proc.stdin.write(("js: TPAPP " + json.dumps(e) + "\n").encode())
    proc.stdin.flush()


def wait_active(name, secs=6.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if wp.active_profile_name() == name:
            return True
        time.sleep(0.05)
    return False


def live_key0():
    return wp.kreadconfig6(["ButtonRebinds", "Tablet", PAD], "0")


proc = start()
time.sleep(0.4)
pos = log_pos()
send(proc, ev("krita"))
t0 = time.monotonic()
ok = wait_active("Krita")
check("follow: a listed app switches the profile", ok and live_key0() == "Key,F7")
check("follow: one focus change = one kcminputrc write (one key differs)", len(writes_from(pos)) == 1)
pos = log_pos()
send(proc, ev("blender"))
check("follow: Krita to Blender (same bindings) switches the profile", wait_active("Blender"))
check("follow: ...with no kcminputrc write", len(writes_from(pos)) == 0)
pos = log_pos()
for e in (ev("kando"), ev("tablet-center"), ev("krita", dlg=True), ev("krita", tr=True), ev("x", nw=False),
          {"ms": time.time() * 1000, "none": True}):
    send(proc, e)
time.sleep(0.8)
check("follow: Kando, Tablet Center, a dialog, a transient, a panel and no window change nothing",
      wp.active_profile_name() == "Blender" and not writes_from(pos))
send(proc, ev("firefox"))
check("follow: an unlisted app switches to the default", wait_active("Desk") and live_key0() == "Key,F6")

# ---- a key held: nothing is written; the last wish is applied once on release
HELD.write_text(f"{os.getpid()} 1\n")
pos = log_pos()
for i in range(100):
    send(proc, ev("krita" if i % 2 == 0 else "firefox"))
send(proc, ev("krita"))
time.sleep(1.0)
check("held: 101 focus changes with a key held write nothing and change nothing",
      not writes_from(pos) and wp.active_profile_name() == "Desk" and live_key0() == "Key,F6")
HELD.write_text(f"{os.getpid()} 0\n")
check("held: on release the LAST wish is applied", wait_active("Krita") and live_key0() == "Key,F7")
check("held: ...with exactly one kcminputrc write", len(writes_from(pos)) == 1)

# ---- a stale HELD file (dead pid) never blocks
HELD.write_text("999999 1\n")
send(proc, ev("firefox"))
check("held: a file of a dead daemon is ignored", wait_active("Desk"))

# ---- a key goes down AFTER the switch started: the rebind write waits for the release (wait_ready)
send(proc, ev("firefox"))
wait_active("Desk")
pos = log_pos()
send(proc, ev("krita"))
time.sleep(0.3)                                  # past the debounce, inside switch_to's checkpoint and sleep
HELD.write_text(f"{os.getpid()} 1\n")
time.sleep(1.2)
check("held: a key down mid-switch holds the rebind write back", not writes_from(pos) and live_key0() == "Key,F6")
t_rel = time.time()
HELD.write_text(f"{os.getpid()} 0\n")
check("held: ...and the write lands after the release", wait_active("Krita") and live_key0() == "Key,F7")
check("held: the quiet time after a release is kept", appfocus.held_now() is False or time.time() - t_rel < 0.25)
HELD.write_text(f"{os.getpid()} 0\n")
check("held: right after a release it still counts as held, 0.3 s later it does not",
      appfocus.held_now() is True and (time.sleep(0.3) or appfocus.held_now() is False))
send(proc, ev("firefox"))
wait_active("Desk")

# ---- the same wish twice and the manual choice
pos = log_pos()
send(proc, ev("firefox"))
send(proc, ev("thunderbird"))
time.sleep(0.8)
check("follow: a second window of the same wish does nothing", not writes_from(pos))
proc.stdin.close()
err = proc.stderr.read().decode()
proc.wait(timeout=5)
lat = [l for l in err.splitlines() if "focus to applied" in l]
check("follow: every switch logged its time", len(lat) >= 4)
print("INFO", " | ".join(l.split(" applied ", 1)[1] for l in lat[:3]))

# ---- feature off: no rule, no reaction
wp.perapp_set("", [])
proc = start()
time.sleep(0.4)
pos = log_pos()
send(proc, ev("krita"))
time.sleep(0.8)
check("off: with no rule a focus change does nothing", wp.active_profile_name() == "Desk" and not writes_from(pos))
proc.stdin.close()
proc.wait(timeout=5)
check("off: the follower left no runtime file but seen-apps", sorted(p.name for p in RD.iterdir() if p.name.startswith("perapp")) == [])

# ================================================================ E2 installed apps -> keys
import multiprocessing as mp  # noqa: E402
import types  # noqa: E402

apps_dir = SCRATCH / "xdg-apps"
snap_dir = SCRATCH / "xdg-snap"


def desktop(folder, fname, name, extra=""):
    (folder / "applications").mkdir(parents=True, exist_ok=True)
    (folder / "applications" / fname).write_text(f"[Desktop Entry]\nType=Application\nName={name}\nIcon={name.lower()}\n{extra}")


desktop(apps_dir, "org.kde.krita.desktop", "Krita")
desktop(apps_dir, "org.kde.dolphin.desktop", "Dolphin", "StartupWMClass=dolphin\n")
desktop(apps_dir, "com.obsproject.Studio.desktop", "OBS Studio", "StartupWMClass=obs\n")
desktop(apps_dir, "kde_wacom_tabletfinder.desktop", "Tablet finder")
desktop(apps_dir, "org.example.Editor.desktop", "Example editor")
desktop(apps_dir, "hidden-launcher.desktop", "Hidden launcher", "NoDisplay=true\n")
desktop(snap_dir / "snapd" / "desktop", "firefox_firefox.desktop", "Firefox")
os.environ["XDG_DATA_HOME"] = str(SCRATCH / "no-home-apps")
os.environ["XDG_DATA_DIRS"] = f"{apps_dir}:{snap_dir / 'snapd' / 'desktop'}"
found = {a["id"]: a for a in wp.desktop_apps()}
os.environ["XDG_DATA_HOME"] = str(SCRATCH / "data")
os.environ.pop("XDG_DATA_DIRS")
hits = []                    # (app, window name) pairs perapp_decide wants for the app's profile / pairs tried
windows = {"org.kde.krita": ["krita", "org.kde.krita"], "org.kde.dolphin": ["dolphin", "org.kde.dolphin"],
           "com.obsproject.Studio": ["obs", "com.obsproject.Studio"], "firefox_firefox": ["firefox_firefox"],
           "kde_wacom_tabletfinder": ["kde_wacom_tabletfinder"], "org.example.Editor": ["org.example.Editor"]}
for ident, names in windows.items():
    for window in names:
        got = wp.perapp_decide(ev(window), "", [("P", found[ident]["keys"] if ident in found else [])])
        hits.append(got == ("want", "P"))
check("E2 installed apps: every key set hits the app's X11 class AND its Wayland desktop-file name "
      f"({sum(hits)} of {len(hits)}), a NoDisplay launcher is not listed",
      len(hits) == 9 and all(hits) and "hidden-launcher" not in found and len(found) == 6
      and found["org.example.Editor"]["keys"] == ["org.example.editor"]
      and found["com.obsproject.Studio"]["keys"] == ["obs", "com.obsproject.studio"]
      and found["firefox_firefox"]["keys"] == ["firefox_firefox", "firefox"])
rows = wp.perapp_rows(["krita", "x-unknown"], list(found.values()))
check("E2 a profile's keys come back as rows: an installed app claims its keys, a stranger shows raw",
      [(r["name"], r["keys"]) for r in rows] == [("Krita", ["krita"]), ("x-unknown", ["x-unknown"])])


# ================================================================ E3 the locked writer
def _bump(section, key, n, locked, q):
    def change(cp):
        if not cp.has_section(section):
            cp.add_section(section)
        cp.set(section, key, str(int(cp.get(section, key, fallback="0")) + 1))
    for _ in range(n):
        if locked:
            wp.update_ini(change, wait=None)
        else:
            cp = wp._read_ini()
            change(cp)
            wp._write_ini(cp)
    q.put(1)


def race(locked, n):
    if wp.PROFILES_INI.exists():
        wp.PROFILES_INI.unlink()
    wp.set_active_profile("Desk")
    q = mp.Queue()
    ps = [mp.Process(target=_bump, args=(sec, key, n, locked, q)) for sec, key in (("Active", "a"), ("PerApp", "b"))]
    for p in ps:
        p.start()
    for p in ps:
        p.join()
    cp = wp._read_ini()
    return 2 * n - int(cp.get("Active", "a", fallback="0")) - int(cp.get("PerApp", "b", fallback="0"))


mp.set_start_method("fork", force=True)       # 3.14's forkserver needs a UNIX socket the sandbox refuses
lost_unlocked, lost_locked = race(False, 1000), race(True, 1000)
src_text = (REPO / "tablet_profiles.py").read_text()
check(f"E3 profiles.ini: 2 processes x 1000 read-modify-writes lose {lost_locked} locked (unlocked control: {lost_unlocked} lost)",
      lost_locked == 0 and lost_unlocked > 0)
check("E3 no writer of profiles.ini bypasses the lock: _write_ini is called from update_ini only",
      len(re.findall(r"^\s+_write_ini\(cp\)", src_text, re.M)) == 1)

# ================================================================ E4 the follower compares the wish with [Active]
for _n in ("Desk", "Krita"):
    wp.write_profile(wp.profile_path(_n), wp.blank_sections())
wp.perapp_set("Desk", [("Krita", ["krita"])])


def run_rows(rows):
    calls = []

    def applier(name):
        calls.append(name)
        wp.set_active_profile(name)
    sw = appfocus.Switcher(applier=applier)
    t = 100.0
    for item in rows:
        if isinstance(item, float):               # a pause: time passes, due() runs
            for _ in range(int(item / 0.05)):
                t += 0.05
                sw.due(t)
        elif isinstance(item, str):
            wp.set_active_profile(item)            # a hand pick in Tablet Center
        else:
            sw.event(item, t)
            t += 0.05                              # 50 ms apart: inside the 0.15 s debounce
            sw.due(t)
    for _ in range(8):
        t += 0.2
        sw.due(t)
    return calls, wp.active_profile_name()


wp.set_active_profile("Desk")
trap = run_rows([ev("firefox"), 1.0, ev("krita"), 1.0, ev("tablet-center"), 1.0, "Desk", 1.0, ev("krita"), 1.0])
wp.set_active_profile("Desk")
double = run_rows([ev("thunderbird"), ev("krita"), ev("krita", dlg=True), ev("krita")])
wp.set_active_profile("Krita")
guard = run_rows([ev("krita"), ev("kando"), ev("krita")])
check(f"E4 a hand pick is undone at the next focus: calls {trap[0]}, ends on {trap[1]}",
      trap == (["Krita", "Krita"], "Krita"))
check(f"E4 two activations inside the debounce still switch once: calls {double[0]}", double == (["Krita"], "Krita"))
check(f"E4 a pie between two windows of the active app does not switch: calls {guard[0]}", guard == ([], "Krita"))

# ================================================================ E5 a file error in the switch does not end the follower (L6)
_notes, _real_run, _real_switch = [], appfocus.subprocess.run, wp.switch_to


def _boom(*a, **k):
    raise OSError(28, "No space left on device")


wp.switch_to = _boom
appfocus.subprocess.run = lambda cmd, **k: (_notes.append(cmd) if cmd[0] == "notify-send" else None) or subprocess.CompletedProcess(cmd, 0, "", "")
_logpos = len((RD / "appfocus.log").read_text()) if (RD / "appfocus.log").exists() else 0
_sw = appfocus.Switcher()
wp.set_active_profile("Desk")
try:
    _sw.switch("Krita")
    _survived = True
except Exception as _e:                              # noqa: BLE001 - the old code lets the OSError out
    _survived = False
wp.switch_to, appfocus.subprocess.run = _real_switch, _real_run
_logtext = (RD / "appfocus.log").read_text()[_logpos:] if (RD / "appfocus.log").exists() else ""
check("E5 an OSError in the switch does not reach main", _survived)
check("E5 ...one log line names the error", _logtext.count("switch stopped: [Errno 28] No space left on device") == 1)
check("E5 ...the notice call is made, in plain words",
      len(_notes) == 1 and _notes[0][0] == "notify-send" and "No space left on device" in _notes[0][-1]
      and "Switch a profile by hand" in _notes[0][-1])
check("E5 ...the follower pauses until a hand switch (stopped_at set)", _sw.stopped_at == "Desk")

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
