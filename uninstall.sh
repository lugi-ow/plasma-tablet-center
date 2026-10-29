#!/bin/bash
# uninstall.sh - remove plasma-tablet-center for the current user: the reverse of install.sh; safe to re-run.
# Your settings are kept; the paths and the one sudo line are printed at the end.
set -e
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
RD="${XDG_RUNTIME_DIR:-/tmp}/tabprec"

# Step 1 switches precision mode off FIRST, and stops if it cannot. KWin keeps
# the pen's mapping in kcminputrc and loads it again every time the tablet
# appears, so a precision rectangle left behind outlives the uninstall: the pen
# would stay in a small part of the screen with every tool that knew the full
# mapping deleted. RESCUE.txt says how to undo that by hand.

echo "== 1/5 Switching precision mode off"
if [ -f "$RD/saved-area" ]; then
    [ -x "$BIN/tablet-precision.sh" ] && "$BIN/tablet-precision.sh" >/dev/null 2>&1 || true
    if [ -f "$RD/saved-area" ]; then
        echo >&2
        echo "Precision mode is ON and this script cannot switch it off." >&2
        echo "Your pen would stay mapped to the small rectangle after the uninstall," >&2
        echo "because KWin keeps that mapping in kcminputrc." >&2
        echo "Switch precision mode off with its pad key, then run this script again." >&2
        echo "If the pad key does not work, see RESCUE.txt next to this script." >&2
        exit 1
    fi
    echo "switched off"
else
    echo "was not on"
fi

echo "== 2/5 Stopping the daemon"
pkill -f "$BIN/tablet-hover.py" 2>/dev/null && echo "stopped" || echo "was not running"

echo "== 3/5 Removing the scripts from $BIN; the pen ledgers go to the Trash"
for f in tablet-precision.sh tablet-precision-size.sh tablet-pen-pos.py \
         tablet-overlay.py tablet-overlay.qml tablet-size-preview.py \
         tablet-pointer-warp.py tablet-hover.py tablet-pad-probe.py \
         tablet_center.py tablet_profiles.py tablet-pie.sh tablet-size-preview.qml; do
    rm -f "$BIN/$f"                       # the last two: files older installs left behind
done
# The pen ledgers (tablet-precision.sh heal) go to the Trash, never deleted: a folder of records
# the user can still get back. No gio, or a refusal: the folder stays and its path is printed.
LEDGERS="${XDG_STATE_HOME:-$HOME/.local/state}/tabprec"
if [ -e "$LEDGERS" ]; then
    gio trash "$LEDGERS" 2>/dev/null && echo "the pen ledgers: moved to the Trash" ||
        echo "the pen ledgers stay in $LEDGERS: move that folder to the Trash yourself"
fi

# An install from before the rename left these two files and this menu entry.
rm -f "$BIN/wacom_center.py" "$BIN/wacom_profiles.py" "$APPS/wacom-center.desktop"   # keep-old-name

echo "== 4/5 Removing the launcher entries and their shortcuts"
for e in net.local.tabprec-toggle net.local.tabprec-bigger \
         net.local.tabprec-smaller net.local.tabpie; do
    # unregister first: the running shortcut daemon frees the key at once, not at the next login.
    # It keeps the entry's group in its config file, so the two key deletes below stay.
    busctl --user call org.kde.kglobalaccel /kglobalaccel org.kde.KGlobalAccel \
        unregister ss "$e.desktop" _launch >/dev/null 2>&1 || true
    rm -f "$APPS/$e.desktop"
    # kglobalaccel nests every .desktop under [services]: a flat group deletes nothing at all
    kwriteconfig6 --file kglobalshortcutsrc --group services --group "$e.desktop" --key _launch --delete 2>/dev/null || true
    kwriteconfig6 --file kglobalshortcutsrc --group services --group "$e.desktop" --key _k_friendly_name --delete 2>/dev/null || true
done
rm -f "$APPS/tablet-center.desktop" "$HOME/.config/autostart/tablet-hover.desktop"
kbuildsycoca6 2>/dev/null || true

echo "== 5/5 What stays (yours to keep or delete)"
cat <<'EOF'

- ~/.config/tabprec.conf                    your settings; delete it to forget them
- ~/.local/share/tablet-center/              your Tablet Center profiles; delete it to forget them
- ~/.local/share/wacom-center/               the profiles of the version before the rename, if you have it
- the pad-key and ring entries in kcminputrc  what the pad keys send; clear them in
                                            Tablet Center before uninstalling, or in
                                            System Settings -> Drawing Tablet after
- the pen's screen mapping in kcminputrc    step 1 put it back. If the pen still
                                            reaches only a small part of the screen,
                                            see RESCUE.txt next to this script
- /etc/udev/rules.d/70-tablet-access.rules  the permission rule (one sudo):

  sudo rm /etc/udev/rules.d/70-tablet-access.rules && sudo udevadm control --reload

Log out and back in once, so the shortcut daemon forgets the entries.
Done.
EOF
