# The knowledge (why these scripts look the way they do)

Companion to the plasma-tablet-center README.

Hard-won facts, each of which cost a debugging session. If you build your own
tablet tooling on Plasma Wayland, read this first.

**KWin owns the tablet mapping.** Each input device is a D-Bus object under
`org.kde.KWin /org/kde/KWin/InputDevice/eventN` with writable properties -
`outputArea` (the mapped screen rectangle, as fractions) is all precision
mode needs. Event numbers change on every Bluetooth reconnect: a cached
sysname is good only with a one-call `tabletTool` check on every run (the
toggle keeps one in `$XDG_RUNTIME_DIR/tabprec/pen`), and a stale one means
a new walk over the device list by its `tabletTool` / `tabletPad` property.

**KWin keeps every mapping you give it.** Each write to `outputArea` also
goes into `kcminputrc`, under
`[Libinput][<vendor id>][<product id>][<device name>]`, and KWin loads it
again when the device appears. Over USB and over Bluetooth the same tablet
has two product ids, so KWin keeps two pens. A precision rectangle thus
outlives an unplug, a switch to the USB cable and a logout. The pen then
comes back on the rectangle while no state file says that the mode is on.
The next toggle ON saves the rectangle as the base mapping, and every
toggle OFF returns to it.

This happened on 2026-09-10. Precision mode was on over Bluetooth when the
user connected the cable, and the next toggle OFF ran on the USB pen. The
Bluetooth pen then came back on the old rectangle.

The cable is a red herring. Logging out while the mode is on strands the pen
in exactly the same way, with one connection and no cable, and so does a
crash or an uninstall. The real fault is a lifetime mismatch: the effect
(`outputArea`) is permanent, and the only record that it was meant to be
temporary lives in `$XDG_RUNTIME_DIR`, which dies with the session.

So do not leave the effect behind. After every mapping write, write the
**base** mapping back into `kcminputrc` straight away, with
`kwriteconfig6 --notify` — delete the key when the base is the whole screen,
because an absent key and `0,0,1,1` are the same to KWin — and then read it
back to prove it landed. The precision rectangle then lives only in KWin's
memory, and the file always names the mapping the pen must have with the
mode off.

Two things make this work, and both were measured on Plasma 6.6 rather than
assumed. Writing the file does **not** end precision mode: the live mapping
stays in KWin's memory, and KWin applies the saved mapping only when a
device appears (`applyDeviceConfig` on `DEVICE_ADDED` in its libinput
backend, read from KWin's own kcminputrc object), so the pen stays in the
rectangle. And KWin does not write its own copy back over yours — the value
held for 90 seconds with the mode on. Write with `--notify`, as the toggle
does: the source read on 2026-10-20 did not settle what refreshes KWin's
copy of the file, and every hardware test so far used the flag.

Keep the old repair path as a fallback for a KDE that stores the mapping
somewhere else. If the read-back disagrees, record the rectangle in a file of
your own and put the pen right when it next appears. On a healthy system that
file is never written.

**OFF must not need the device.** A toggle that looks its device up before it
decides what to do cannot switch off once the device is gone, and a tablet
switched off while precision mode is on is an ordinary evening, not an edge
case. On 2026-09-12 the rectangle stayed on the screen after the tablet was
switched off, and the pad key, the keyboard shortcut and Tablet Center all
failed the same way, because all three ran the same lookup first. Ending the
mode needs only its own state. Restoring the live mapping needs the device,
but once the file already names the base, KWin does that by itself on the
next connect.

**Letter chords die under non-Latin layouts.** KWin's button-rebind injector
resolves a chord's letter through the *currently active* keyboard layout
(`keycodeFromKeysym` searches only that layout). With a Cyrillic layout
active there is no key producing a Latin `p`, so `Meta+Shift+P` sends
nothing - silently, except for a `kwin_buttonrebinds` line in the journal.
F-keys and bare modifiers exist in every layout. Bind pads and rings to
F-key chords only.

**Command shortcuts need three things.** A `.desktop` file that the shortcut
daemon will execute must carry `X-KDE-GlobalAccel-CommandShortcut=true`, must
be in the service cache (`kbuildsycoca6` after creating it), and must be
registered with the daemon (`doRegister` + `setForeignShortcut` on
`org.kde.kglobalaccel`). Miss any of the three and the chord does nothing.
After a relogin the daemon rebuilds everything from `kglobalshortcutsrc`.
That relogin is not optional. A launcher registered during the session holds
its key at once, but the key starts nothing until the next login (Plasma 6.6,
measured 2026-09-12). The reverse, `unregister(component, "_launch")` on
`org.kde.KGlobalAccel`, frees the key in the running daemon at once. The
daemon keeps the entry's group in `kglobalshortcutsrc`, so delete its keys as
well. At login the daemon skips an entry whose `.desktop` file is gone
(kglobalacceld source, read 2026-09-13).

**Ring bindings** live in `kcminputrc` as
`[ButtonRebinds][TabletRing][<pad device name>][<mode>]` with
`0=AxisKey,<up chord>,<down chord>,<threshold>`. The threshold is NOT in
degrees: KWin takes the degrees travelled since the last tick, multiplies
them by 120 (the mouse-wheel notch convention) and fires one chord when
that reaches the threshold, then restarts from the current position. The
Intuos Pro ring reports 5-degree steps, so any threshold up to 600 fires on
every step (72 ticks per turn) and 1800 fires every 15 degrees. An earlier
version of this note said 120 was 5 degrees and 360 was 15; it was wrong.
KWin fires the up chord on a counter-clockwise turn and the down chord on a
clockwise turn (an Intuos Pro M, October 2026). Tablet Center labelled the
two boxes the other way round until then.

**The pen's screen position is not exposed to scripts.** KWin's scripting
`workspace.cursorPos` is hardwired to the mouse. Two usable sources instead,
best first: the kernel's evdev state (`EVIOCGABS`, always fresh while the
pen is in proximity - needs the udev `uaccess` rule), and XWayland's stylus
device (XInput2 valuators in desktop coordinates - fresh only while the pen
hovers an X11 window).

**A lifted pen has no evdev position.** The kernel (`wacom_exit_report`)
zeroes the pen's X, Y and tool key whenever it leaves proximity, so a node
whose pen was just lifted away reads identically to one that has not
reported yet: `EVIOCGABS` returns the minimum on both axes with
`BTN_TOOL_PEN` clear (`EVIOCGKEY`). Taken as a position, it put the
precision area, the ghost and the mouse warp in the top-left corner (seen on
the hardware, 2026-09-12); both readers now treat that state as no position
at all (`OUT_OF_RANGE`) and fall back to the mouse. `tablet-pen-pos.py`
finds the pen node by that same key bit in `/proc/bus/input/devices`,
never by a bare name substring: "pen" is also in "Suspend". (The daemon's
own reader matches the end of a tablet device name, `Pen"`, which that
trap does not reach.)

**Left-handed mode turns the tablet in libinput, never in the kernel.**
With Left-handed mode on (System Settings > Drawing Tablet > Pen, KWin's
`leftHanded` on the pen device), libinput reads each axis of the pen as
`maximum - (value - minimum)` before the calibration, the tablet area and
KWin's `outputArea` (`evdev-tablet.c`, `invert_axis`, libinput 1.31). The
mapping thus works on the turned tablet, while the kernel's evdev position,
which a toggle reads to place the area under the pen, stays unturned. Read
raw, it put the area, the previews and the mouse warp on the opposite side
of the screen; turn the reading the same way (`1 - x`, `1 - y`). More facts
from the same sources. libinput applies a change only while the pen is out
of proximity, so it starts at the next lift. KWin stores the setting per
product id, so the USB and the Bluetooth connection keep one each (Tablet
Center's box writes both, like the pressure curve). libinput also turns the
pen when the tablet's touch surface has its own Left-handed mode on, and
the pen's `leftHanded` then stays false. The pad keeps its button numbers,
and the ring keeps its direction: a turn by 180 degrees leaves clockwise
clockwise. KDE's box sets the pen only. The pad has a Left-handed flag of
its own, which turns only the ring's absolute angle (libinput adds 180
degrees); KWin's ring shortcuts use the direction of a turn, so they never
see it. The Orientation box on the Display tab needs a
calibration matrix, which libinput gives only to display tablets, so on an
Intuos it reads "Not Applicable".

**The mouse pointer and the pen cursor are two different things.** KWin
keeps a pointer position for the mouse and a separate cursor for the
tablet. Everything that asks the compositor for "the pointer" gets the
mouse: `workspace.cursorPos` in KWin scripts, and Kando's KWin backend,
which uses that same call. A pie opened from a pad button lands where the
mouse was last left. The fix is to move the mouse onto the pen first. The
logged-in user may write `/dev/uinput` once a udev rule tags it `uaccess`
(the install prints one; on the development box Steam's rule had already done it),
so `tablet-pointer-warp.py` creates a virtual absolute mouse, sends one
motion, and removes it again, all in about 150 ms - no root, no portal
dialog. libinput maps the motion as a fraction of the axis range, so the
pointer lands on the same physical spot at any display scale.

**A warp racing KWin's own chord loses; a chord sent after the warp on the
same device cannot.** A pad key bound in `kcminputrc` fires its chord
INSIDE KWin's handling of the pad button, in the same pass of the event
loop: `busctl monitor` shows KWin's `globalShortcutPressed` signal, then
Kando's `loadScript` 0.6 ms later and `run` (the `workspace.cursorPos`
read) 1.2 to 2.2 ms after the signal. A warp started from the same HID
report needs a userspace round trip (select wake, evdev reads, uinput
writes, libinput, back into KWin) and landed 1 to 4 ms after that read -
the pie opened one press behind, every press (2026-09-09). The touch sense
cannot close the race either: over Bluetooth it led only 3 presses of 7,
and the pen kept moving after the finger landed, so the early warp was
itself stale. The fix is structural, not temporal: conf `CHORD_<n>` sets
the pad key to `Disabled` in kcminputrc and the daemon presses the chord
itself right after the warp on the SAME virtual device - the kernel,
libinput and KWin process one device's events in write order, so the
motion is in place before the chord triggers anything. `Disabled`, not a
deleted entry: KWin hands an unbound pad button to a tablet-aware app over
the tablet-pad protocol. The daemon keeps that one device for its whole
life: creating one per press would spend milliseconds on KWin's device
enumeration, and the chord's keys must already be registered.

**A release sent after a `kcminputrc` rebind write never lands.** KWin's
buttonrebinds plugin rebuilds its whole table and its virtual device on any
`[ButtonRebinds]` change, and sends no release for keys held at that
moment. A touch chord (Shift) held through a profile switch stayed pressed
on the hardware even when both profiles held the same chord (2026-09-14).
The daemon's journal showed it kept the chord and sent the release 4 s
later - correct, and too late: the switch had rewritten `kcminputrc` 0.3 s
after its reload poke. So the daemon releases every held chord AT the
reload, while the finger still rests, and spends that contact: lift and
land again for a new chord. Carrying the state was what the code already
did, and was exactly what failed.

**The kernel drops an unchanged ABS value.** `input_handle_abs_event`
ignores an `ABS_X` whose value equals the axis's current one. A long-lived
virtual mouse that warps to the spot of its previous warp sends a frame
with no events, and the pointer stays wherever the real mouse moved it
since. Send a first frame one unit away, then the real one.

**A sleeping monitor removes the output.** When a DisplayPort monitor goes
to standby, the link drops, KWin removes the output and runs a
`Placeholder-1` screen of 1920x1080. `workspace.virtualScreenSize` and
`cursorPos` then refer to that placeholder, Qt clients report "There are no
outputs", screenshots come back empty, and Xwayland keeps the old screen
size. Positions read while the monitor sleeps live in a space that does not
exist. (Plasma 6.6, September 2026.)

**The kernel does not expose the ExpressKey touch sense.** The Intuos Pro's
keys report a resting finger before the press - the vendor's "Express View".
The kernel's Bluetooth pad parser (`wacom_intuos_pro2_bt_pad`) reads only
bytes 281, 282 and 285 of report 0x80 (keys, centre button, ring), and the
`EXPRESSKEYCAP` HID usage has no mapping. The only way to get it is to read
the raw reports from `/dev/hidraw*` next to the kernel driver
(`tablet-pad-probe.py`, `tablet-hover.py`).

**A hidraw node exists before udev grants access to it.** On a USB plug-in
the daemon's 2 s rescan can open `/dev/hidraw*` in the gap between the
kernel creating the node and udev applying the `uaccess` rule: `[Errno 13]
Permission denied` in the journal (2026-09-30, twice in one night). The
daemon opened each node once and compared only node names afterwards, so
the refused node stayed closed until the bus changed: no preview, no pies,
no touch chords, while the other node and KWin's own shortcuts worked. A
node that would not open is now tried again at every rescan, and so is a
node whose read ends while its name stays.

**The tablet ends a Bluetooth connection that nobody uses.** Switched on
over Bluetooth and then left alone, an Intuos Pro M disconnected after 17
seconds. BlueZ reported `Device1.Disconnected` with
`org.bluez.Reason.Remote` ("Connection terminated by remote user"), so the
tablet ended the connection, not the computer. The tablet sent no HID
report between the connect and the disconnect. In use, the same tablet
stayed connected for 33 minutes. The toolkit opens every tablet node
read-only, and BlueZ's `IdleTimeout` in `/etc/bluetooth/input.conf` stays
at 0, which means no timeout. The timer is in the tablet, and the vendor does
not document it. (Kubuntu 26.04, BlueZ 5.85, October 2026.)

**Overlays that must not eat input**: a Qt window with
`Qt.WindowTransparentForInput` on the layer-shell overlay layer
(`org.kde.layershell` QML module) covers the screen, stays click-through,
and survives everything except its own process exit. The module is the
package `qml6-module-org-kde-layershell`, and a Plasma desktop does not
always pull it in: on Kubuntu 26.04 only `plasma-keyboard` depends on it
(`apt-cache rdepends --installed`, 2026-09-13). Without it the overlay
process exits at load, after the pen mapping has already changed.

**A layer-shell window's scope sets its KWin window type.** KWin 6.6 maps
the scope to a type (`desktop`, `dock`, `notification`, `tooltip`,
`on-screen-display`, `dialog`, `splash`, `utility`). Any other scope is a
normal window, and layer-shell-qt's default scope is `window`. A normal
window that appears ends Peek at Desktop (`Meta+D`), so every hidden window
comes back, and Peek at Desktop hides the window itself. KWin exempts docks
(`Workspace::breaksShowingDesktop`), so the overlay sets `scope: "dock"`, the
type Plasma's panels use. Measured in a headless nested KWin 6.6.6 on
2026-09-13: with Peek at Desktop on, the overlay started. Without the scope,
KWin typed it `normal=true` and Peek at Desktop turned off. With `dock`, it
was `dock=true` and Peek at Desktop stayed on.

**Moving a running overlay from other processes**: give it a named pipe
and open the pipe `O_RDWR | O_NONBLOCK` on the reading side. A read-only
open hits EOF, and a spinning read notifier, as soon as the last writer
closes; with the reader also holding a write end, writers come and go
(`echo "X Y W H" > pipe` from the shell, `os.write` from the daemon), and
lines under `PIPE_BUF` stay whole. Wrap the shell write in `timeout` for
the case of a reader that died.

**Two processes see one button press, at different times.** The pad's raw
hidraw report reaches the hover daemon milliseconds before KWin's shortcut
even starts the toggle script, so the daemon must not delete its "this
press is a move" marker on the press. It stops refreshing the marker
instead, and the script accepts only a marker younger than 3 s. A stale
marker is then harmless by construction.

**One-shot KWin scripts** are the escape hatch for anything only the
compositor knows: load JS via `org.kde.kwin.Scripting`, `print()` the
answer, read it back from `journalctl --user -u plasma-kwin_wayland`.

**A dashed border that flows** is a `QtQuick.Shapes` path with
`strokeStyle: ShapePath.DashLine` and an animated `dashOffset` (a
`Rectangle` border cannot be dashed at all). Two facts the docs leave you
to find: the dash pattern and the offset are in units of the pen WIDTH, not
pixels (`[2.5, 2.5]` at width 2 is 5 px on, 5 px off), and a GROWING
offset moves the dashes BACKWARD along the path - to flow clockwise around
a rectangle drawn clockwise, animate the offset from one period down to 0.
Use `capStyle: ShapePath.FlatCap`, or the default square caps stretch every
dash by a pen width at each end. `qml6-module-qtquick-shapes` is a
dependency of plasma-desktop, so it is on every Plasma 6 system.

**Two runs of the same script can overlap.** A long press on the pad key
means KWin's shortcut runs the toggle on the key-down and the daemon runs
it again later; with a short long-press time they overlap, and two
interleaved runs leave the mapping set by one and the state files removed
by the other. `flock` on a runtime file at the top of the script
(`exec 9>lock; flock -w 5 9`) serializes them, with one trap: the overlay
the script spawns with `nohup … &` inherits descriptor 9 and would hold
the lock for as long as it lives. Close it on the spawn (`9>&-`).

**A shortcut-driven ring tick is a process, and processes queue.** KWin
fires the chord on every 5-degree step, the shortcut daemon launches the
script for each one, and a quick swipe puts a dozen in flight while each
needs about 100 ms of D-Bus work; serialized by a lock they run one after
another and the area keeps stepping after the finger has stopped. Two
rules make the queue collapse: read the setting AFTER taking the lock, so
a queued run applies the newest value, and do nothing when what is on
screen already matches, so the rest of the queue drains in milliseconds.
Cache the device lookup as well: asking KWin for the name of each of a
dozen devices costs more than the resize itself.

**Rendering a layer-shell QML file without a display** works for tests:
`QT_QPA_PLATFORM=offscreen`, load it, `grabWindow()` on the root
(LayerShellQt only warns "not a wayland window"). In PyQt6 the root comes
back as a plain `QWindow` without `grabWindow` unless `PyQt6.QtQuick` is
imported somewhere first.
