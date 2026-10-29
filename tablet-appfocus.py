#!/usr/bin/env python3
# tablet-appfocus.py - per-app profiles: follows which app has focus (a KWin script's D-Bus calls, seen by busctl monitor) and switches the profile.
# Part of plasma-tablet-center (MIT). Idle while profiles.ini names no app. --stdin reads the same events from stdin (the rig).
import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR))
import tablet_profiles as wp  # noqa: E402 - the path above finds it beside this file

RD = wp.RD
HELD = RD / "held"                 # the daemon: '<pid> <0|1>', 1 while a pad key, touch chord or pie is held
SCRIPT_FILE = RD / "appfocus.js"
SCRIPT_NAME = "tabprec_appfocus"
TAG = "TPAPP "                      # the rig's --stdin lines may carry this tag before the JSON
BUS_IF = "net.local.Tabprec"        # the KWin script calls this name; nobody owns it, busctl monitor sees the call anyway
DEBOUNCE = 0.15                    # s of quiet after the last focus change before a switch (alt-tab storms)
QUIET = 0.25                       # s a released key stays "held": the hidraw release reaches us ~100 ms before KWin's
HELD_STEP = 0.04                   # s between looks at HELD while a switch waits
HEALTH_STEP = 10.0                 # s between looks at whether KWin still has our script
RULES_STEP = 2.0                   # s between looks at profiles.ini
KWIN_JS = r"""
function send(w) {
    var o = {ms: Date.now(), c: "", d: "", nw: false, dlg: false, tr: false, sp: false};
    if (w) {
        o.c = String(w.resourceClass); o.d = String(w.desktopFileName);
        o.nw = !!w.normalWindow; o.dlg = !!w.dialog; o.tr = !!w.transient; o.sp = !!w.specialWindow;
    } else { o.none = true; }
    callDBus("net.local.Tabprec", "/", "net.local.Tabprec", "focus", JSON.stringify(o));
}
workspace.windowActivated.connect(send);
send(workspace.activeWindow);
"""
KW, SCR = "org.kde.KWin", "org.kde.kwin.Scripting"


# ── chunk: log
def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, file=sys.stderr, flush=True)
    try:                                        # Tablet Center starts the follower with no stderr: this file is its log
        with open(RD / "appfocus.log", "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ── chunk: held_now
def held_now():
    """True while the daemon says a key, touch chord or pie is held, and QUIET s
    after the release (the file's age). No file or a dead pid = not held, so a
    stale file never blocks."""
    try:
        pid, state = HELD.read_text().split()[:2]
        if not Path(f"/proc/{int(pid)}").exists():
            return False
        return state == "1" or time.time() - HELD.stat().st_mtime < QUIET
    except (OSError, ValueError):
        return False


# ── chunk: wait_settled
def wait_settled():
    """Block until nothing is held: switch_to calls it right before each rebind write."""
    while held_now():
        time.sleep(HELD_STEP)


# ── chunk: rules_now
def rules_now():
    """(default, rules) of profiles.ini with every name of a missing profile dropped."""
    default, rules = wp.perapp_get()
    have = set(wp.list_profiles())
    return (default if default in have else ""), [(n, a) for n, a in rules if n in have]


# ── chunk: kwin_load
def kwin_load():
    """Load and start the focus script in KWin (busctl, like the toggle's probe). False = refused."""
    RD.mkdir(parents=True, exist_ok=True)
    SCRIPT_FILE.write_text(KWIN_JS)
    base = ["busctl", "--user", "call", KW, "/Scripting", SCR]
    kwin_unload()
    for args in (["loadScript", "ss", str(SCRIPT_FILE), SCRIPT_NAME], ["start"]):
        try:
            if subprocess.run(base + args, capture_output=True, timeout=5).returncode != 0:
                return False
        except (OSError, subprocess.SubprocessError):
            return False
    return True


# ── chunk: kwin_loaded
def kwin_loaded():
    """True while KWin still has the focus script (a KWin restart drops it). Unknown answers count as loaded."""
    try:
        out = subprocess.run(["busctl", "--user", "call", KW, "/Scripting", SCR, "isScriptLoaded", "s", SCRIPT_NAME],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return True
    return out.returncode != 0 or "false" not in out.stdout


# ── chunk: kwin_unload
def kwin_unload():
    try:
        subprocess.run(["busctl", "--user", "call", KW, "/Scripting", SCR, "unloadScript", "s", SCRIPT_NAME],
                       capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


# ── chunk: find_devices
def find_devices():
    """(pad name, pen name, pen sysname) from KWin's device list, as Tablet Center does; None where unknown."""
    mgr, iface = "/org/kde/KWin/InputDevice", "org.kde.KWin.InputDevice"

    def get(path, prop, i=iface):
        try:
            return subprocess.run(["busctl", "--user", "get-property", KW, path, i, prop],
                                  capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    pad = pen = sys_name = None
    for sysname in get(mgr, "devicesSysNames", "org.kde.KWin.InputDeviceManager").replace('"', " ").split()[2:]:
        path = f"{mgr}/{sysname}"
        for kind in ("tabletPad", "tabletTool"):
            if "true" not in get(path, kind):
                continue
            parts = get(path, "name").split('"')
            if len(parts) < 2:
                continue
            if kind == "tabletPad" and pad is None:
                pad = parts[1]
            if kind == "tabletTool" and pen is None:
                pen, sys_name = parts[1], sysname
    return pad, pen, sys_name


# ── chunk: parse_line
def parse_line(line):
    """The focus event dict of one line, else None: a busctl monitor JSON line of the
    script's call (the event is its one string argument), or - for the rig - the
    event JSON alone, after an optional TPAPP tag."""
    at = line.find(TAG)
    try:
        ev = json.loads(line[at + len(TAG):] if at >= 0 else line)
        if isinstance(ev, dict) and ev.get("member") == "focus":
            ev = json.loads(ev["payload"]["data"][0])
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    if not isinstance(ev, dict):
        return None
    for key in ("c", "d"):
        if ev.get(key) in ("undefined", "null"):    # a KWin build without that property
            ev[key] = ""
    return ev


# ── chunk: Switcher
class Switcher:
    """The state of the follower: the profile the last non-ignored focus asked
    for, the switch waiting for quiet and for no held key, and the numbers
    for the log (event to applied). apply() is the only place that writes."""

    def __init__(self, applier=None):
        self.last = None             # profile the last non-ignored window asked for
        self.want = None             # (profile, quiet-since, event time) while a switch waits
        self.applier = applier or self.switch
        self.seen = []               # window classes met, newest last (Tablet Center's picker)
        self.stopped_at = None       # the active profile when a switch failed half way: no auto switch until it changes

    def event(self, ev, now):
        default, rules = rules_now()
        cls = str(ev.get("c", "")).lower()
        if cls and ev.get("nw", True) and not ev.get("tr") and not wp.perapp_ignored(cls) and cls not in self.seen:
            self.seen = (self.seen + [cls])[-40:]
            self.note_seen()
        if self.stopped_at is not None:
            if wp.active_profile_name() == self.stopped_at:
                log(f"focus {cls or '-'}: paused after a failed switch (switch by hand to resume)")
                return
            self.stopped_at = self.last = None       # recovered by hand: the next focus decides afresh
        kind, what = wp.perapp_decide(None if ev.get("none") else ev, default, rules)
        if kind != "want":
            log(f"focus {cls or '-'}: {kind} ({what})")
            return
        if what == wp.active_profile_name():
            self.want = None         # the profile in force already: a pending switch is void (a pick made in Tablet Center counts)
            self.last = what
            log(f"focus {cls}: {what} already")
            return
        self.last = what
        recv = (time.time() * 1000 - ev["ms"]) if isinstance(ev.get("ms"), (int, float)) else None
        self.want = (what, now, recv)
        log(f"focus {cls}: wants {what}" + (f" (script to us {recv:.0f} ms)" if recv is not None else ""))

    def due(self, now):
        """Seconds until the next step is due (None = nothing waits); applies when ready."""
        if self.want is None:
            return None
        name, since, recv = self.want
        wait = since + DEBOUNCE - now
        if wait > 0:
            return wait
        if held_now():
            return HELD_STEP
        self.want = None
        if wp.active_profile_name() == name:
            log(f"{name} is active already")
            return None
        t0 = time.monotonic()
        self.applier(name)
        total = (time.monotonic() - since) * 1000 + (recv or 0)
        log(f"applied {name}: switch {1000 * (time.monotonic() - t0):.0f} ms, focus to applied {total:.0f} ms")
        return None

    def note_seen(self):
        try:
            RD.mkdir(parents=True, exist_ok=True)
            wp.atomic_write(RD / "seen-apps", "\n".join(self.seen) + "\n")
        except OSError:
            pass

    def switch(self, name):
        pad, pen, sysname = find_devices()
        try:
            for note in wp.switch_to(name, pad=wp.resolve_pad_name(pad), pen=wp.resolve_pen_name(pen),
                                     sysname=sysname, wait_ready=wait_settled) or []:
                log(f"note: {note}")
        except (wp.SwitchStopped, OSError) as err:     # an OSError: a profile file could not be written (disk full, no rights)
            log(f"switch stopped: {err}")
            self.stopped_at = wp.active_profile_name()
            why = f" ({err.strerror})" if isinstance(err, OSError) and err.strerror else ""
            subprocess.run(["notify-send", "-a", "Tablet", "-i", "input-tablet", "Per-app profiles paused",
                            f"The switch to {name} failed{why}. Switch a profile by hand to resume."],
                           capture_output=True)


# ── chunk: follow
def follow(lines, sw, health=None):
    """The loop: `lines` is a file object of events. Runs until it ends; `health`, when given,
    runs every HEALTH_STEP s (main: reload the KWin script when KWin lost it)."""
    fd = lines.fileno()
    buf = b""
    next_rules = next_health = 0.0
    on = bool(rules_now()[1])
    while True:
        now = time.monotonic()
        timeout = sw.due(now)
        if now >= next_rules:
            next_rules = now + RULES_STEP
            was_on, on = on, bool(rules_now()[1])
            if not on:
                sw.last = sw.want = None     # rules gone: the next rule starts from a clean state
                if was_on:
                    return                   # main unloads the KWin script and idles
        if health is not None and on and now >= next_health:
            next_health = now + HEALTH_STEP
            health()
        ready, _, _ = select.select([fd], [], [], RULES_STEP if timeout is None else min(timeout, RULES_STEP))
        if not ready:
            continue
        chunk = os.read(fd, 65536)
        if not chunk:
            return
        buf += chunk
        *whole, buf = buf.split(b"\n")
        for raw in whole:
            ev = parse_line(raw.decode("utf-8", "replace"))
            if ev is not None and on:
                sw.event(ev, time.monotonic())


# ── chunk: main
def main():
    RD.mkdir(parents=True, exist_ok=True)
    try:
        if (RD / "appfocus.log").stat().st_size > 200_000:
            (RD / "appfocus.log").unlink()      # a long login: start the log afresh
    except OSError:
        pass
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))      # `systemctl stop`: the finally below unloads the KWin script
    (RD / "appfocus.pid").write_text(f"{os.getpid()}\n")      # tablet_profiles.follower_running() reads it
    sw = Switcher()
    if "--stdin" in sys.argv[1:]:
        follow(sys.stdin.buffer, sw)
        return 0
    while True:
        if not rules_now()[1]:
            time.sleep(RULES_STEP * 3)           # no app named anywhere: nothing loaded, nothing followed
            continue
        proc = subprocess.Popen(["busctl", "--user", "monitor", "--json=short", "--match",
                                 f"type=method_call,interface={BUS_IF}"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        time.sleep(0.3)                          # the monitor is up BEFORE the script makes its first call
        if not kwin_load():
            proc.terminate()
            log("KWin refused the focus script; next try in 30 s")
            time.sleep(30)
            continue
        log("focus script loaded")
        try:
            follow(proc.stdout, sw, health=lambda: kwin_loaded() or (log("KWin lost the focus script: loaded again"), kwin_load()))
        finally:
            proc.terminate()
            kwin_unload()
        log("the monitor ended; starting again in 5 s")
        time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
