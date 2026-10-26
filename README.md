# plasma-wacom-center

Precision mode for drawing tablets on KDE Plasma 6 (Wayland), like the one
in Wacom's own driver on Windows. It comes with a pie menu that opens under
the pen and a small settings window.

Wacom's own settings app is for Windows and macOS only. Plasma's Drawing Tablet page sets the mapping,
the pressure curve and the buttons, but it has no precision mode. This
project adds it with a few small scripts. There is nothing to compile.

Built and tested on Kubuntu 26.04 with Plasma 6.6 and a Wacom Intuos Pro M
over Bluetooth. Any tablet that Plasma's Drawing Tablet page recognizes
works. The touch preview, the area drag and the pie menu under the pen
need a Wacom tablet with touch-sensitive pad keys (Intuos Pro, Cintiq Pro,
MobileStudio Pro).

## What it does

- **Precision mode.** Press a pad key. The pen now maps to a small area
  around the cursor, for detail work. Press the key again to get the full
  screen back. The cursor does not jump, and the screen outside the area
  dims. The area has the shape of your tablet, so your strokes keep their
  proportions.
- **Ring size control.** Turn the touch ring to make the area bigger or
  smaller. With precision mode off, a preview shows the new size. With it
  on, the area itself grows or shrinks in place.
- **Touch preview.** Rest a finger on the precision key. A ghost shows where
  the area will land, and it follows the pen.
- **Area drag.** With precision mode on, rest a finger on the precision key
  and move the pen. The area follows the pen. Press the key to put the area
  there. Keep the key pressed to leave precision mode.
- **Pie menu under the pen.** A pad key opens a [Kando](https://kando.menu)
  pie menu at the pen, not where the mouse was left.
- **Touch chords.** Rest a finger on a pad key and the key holds a chord
  for you - rest for Ctrl while you sculpt in Blender, lift to release.
- **Wacom Center.** A settings window for the area size, the dim strength,
  the times, the ring's four modes, every pad key, and the pen's buttons
  and pressure curve.
- **Profiles.** Save your Wacom Center settings as a profile, and switch
  between profiles with one click.
- **Left-handed use.** Use the tablet turned around, with the keys on the
  right. One box in Wacom Center sets left-handed mode for the cable and
  for Bluetooth.

Precision mode has a solid amber border. A preview has a dashed border that
moves.

## In pictures

https://github.com/user-attachments/assets/5c04b1b3-ea14-4669-b458-18271664d768

<p align="center"><img src="docs/screenshot-precision-mode.png" width="880" alt="Precision mode on: a reference photo in Gwenview on the left, Krita on the right, and a tablet-shaped area with an amber border over the Krita canvas"></p>

Precision mode on, with a reference photo in Gwenview and the drawing in
Krita. The pen maps to the area with the amber border. The rest of the
screen dims. The cursor did not move when the mode came on.

<p align="center"><img src="docs/screenshot-wacom-center-precision.png" width="400" alt="Wacom Center, Precision tab"> <img src="docs/screenshot-wacom-center-pad.png" width="400" alt="Wacom Center, Pad buttons tab"></p>

Wacom Center. Left, the Precision tab. Right, the Pad buttons tab.

## Requirements

- KDE Plasma 6.5 or newer, on Wayland. This project does not work on X11.
  On X11, use `xsetwacom` instead.
- Three packages: two for Python and one for the overlay. Some Plasma
  installs do not include the overlay package. Without it, precision mode
  still changes the pen area, but no border and no dim appear.
- [Kando](https://kando.menu), if you want the pie menu.

```
sudo apt install python3-pyqt6 python3-pyqt6.qtquick qml6-module-org-kde-layershell
```

## Install

1. In a terminal, run:

   ```
   git clone https://github.com/lugi-ow/plasma-wacom-center
   cd plasma-wacom-center
   ./install.sh
   ```

   The installer copies the scripts to `~/.local/bin` and creates the
   shortcuts. It adds the touch preview to autostart and offers to bind
   the touch ring. At the end it prints the steps that need you.

2. Run the `sudo` command the installer prints. It adds one permission
   rule, so the scripts can read the pen position and the touch sense of
   the pad keys, and so the pie menus can open under the pen.

3. Open Wacom Center from the application menu, then the Pad buttons tab.

4. Tick the round Precision box for the pad key you want, then click
   "Apply bindings". That key now toggles precision mode, shows the ghost,
   and drags the area.

5. Log out and log in again. This starts the touch preview and makes the
   shortcuts work.

Bind pad keys to F-keys and modifiers only. A letter does not work while a
non-Latin keyboard layout is active.

## Update

1. Close Wacom Center if it is open.

2. In the folder you cloned, run:

   ```
   git pull
   ./install.sh
   ```

   The installer replaces the scripts in place. Your settings and your
   profiles stay.

3. Restart the pad helper, so it runs the new code:

   ```
   systemctl --user restart 'app-tablet\x2dhover@autostart.service'
   ```

   Or log out and log in again. That restarts the helper too.

## The precision key

You pick the precision key yourself: in Wacom Center's Pad buttons tab,
tick the round box in the Precision column for the key you want, then
click "Apply bindings". That key now toggles precision mode. On a Wacom
Intuos Pro (2017 or later) the pad keys sense a finger that rests on
them, before the press — Wacom calls these keys ExpressKeys — and the
toolkit uses that sense for the ghost and the area drag. The key's row
shows red moving outlines: precision mode owns that key, and its Touch
and Press fields do nothing else. Untick the box and the ghost and the
drag switch off.

| Precision mode | What you do | What happens |
|---|---|---|
| off | press the key | precision mode comes on, around the cursor |
| off | rest a finger on the key | a ghost shows where the area will land, and follows the pen |
| on | press the key at once | precision mode goes off |
| on | rest a finger for the key's Touch register, then press | the area moves to the pen |
| on | rest a finger, then lift it without a press | nothing changes |
| on | rest a finger, press, and keep the key down for the long-press time | the area moves, then precision mode goes off |

The Touch register is the key's own time field in the Pad buttons tab;
the long-press time is in the Precision tab. With the register at 0,
every press is a move, and the long press is the way out of precision
mode.

If the tablet disconnects while precision mode is on, precision mode
switches off after 10 seconds, so the border does not stay on the screen.
If the tablet connects again within the reconnect time (60 seconds, in the
Precision tab), precision mode comes back in the same place. Set the
reconnect time to 0 to keep precision mode off.

## Settings

Open Wacom Center from the application menu, in the Settings category.
The Precision tab has the area size (5 to 80 % of the screen width), the
smallest and the largest size (change these two limits, or set one to 0
for no limit), the dim strength, the long-press time, the reconnect time, the ring step, the
tick angle, and a switch for the ring direction. The Pad buttons tab is the whole pad as a
table: each key's Touch and Press shortcuts, its own Touch register, the
ring's four modes, the Pie keys column (one tick per side), and the
Precision column that names the precision key. Hover a column title for
the explanation. The Left-handed box above the table is for a tablet
turned around (see "Left-handed use").

## Pen

Wacom Center has a Pen tab. It sets the pen's three buttons, its
pressure curve, and its pressure range.

Pen button 1 and Pen button 2 work like the boxes in the Pad buttons
tab. Type a shortcut, leave the box empty, or type "Disabled". Pen
button 3 needs both of the pen's side buttons pressed together, and it
only works over a USB connection. Some pens have no third button.

A box that holds a mouse-button binding from System Settings turns
read-only, with a note that tells you where to change it. A keyboard
shortcut set there shows as a normal box you can edit. Wacom Center
never clears a binding it did not set.

The pressure curve is a small graph with two handles you drag, like
the one in System Settings' Drawing Tablet page. Four number fields
next to the graph show the same two points. Drag the graph to change
the numbers, or type a number to move the graph. Click "Reset
Pressure" to get a straight line back.

Two more fields set the lowest and the highest pressure the pen uses.
Click "Apply" to save the tab.

The tab works even with the pen disconnected. Set your bindings and
your curve now. They take effect once the pen connects.

## Left-handed use

To use the tablet turned around, with the keys on the right, tick
"Left-handed" at the top of Wacom Center's Pad buttons tab. Wacom Center
sets KDE's Left-handed mode for the cable and for Bluetooth at once.
(System Settings > Drawing Tablet > Pen has the same box, but it sets
only the connection that you use at that time.) The precision area, the
touch preview and the pie menus follow it. The change starts when you
next lift the pen away from the tablet.

The Pad buttons table turns with the tablet: the keys on the right side,
key 8 on top. Each row stays with its own key, so a shortcut that you
type in a row goes to the key that you see beside it. The ring keeps its
directions: clockwise stays clockwise.

## Profiles

A profile is a saved copy of every setting on the Precision, Pad
buttons, and Pen tabs. Pie menus stay in Kando, so they are not part
of a profile.

The Profiles tab is the last tab in Wacom Center. A button at the top
names the profile you use now. Click it to open a list. Type to
search, or click a profile in the list to switch to it. A "+" button
next to it adds a profile from a file on disk.

Below the button is a grid of nine cells. A filled cell shows a
profile's picture and name. Click a filled cell to switch to that
profile at once. A dashed cell is empty. Click "Edit Grid" to fill a
cell, remove a picture, or add one.

Click "Export All Current Settings as Profile..." to save your
current setup as a new profile file. Use it to back up your settings,
share them, or start a new profile from where you are.

Switching profiles is instant. It never turns precision mode on or
off by itself. Wacom Center keeps one backup of a profile the first
time you change it in a session, so you can undo a mistake. Your
profiles survive an uninstall.

## Pie menu

Plasma has no pie menus. [Kando](https://kando.menu) adds them, and it
works on Plasma Wayland. `examples/kando-krita-menu.json` is a Kando menu
file with a Krita menu to start from: brush, transform, selection tools,
and a canvas rotation reset.

For each menu, give it a shortcut in Kando's editor. Then open Wacom
Center's Pad buttons tab, type the same shortcut for the pad key you
want — in Press to open it with a press, or in Touch to open it when
your finger rests on the key — and tick that side's box in the Pie keys
column. The toolkit moves the mouse onto the pen and then presses the
shortcut for you, in that order, so the menu opens under the pen every
time. A touch pie even selects when you lift the finger on a slice. Use
a shortcut made of F-keys and modifiers, and use each one once.
`TECHNICAL.md` says how it works.

## Known limitations

- One monitor. With two monitors the area lands in the wrong place.
- Display scale 100 % only. Nobody has tested other scales.
- Keep the tablet mapped to the full screen in System Settings. With a
  custom mapping, precision mode still turns off correctly, but the area
  can land away from the cursor.
- The toolkit knows where the pen is only while the pen is near the
  tablet. If you turn precision mode on with the pen lifted away, the area
  lands around the mouse pointer. The touch preview does the same.
- The touch preview, the touch chords, the area drag and the pie keys need
  pad keys with a touch sensor (Wacom Intuos Pro, Cintiq Pro, MobileStudio
  Pro): the helper that opens a pie reads the keys through that sensor. On
  other tablets the toggle and the ring still work. For a pie there, type
  the menu's shortcut into the key's Press box and leave its Pie box
  unticked. The menu then opens at the mouse pointer, not under the pen.
- A pen click does not move the focus to another window. This is a Plasma
  6.6 bug (KDE bug 498386), not something this project can fix.
- Over Bluetooth, the tablet switches itself off if you do not use it soon
  after you switch it on. On an Intuos Pro M this happens within about 20
  seconds. This is the tablet's own power saving, not something this
  project does. Have the pen in your hand before you switch the tablet on.
- If the pen reaches only a small part of the screen after an uninstall
  or a crash, [the rescue steps](RESCUE.txt) put it back.

## Uninstall

If you want the pad keys back to plain shortcuts, clear them in Wacom
Center first (untick the Pie and Precision boxes, set the shortcuts you
want, Apply). Then run, from the folder you cloned this repository
into:

```
./uninstall.sh
```

It stops the daemon and removes what the install put on your system:

- the scripts, from `~/.local/bin`
- the launcher entries, from `~/.local/share/applications`
- the autostart entry, from `~/.config/autostart`
- their shortcuts, from KDE's shortcut service and its config file
- the pen records in `~/.local/state/tabprec`: these go to the Trash,
  so you can get them back

Your settings stay in `~/.config/tabprec.conf`; the script prints that
path and the one `sudo` line that removes the permission rule from
`/etc/udev/rules.d`. Log out and back in once to finish. If you deleted
the cloned folder, clone it again for the script — or remove the files
from the folders above by hand.

[If the pen stays in a small area: the rescue steps](RESCUE.txt)

## For contributors and AI agents

- `TECHNICAL.md` says how the toolkit works: the placement rule, the
  overlay, the ring, the touch preview, the settings and the tests.
- `PROJECT_MAP.md` lists every file, process and runtime file, and every
  chunk marker (`# ── chunk: <name>`), so you can grep instead of read.
- `KNOWLEDGE.md` holds the facts behind the design. Each one cost a
  debugging session.
- `tests/run_all.sh` runs every gate in about 85 seconds, without a tablet.

## License

MIT. Built with Claude Code.
