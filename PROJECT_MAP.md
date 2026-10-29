# plasma-tablet-center — Project Map

Where things are, for a reader (human or model) who has not seen the code.
Files are listed in reading order. Each entry says what the file is FOR, what
it talks to, and lists its chunks: the `# ── chunk: <name>` markers
(`// ── chunk:` in QML) above every top-level def, class, bash function and
script mode, plus a few block markers in the script-style files. Grep a chunk
name to jump to it; line numbers are never used here because they drift and
markers do not. `tests/check_map.py` keeps this file and the markers in step;
`tests/run_all.sh` runs every gate.

## Processes

| Process | Started by | Lives | Talks to |
|---|---|---|---|
| `tablet-precision.sh` | the pad key's chord (a KWin global shortcut), the ring script, the hover daemon, Tablet Center | one run per call, ~150 ms | KWin D-Bus (`outputArea`), `kcminputrc` (the write-back), the runtime files, the fallback ledgers, the overlay's pipe |
| `tablet-overlay.py` + `.qml` | the toggle (the precision overlay, `--fifo`), the daemon (the ghost, `--waiting --follow`) | precision: ON to OFF; ghost: touch to lift | its pipe or its stdin |
| `tablet-hover.py` | the autostart entry (install.sh) | always | every tablet hidraw node, the pen's evdev node, the toggle (`where`, `suspend`, `resume`, `toggle` on a long press, `heal` when a node set opens, `pause` when the tablet stays gone), the overlay pipe, the conf + the `hover.ctl` reload pipe, one virtual device on `/dev/uinput` (the mouse onto the pen, then a key's press or touch chord) |
| `tablet-precision-size.sh` | the ring's two chords | one run per tick | the conf (SCALE, RING_STEP), the relocate marker, the toggle (`resize`, mode on) or the size preview (mode off) |
| `tablet-size-preview.py` (+ `tablet-overlay.qml`) | the ring script, mode off, when none is running | until 1.2 s after the last conf change, or at once when the mode comes on | the conf (mtime), the state file |
| `tablet_center.py` | the launcher entry | a window (`QLocalServer` "tablet-center": a second start raises this one and exits) | the conf, `kcminputrc`, `kglobalshortcutsrc` (read), KWin's device list (the pad, the pen, the aspect) and D-Bus properties (pressure, `leftHanded`), the toggle, `tablet_profiles.py` (profile files, checkpoint, the switch) |
| `tablet-appfocus.py` | Tablet Center (on Save of a per-app rule), or login autostart | always once started; idle while no profile names an app | a KWin script's D-Bus calls (`busctl monitor`), `profiles.ini`, `$RD/held`, `tablet_profiles.switch_to` |
| `tablet-pad-probe.py` | you, in a terminal | until Ctrl+C | hidraw |

Who ends what: the toggle kills the precision overlay through `overlay.pid`;
the daemon terminates its own ghost; the size preview quits by itself; nothing
else is killed by anything.

## Contracts

Runtime files live in `$XDG_RUNTIME_DIR/tabprec/` (`$RD` below). The pen
ledgers must outlive the session, so they live in
`${XDG_STATE_HOME:-~/.local/state}/tabprec/`. The conf is
`~/.config/tabprec.conf`; every writer keeps the lines it does not own. ONE line shape for every reader (the daemon, the size preview, Tablet Center, both shell scripts; none RUNS the file): `KEY [spaces] = [spaces] VALUE`, a `#` starts a comment, a value in one pair of quotes loses them, a comma decimal reads as a dot where a number is parsed (the shell: SCALE, DIM), any other line is ignored, the last duplicate wins. The shell reads only SCALE, DIM, SCALE_MIN, SCALE_MAX, RECONNECT, RING_STEP.

| Thing | Written by | Read by | Content / meaning |
|---|---|---|---|
| `$RD/saved-area` | toggle ON | toggle OFF, `suspend`; the daemon, the ring script, the size preview, `tablet_profiles.switch_to` (a resize only while it exists) (exists = precision ON) | the base mapping `FX FY FW FH` |
| `$RD/area` | `apply_area` (ON, resize, move) | `resize` (its centre), the daemon's drag and its warp (the fractions), `resume` | `X Y W H DIM SW SH FX FY FW FH` of the current area; the fractions are the truth, X Y W H SW SH are redone for the live screen at a read |
| `$RD/overlay.pid` | `apply_area` | `apply_area` (alive?), toggle OFF (kill) | pid of the precision overlay |
| `$RD/overlay.fifo` | created by the overlay (`--fifo`, held O_RDWR); written by `apply_area` and the daemon | the overlay | lines `X Y W H [DIM]` (move), `waiting` / `solid` (the border); applied in order, under PIPE_BUF |
| `$RD/relocate` | the daemon: touched at the hold, then at 30 Hz while dragging; removed on a cancel | the toggle: younger than 3 s = MOVE instead of OFF; the ring script: younger than 3 s = the tick does nothing | its mtime is the message; never deleted on a press |
| `$RD/lock` | every run of the toggle script (`flock`), after `heal` has looked for the pen | — | one run at a time; the spawned overlay closes the descriptor (`9>&-`) |
| `$RD/pen` | the toggle, after a walk over KWin's device list | the toggle (every run that needs the pen), `tablet-pen-pos.py --mapped` | the pen's KWin sysname; one `tabletTool` call confirms it, a stale one triggers a new walk |
| `$RD/paused` | `pause` (the daemon: the tablet gone past its grace with the mode on) | `unpause` (`heal`, toggle ON) | three lines: when the tablet went (epoch s), the base, the area line. Resumed within `RECONNECT` s, else dropped; dies at logout |
| ledger `<state>/tabprec/<vendor>-<product>` | the toggle's `map_area`, ONLY when `unpersist` cannot prove the write-back | `heal_off` (mode `heal`, toggle ON); toggle OFF and a proved write-back delete it | `BFX BFY BFW BFH FX FY FW FH`: the pen's base and the last rectangle set on it. Never written on a healthy system |
| `$RD/preview.pid`, `overlay.log`, `preview.log`, `probe.js` | the ring script / the toggle | the ring script / nobody | housekeeping |
| conf `SCALE` | Tablet Center, the ring script | the toggle (every run), the size preview | fraction of the screen width, held inside the soft cap; default 0.36 |
| conf `SCALE_MIN`, `SCALE_MAX` | Tablet Center (the Precision tab) | the toggle, the ring script, the size preview, Tablet Center's size slider | the soft cap on `SCALE`; defaults 0.05 and 0.80; 0 = no cap on that side (the area still ends at the full width and at one pixel) |
| conf `DIM` | Tablet Center | the toggle, the size preview | 0–0.8; default 0.10 |
| conf `HOLD` | you, by hand | the daemon (on a `hover.ctl` poke only, NOT by polling); Tablet Center (the register column's default) | the touch register of a key with no `TOUCH_HOLD_<n>`: seconds a resting finger waits; no floor (0 = at once), default 0.15 |
| conf `LONG` | Tablet Center | the daemon (on a `hover.ctl` poke only, NOT by polling) | seconds a press that confirmed a drag stays down to leave the mode; 0 = never, default 0.7 |
| conf `RECONNECT` | Tablet Center (the Precision tab) | the toggle's `unpause` | seconds a paused mode waits for the tablet to come back; 0 = never resume, default 60 |
| conf `RING_STEP` | Tablet Center | the ring script | percentage points of screen width per ring tick; default 0.5 |
| conf `PEN_DRIVER` | you, by hand (the trial) | the helper's `--check` (the unit's ExecCondition) and `main`; the toggle's `read_conf_keys` | `1` = the pen driver trial runs; anything else or absent = off (default). Never the environment |
| conf `DRIVER_WAIT` | you, by hand | the toggle's `driver_send` | seconds a mapping waits for `pen-driver.sock` while a helper takes the pen; default 3, 0 = never |
| `$RD/pen-driver.sock` | the helper: bound at start and at each grab; gone after `NOPEN` s (5) with no pen, and at exit | the toggle's `driver_send` (datagrams), the daemon's `pen_copy_node`, `tablet_profiles` never | `FX FY FW FH` or `full`; the helper keeps the newest across a reconnect and forgets it with the socket |
| `$RD/pen-driver.lock` | the helper (`flock`, held while it takes or holds the pen) | the toggle's `driver_send` (`flock -n -s`) | held = wait for the socket; free = map through KWin at once (D2) |
| `$RD/screen-seen` | `fit_area` (touched BEFORE it looks at the screen) | `fit_area`, the daemon's `screen_moved` | its mtime: the screen was looked at; `kwinoutputconfig.json` newer = look again (no process otherwise) |
| `kcminputrc` `[ButtonRebinds][TabletRing][<pad>][<mode 0–3>]` `0=AxisKey,<up>,<down>,<threshold>` | Tablet Center (Pad tab: any mode's chords; Precision tab: angle and direction, on the size pair's mode), install.sh (600) | KWin | threshold = degrees per tick × 120; the ring reports 5° steps, so ≤ 600 = every step (72 ticks a turn) |
| conf `PRESSURE_RECORD` | you, by hand | the daemon (at a node set, a rescan and a `hover.ctl` poke) | `0` = no pressure record (no fd on the pen node, no file, no SIGTERM handler); anything else or absent = record (default) |
| `<state>/tabprec/pressure/<profile>.json` | the daemon (atomic, ≤ 1 write a minute, at a profile switch, at exit) | Tablet Center's Pen tab tooltip; `main` never reads it | `{v:1, buckets:{n: 101 stroke counts by peak %}, active_s, strokes}`; the name is the profile's, percent-quoted |
| conf `WARP` | you | the daemon (on a `hover.ctl` poke only, NOT by polling) | `0` = no mouse warp on a touch or press; anything else or absent = warp (default) |
| conf `CHORD_<n>` | Tablet Center (the Pad tab), or you by hand | the daemon (on a `hover.ctl` poke only, NOT by polling) | chord (modifiers + one key) the daemon presses on its virtual device when pad key n goes down, released with it; key n = press-byte bit n-1, and the key is set to the literal `Disabled` in kcminputrc (never deleted: a deleted entry leaks the raw pad button to the focused app) |
| conf `HOVER_MASK` | Tablet Center (the Precision column) | the daemon | the precision key's bit; absent = the ghost and the drag are off; a `HOVER_MASK_USB` / `_BT` line (by hand) wins over it on that bus |
| conf `TOUCH_CHORD_<n>` | Tablet Center (the Touch box) | the daemon (on a poke) | chord held while a finger rests on key n (engages after its register, mirror release); bare modifiers allowed, never on the mask key |
| conf `TOUCH_HOLD_<n>` | Tablet Center (the Touch register column) | the daemon (on a poke) | key n's touch register in seconds; absent = `HOLD`, the default register |
| `$RD/held` | the daemon: at node open, then at each change | `tablet-appfocus.py` | `<pid> <0\|1>`: 1 while a pad key, touch chord or pie is held; a dead pid = not held |
| `$RD/appfocus.pid`, `$RD/seen-apps` | `tablet-appfocus.py` | `tablet_profiles.follower_running`; the Per-app box's picker | the follower's pid; window classes met with focus |
| `profiles.ini` `[PerApp]` `[PerAppProfile]` `[PerAppApps]` | the Profiles tab's Per-app box | `tablet-appfocus.py` | `default` profile; numbered rules: profile, `;`-joined apps. Not in a `.tcprofile`: `main` keeps the sections |
| `$RD/hover.ctl` | Tablet Center (Apply, a register change, the long-press save), a hand `echo reload` | the daemon (in its select loop) | any line = re-read the conf (every held chord released first, the contact spent); deferred while a rectangle follows the pen |
| conf `HOVER_REPORT`, `HOVER_BYTE`, `HOVER_MASK`, `PRESS_BYTE`, `PRESS_MASK` (+ `_USB` / `_BT`) | you, for an unknown model (from the probe) | the daemon | report layout overrides; a `_USB` / `_BT` key beats the plain one on its bus |
| KWin `outputArea` (D-Bus) | the toggle only | — | the pen's LIVE mapping, in KWin's memory. The precision rectangle exists only here and in `$RD`, both of which die with the session; the device's `size` gives the tablet's aspect |
| KWin `leftHanded` (D-Bus); `kcminputrc` `LeftHanded` | Tablet Center's Left-handed box (every pen group + the sibling, then live: `apply_left_handed`); System Settings (Drawing Tablet > Pen), which KWin keeps per product, USB and Bluetooth apart. Never in a profile | `tablet-pen-pos.py` (every run), the daemon (`turn_poll`), Tablet Center (the Pad tab's turn, `left_handed_state`) | on = libinput turns the tablet 180° before KWin maps it (`1 − x`, `1 − y`, from the pen's next exit from proximity); both readers turn the kernel's evdev position to match. The rectangle math needs nothing: `outputArea` is applied after the turn |
| `kcminputrc` `[Libinput][<vendor>][<product>][<name>]` `OutputArea` | KWin (on every D-Bus write) and then the toggle's `unpersist`, which puts the BASE back at once and reads it to prove it | KWin, whenever that device appears | the mapping the pen must have with precision mode OFF. The key is DELETED when that is the whole screen (absent = `0,0,1,1` to KWin). USB and Bluetooth are two products = two groups |
| `$RD/conf.lock` | the ring script AND now Tablet Center (`save_conf_key`/`drop_conf_key`, `switch_to`'s conf block) - `flock`, non-blocking, retried up to 5 s | — | one conf writer at a time; not taken, the write raises and nothing is written |
| `${XDG_DATA_HOME:-~/.local/share}/tablet-center/profiles/<name>.tcprofile` | Tablet Center (`checkpoint`, import, export, the cell dialog's image) | Tablet Center (`read_profile`, the switch) | one profile: `[Conf]` `[Pad]` `[Ring]` `[Pen]` `[Pressure]` `[Image]` sections, the `.tcprofile` format below |
| `.../backup/<name>.tcprofile` | Tablet Center, once per window, the first time a profile's text changes (`checkpoint`, `backup_now`) | you, by hand | the profile as it was before this window touched it |
| `.../profiles.ini` | Tablet Center | Tablet Center (every tab rebuild) | `[Active] name`; `[Grid] 1`–`9` (a profile name or empty, 1 = top-left, row-major); `[Device] pad`, `pen` - only a LIVE KWin detection writes `[Device]`, the asleep/disconnected fallbacks only read it |
| `.tcprofile` sections | `write_profile` (the fixed key order) | `read_profile` (the only reader; refuses a file over 2 MB or not UTF-8) | `[Pad]` keys are 0-based libinput pad button numbers, `[Pen]` keys are evdev codes (331/332/329) - same shape, two schemes. An empty value means the key is absent in the live file |

Placement, shared by the toggle, the ghost and the drag: `w = sw · SCALE`,
`h = w / aspect`, `x = cx/sw · (sw − w)`, `y = cy/sh · (sh − h)`, with the pen
at `cx, cy` in full-screen pixels. The cursor never moves on a toggle. A
resize (the ring, mode on) keeps the area's own centre instead:
`x = clamp(cx − w/2, 0, sw − w)` with `cx, cy` the centre on record; the
size preview (the ring, mode off) is centred on the screen.

One drawing, `tablet-overlay.qml`, for every rectangle the toolkit shows: dim
bands around the clear area and a 2 px amber border inside it. Precision mode
itself has the solid border. Every preview has the WAITING look - the same
bands, the border as 5 px dashes flowing clockwise, one period per 0.3 s:
the preview ghost (a finger resting on the key, mode off), the size preview
(a ring tick, mode off) and the PrM ghost (the real overlay while it is
dragged, mode on), which turns solid again on the press or the lift.

Timings: ghost debounce 0.06 s, drag hold `HOLD` (no floor), long press
`LONG`, a touch chord engages after its key's register, pen poll 30 Hz, node rescan 2 s, absence grace 10 s (`GRACE`), reconnect window `RECONNECT` (60 s)
(hardware only; the conf reloads on a `hover.ctl` poke), marker freshness
3 s, pipe write timeout 1 s, lock wait 5 s, size preview hold 1.2 s and
fade 0.45 s, dash flow 0.3 s per period.

The long press: KWin fires the toggle on the key-down, so a press during a
drag always lands the area (MOVE); if the key then stays down for `LONG`,
the daemon runs the toggle again (OFF). The lock makes the two land in a
row whatever their timing.

## Files

### `tablet-precision.sh` — the toggle, and the only writer of the pen mapping
Modes `toggle` (default), `resize`, `where`, `suspend`, `resume`, `heal`, `pause`. Finds
the pen in KWin's device list (cached sysname, one call to confirm), reads the
pen position from `tablet-pen-pos.py` (the mouse as fallback, through a
one-shot KWin script), computes the area, sets `outputArea`, then moves or
spawns the overlay. One run at a time; a run reads the conf after taking the
lock, and `heal` looks for the pen before it. KWin writes every mapping it
is given into `kcminputrc`, so every
rectangle goes through `map_area`, which writes the base back there at once:
no rectangle outlives the session. A ledger is written only when that
write-back cannot be proved. OFF needs no pen; the daemon pauses the mode when
the tablet goes, and `heal` resumes it.
- **chunk: `config-and-paths`** — the D-Bus names, the runtime dir and its file names.
- **chunk: `note`** — notify-send wrapper; only errors notify.
- **chunk: `kload`** / **`kunload`** — load and unload the mouse fallback's one-shot KWin script (busctl, not qdbus6).
- **chunk: `find_pen`** — the pen's sysname: cached (`$RD/pen`, one call confirms it) or a walk; none = notice + exit (`heal`: silent 2 s; OFF: returns).
- **chunk: `area_math`** — W H from the capped SCALE and the aspect, then X Y and the fractions: `pen` = cursor-stationary, `centre` = centred and clamped.
- **chunk: `compute_area`** — pen position (evdev, XWayland, then the mouse), then `area_math pen`; used by ON, a move and `where`.
- **chunk: `pen_group`** — the pen's `kcminputrc` group path into `$GV $GP $GN` (maker, model, name); fails rather than guess one.
- **chunk: `ledger`** — the pen's ledger file, `<vendor>-<product>`: written only when `unpersist` cannot prove the write-back.
- **chunk: `unpersist`** — `kcminputrc` back to the OFF mapping (key deleted when that is the whole screen), then read back to prove it.
- **chunk: `driver_send`** — words to the pen driver's socket (turned when Left-handed); waits `DRIVER_WAIT` s only while the helper's lock is held.
- **chunk: `read_conf_keys`** — the conf READ in THE line shape (never sourced); `PEN_DRIVER`, `DRIVER_WAIT` cleared first.
- **chunk: `map_area`** — sets `outputArea` in KWin, then `unpersist` (ledger only if that failed); driver socket live = sends it there.
- **chunk: `apply_area`** — `map_area` for the computed area, writes the area file, moves the live overlay through its pipe or (re)spawns it.
- **chunk: `remap`** — the area on record onto the pen again (`resume`, `heal` while ON); a 7-field file gets fractions; a new screen = `fit_area` + `apply_area`.
- **chunk: `live_screen`** — the screen size now (`LSW` `LSH`) from `tablet-pen-pos.py --screen`; 1 = unknown.
- **chunk: `fit_area`** — UNU-03: the fractions are the truth; redone for the live screen, looked at only after a KWin output change.
- **chunk: `heal_off`** — mode OFF: a pen still on its ledger's rectangle gets its base back; the ledger is spent either way.
- **chunk: `unpause`** — a pause younger than `RECONNECT` s: the mode back at the SAME area (`fit_area`); older, or 0 = dropped.
- **chunk: `run-lock`** — `heal`, `grabbed` find the pen first (no lock held); an unknown verb exits 2; `flock` (5 s), the conf, its defaults.
- **chunk: `resize`** — while ON: `area_math centre` on the recorded centre (`fit_area`), `apply_area`; nothing when width and screen match; no record = at the pen.
- **chunk: `where`** — prints the area a toggle ON would map now, nothing changed (the ghost).
- **chunk: `suspend`** / **`resume`** — while ON: the base mapping back / the recorded area again through `remap` (the daemon brackets a drag with them).
- **chunk: `pause`** — the tablet gone past the daemon's grace, mode ON: ends it without a pen, keeps the area; a pen KWin lists = nothing to pause.
- **chunk: `heal`** — a tablet appeared (the daemon, in the background): ON = `remap`; OFF = `unpause`, else `heal_off`.
- **chunk: `grabbed`** — the helper grabbed: while ON every tablet tool back to the base, `unpersist`, `remap`.
- **chunk: `toggle`** — OFF→ON: `unpause`, else `heal_off` + save the base; ON + fresh marker = move (needs the pen); else OFF, tablet or not.

### `tablet-pen-pos.py` — "X Y SW SH" of the pen, in physical pixels
Best source first: the kernel's evdev state of the pen node (needs the udev
uaccess rule), then XWayland's stylus valuators (fresh only over X11 windows).
The kernel zeroes the pen's X, Y and tool key whenever it leaves proximity, so
a lifted pen has no evdev position; exit 1 when neither source works, or at
once on that state (`OUT_OF_RANGE`) so callers fall back to the mouse. `DEBUG=1` traces.
Plain, evdev is stretched over the whole screen — the space the
cursor-stationary math wants; `--mapped` puts it through the pen's live
outputArea instead, for a caller that needs the pixel the cursor is on.
With KDE's Left-handed mode on, libinput turns the tablet 180° before KWin
maps it, so the evdev position is turned the same way (`1 − x`, `1 − y`);
XWayland's comes through KWin, already turned.
- **chunk: `trace`** — stderr tracing under DEBUG=1.
- **chunk: `screen_size`** — the X screen size (physical pixels) and the display handle.
- **chunk: `left_handed`** — KWin's `leftHanded` for the pen node read (eventNN = its sysname); no answer = not turned.
- **chunk: `evdev_pen_norm`** — the BTN_TOOL_PEN node's EVIOCGABS position, turned if `left_handed`; `OUT_OF_RANGE` = no position (zeroed).
- **chunk: `XIAnyClassInfo`** / **`XIValuatorClassInfo`** / **`XIDeviceInfo`** — ctypes mirrors of the XInput2 structs.
- **chunk: `xwayland_stylus_norm`** — normalized position of the first enabled stylus device, via XIQueryDevice.
- **chunk: `output_area`** — the pen's live outputArea from KWin for `--mapped`; sysname from the toggle's cache; no cache or no answer = the whole screen.
- **chunk: `main`** — evdev (`OUT_OF_RANGE` = exit 1 at once), then XWayland, then exit 1; `--mapped` puts evdev through the area; prints pixels.

### `tablet-overlay.py` — the dim-around overlay
One process per overlay. Args `X Y W H [DIM]`; `--waiting` starts it in the
waiting look (the ghost). `--follow` moves it from stdin lines and quits on
EOF; `--fifo PATH` moves it from a named pipe any process may write, and never
quits on its own. The lines `waiting` and `solid` switch the border.
- **chunk: `args`** — argument parsing; `--fifo PATH`, `--follow` and `--waiting` are pulled out first.
- **chunk: `window`** — the Qt app, the QML engine, the root window of `tablet-overlay.qml`.
- **chunk: `place`** — sets px py pw ph (and dimval) on the root; the first placement comes from the args (no DIM: the QML's 0.10).
- **chunk: `input-source`** — the pipe (created here, opened O_RDWR so writers may come and go) or non-blocking stdin.
- **chunk: `on_input`** — applies `X Y W H [DIM]` and `waiting` / `solid` lines in order; EOF on stdin quits.

### `tablet-overlay.qml` — the one drawing
Root properties, set from Python: `px py pw ph` (the clear rectangle),
`dimval`, `waiting` (the flowing dashed border), `shown` (false = fade out),
`phase` (the dash offset the animation drives).
- **chunk: `overlay-window`** — full-screen layer-shell window on the overlay layer, scope `dock` (Peek at Desktop stays), input-transparent; the properties.
- **chunk: `fading-item`** — everything drawn sits in it; its opacity follows `shown` with a 450 ms animation.
- **chunk: `dim-bands`** — four rectangles around the clear area at `dimval` alpha.
- **chunk: `solid-border`** — the 2 px inset amber border of precision mode; hidden while waiting.
- **chunk: `waiting-border`** — the same border as a dashed `Shape` path, 5 px on / 5 px off, `dashOffset` bound to `phase`; the path runs clockwise.
- **chunk: `flow-animation`** — `phase` from 5 to 0 (one period) every 300 ms while waiting: a shrinking offset moves the dashes forward.

### `tablet-hover.py` — the ExpressKey daemon: ghost, chords, the drag
Reads the pad's raw hidraw reports (the kernel drops the touch sense).
Precision OFF: a finger resting on the precision key shows the ghost (the
look of the mode in the waiting style), which follows the pen. Precision ON:
a finger held for `HOLD` seconds starts a drag of the real overlay, waiting
border on, with the pen mapped to the whole screen; a press moves the area
there, a lift puts everything back. The precision key = conf `HOVER_MASK`,
written by Tablet Center's Precision column (none = ghost and drag off).
Report layouts per product id, conf keys override. A finger landing on any
key warps the mouse onto the pen (one virtual device kept for life; the
press repeats it); a key with conf `CHORD_<n>` — Disabled in kcminputrc —
gets its chord pressed by the daemon right after the warp on the same
device (KWin processes the motion first, so a pie opens under the pen),
and conf `TOUCH_CHORD_<n>` is held from the key's touch register
(`TOUCH_HOLD_<n>`, `HOLD` the default) to the lift. The conf reloads on
a `hover.ctl` poke, not by polling; the reload releases every held chord
first, while the finger is still down, and spends the contact (a profile
switch rewrites kcminputrc a moment later, and no release sent after that
rewrite reaches the focused window). Each node set it opens starts the
toggle's `heal` (the area onto the pen that came back, or a recent pause
resumed); a tablet gone for `GRACE` pauses the mode. Every pen position is
turned 180° while KDE's Left-handed mode is on (`TURN`, read from KWin off
the hot path, again within one rescan of a change in System Settings).
Pressure record (conf `PRESSURE_RECORD`, on unless `0`): the pen's evdev node is read, never grabbed (raw
pressure, before KWin's curve, the same on USB and Bluetooth). A stroke = BTN_TOUCH down to up with the pen
tool, 30 ms or more; its peak goes into a 0..100 % histogram. Buckets hold 900 s of pen-in-range time (a
silence counts 1 s at most); the newest 40 stay (10 hours of drawing, not of the clock). One file per
profile in `<state>/tabprec/pressure/`, written when changed and a minute passed, at a profile switch and
at exit (a SIGTERM handler, installed only with the switch on). Switch `0`: no fd, no read, no file.
- **chunk: `log`** — stderr line with the `tablet-hover:` prefix (the journal under autostart).
- **chunk: `publish_held`** — writes `$RD/held` (`pid 0|1`) when a pad key or chord goes down or up.
- **chunk: `read_conf`** — the conf as a dict in THE line shape.
- **chunk: `screen_now`** / **`screen_moved`** / **`read_area`** — the live size; asked only after an output change; pixels redone.
- **chunk: `as_int`** — int() that accepts 0x.. and returns a default.
- **chunk: `touch_delay`** / **`hold_delay`** — key n's touch register (`TOUCH_HOLD_<n>`, else `HOLD`) before a chord or the drag.
- **chunk: `long_delay`** — conf LONG: how long a press that confirmed a drag stays down before the mode goes off; 0 = never.
- **chunk: `FAMILIES`** / **`MODELS`** — built-in report layouts per family and bus; product id → family.
- **chunk: `layout_for`** — one node's layout: conf overrides over built-ins; mask None = no precision key.
- **chunk: `tablet_nodes`** — every tablet hidraw node with its bus and product id, from sysfs.
- **chunk: `pen_node`** / **`pen_open`** / **`pen_norm`** — the pen's eventNN (= KWin sysname), its fd; its EVIOCGABS position (None: zeroed), turned by `TURN`.
- **chunk: `pressure_file`** / **`active_profile`** / **`pressure_open`** — a profile's record path; its name; the pen fd + max.
- **chunk: `pen_copy_node`** — the pen driver's copy (socket, then the last virtual pen); the record reads it.
- **chunk: `PressureRecord`** — stroke peaks per 900 s of pen time, newest 40; written ≤ 1/min, at exit.
- **chunk: `left_handed`** / **`turn_poll`** — KWin's `leftHanded`; `TURN` re-asked at a rescan only after the node or kcminputrc changed, or no answer.
- **chunk: `toggle`** — runs `tablet-precision.sh MODE` (suspend / resume / toggle / pause); reports success.
- **chunk: `heal`** / **`heal_poll`** — the toggle's `heal` in the background, one at a time; a failed run is retried, 5 tries.
- **chunk: `Follower`** — the rectangle that follows the pen: `show` (ghost), `move` (drag, `waiting`), `follow` (tick), `hide` (cancel or confirm, `solid`).
- **chunk: `Warper`** — mouse onto the pen and the chords: `ensure(probe)`, `warp`, `chord_down` (refuses a Ctrl+Alt+F1-12 union via `chord_hazard`)/`chord_up`, `release_all`, `close`.
- **chunk: `mask_text`** — "none" or the hex mask, for the log.
- **chunk: `open_ctl`** / **`drain_ctl`** — the control pipe: made (a plain file replaced), opened ONCE and held for the helper's life; drained while idle.
- **chunk: `watch`** — opens the nodes (retried), `heal`s; drains the pipe, re-reads the conf; reports → warp, chords; timers; a reload releases held chords.
- **chunk: `Absence`** — the tablet gone with the mode ON: past `GRACE` (10 s) runs `pause` with the moment it went.
- **chunk: `cycle`** — one daemon pass: `watch` while the tablet is here, else time its absence, drain the pipe, sleep.
- **chunk: `main`** — `--simulate`, or `open_ctl`, then `cycle` forever with one `Warper` and one `Absence`.

### `tablet-precision-size.sh` — one ring tick: SCALE ± RING_STEP points
- **chunk: `tick-lock`** — `$RD/conf.lock` (not the toggle's), taken before the conf is read and dropped after it is written.
- **chunk: `read_conf_keys`** — the toggle's function, the same body (the conf rig compares both).
- **chunk: `drag-guard`** — a relocate marker under 3 s old (the area is being dragged): exit, nothing changes.
- **chunk: `step`** — SCALE ± RING_STEP/100 (conf, default 0.5 points; junk = 0.5), held inside the soft cap.
- **chunk: `conf-write`** — rewrites SCALE and DIM under the tick lock, temp file then `mv`, keeping every other line.
- **chunk: `resize-or-preview`** — mode on: `tablet-precision.sh resize` (the area, around its centre); off: the size preview unless one runs.

### `tablet-size-preview.py` — the centred fading size preview
Draws with `tablet-overlay.qml` in the waiting look, so it is precision mode's
own picture centred on the screen. Watches the conf's mtime at 10 Hz, redraws
on a change, fades 1.2 s after the last change and exits; fades at once when
precision mode comes on.
- **chunk: `read_conf`** — SCALE (soft cap applied) and DIM from the conf.
- **chunk: `tablet_aspect`** — width / height of the pen tablet from KWin's device list, 1.6 fallback.
- **chunk: `window`** — the Qt app, the QML engine, the root window; `waiting` set once.
- **chunk: `apply_scale`** — pixel size from SCALE and the aspect, centred; pushes px py pw ph dimval and shows.
- **chunk: `tick`** — 100 ms poll: the state file fades the preview out; a conf change cancels a pending exit and restarts the hold.
- **chunk: `start_fade`** — hides (QML fades) and arms the quit timer.
- **chunk: `timers`** — the quit, hold and poll timers.
- **chunk: `first-show`** — the initial mtime, the first draw, the first hold.

### `tablet-pointer-warp.py` — move the mouse to X Y without root
A virtual absolute mouse on `/dev/uinput`: waits until KWin lists it, sends
one motion, holds 50 ms, removes it (the CLI, for one-shot debugging).
Fractions of the axis range, so it lands on the same physical spot on a
scaled display. The daemon imports it and keeps one device for life
(`create` / `warp` / `destroy`); that device also carries the chord
alphabet (modifiers, F-keys and the typed keys), pressed after a warp so KWin orders the
motion before the chord (`parse_chord` / `chord`).
- **chunk: `chord-keys`** — key names → US-position kernel codes (`KEYCODES`, `TYPED_KEYS`, `SHIFTED`); never SYSRQ or BTN_*; `CHORD_KEYS` = `create`'s set.
- **chunk: `chord_parts`** — a chord's '+'-split names, the '+' key kept (`Ctrl++`); None on a trailing '+'.
- **chunk: `safe_chord`** — chord text for the SOURCED conf: punctuation as Qt words, a shifted symbol Shift + base.
- **chunk: `parse_chord`** — chord text → codes: the modifiers in KWin's order, then the ONE non-modifier key; None on a name no device key carries, modifier-only, or two keys.
- **chunk: `chord`** — press or release the parsed codes, one frame per key, releases reversed; after the warp's frames, so KWin orders the motion first.
- **chunk: `create`** — the virtual mouse (`keys` = chords); `strict` RuntimeError if KWin never lists it, else kept; `probe=False` skips it.
- **chunk: `destroy`** — removes the device and closes the fd.
- **chunk: `warp`** — the pointer to a screen fraction: two frames, the first one unit off (the kernel drops an ABS value equal to the current one).
- **chunk: `trace`** — stderr tracing under DEBUG=1.
- **chunk: `_IO`** / **`_IOW`** / **`_IOR`** — ioctl number encoding.
- **chunk: `uinput-constants`** — the uinput ioctls, event types and the axis range.
- **chunk: `screen_size`** — the X screen size (physical pixels) when SW SH are not given.
- **chunk: `kwin_sees`** — polls KWin's device list for the node as a whole word (text or JSON); no busctl = False.
- **chunk: `emit`** — one input_event write.
- **chunk: `main`** — create the device, wait for KWin, move, hold, destroy.

### `tablet-pen-driver.py` — TRIAL pen driver: grab the pen, re-emit it on a uinput copy (conf `PEN_DRIVER=1`, default off)
- **chunk: `ioc`** / **`bits_of`** — ioctl number encoding; the set bits of a kernel bitmask.
- **chunk: `read_conf`** — the conf in THE line shape as a dict; only `PEN_DRIVER` is read (`--check` too).
- **chunk: `pen_nodes`** — real Wacom pen event nodes: vendor 056a, name ends `Pen`, no uinput copy (`virtual/input`); Bluetooth's uhid counts (VM tests: env flag).
- **chunk: `read_caps`** — ids, name, key/abs/msc bits, ranges and props of the pen, by evdev ioctls.
- **chunk: `make_copy`** — the uinput copy with the same ids, name and ranges (KWin finds the pen's group); (fd, sysname).
- **chunk: `parse_area`** — "FX FY FW FH" or "full" to a clamped rectangle, None = whole screen; ValueError on junk.
- **chunk: `remap`** / **`transform`** — precision mode as arithmetic: ABS_X/ABS_Y of a read() into the rectangle.
- **chunk: `notify`** — sd_notify (READY, WATCHDOG) to `$NOTIFY_SOCKET`; a no-op outside systemd.
- **chunk: `beat`** — WATCHDOG=1 at most every PING/2, not once per pen report.
- **chunk: `syn_at`** / **`splice`** — a SYN_DROPPED drops to the next SYN_REPORT; the pen's state stands in.
- **chunk: `state_frame`** — keys (and axes) read from the pen node as events: the grab's head, the resync.
- **chunk: `control_socket`** — datagram socket `$RD/pen-driver.sock`; the toggle writes the mapping there.
- **chunk: `ctl_open`** / **`ctl_close`** / **`ctl_read`** — the socket bound once; gone with the area; the newest datagram.
- **chunk: `hold`** — `$RD/pen-driver.lock` while the pen is taken or held: the toggle waits only then.
- **chunk: `wake_pipe`** / **`idle`** — SIGTERM wakes every select; the no-pen wait reads the socket.
- **chunk: `grabbed_hook`** — `tablet-precision.sh grabbed`, detached, after every grab.
- **chunk: `run`** — open, copy, THEN grab, then the hook; held keys head the first packet; ENODEV returns 1.
- **chunk: `main`** — `--check`; off unless `PEN_DRIVER=1`/`--force`; socket for life; no pen `NOPEN` s or a failed grab = gone.

### `tablet-pad-probe.py` — find the touch and press bytes on an unknown tablet
- **chunk: `quiet-reports`** — the pen-noise regions and report ids hidden unless `--all`.
- **chunk: `tablet_nodes`** — every tablet hidraw node with its bus and name.
- **chunk: `fmt`** — a byte as hex and binary.
- **chunk: `main`** — reads every node, prints each changed byte with node, bus and report id.

### `tablet_center.py` — the settings window
- **chunk: `paths`** — the conf path, KWin names, the ring's default pair, colors, STE tooltips.
- **chunk: `busget`** / **`detect_devices`** — KWin reads; pad, pen+sysname, aspect; saves both names to `profiles.ini [Device]`.
- **chunk: `read_conf`** — the numeric conf keys with defaults, for the tabs' initial values.
- **chunk: `live_pen_sysname`** — the pen's KWin node now (a bus switch renumbers it); every live pressure write asks first.
- **chunk: `save_conf_key`** / **`drop_conf_key`** / **`conf_value`** — wrap `tablet_profiles`' locked versions; `conf_value` reads one raw value.
- **chunk: `poke_daemon`** — wraps `tablet_profiles.poke_daemon`, returns its bool.
- **chunk: `kread`** / **`kwrite`** — a pad key's raw value (`Key,<seq>` stripped, else raw text); `Disabled` swallows the key.
- **chunk: `is_raw_form`** / **`kread_group`** / **`kwrite_group`** — a binding to show read-only; the same read/write for any group (the Pen tab).
- **chunk: `ring_read`** / **`ring_write`** — one ring mode's binding: two chords + angle; both empty = unbound; threshold = degrees × 120.
- **chunk: `precision_ring_mode`** — which mode carries the precision-size pair; (0, defaults) when none; looked up fresh.
- **chunk: `DotSpinBox`** — a C-locale decimal box; a typed comma reads as the dot (that locale's group separator: 0,5 read as 5).
- **chunk: `elided`** — a long profile name cut in the middle; the tooltip keeps it whole.
- **chunk: `PrecisionTab`** — size/dim sliders, ring angle + swap (debounced), size limits (`relimit`; min > max warns), long press, reconnect, ring step.
- **chunk: `pad_icon`** / **`ring_icon`** — the key and ring pictures: dot/dash mark, the doughnut with mode n lit (7:30, clockwise; 1:30 turned).
- **chunk: `draw_ants`** / **`AntsLineEdit`** / **`AntsCheckBox`** / **`AntsRadio`** — the flowing outline: amber = a pie box, red = precision's field.
- **chunk: `PadTab`** — the pad table: Touch+Press and rings (read-only if raw), pie sides checked first, the Precision tick, Apply + `checkpoint()`; `_place` = Left-handed.
- **chunk: `_parse_curve`** / **`_read_pressure_group`** — `"x1,y1;x2,y2;"` to 4 floats; the first kcminputrc pen group with the pressure keys.
- **chunk: `CurveGraph`** — the 160×160 px Bézier editor: two draggable handles (≥12 px grab, clamped 0–1); `changed`/`set_points` sync; `tip()` = its tooltip.
- **chunk: `PenTab`** — pen buttons 331/332/329 (read-only if raw); the curve graph + 4 spins; range; Reset; Apply via `apply_pressure`+`checkpoint()`.
- **chunk: `PerAppBox`** — "Profile specifics": the active profile's app rows, Add app, the all-other-apps tick; every press is written at once.
- **chunk: `AddAppDialog`** / **`icon_named`** — the app picker (installed apps, then unclaimed seen windows; search only filters) and a theme-icon helper.
- **chunk: `stale_confirm`** — Discard/Cancel for a Pad or Pen tab whose profile an app rule replaced; Apply is not offered.
- **chunk: `ProfilesTab`** — the profile menu (search+Enter) + `+` import, the 3×3 grid (Edit Grid: image), Export All, the status line.
- **chunk: `show_exception`** — `sys.excepthook`: the traceback to stderr AND a dialog (Apport's hook made slot errors silent).
- **chunk: `CenterWindow`** — the main window: `closeEvent` asks the Pad and Pen tabs about unapplied edits.
- **chunk: `main`** / **`single_instance`** — `QLocalServer` "tablet-center" (2nd start raises it); detects, `checkpoint()`s, builds tabs.
- **chunk: `package_first_start`** — `package_setup`'s notes in a dialog; an unbound ring gets install.sh's question.

### `tablet_profiles.py` — profile files, checkpoint and the switch (Qt-free)
Imported by `tablet_center.py` (a plain top-level `import`: no Qt, no import
cycle). Owns `${XDG_DATA_HOME:-~/.local/share}/tablet-center/` (`profiles/`,
`backup/`, `profiles.ini`), `$RD/conf.lock`, and the pen write paths
(kcminputrc groups + the live D-Bus properties): pressure, which
`tablet_center.py`'s Pen tab and this file's own `switch_to` both call, and
Left-handed mode, which the Pad tab's box calls and no profile carries.
- **chunk: `paths`** — profile/backup/conf/kcminputrc paths, the `[Conf]` allowlist (two numbering schemes), the section/key tables.
- **chunk: `ProfileError`** / **`SwitchStopped`** — a refused `.tcprofile` (never partial values); a switch step that failed.
- **chunk: `atomic_write`** / **`conf_lock`** — a temp file then `os.replace`; `$RD/conf.lock`, non-blocking, retried 5 s, else `TimeoutError`.
- **chunk: `save_conf_key`** / **`drop_conf_key`** — the locked, atomic conf surgery `tablet_center.py`'s same-name functions delegate to.
- **chunk: `poke_daemon`** — one `reload` line into `hover.ctl`; False when nothing reads it.
- **chunk: `kreadconfig6`** / **`kwriteconfig6`** / **`kwrite_literal`** — generic kcminputrc read/write; `kwriteconfig6` raises on a nonzero exit.
- **chunk: `busctl_set`** / **`busctl_get`** — the InputDevice's live pressure props; `set` is effect-only and returns exit 0, `get` parses.
- **chunk: `pen_twins`** — the pen plus KWin tools with its name, vendor, product (the driver's copy): each live write.
- **chunk: `parse_conf_line`** — THE conf line shape (see Contracts): (key, value) or None.
- **chunk: `toggle_chord`** — the toggle's shortcut from kglobalshortcutsrc, the documented default when missing; `tablet_center.py` uses it.
- **chunk: `load_parse_chord`** — `parse_chord` from `tablet-pointer-warp.py` (dynamic import); None = pie validation off; the Pad tab's too.
- **chunk: `conf_chord`** — `safe_chord` for the Pad tab's save and a profile import; as typed if the import fails.
- **chunk: `chord_hazard`** — `console` = Ctrl+Alt+F1-12, no Shift (KWin VT switch); `mic` = F20.
- **chunk: `hazard_note`** — `(kind, sentence)` for a flagged chord; Pad, Pen, ring and profile reader share it.
- **chunk: `pen_groups_in_kcminputrc`** / **`libwacom_sibling_pairs`** — the pen's `[Libinput]` groups; the sibling's group from `DeviceMatch`.
- **chunk: `apply_pressure`** — the ONE pressure path: kcminputrc per group (+ the sibling's), D-Bus to every twin; warning on a miss.
- **chunk: `apply_left_handed`** / **`left_handed_state`** — `LeftHanded` the same way, never in a profile; state: live, else any group.
- **chunk: `resolve_pad_name`** / **`resolve_pen_name`** / **`_only_group`** — live name, else `profiles.ini`, else the only matching group.
- **chunk: `_read_ini`** / **`_write_ini`** / **`get_device_ini`** / **`save_device`** — `profiles.ini [Device]`; only a live detection saves it.
- **chunk: `active_profile_name`** / **`set_active_profile`** — `profiles.ini`'s `[Active]`.
- **chunk: `pressure_file`** / **`pressure_stats`** / **`pressure_tip`** — record path; P10, P95 of peaks; tooltip text.
- **chunk: `get_grid`** / **`set_grid_cell`** — `profiles.ini`'s `[Grid]` 1-9.
- **chunk: `perapp_get`** / **`perapp_set`** / **`perapp_split`** — the per-app block of `profiles.ini`; set_ replaces it under the lock.
- **chunk: `ini_lock`** / **`update_ini`** — the profiles.ini lock and the one read-modify-write every writer goes through.
- **chunk: `_perapp_read`** / **`_perapp_write`** — the per-app block from / onto a parsed profiles.ini (no locking of their own).
- **chunk: `perapp_edit`** / **`perapp_add`** / **`perapp_remove`** / **`perapp_default`** — locked edits of the block; add moves an app.
- **chunk: `perapp_related`** / **`perapp_rows`** — the engine's name relation; a profile's keys grouped into rows by installed app.
- **chunk: `desktop_keys`** / **`desktop_apps`** / **`seen_apps`** — an installed app's window names; the .desktop scan; the follower's seen list.
- **chunk: `perapp_ignored`** — True for a class or desktop-file name on the ignore list: the whole name or the part after the last dot.
- **chunk: `perapp_decide`** — one focus event to ignore, keep or want-a-profile; the never-switch lists.
- **chunk: `follower_running`** — the follower's pid file names a live `tablet-appfocus` process.
- **chunk: `profile_path`** / **`list_profiles`** — a profile's file path; every profile name on disk, sorted.
- **chunk: `valid_name`** / **`unique_name`** — refuse empty/`.`/`..`/`/`/NUL, 255 bytes with the extension; "name (2)" for Keep Both.
- **chunk: `blank_sections`** / **`_render_profile`** / **`write_profile`** — the all-keys skeleton, as fixed-order text, atomically written.
- **chunk: `backup_now`** — a profile copied to `backup/`, once per window (an explicit Replace; `checkpoint` has its own check).
- **chunk: `_line_ok`** / **`_as_float`** — the final `[Conf]` line-format gate; a comma-or-dot float parse.
- **chunk: `_validate_pad_or_pen`** — empty/`Disabled`/`Scroll`/`Key,`/`MouseButton,`/`TabletToolButton,` only; warns on a one-letter chord.
- **chunk: `_validate_curve`** / **`_validate_range`** / **`_validate_png`** — format, 0–1 bounds, min<max (comma decimal → dot), PNG signature, ≤16000 px.
- **chunk: `_apply_pie_rule`** — a `CHORD_n` disables `[Pad]` key n−1; `TOUCH_CHORD_n` never changes its key; on `HOVER_MASK`'s key both are dropped.
- **chunk: `read_profile`** — the only `.tcprofile` reader: size/UTF-8 refusal, BOM/CRLF, parsing, every validator, the pie rule.
- **chunk: `_raw_conf_values`** / **`_live_sections`** — live values: conf raw; kreadconfig6 for Pad/Ring/Pen; Pressure from D-Bus or a group.
- **chunk: `ensure_first_start`** — first run: "My settings" from live values, active, grid 1; `[Move] fallback` while a move waits.
- **chunk: `_move_pairs`** — the old data folder's files and their targets (new extension).
- **chunk: `migrate_old_data`** — copies the old folder, verifies, `.moved-in` mark; a retry replaces the fallback ini.
- **chunk: `package_setup`** — the .deb copy: registers the 3 shortcuts (first start, or kglobalaccel lost them), starts the helper.
- **chunk: `checkpoint`** — copies live values into the active profile, keeping `[Image]`; backs up on a change; missing = recreated.
- **chunk: `switch_to`** — `checkpoint`, `read_profile` (stops on error), sync `Disabled` Pad, the conf replace, other keys, pressure, resize, active.

### `tablet-appfocus.py` — per-app profiles: follow the focus, switch the profile
Loads one KWin script (`workspace.windowActivated`, sent with `callDBus` to a name nobody owns)
and reads those calls with `busctl monitor` (no D-Bus library; the journal route lagged 0.3-0.5 s). A window of a listed app asks
for its profile, any other normal window for the default; Kando, Tablet Center,
dialogs, transients and no window never switch. A switch waits 0.15 s of quiet
and for `$RD/held` to read 0, then runs `tablet_profiles.switch_to`. Idle (no
script loaded) while `profiles.ini` names no app; `--stdin` feeds the lines (rig).
- **chunk: `log`** — stderr line with a time stamp.
- **chunk: `held_now`** — `$RD/held` says 1 and its pid lives.
- **chunk: `wait_settled`** — blocks until nothing is held; `switch_to` calls it before each rebind write.
- **chunk: `rules_now`** — default and rules with missing profiles dropped.
- **chunk: `kwin_load`** / **`kwin_loaded`** / **`kwin_unload`** — the focus script in KWin through busctl; whether KWin still has it.
- **chunk: `find_devices`** — pad name, pen name and pen sysname from KWin, as Tablet Center does.
- **chunk: `parse_line`** — the event dict of a monitor line (or a rig line).
- **chunk: `Switcher`** — the wish, the debounce, the held wait, the log of switch and focus-to-applied times; a file error in a switch pauses it like `SwitchStopped`.
- **chunk: `follow`** / **`main`** — the select loop over the monitor; the load, follow and reload cycle.

### `tests/` — the gates
`run_all.sh` runs everything (~85 s; no tablet, no KWin; Qt offscreen for the
import smoke and the center rig only): `py_compile`, an offscreen import of `tablet_center.py` and
`tablet_profiles.py`, `bash -n`, `check_map.py`, then `test_script.sh` (the toggle through on, move,
resize around the centre with the clamp, suspend, resume and off, the run
lock (heal's pen retry outside it), the write-back to `kcminputrc` (a bus switch and a logout while ON, a
base set by hand, the ledger fallback), OFF with the tablet gone, pause and
resume, the screen changing while ON; with a stubbed `busctl` that keeps `outputArea` per product like KWin,
and a fake overlay), `test_size.sh` (the ring
script: the step and the soft cap, RING_STEP from the conf, the resize only
with the mode on, the preview only with it off, nothing while the marker is
fresh; a stub toggle and a fake preview), `test_pen_wait.sh` (the pen driver's socket late after a reconnect: waits, the bound, off = no wait; needs unix sockets) and `test_hover.py` (the daemon with a named pipe as the
pad, a fake pen, fake overlays and a fake toggle that logs its modes; the
`waiting` / `solid` lines; a conf edit mid-rest; HOLD 0; the long press and
what must not arm it; the heal a node set starts, one at a time, retried on failure; the pause after the grace; a held
chord and the ghost released when the tablet goes, and its heal on return; a pen
with no position yet, in the daemon and in `tablet-pen-pos.py`; left-handed
mode: both readers turn the kernel's position, the warp before the area, KWin
asked once per change),
`test_transition.py` (the old data folder copied: 3 profiles listed, bytes equal, a second start inert, a damaged copy
`test_perapp.py` (the per-app decision table, the `profiles.ini` block read and kept by `main`'s code,
the follower on `--stdin`: writes per focus change, 101 changes with a key held, a dead-pid held file, feature off),
`test_profiles.py` (the profile library: a hostile profile, the pie rule, the
switch order, owned conf keys kept, the 2026-09 fixture's round trip, the
pressure writes, Left-handed mode for both connections and its live-first
state, the conf lock timeout, a missing active profile, a ring tick
between two saves, the .deb copy's first start; the real kreadconfig6 and kwriteconfig6 on a scratch
kcminputrc, logged by stubs, on a dead bus address), `test_install.sh` (the .deb: its files, control
and first start, unpacked into a scratch root; a scratch HOME) and `test_center.py`
(Tablet Center offscreen, same stubs: the turned table mirrored and upside
down, a shortcut typed in its top row landing on key 8 and on key 1 unturned,
the box turning the table in place, the ring lights turned, a ring box named
after the turn that fires it, the default ring direction; the Precision tab's
conf round trip; a pressure write to a node the pen left; skipped without
PyQt6; the Pen tab's pressure tooltip), `test_pressure.py` (the pressure record: strokes against hand-computed percentiles, the 10-hour window, the write cadence, the switch, SIGTERM, the daemon's CPU with and without it), `test_conf.py` (one conf line shape in every reader, the real ring
script end to end, nothing run from the conf) and `test_warp.py` (the virtual
mouse kept when KWin's list cannot be matched). Every rig takes `<scratch dir> <script path>`. `add_markers.py <dir> [out dir]` puts a marker
above every new def, function or mode (idempotent; block markers are listed
inside it); `check_map.py` imports its rule to report a def with no marker
in the files it lists (the installer scripts have none by design).
`stage_public.sh <public repo> <staging dir>` copies the sources, the map and
`tests/` into the staging folder, adds the public-only files from the repo,
runs every gate there and checks byte identity with this folder.

### `packaging/` — the Debian package
`build-deb.sh [VERSION] [OUT_DIR]` builds `plasma-tablet-center_VERSION_all.deb` from the checkout it
sits in (any checkout; the build tree in `$TMPDIR`): the scripts in `/usr/lib/plasma-tablet-center/`
(the Python shebang made `/usr/bin/python3`), install.sh's launchers and menu entry in
`/usr/share/applications`, the helper's autostart in `/etc/xdg/autostart` (same name, same unit),
the udev rule in `/usr/lib/udev/rules.d`; postinst/postrm reload udev. The per-user steps run at
Tablet Center's first start (`package_setup`). No chunk markers.

### `uninstall.sh` — the reverse of the installer *(public copy only)*
Stops the daemon, removes the scripts, the launcher entries and their
shortcut config, and moves the pen ledgers to the Trash (no `gio`, or a
refusal: they stay, their path printed); settings, the pad-key bindings and the udev rule stay,
with the paths and the one sudo line printed. Safe to re-run. No chunk
markers: the numbered steps it echoes are the map.

### `install.sh` — the installer *(public copy only)*
Five steps: the scripts into `~/.local/bin`, the `.desktop` launcher entries
with the command-shortcut marker, the three chords registered with
kglobalaccel (toggle Meta+Shift+F12, bigger Meta+Shift+F10, smaller
Meta+Shift+F9), the optional ring binding, and the manual steps it prints (the
udev rule that needs one sudo, the pad buttons, the relogin). It also writes
the daemon's autostart entry, and is safe to re-run. No chunk markers: the
numbered steps it echoes are the map.
