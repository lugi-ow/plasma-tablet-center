#!/bin/bash
# install.sh - deploy plasma-wacom-center for the current user; safe to re-run. No root for the deploy itself:
# one udev rule (printed at the end) needs sudo - the pen position, the express keys' touch sense, /dev/uinput for the pies.
set -e
cd "$(dirname "$0")"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
KW=org.kde.kglobalaccel

echo "== 1/5 Deploying to $BIN"
mkdir -p "$BIN" "$APPS"
install -m 755 tablet-precision.sh tablet-precision-size.sh \
    tablet-pen-pos.py tablet-overlay.py tablet-size-preview.py \
    tablet-pointer-warp.py tablet-hover.py tablet-pad-probe.py \
    wacom_center.py wacom_profiles.py "$BIN/"
install -m 644 tablet-overlay.qml "$BIN/"
rm -f "$BIN/tablet-size-preview.qml"    # retired: the size preview draws with tablet-overlay.qml

echo "== 2/5 Shortcut launcher entries (command-shortcut marker included)"
mkentry() {  # name, exec-args, description
    cat > "$APPS/net.local.$1.desktop" <<EOF
[Desktop Entry]
Exec=$BIN/$2
Name=$3
NoDisplay=true
StartupNotify=false
Type=Application
X-KDE-GlobalAccel-CommandShortcut=true
EOF
}
mkentry tabprec-toggle "tablet-precision.sh" "Precision mode toggle"
mkentry tabprec-bigger "tablet-precision-size.sh up" "Precision area bigger"
mkentry tabprec-smaller "tablet-precision-size.sh down" "Precision area smaller"
cat > "$APPS/wacom-center.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Wacom Center
Comment=Tablet precision mode and express key settings
Exec=python3 $BIN/wacom_center.py
Icon=input-tablet
Terminal=false
Categories=Settings;HardwareSettings;
EOF
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/tablet-hover.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Tablet hover preview
Comment=Ghost of the precision area while a finger rests on the precision key (idle until a supported tablet appears)
Exec=python3 $BIN/tablet-hover.py
NoDisplay=true
EOF
kbuildsycoca6 2>/dev/null || true

echo "== 3/5 Registering global shortcuts (F12 toggle, F10 bigger, F9 smaller)"
reg() {  # component, friendly, qt-keycode
    busctl --user call $KW /kglobalaccel org.kde.KGlobalAccel doRegister \
        as 4 "net.local.$1.desktop" "_launch" "$2" "$2"
    busctl --user call $KW /kglobalaccel org.kde.KGlobalAccel setForeignShortcut \
        asai 4 "net.local.$1.desktop" "_launch" "$2" "$2" 1 "$3"
}
reg tabprec-toggle  "Precision mode toggle"  318767163   # Meta+Shift+F12
reg tabprec-bigger  "Precision area bigger"  318767161   # Meta+Shift+F10
reg tabprec-smaller "Precision area smaller" 318767160   # Meta+Shift+F9

echo "== 4/5 Ring binding (optional)"
pad=""
for n in $(busctl --user get-property org.kde.KWin /org/kde/KWin/InputDevice \
           org.kde.KWin.InputDeviceManager devicesSysNames 2>/dev/null \
           | tr -d '"' | cut -d' ' -f3-); do
    p=$(busctl --user get-property org.kde.KWin "/org/kde/KWin/InputDevice/$n" \
        org.kde.KWin.InputDevice tabletPad 2>/dev/null)
    case "$p" in *true*)
        pad=$(busctl --user get-property org.kde.KWin "/org/kde/KWin/InputDevice/$n" \
              org.kde.KWin.InputDevice name | cut -d'"' -f2)
        break;;
    esac
done
if [ -n "$pad" ]; then
    printf 'Bind the touch ring of "%s" (mode 1) to the size control? [y/N] ' "$pad"
    read -r yn || yn=""        # stdin at EOF (a piped run) must not end the script under set -e
    if [ "$yn" = "y" ] || [ "$yn" = "Y" ]; then
        if kwriteconfig6 --notify --file kcminputrc --group ButtonRebinds \
            --group TabletRing --group "$pad" --group 0 --key 0 \
            "AxisKey,Meta+Shift+F10,Meta+Shift+F9,600"; then
            echo "Ring bound: one tick per 5-degree step of the ring (KWin's threshold is degrees x 120); change the step and the angle in Wacom Center."
        else
            echo "Could not write the ring binding - bind it in Wacom Center (Pad buttons tab, the ring rows)."
        fi
    fi
else
    echo "No tablet pad detected (tablet asleep?). Re-run later for the ring."
fi

echo "== 5/5 Manual steps that need you"
QML=$(python3 -c 'import PyQt6.QtQuick; from PyQt6.QtCore import QLibraryInfo as L; print(L.path(L.LibraryPath.QmlImportsPath))' 2>/dev/null) || QML=""
if [ -z "$QML" ] || [ ! -d "$QML/org/kde/layershell" ]; then   # the overlay imports org.kde.layershell: without it the pen is mapped and nothing is drawn
    cat <<'EOF'

0. Install the missing packages first. Without them the overlay cannot start:
   precision mode would still change the pen area, but draw nothing.

   sudo apt install python3-pyqt6 python3-pyqt6.qtquick qml6-module-org-kde-layershell
EOF
fi
cat <<'EOF'

A. One permission rule (one sudo, once). It lets the scripts read the pen's
   position, on Wacom tablets the express keys' touch sense, and the
   virtual mouse that opens a pie under the pen (/dev/uinput):

   printf '%s\n' 'SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="*Pen*", TAG+="uaccess"' 'KERNEL=="hidraw*", KERNELS=="0003:056A:*|0005:056A:*", TAG+="uaccess"' 'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess"' | sudo tee /etc/udev/rules.d/70-tablet-access.rules && sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=input --subsystem-match=hidraw --subsystem-match=misc

   Without it, precision mode centres on the mouse cursor instead of the
   pen, and the touch preview stays off.

B. Bind the pad buttons in Wacom Center (the Pad buttons tab):
   - Precision mode: tick one key's "Precision" box, click "Apply
     bindings". The tab gives that key the toggle shortcut, the ghost
     and the area drag by itself.
   - A pie menu (needs Kando): give the menu a shortcut in Kando's
     editor, type the same shortcut for a pad key (Touch or Press),
     tick that side's "Pie keys" box, apply - the toolkit then opens
     the menu under the pen.
   - A touch chord: type a chord (Ctrl, say) into a key's "Touch" box,
     apply - the chord is held while a finger rests on that key.
   Use only F-keys and modifiers in pad chords - letters silently fail
   under non-Latin keyboard layouts.

C. Log out and back in once. Until then, the new shortcuts start nothing:
   the shortcut daemon loads their commands only at login.

D. ExpressKey touch preview - a ghost of the precision area while a finger
   RESTS on the precision key, before the press. Wacom Intuos Pro (2017 or
   later): after step A, a relogin and the "Precision" tick from step B,
   a resting finger shows the ghost. With the mode on, a resting finger
   drags the area, a press lands it, and keeping the key pressed leaves the
   mode (the times are fields in Wacom Center). Other tablets: only keys with a touch
   sensor can do this (Intuos Pro, Cintiq Pro, MobileStudio Pro). For a
   Wacom model the daemon does not know, run
   ~/.local/bin/tablet-pad-probe.py once per connection type (USB and
   Bluetooth differ) and put the bytes it shows into ~/.config/tabprec.conf
   as HOVER_REPORT_USB, HOVER_BYTE_USB, PRESS_BYTE_USB (and the same with
   _BT). Reference, Intuos Pro M: USB report 0x11, touch byte 2, press
   byte 1; Bluetooth report 0x80, touch byte 283, press byte 282.

To remove everything later, run ./uninstall.sh from this folder - your
settings stay, and it prints the one sudo line for the permission rule.

Done.
EOF
