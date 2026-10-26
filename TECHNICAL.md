# plasma-wacom-center: technical notes

How the toolkit works, for a reader who wants to change it or port it. The
README says what it does. `PROJECT_MAP.md` lists every file, process,
runtime file and chunk marker. `KNOWLEDGE.md` holds the facts that each
cost a debugging session. Written for plasma-wacom-center v2 on Plasma 6.6,
September 2026.

## The pieces

| File | Role |
|---|---|
| `tablet-precision.sh` | the toggle, and the only writer of the pen mapping. Modes: `toggle`, `resize`, `where`, `suspend`, `resume`, `heal`, `pause`; any other word exits 2 |
| `tablet-pen-pos.py` | the pen position on the screen, in pixels |
| `tablet-overlay.py` + `tablet-overlay.qml` | the dim overlay. One drawing for every rectangle the toolkit shows |
| `tablet-hover.py` | the ExpressKey daemon: the touch preview, the touch chords, the pie chords, the area drag, the long press |
| `tablet-precision-size.sh` | one ring tick |
| `tablet-size-preview.py` | the size preview, with precision mode off |
| `tablet-pointer-warp.py` | the virtual device: the warp and the chords (the daemon imports it) |
| `wacom_center.py` | the settings window |
| `tablet-pad-probe.py` | prints the touch and press bytes of an unknown tablet |
| `install.sh` | the deploy: scripts, shortcuts, autostart, the ring binding |
| `uninstall.sh` | the reverse: daemon stopped, scripts, entries and shortcuts removed, the pen ledgers moved to the Trash; settings kept |

No compiled code, no kernel module. One daemon (`tablet-hover.py`), started
by an autostart entry. Everything else is one process per key press.

An update (`install.sh` run again) replaces the scripts in place. The daemon
keeps its old code until a login or
`systemctl --user restart 'app-tablet\x2dhover@autostart.service'`, though
the scripts it starts are the new ones at once; an open Wacom Center window
keeps its old code until it is closed.

## Precision mode

KWin owns the tablet mapping. Each input device is a D-Bus object under
`org.kde.KWin /org/kde/KWin/InputDevice/eventN`. Its `outputArea` property
holds the mapped screen rectangle as fractions. Event numbers change on
every Bluetooth reconnect. So the toggle finds the pen by its `tabletTool`
property, caches the sysname in the runtime directory and confirms it with
one call per run.

A run reads the pen position, computes the area, writes `outputArea`, then
spawns or moves the overlay. About 150 ms. Toggle OFF restores the mapping
saved at ON, not blindly the full screen.

### Unplugged while on

KWin writes each `outputArea` into `kcminputrc`, per vendor id, product id
and device name, and loads it again when the device appears. Over USB and
over Bluetooth the tablet has two product ids, so KWin keeps two pens. A
rectangle set while the mode is on would therefore outlive an unplug, a
switch to the other connection, a logout and a crash — while the only note
saying the rectangle was temporary lives in `$XDG_RUNTIME_DIR` and dies with
the session. One permanent effect, one temporary note. That mismatch, not
the cable, is the bug.

So the toggle does not leave the effect behind. Every mapping write goes
through `map_area`, which sets `outputArea` in KWin and then writes the
**base** mapping back into `kcminputrc` at once (`unpersist`), deleting the
key when the base is the whole screen. It then reads the value back to prove
the write landed. The precision rectangle exists only in KWin's memory and
in `$XDG_RUNTIME_DIR/tabprec/`, both of which die with the session, so an
unplug, a bus switch, a logout, a crash or an uninstall all leave the pen on
its normal mapping with nothing to repair. If a rectangle ever does stay
behind (an older version, or a settings file the toggle could not write),
`RESCUE.txt` removes it by hand.

Measured on Plasma 6.6 on 2026-09-12: with precision mode on, `kcminputrc`
read `0,0,1,1` while KWin's live `outputArea` read the rectangle, for 90
seconds, and the pen stayed inside the rectangle. Writing the file does not
end the mode, and KWin does not write its own copy back over it.

`heal` still runs whenever the daemon opens the tablet's nodes. With the mode
on it maps the pen that turned up to the area on record, so a tablet that
changes connection stays in precision mode. With the mode off there is
normally nothing to do. The daemon runs one `heal` at a time. A `heal` that
exits non-zero (KWin did not list the pen within 2 s, the lock, a failed
mapping write) is started again at the next 2 s rescan, 5 runs for each
appearance of the tablet, and each failure is a `heal failed` line in the
journal.

The pen ledger, `~/.local/state/tabprec/<vendor>-<product>`, remains as a
fallback only. `map_area` writes one when it cannot prove the write-back —
no `kwriteconfig6`, or a KDE that stores the mapping somewhere else — and
`heal` then repairs from it the old way and says so in the journal.

### The tablet goes away while the mode is on

Switched off, a flat battery, or the cable out with Bluetooth off: the tablet
is gone and precision mode is still on. Two things make that safe.

Toggle OFF does not need the tablet. Every mode used to look the pen up first
and exit when there was none, so the OFF branch never ran and the overlay
stayed on the screen with nothing able to close it — not the pad key, the
keyboard shortcut, Wacom Center or the daemon. Found on the hardware on
2026-09-12. OFF now ends the mode without a pen. Nothing live needs restoring,
because `kcminputrc` already names the base and KWin loads it when the tablet
returns.

The daemon also ends the mode by itself. When the tablet has been gone for 10
seconds with the mode on (`GRACE`), it runs `tablet-precision.sh pause`, which
closes the overlay and keeps the area in `$XDG_RUNTIME_DIR/tabprec/paused`. If
the tablet comes back within `RECONNECT` seconds (a conf key and a Precision
tab field, default 60, 0 = never), `heal` resumes precision mode at the same
area. It does not place a new area at the pen: the kernel zeroes the pen's
position whenever it leaves proximity, so it still has none right after a
reconnect, and a new area would land around the mouse. A press of
the precision key inside the window resumes it too. After the window the
record is dropped. The grace keeps a cable swap from blinking the border, and
the record dies at logout, so a crash never leaves a pause behind.

### The placement rule

<p align="center"><img src="docs/placement.svg" width="880" alt="The placement rule in three cases: pen at the centre, off-centre, and in a corner"></p>

The placement keeps the cursor where it is:

    w = sw * SCALE
    h = w / aspect
    x = cx / sw * (sw - w)
    y = cy / sh * (sh - h)

`sw, sh` is the screen size, `cx, cy` is the pen position in screen pixels,
`SCALE` is the conf value (a fraction of the screen width, held inside
`SCALE_MIN` and `SCALE_MAX`, see Settings) and `aspect`
is the tablet's own width / height, read from the device's `size` property.
Wacom's Windows driver uses the same rule. Near a screen border the area
drifts slower than the cursor, and it never leaves the screen.

A resize (the ring, mode on) keeps the area's own centre:
`x = clamp(cx - w/2, 0, sw - w)`, with `cx, cy` the centre on record. The
size preview (the ring, mode off) sits at the centre of the screen.

### The pen position

`tablet-pen-pos.py` tries three sources, best first:

1. The kernel's evdev state of the pen node (`EVIOCGABS`). Fresh whenever
   the pen is in proximity. Needs the udev `uaccess` rule from the install.
2. XWayland's stylus device (XInput2 valuators). Fresh only while the pen
   hovers an X11 window.
3. The mouse, through a one-shot KWin script. The last fallback.

The pen node is the input device that can report `BTN_TOOL_PEN`, found in
`/proc/bus/input/devices` by that key bit, not by its name. Right after the
tablet appears, the node reports the minimum on both axes until the pen first
comes into proximity. With `BTN_TOOL_PEN` clear as well, that is no position:
`tablet-pen-pos.py` exits 1 at once and the toggle uses the mouse. XWayland is
not asked, because its stylus data is older. The daemon's own reader applies
the same rule, so the ghost and the mouse warp need the pen in proximity. A
pen lifted away has no position at all, not only right after a connect.

Evdev gives the position on the TABLET. It is the position on the screen
only while the whole tablet is mapped to the whole screen, which is not
true in precision mode. `--mapped` puts the evdev position through the
pen's live `outputArea` — the rectangle precision mode maps it to — and
prints the pixel the pen cursor is really on. Without the flag the plain
full-tablet stretch is printed, which is the space the cursor-stationary
placement works in. The XWayland source already reports the mapped
position (its valuators are the screen scaled into 0..262143), so
`--mapped` leaves it alone. The daemon applies the same correction to its
warps through the area file's fractions.

With Left-handed mode on (System Settings > Drawing Tablet > Pen), libinput
turns the tablet 180 degrees before it maps the pen, but the kernel's evdev
position does not turn (`KNOWLEDGE.md` has the facts). Both evdev readers ask
KWin for the pen's `leftHanded` property and turn the reading to `1 - x`,
`1 - y`, the frame that `outputArea` applies to. `tablet-pen-pos.py` asks once
per run, for the node it read: the node's name (`eventNN`) is also its KWin
sysname. The daemon keeps the answer and asks again only when a node set
opens, or at a rescan after the pen node or `kcminputrc` changed (KWin writes
the setting there). So a change in System Settings reaches it within one
rescan (2 seconds), and no warp waits for D-Bus. The placement rule and the
area math need no change. The XWayland source comes through KWin and is
turned already.

Wacom Center has its own box for the mode, at the top of the Pad buttons
tab. The box writes `LeftHanded` into every `kcminputrc` group of the pen,
plus the other connection's group from libwacom's `DeviceMatch`, then sets
`leftHanded` live over D-Bus: System Settings writes only the connection in
use. The setting is never part of a profile, because it is about the desk,
not the program. Like System Settings, the box sets the pen only: the pad's
own flag turns just the ring's absolute angle, which KWin's ring shortcuts
never read. The table turns with the tablet, mirrored and upside down: the
key pictures on the right, key 8 on top, the ring still between the fourth
and the fifth key from the top. The widgets move; the table is not rebuilt,
so each row keeps its own key (its libinput button number and its hidraw
bit) and any edit not applied yet. A shortcut typed in a row thus lands on
the key beside it. The ring keeps its directions, because a turn by 180
degrees keeps clockwise clockwise; the lights in its mode pictures turn by
180 degrees. Ticked while precision mode is on, the box leaves the area
where it is: the pen covers the same rectangle, turned, until the next
toggle places a new one.

## The overlay

`tablet-overlay.qml` is one drawing: four dim bands around the clear area
and a 2 px amber border inside it. It is a full-screen layer-shell window
on the overlay layer, transparent for input, so it never eats a click.
The layer-shell QML module is the package `qml6-module-org-kde-layershell`,
and not every Plasma install has it. Without it the overlay process exits at
load: precision mode maps the pen and draws nothing. `install.sh` warns.
The overlay sets the layer-shell scope `dock`. KWin takes a layer-shell
window's type from its scope, and the default scope makes it a normal window.
A normal window that appears ends Peek at Desktop, and Peek at Desktop hides
it. A dock does neither.
Precision mode has the solid border. Every preview has the waiting look:
the same bands, with the border as 5 px dashes that flow clockwise, one
period per 0.3 s. The previews are the ghost (a finger on the key, mode
off), the size preview (a ring tick, mode off) and the dragged area (mode
on).

The toggle spawns the overlay once per ON with
`--fifo $XDG_RUNTIME_DIR/tabprec/overlay.fifo` and then moves it through
that named pipe. The line `X Y W H [DIM]` moves it. The lines `waiting` and
`solid` switch the border. A resize or a move never respawns it. The overlay
opens the pipe `O_RDWR`, so writers come and go without an EOF.

## The ring

`install.sh` binds the ring in `kcminputrc`:

    [ButtonRebinds][TabletRing][<pad name>][0]
    0=AxisKey,Meta+Shift+F9,Meta+Shift+F10,600

The threshold is not in degrees. KWin multiplies the degrees travelled since
the last tick by 120 and fires one chord when the product reaches the
threshold. The Intuos Pro ring reports 5-degree steps, so a threshold up to
600 fires on every step (72 ticks per turn) and 1800 fires every 15
degrees. Wacom Center shows the threshold as degrees and rewrites the
binding.

Each tick runs `tablet-precision-size.sh up` or `down` once. The script adds
or removes `RING_STEP` percentage points (conf, default 0.5), holds
`SCALE` inside `SCALE_MIN` and `SCALE_MAX` and rewrites the conf in place. With the mode on it
calls `tablet-precision.sh resize`. With the mode off it starts the size
preview, one per spin. A tick while the area is dragged (a relocate marker
under 3 s old) does nothing.

A fast swipe puts a dozen ticks in flight, one process each. Two rules make
the queue collapse. The toggle reads the conf after it takes the lock, so a
queued run applies the newest `SCALE`. A resize whose width is already on
screen exits at once. libinput counts ring degrees counter-clockwise, and
KWin fires the first chord on a counter-clockwise turn (checked on an Intuos
Pro M, October 2026). So the binding puts the smaller chord first, and a
clockwise turn makes the area bigger. The "Swap the ring direction" box in
Wacom Center exchanges the two chords. Installs from before October 2026
put the bigger chord first, and Wacom Center shows them with the box
ticked. The Pad buttons tab names each ring box after the turn that fires
it.

## The pie menu

Kando asks KWin for the pointer and gets the mouse. KWin keeps a separate
cursor for the pen. So a pie opened from a pad key lands where the mouse
was last left. The fix is to move the mouse onto the pen right before the
pie opens; the daemon does both, in that order, on one device.

`tablet-hover.py` keeps one virtual device on `/dev/uinput` for its whole
life (KWin lists it once, at start); the device carries both the absolute
mouse and a small keyboard (the modifiers and F1–F24). Type the menu's
shortcut into a key's Touch or Press box and tick that side in the Pie
keys column: the tab stores the chord in the conf (`CHORD_<n>` for a
press pie, `TOUCH_CHORD_<n>` for a touch pie; n = the key number, top to
bottom) and sets the key to `Disabled` in kcminputrc — `Disabled`, not a
deleted entry: KWin hands an unbound pad button to a tablet-aware app
over the tablet-pad protocol. The ticked side's box wears the overlay's
amber waiting outline, so a pie key is visible at a glance. A press pie
opens on the press; a touch pie opens after the key's touch register and
lift-selects through Kando's turbo mode. Either way the daemon warps the
mouse onto the pen and then presses the chord, on that one device. The kernel,
libinput and KWin all process one device's events in write order, so the
warp is applied before the chord reaches Kando — deterministic, about a
millisecond, nothing per menu to install. A pad key bound in `kcminputrc`
races instead and loses: KWin synthesizes its chord inside its own
handling of the pad button, Kando's KWin script reads the pointer within
2 ms of the signal (measured with `busctl monitor`), and the daemon's
warp — a userspace round trip from the same HID report — lands a few ms
after that read, so the menu opened one press behind. The touch-sense warp
(`warp (touch)` in the journal) stays as a best effort for keys
`kcminputrc` still owns. The chord is held while the pad key is held, so
Kando's turbo mode works; the release mirrors the key, and a vanished
tablet or a daemon exit releases anything still down (the kernel emits
key-ups when a device is destroyed). A chord is modifiers plus ONE F-key:
letters resolve through the active layout, and a modifier-only chord would
form the Meta-tap launcher gesture. Each warp and chord is a journal line
(`warp (press): mouse to (x, y)`, `chord (key 2): Meta+Shift+F8 down`), so
a landing can be checked against `tablet-pen-pos.py --mapped`. In
precision mode the pen position goes through the area's fractions from the
area file; during a drag the base mapping is back and the plain position
is right. Without `/dev/uinput` or KWin the daemon says so in the journal
and tries again every 30 s. `WARP=0` in the conf switches the warp off
(chords still fire). A Pie tick works only on a pad whose raw reports the
daemon decodes (the models under "Report layouts" below, or a probed one):
the daemon sees the key through the hidraw node, and KWin holds the key
`Disabled`. On another tablet leave the tick off; the chord in the Press
box then goes through KWin's own rebinding, and the menu opens at the
mouse pointer.

`examples/kando-krita-menu.json` is a Kando menu file with a Krita menu
to start from.

`tablet-pointer-warp.py` on its own (the command line, for one-shot
debugging) creates the virtual mouse, sends one motion as a fraction of
the axis range, holds 50 ms and removes it. A warp sends two frames, the
first one unit off: the kernel drops an ABS value equal to the axis's
current one, so a second warp to the same spot would otherwise be lost.
Everything here needs write access to `/dev/uinput`. The udev rule the
install prints tags the node `uaccess` for the logged-in user (on the
development box Steam's own rule had already done so, which hid the need
for a while):

    KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess"

That tag lets any program of the logged-in user send input events, the
same grant Steam's `steam-devices` package makes.

## Pen

Wacom Center's Pen tab sets the three buttons built into the pen, plus
the pressure curve and the pressure range. These live in the pen
device itself, not in the pad, so the tab uses a separate code path
from the Pad buttons tab even though the layout matches it.

Pen button 1 and Pen button 2 behave like a Pad buttons tab Press box:
a shortcut, empty, or `Disabled`. Pen button 3 fires only when both
side buttons of the pen are down together, and only over USB. The
Bluetooth report for this tablet family has no bit for the combined
press, so the button does nothing there. A pen with no third button
still shows the box, and the box simply never fires.

A box that holds a mouse-button or scroll binding set outside Wacom
Center, in System Settings' Drawing Tablet page (any form but a
keyboard shortcut), shows read-only with the tooltip "Set in System
Settings > Drawing Tablet." Wacom Center reads the binding to display
it, and never writes over a binding it does not own. A keyboard
shortcut set there is an ordinary box you can edit.

### Pressure curve and range

The curve editor is a two-handle curve on a unit square, the same
control System Settings' Drawing Tablet page uses. Drag a handle from
the lower-left corner (no pressure) toward the upper-right corner
(full pressure). Four spin boxes mirror the two handle positions:
"Point 1 input", "Point 1 output", "Point 2 input", "Point 2 output",
each ranging 0.00 to 1.00. Type a value into one of these instead of
dragging, and the graph updates to match. "Reset Pressure" sets both
handles back to the diagonal, which is no curve at all.

The pressure range is two more fields, minimum and maximum, also 0.00
to 1.00. They stay enabled even when the pen has not yet reported its
pressure capability to the system. A note in the tab says so, and the
range applies once the pen reports. When the minimum is not below the
maximum, Apply shows a warning and writes the two values as they are. A
profile that holds such a range loads with the full range, 0.00 to 1.00.

"Apply" writes the pen buttons and the pressure settings together. If
the pen is not connected, the tab keeps every field editable and
shows a note that the settings apply once the pen connects, so you
can prepare a binding or a curve ahead of time.

## Profiles

A profile is a full copy of the settings on the Precision, Pad
buttons, and Pen tabs. Pie menus are not part of a profile. They live
in Kando, a separate app, and Wacom Center does not read or write
them.

Profiles are `.wcprofile` files, plain text, split into sections:

| Section | Holds |
|---|---|
| `[Conf]` | the `tabprec.conf` settings, such as `SCALE` and `DIM` |
| `[Pad]` | the 8 pad buttons |
| `[Ring]` | the ring's 4 modes |
| `[Pen]` | the 3 pen buttons |
| `[Pressure]` | the pressure curve and range |
| `[Image]` | the profile's picture, if it has one |

Profiles live in `~/.local/share/wacom-center/profiles/`. Backups
live in `~/.local/share/wacom-center/backup/`. Wacom Center writes
one backup of a profile the first time that profile changes in a
session, so you can undo the change. Wacom Center records which
profile is active, and which profile sits in which grid cell, in
`~/.local/share/wacom-center/profiles.ini`.

Importing a profile is safe against a malformed or hand-edited file.
Wacom Center checks every value against a strict format before it
applies the value. Wacom Center drops a value that does not fit the
format, does not apply it, and reports it in the tab's status line.

The Profiles tab is the last tab. The button at the top opens a
search list of every profile. Click an entry to switch to it. The "+"
button next to it imports a profile from a file on disk. The 3-by-3
grid below gives one-click access to up to 9 profiles. A filled cell
switches to its profile at once. The current profile's cell is
outlined and does nothing when clicked. An empty cell is dashed.
"Edit Grid" (it reads "Done" while active) opens a per-cell dialog to
assign a profile and set or clear its picture, with `Choose Image...`
and `Remove Image`.

"Export All Current Settings as Profile..." writes the current
Precision, Pad buttons, and Pen tab settings out as a new profile
file.

Switching profiles applies the new `[Conf]`, `[Pad]`, `[Ring]`,
`[Pen]`, and `[Pressure]` values at once. It never toggles precision
mode by itself. A changed pen button, pie key, or pressure curve
takes effect within about a second of the switch.

One Wacom Center window can be open at a time. Opening a second one
brings the first one to the front instead.

## The ExpressKey touch preview and the area drag

The Intuos Pro's pad keys report a resting finger before the press. The
kernel does not expose it: the Bluetooth pad parser reads only the key,
centre-button and ring bytes, and the `EXPRESSKEYCAP` HID usage has no
mapping. `tablet-hover.py` reads the raw HID reports from every Wacom
`/dev/hidraw*` node, next to the kernel driver. The udev rule from the
install tags the nodes `uaccess` (USB `0003:056A:*`, Bluetooth
`0005:056A:*`).

### Report layouts

Built into `tablet-hover.py` (the `MODELS` and `FAMILIES` tables). Key N is
bit N-1, so key 8 is `0x80`.

| Model | Product id | Bus | Report | Touch byte | Press byte |
|---|---|---|---|---|---|
| Intuos Pro S (PTH-460) | 0392 | USB | 0x11 | 2 | 1 |
| Intuos Pro S | 0393 | Bluetooth | 0x80 | 283 | 282 |
| Intuos Pro M (PTH-660) | 0357 | USB | 0x11 | 2 | 1 |
| Intuos Pro M | 0360 | Bluetooth | 0x80 | 283 | 282 |
| Intuos Pro L (PTH-860) | 0358 | USB | 0x11 | 2 | 1 |
| Intuos Pro L | 0361 | Bluetooth | 0x80 | 283 | 282 |

The M size is the one measured. S and L share the family, so the daemon
uses the same layout for them.

For a Wacom model the daemon does not know, run `tablet-pad-probe.py` once
per connection type. It prints each byte that changes, with the node, the
bus and the report id. Put the values into `~/.config/tabprec.conf` as
`HOVER_REPORT_USB`, `HOVER_BYTE_USB` and `PRESS_BYTE_USB`, and the same
with `_BT`. `HOVER_MASK_USB` and `PRESS_MASK_USB` (and `_BT`) override the
precision key per bus. Only keys with a touch sensor can do this (Intuos
Pro, Cintiq Pro, MobileStudio Pro). On other tablets the daemon idles.

### Defining the precision key

The Precision column in Wacom Center's Pad buttons tab names the key. One
key at most carries the tick. Apply writes that key's bit to the conf as
`HOVER_MASK`, gives the key the toggle's global shortcut in `kcminputrc`,
and removes the key's other chords. The row shows red flowing outlines:
the mode consumes the Touch and Press fields, and the Pie tick has no
effect there. No tick = no `HOVER_MASK` = the ghost and the drag are off.
The daemon does not guess: if you bind the toggle outside Wacom Center,
tick the key in the tab as well.

### Touch chords

`TOUCH_CHORD_<n>` in the conf holds a chord down while a finger rests on
key n, and releases it when the finger lifts - a held Ctrl inverts the
brush in Blender's sculpt mode. The chord engages after key n's touch
register (`TOUCH_HOLD_<n>`, the Pad tab's per-key column; `HOLD` is the
default when a key has none; 0 = at once). A press that
comes sooner wins: that contact fires only the press action, and no touch
chord until the finger has left the key. A press after the chord engaged
keeps the chord held, so Ctrl+click combos work. Right before the chord
engages the daemon warps the mouse onto the pen again, so a pie menu
bound to a touch opens at the pen's current spot; with Kando's turbo
mode, lift the finger on a slice to select it. Touch chords may be bare
modifiers - but not Meta alone, whose synthetic press-and-release is the
launcher tap. The precision key is exempt: its touch belongs to the ghost
and the drag. Touch chords need pad keys with a touch sensor. A conf
reload (a profile switch, an Apply, a hand poke) releases every held chord
first and spends that contact: lift the finger and land again for a new
chord. A release sent after the switch's `kcminputrc` write would never
land (see `KNOWLEDGE.md`).

### The ghost and the drag

With precision mode off, a finger on the precision key shows the ghost.
`tablet-precision.sh where` prints the area a toggle would map now, and a
`tablet-overlay.py --waiting --follow` draws it and follows the pen at
30 Hz.

With precision mode on, a finger held for `HOLD` seconds starts a drag:

1. The daemon runs `tablet-precision.sh suspend`. The base mapping comes
   back and the cursor roams the whole screen.
2. The daemon sends `waiting` to the overlay's pipe and moves the real
   overlay after the pen with the placement rule.
3. The daemon touches the marker file `$XDG_RUNTIME_DIR/tabprec/relocate`
   and refreshes it at 30 Hz.
4. On a press, KWin runs the toggle. A marker younger than 3 s means MOVE,
   not OFF. The toggle maps the area at the pen. The daemon sends `solid`.
5. On a lift without a press, the daemon removes the marker, sends `solid`
   and runs `tablet-precision.sh resume`. The old area comes back.

The daemon never deletes the marker on the press. Its hidraw report reaches
it about 100 ms before KWin's shortcut starts the toggle, so a deleted
marker would turn the move into an OFF. It stops refreshing the marker
instead, and the toggle accepts only a marker under 3 s old.

### The long press

`HOLD` has no floor. At 0 s every touch starts a drag at once and every
press is a move. The way out is the long press. Keep the key pressed for
`LONG` seconds after a press that confirmed a drag, and the daemon runs
`tablet-precision.sh toggle` again, which is OFF. `LONG` 0 disables the
long press.

The area lands at the press first, because KWin fires the toggle on the
key-down. The toggle script serializes its runs with `flock` on
`$XDG_RUNTIME_DIR/tabprec/lock`, so the two runs land in a row whatever
their timing.

| Precision mode | On the precision key | Result |
|---|---|---|
| off | press | the mode comes on, the area around the cursor |
| off | rest a finger | the ghost shows where the area would go, following the pen |
| on | press before `HOLD` has passed | the mode goes off |
| on | rest for `HOLD`, then press | the area moves to the pen |
| on | rest for `HOLD`, lift without pressing | nothing changes |
| on | rest for `HOLD`, press and keep the key down for `LONG` | the area moves, then the mode goes off |

### Timings

| What | Value |
|---|---|
| ghost debounce | 0.06 s |
| touch register (chords, the drag) | `TOUCH_HOLD_<n>`, else `HOLD`; no floor, default 0.15 s |
| long press | `LONG`, default 0.7 s, 0 = never |
| pen poll while following | 30 Hz |
| hidraw node rescan (hardware only) | 2 s |
| conf reload | on a `hover.ctl` poke, at once |
| marker freshness | 3 s |
| pipe write timeout | 1 s |
| lock wait | 5 s |
| size preview hold, then fade | 1.2 s, then 0.45 s |
| dash flow | 0.3 s per period |

## Settings reference

The conf is `~/.config/tabprec.conf`. Every writer keeps the lines it does
not own. The daemon re-reads it when a line arrives on its control pipe.
Wacom Center pokes `$XDG_RUNTIME_DIR/tabprec/hover.ctl` after an Apply, a
touch-register change and a long-press change, the keys the daemon reads;
the size, dim, reconnect and ring-step saves do not poke, because the toggle
and the ring read the conf on every run, and a poke releases every held
chord. After a hand edit, poke it yourself:

    echo reload > "$XDG_RUNTIME_DIR/tabprec/hover.ctl"

The toggle reads the conf on every run and needs no poke.

| Key | Meaning | Default | Written by |
|---|---|---|---|
| `SCALE` | area width as a fraction of the screen width, held inside the two limits below | 0.36 | Wacom Center, the ring |
| `SCALE_MIN`, `SCALE_MAX` | the smallest and the largest `SCALE` the toggle, the ring and the size slider allow; 0 = no limit on that side (the area still ends at the full screen width and at one pixel); a smallest above the largest: the smallest wins, and Wacom Center shows a warning | 0.05, 0.80 | Wacom Center |
| `DIM` | dim strength outside the area, 0 to 1 (1 = black) | 0.10 | Wacom Center |
| `HOLD` | the default touch register: seconds a resting finger waits before its touch action when the key has no `TOUCH_HOLD_<n>`, 0 = at once | 0.15 | you (a hand edit; the tab writes per-key values) |
| `TOUCH_HOLD_<n>` | key n's own touch register | `HOLD` | Wacom Center (the Touch register column) |
| `LONG` | seconds a press that confirmed a drag stays down to leave the mode, 0 = never | 0.7 | Wacom Center |
| `RECONNECT` | seconds a paused mode waits for the tablet to come back, 0 = never | 60 | Wacom Center |
| `RING_STEP` | percentage points of screen width per ring tick; 0 or absent = 0.5 | 0.5 | Wacom Center |
| `HOVER_MASK` | the precision key's bit | none | Wacom Center (the Precision column) |
| `CHORD_<n>` | the chord the daemon presses after the warp when pad key n goes down (a pie key; the key is `Disabled` in kcminputrc) | none | Wacom Center (a ticked Pie key) |
| `TOUCH_CHORD_<n>` | the chord held while a finger rests on key n (see "Touch chords") | none | Wacom Center (the Touch box) |
| `WARP` | `0` switches the mouse warp off; chords still fire | on | you |
| `HOVER_REPORT_<BUS>`, `HOVER_BYTE_<BUS>`, `PRESS_BYTE_<BUS>`, `HOVER_MASK_<BUS>`, `PRESS_MASK_<BUS>` | report layout overrides for an unknown model, `<BUS>` = `USB` or `BT` | none | you, from the probe |

The ring binding lives in `kcminputrc`, not in the conf (see "The ring").

`install.sh` registers three global shortcuts and creates the launcher
entries in `~/.local/share/applications/`:

| Shortcut | Entry | Runs |
|---|---|---|
| `Meta+Shift+F12` | `net.local.tabprec-toggle.desktop` | `tablet-precision.sh` |
| `Meta+Shift+F10` | `net.local.tabprec-bigger.desktop` | `tablet-precision-size.sh up` |
| `Meta+Shift+F9` | `net.local.tabprec-smaller.desktop` | `tablet-precision-size.sh down` |

To change one of these chords, open System Settings → Keyboard →
Shortcuts, find the entry by its name ("Precision mode toggle",
"Precision area bigger", "Precision area smaller"), and record a new
chord there. The Precision column reads the toggle's current chord on
every Apply — re-apply the Pad tab after the change. If you changed the
two size chords, also retype them in their ring mode's boxes.
`uninstall.sh` reverses the install; settings and the udev rule stay,
with the removal lines printed. The pen ledgers go to the Trash
(`gio trash`) and are never deleted; without `gio`, or when it refuses,
they stay and the script prints their folder.

A command shortcut needs all three: `X-KDE-GlobalAccel-CommandShortcut=true`
in the entry, `kbuildsycoca6`, and `doRegister` + `setForeignShortcut` on
the shortcut daemon. `install.sh` does all three. The shortcuts start
nothing until the next login, when the daemon rebuilds them from
`kglobalshortcutsrc`. `uninstall.sh` first calls `unregister` for each entry,
which frees its key at once, then deletes the entry's keys from that file.

Pad and ring chords: F-keys and modifiers only. KWin resolves a letter
through the active keyboard layout, so `Meta+Shift+P` sends nothing under a
Cyrillic layout.

The udev rule `install.sh` prints (`/etc/udev/rules.d/70-tablet-access.rules`):

    SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="*Pen*", TAG+="uaccess"
    KERNEL=="hidraw*", KERNELS=="0003:056A:*|0005:056A:*", TAG+="uaccess"
    KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess"

The first line gives the pen position (evdev). The second gives the touch
sense (hidraw). The third lets the daemon create the virtual device that
warps the mouse and presses the pie chords (`/dev/uinput`). Without the
rule, precision mode centres on the mouse, the daemon idles, and no pie
opens under the pen.

Runtime files live in `$XDG_RUNTIME_DIR/tabprec/`. The pen ledgers live in
`~/.local/state/tabprec/`, because they must outlive a logout.
`PROJECT_MAP.md` lists them with their writers and readers.

## Limitations, in detail

- Single monitor. The placement math uses the virtual screen, and
  `outputArea` fractions are per mapped output. With two outputs the area
  lands wrong. A fix computes in the mapped output's space.
- Display scale. The overlay takes pixel positions from the X screen and
  draws in Qt's logical pixels. At 100 % both are the same space. Nobody
  has tested other scales.
- Base mapping. The placement assumes the default full-tablet stretch.
  Toggle-off restores a letterboxed mapping from the Display page
  correctly, but the cursor-stationary placement drifts.
- A pen out of proximity. Whenever the pen is lifted away from the tablet,
  not only after the tablet has just appeared, there is no pen position,
  and a toggle places the area, and the ghost, around the mouse pointer.
- Stylus click-to-focus between windows does not work in Plasma 6.6 (KDE
  bug 498386 and friends). The bug is upstream.
- A sleeping DisplayPort monitor removes the output. KWin runs a 1920x1080
  placeholder, and every position read then is invalid. See `KNOWLEDGE.md`.
- This project does not support X11 sessions. `xsetwacom` covers the
  mapping there.

## Tests

`tests/run_all.sh` runs every gate in about 85 seconds. It needs Python 3,
bash and PyQt6 (for the import smoke and the center rig, both skipped
without it): no tablet, no KWin, no window on the desktop. Exit 0 = all
green.

| Gate | What it checks |
|---|---|
| `py_compile`, `bash -n` | every Python file compiles, every shell script parses |
| import smoke | `wacom_center.py` and `wacom_profiles.py` import offscreen (`QT_QPA_PLATFORM=offscreen`): a wrong import name cannot pass through `py_compile` |
| `tests/check_map.py` | every top-level def, function and mode has a chunk marker, every marker has a bullet in `PROJECT_MAP.md`, and every bullet names a marker; a source file's header stays at 2 comment lines |
| `tests/test_script.sh` (69 checks) | the toggle: on, move, resize around the centre with the clamp, the size limits (the defaults, no limit, one pixel), suspend, resume, off, the run lock, and a heal that waits for the pen without holding that lock. The write-back: a bus switch and a logout while on leave the pen on the whole screen, a base that is not the whole screen is put back as itself, the ledger fallback when the write-back cannot be proved, OFF with the tablet switched off, and a pause that resumes at the same area within `RECONNECT` seconds. A stubbed `busctl` that keeps `outputArea` per product AND seeds a device that has just appeared from `kcminputrc`, the way KWin does |
| `tests/test_size.sh` (24 checks) | the ring script: the step and the size limits, `RING_STEP` from the conf, resize only with the mode on, the preview only with it off, nothing while the marker is fresh, and 12 ticks at once stepping `SCALE` exactly 12 times without losing a conf line |
| `tests/test_hover.py` (95 checks) | the daemon: a named pipe plays the pad, a fake pen, fake overlays, a fake toggle. The ghost, the drag, `waiting` and `solid`, a conf edit mid-rest, `HOLD` 0, the long press and what must not arm it, the heal that a node set starts, and the runtime dir and control pipe the daemon makes for itself at a fresh login, and the absence timer that pauses precision mode. A held touch chord and the ghost released when the tablet goes away, and a new heal when it returns. A chord held through a conf reload goes up at the reload, once. A node that refuses its first open is opened by a later rescan, and so is a node whose reads end under the same name. One heal at a time, and a failed heal is run again, 5 runs at most. A pen with no position yet, in the daemon's reader and in `tablet-pen-pos.py` (the pen node found by `BTN_TOOL_PEN`, then exit 1 without asking XWayland). Left-handed mode: both pen readers turn the kernel's position (the one-shot reader before `--mapped`, the daemon's warp before the area), the XWayland position stays as it is, and KWin is asked again only after a change |
| `tests/test_profiles.py` (48 checks) | the profile library, Qt-free: a hostile profile (a shell payload in a value, an unknown key, a chord on the precision key, an oversized image, a 3 MB file that stops the switch with the live files untouched), the pie rule, the order of a switch's writes, owned conf keys kept, a 2026-09 fixture's round trip, the pressure writes with and without a connected pen, Left-handed mode written for both connections with nothing live while disconnected, and its state read from KWin first, the conf lock timeout, a missing active profile recreated, a ring tick between two saves (the ring script run from a scratch copy beside a no-op preview), profile names. The real `kreadconfig6` and `kwriteconfig6` on a scratch `kcminputrc`, logged by stubs, with a dead D-Bus address so `--notify` reaches no live session |
| `tests/test_center.py` (7 checks) | Wacom Center offscreen, with the profile rig's stubs: the turned table (key 8's picture at the far right of the top row, the Precision tick at the far left), a shortcut typed in the top row landing on key 8 when turned and on key 1 when not, the Left-handed box turning the table in place (an edit not applied yet moves with its row) and writing both connections and the live property, the ring's mode-1 light turned to the upper right, a shortcut typed in a ring mode's "clockwise" box landing in the second field, and the default ring (the smaller chord first, Swap unticked) with an older binding read as Swap ticked |

Not part of the gate: `tests/add_markers.py` puts a chunk marker above every
new def, function or mode (idempotent); `tests/check_map.py` imports its rule
to find a def with no marker.
