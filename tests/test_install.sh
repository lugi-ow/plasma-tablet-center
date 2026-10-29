#!/bin/bash
# tests/test_install.sh <scratch dir> - the .deb rig: builds the package, unpacks it into a scratch root and runs
# its first start on a scratch HOME with the logging stubs. No root, no dpkg database, no live bus.
S="$1/install-test"
HERE=$(cd "$(dirname "$0")" && pwd)
rm -rf -- "$S" && mkdir -p "$S/out" "$S/root" "$S/home" "$S/data"
fails=0
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }

DEB=$(TMPDIR="$S" bash "$HERE/../packaging/build-deb.sh" 9.9+rig "$S/out")
check "build: the .deb is named for its version" '[ "$DEB" = "$S/out/plasma-tablet-center_9.9+rig_all.deb" ] && [ -s "$DEB" ]'
check "build: the build tree is gone" '[ -z "$(ls "$S" | grep -v -e out -e root -e home -e data)" ]'
dpkg-deb -x "$DEB" "$S/root"
dpkg-deb -e "$DEB" "$S/ctl"
L=$S/root/usr/lib/plasma-tablet-center
check "files: 45 entries, owned by root" '[ "$(dpkg-deb -c "$DEB" | wc -l)" = 45 ] && [ -z "$(dpkg-deb -c "$DEB" | grep -v " root/root ")" ]'
check "files: 13 scripts, no __pycache__" '[ "$(ls "$L" | wc -l)" = 13 ] && ! dpkg-deb -c "$DEB" | grep -q pycache'
U=$S/root/usr/lib/systemd/user
check "files: the pen driver unit points at the packaged helper (start and --check) and is enabled for every user" 'grep -qx "ExecStart=/usr/lib/plasma-tablet-center/tablet-pen-driver.py" "$U/tablet-pen-driver.service" && grep -qx "ExecCondition=/usr/lib/plasma-tablet-center/tablet-pen-driver.py --check" "$U/tablet-pen-driver.service" && [ "$(readlink "$U/default.target.wants/tablet-pen-driver.service")" = ../tablet-pen-driver.service ] && [ -x "$L/tablet-pen-driver.py" ]'
check "files: lintian is told why the focus follower's autostart file is not a conffile" 'grep -q "tablet-appfocus.desktop" "$S/root/usr/share/lintian/overrides/plasma-tablet-center"'
check "files: md5sums match" '(cd "$S/root" && md5sum --quiet -c "$S/ctl/md5sums")'
check "files: Python shebang /usr/bin/python3" '[ "$(head -1 "$L/tablet_center.py")" = "#!/usr/bin/python3" ] && ! grep -l "^#!/usr/bin/env" "$L"/* >/dev/null'
check "files: every Exec names a packaged file" \
    '(for e in $(grep -h "^Exec=" "$S/root/usr/share/applications/"*.desktop "$S/root/etc/xdg/autostart/"*.desktop | cut -d= -f2 | cut -d" " -f1); do [ -x "$S/root$e" ] || exit 1; done)'
check "files: the udev rule has the 3 uaccess lines" '[ "$(grep -c "TAG+=\"uaccess\"" "$S/root/usr/lib/udev/rules.d/70-tablet-access.rules")" = 3 ]'
check "control: Depends = the brief's 4 + the overlay's QML modules + kconfig's tools" 'grep -qx "Depends: python3, python3-pyqt6, python3-pyqt6.qtquick, qml6-module-org-kde-layershell, qml6-module-qtquick, qml6-module-qtquick-window, qml6-module-qtquick-shapes, libkf6config-bin" "$S/ctl/control"'
check "app id: setDesktopFileName equals the menu entry's name and the AppStream launchable" \
    'ID=$(sed -n "s/.*setDesktopFileName(\"\([^\"]*\)\").*/\1/p" "$L/tablet_center.py" | head -1); [ -n "$ID" ] && [ -f "$S/root/usr/share/applications/$ID.desktop" ] &&
     grep -q "<launchable type=\"desktop-id\">$ID.desktop</launchable>" "$S"/root/usr/share/metainfo/*.xml'
check "control: it replaces the old package in one apt install" 'grep -qx "Conflicts: plasma-wacom-center" "$S/ctl/control" && grep -qx "Replaces: plasma-wacom-center" "$S/ctl/control"'  # keep-old-name
check "control: prerm stops each user's helper (pkill as the fallback) and is valid sh" 'sh -n "$S/ctl/prerm" && grep -q "systemctl --user -M \"\$u@\" stop tablet-pen-driver" "$S/ctl/prerm" && grep -q "pkill -TERM.*tablet-pen-driver.py" "$S/ctl/prerm"'
check "control: postinst restarts each user's pen driver, try-restarts the pad helper (only a running one), starts the focus follower again, before its exit 0" 'grep -q "systemctl --user -M \"\$u@\" restart tablet-pen-driver" "$S/ctl/postinst" && grep -q "try-restart .app-tablet.x2dhover@autostart.service" "$S/ctl/postinst" && grep -q "start .app-tablet.x2dappfocus@autostart.service" "$S/ctl/postinst" && ! grep -q "try-restart tablet-pen-driver\|SIGUSR1" "$S/ctl/postinst" "$S/ctl/prerm" && [ "$(tail -1 "$S/ctl/postinst")" = "exit 0" ] && [ "$(grep -c "^exit 0" "$S/ctl/postinst")" = 1 ]'
check "control: the pad helper's autostart entry is the one conffile (the focus follower's is a plain file, so a downgrade removes it)" '[ "$(cat "$S/ctl/conffiles")" = /etc/xdg/autostart/tablet-hover.desktop ]'
check "control: prerm ends the tablet-appfocus.py process and is valid sh" 'sh -n "$S/ctl/prerm" && grep -q "pkill -TERM.*tablet-appfocus.py" "$S/ctl/prerm"'
check "control: postinst + postrm reload udev" 'grep -q "udevadm control --reload" "$S/ctl/postinst" && grep -q "udevadm trigger" "$S/ctl/postrm" && sh -n "$S/ctl/postinst"'

# The unpacked copy, its first start as if it sat in /usr/lib: TOGGLE beside it, the shortcuts, the helper.
# A worktree checks the stubs out without the x bit: PATH would then fall through to the real busctl and
# systemctl, so the rig stops instead; the scratch XDG_RUNTIME_DIR keeps even a real systemctl --user off the live manager.
[ -x "$HERE/stubs/busctl" ] && [ -x "$HERE/stubs/systemctl" ] ||
    { echo "FAIL stubs: tests/stubs/* not executable here (chmod +x tests/stubs/*)"; echo "failures: $((fails + 1))"; exit 1; }
mkdir -p "$S/runtime"
OUT=$(cd "$S" && HOME="$S/home" XDG_DATA_HOME="$S/data" XDG_RUNTIME_DIR="$S/runtime" XDG_CONFIG_HOME="$S/home/.config" STUB_LOG="$S/stub.log" \
      PATH="$HERE/stubs:$PATH" DBUS_SESSION_BUS_ADDRESS="unix:path=$S/no-bus" python3 -I -c "
import sys; sys.path.insert(0, '$L')
import tablet_profiles as wp
from pathlib import Path
print(wp.TOGGLE)
print(wp.PKG_DIR)
print(wp.HOVER_UNIT)
wp.PKG_DIR = Path('$L')
print(wp.package_setup()[0])" 2>&1)
AUTO=tablet-hover   # the pad helper's autostart file; the focus follower has its own
check "first start: TOGGLE is the packaged toggle" '[ "$(echo "$OUT" | sed -n 1p)" = "$L/tablet-precision.sh" ]'
check "first start: PKG_DIR is the folder the .deb ships" '[ -f "$S/root$(echo "$OUT" | sed -n 2p)/tablet_profiles.py" ]'
check "first start: HOVER_UNIT is systemd's name for the packaged autostart file" \
    '[ "$(echo "$OUT" | sed -n 3p)" = "app-$(systemd-escape "$AUTO")@autostart.service" ]'
check "first start: ran, 3 shortcuts, the helper started" '[ "$(echo "$OUT" | sed -n 4p)" = True ] &&
    [ "$(grep -c "setShortcut asaiu" "$S/stub.log")" = 3 ] && grep -qF "systemctl --user start $(echo "$OUT" | sed -n 3p)" "$S/stub.log"'
check "first start: only the mark in the scratch HOME" '[ "$(find "$S/home" "$S/data" -type f | sed "s|$S/||")" = data/tablet-center/package-setup-done ]'
echo "failures: $fails"
[ "$fails" -eq 0 ]
