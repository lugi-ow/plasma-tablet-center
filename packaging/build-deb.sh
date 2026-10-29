#!/bin/bash
# packaging/build-deb.sh [VERSION] [OUT_DIR] - build plasma-tablet-center_VERSION_all.deb from the checkout this script sits in; prints its path.
# Works from any checkout (D, a worktree, the public repo); needs only dpkg-deb and gzip. The build tree lives in $TMPDIR.
set -e
umask 022
SRC=$(cd "$(dirname "$0")/.." && pwd)
# Default version: the last commit's UTC time, so it grows across the D and public repos, shallow clones and rebases.
VER=${1:-1.0.$(TZ=UTC git -C "$SRC" log -1 --date=format-local:%Y%m%d%H%M --format=%cd 2>/dev/null || echo 0)}
OUT=$(cd "${2:-.}" && pwd)
PKG=plasma-tablet-center
LIB=/usr/lib/$PKG
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/$PKG-deb.XXXXXX")
trap 'rm -rf -- "$ROOT"' EXIT
chmod 755 "$ROOT"
cd "$SRC"

# The scripts: one folder, found by each other through their own path (tablet-precision.sh's DIR,
# the Python files' __file__). The env shebang becomes /usr/bin/python3, Debian's interpreter path.
install -d "$ROOT$LIB"
for f in tablet-precision.sh tablet-precision-size.sh tablet-pen-pos.py tablet-overlay.py \
         tablet-size-preview.py tablet-pointer-warp.py tablet-hover.py tablet-pad-probe.py \
         tablet_center.py tablet_profiles.py; do
    sed '1s|^#!/usr/bin/env python3$|#!/usr/bin/python3|' "$f" > "$ROOT$LIB/$f"
    chmod 755 "$ROOT$LIB/$f"
done
install -m 644 tablet-overlay.qml "$ROOT$LIB/"

# The shortcut launchers (kglobalaccel runs a command component's Exec), the menu entry and the
# pad helper's autostart: install.sh's entries, system-wide. The autostart file keeps its name, so
# systemd's unit stays app-tablet\x2dhover@autostart.service.
A=$ROOT/usr/share/applications
install -d "$A" "$ROOT/etc/xdg/autostart"
launcher() {  # name, exec-args, description
    printf '%s\n' "[Desktop Entry]" "Exec=$LIB/$2" "Name=$3" "NoDisplay=true" "StartupNotify=false" \
        "Type=Application" "X-KDE-GlobalAccel-CommandShortcut=true" > "$A/net.local.$1.desktop"
}
launcher tabprec-toggle "tablet-precision.sh" "Precision mode toggle"
launcher tabprec-bigger "tablet-precision-size.sh up" "Precision area bigger"
launcher tabprec-smaller "tablet-precision-size.sh down" "Precision area smaller"
printf '%s\n' "[Desktop Entry]" "Type=Application" "Name=Custom Drawing Tablet Center" \
    "Comment=Tablet precision mode and express key settings" "Exec=$LIB/tablet_center.py" \
    "Icon=input-tablet" "Terminal=false" "Categories=Settings;HardwareSettings;" \
    "Keywords=tablet;pen;stylus;drawing;precision;express keys;ring;" > "$A/tablet-center.desktop"
printf '%s\n' "[Desktop Entry]" "Type=Application" "Name=Tablet hover preview" \
    "Comment=Ghost of the precision area while a finger rests on the precision key (idle until a supported tablet appears)" \
    "Exec=$LIB/tablet-hover.py" "NoDisplay=true" "OnlyShowIn=KDE;" > "$ROOT/etc/xdg/autostart/tablet-hover.desktop"

# install.sh's step A: the pen position, the express keys' touch sense, /dev/uinput for the pies.
install -d "$ROOT/usr/lib/udev/rules.d"
printf '%s\n' 'SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="*Pen*", TAG+="uaccess"' \
    'KERNEL=="hidraw*", KERNELS=="0003:056A:*|0005:056A:*", TAG+="uaccess"' \
    'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess"' > "$ROOT/usr/lib/udev/rules.d/70-tablet-access.rules"

# AppStream: the hardware the udev rule serves (pen tablets over USB and Bluetooth), so software centres can offer the package.
install -d "$ROOT/usr/share/metainfo"
cat > "$ROOT/usr/share/metainfo/io.github.lugi_ow.plasma_tablet_center.metainfo.xml" <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>io.github.lugi_ow.plasma_tablet_center</id>
  <metadata_license>CC0-1.0</metadata_license>
  <project_license>MIT</project_license>
  <name>Custom Drawing Tablet Center</name>
  <summary>Precision mode and pad settings for drawing tablets on Plasma</summary>
  <description>
    <p>Precision mode maps the pen to a small area of the screen around the pen, like the
    Windows driver. The pad keys show a preview of that area while a finger rests on them and
    can open pie menus under the pen. Tablet Center sets the pad keys, the touch ring, the pen
    buttons, the pressure curve and settings profiles. Plasma 6 on Wayland only.</p>
  </description>
  <developer id="io.github.lugi_ow"><name>Forest Dew</name></developer>
  <launchable type="desktop-id">tablet-center.desktop</launchable>
  <url type="homepage">https://github.com/lugi-ow/plasma-tablet-center</url>
  <content_rating type="oars-1.1"/>
  <provides>
    <modalias>usb:v056Ap*</modalias>
    <modalias>hid:b0005g*v0000056Ap*</modalias>
  </provides>
</component>
XML

D=$ROOT/usr/share/doc/$PKG
install -d "$D"
WHEN=$(git -C "$SRC" log -1 --format=%cD 2>/dev/null || date -R)
printf '%s\n' "$PKG ($VER) unstable; urgency=medium" "" \
    "  * Packaged build of $(git -C "$SRC" describe --always --dirty 2>/dev/null || echo 'a source tree')." "" \
    " -- Forest Dew <25386416+lugi-ow@users.noreply.github.com>  $WHEN" | gzip -9n > "$D/changelog.gz"
cat > "$D/copyright" <<'EOF'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: plasma-tablet-center
Source: https://github.com/lugi-ow/plasma-tablet-center

Files: *
Copyright: 2026 Forest Dew
License: Expat
 Permission is hereby granted, free of charge, to any person obtaining a copy
 of this software and associated documentation files (the "Software"), to deal
 in the Software without restriction, including without limitation the rights
 to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 copies of the Software, and to permit persons to whom the Software is
 furnished to do so, subject to the following conditions:
 .
 The above copyright notice and this permission notice shall be included in all
 copies or substantial portions of the Software.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 SOFTWARE.
EOF
chmod 644 "$D/copyright" "$D/changelog.gz" "$A"/*.desktop "$ROOT"/usr/share/metainfo/*.xml "$ROOT/etc/xdg/autostart/tablet-hover.desktop" \
    "$ROOT/usr/lib/udev/rules.d/70-tablet-access.rules"

# Control: udev reload + trigger after an install and a removal, so the rule acts without a reboot.
install -d "$ROOT/DEBIAN"
cat > "$ROOT/DEBIAN/control" <<EOF
Package: $PKG
Version: $VER
Architecture: all
Maintainer: Forest Dew <25386416+lugi-ow@users.noreply.github.com>
Installed-Size: $(du -sk --exclude=DEBIAN "$ROOT" | cut -f1)
Depends: python3, python3-pyqt6, python3-pyqt6.qtquick, qml6-module-org-kde-layershell, qml6-module-qtquick, qml6-module-qtquick-window, qml6-module-qtquick-shapes, libkf6config-bin
Conflicts: plasma-wacom-center
Replaces: plasma-wacom-center
Section: utils
Priority: optional
Homepage: https://github.com/lugi-ow/plasma-tablet-center
Description: drawing tablet precision mode and pad settings for KDE Plasma
 Precision mode maps the pen to a small area of the screen around the pen
 and draws its border. The pad keys show a preview of that area while a
 finger rests on them, and can open pie menus under the pen. Tablet Center
 is one settings window for the pad keys, the touch ring, the pen buttons,
 the pressure curve and settings profiles. Plasma 6 on Wayland only.
EOF
echo /etc/xdg/autostart/tablet-hover.desktop > "$ROOT/DEBIAN/conffiles"
for s in postinst postrm; do
    printf '%s\n' '#!/bin/sh' 'set -e' \
        'case "$1" in configure|remove|purge)' \
        '    if command -v udevadm >/dev/null 2>&1; then' \
        '        udevadm control --reload || true' \
        '        udevadm trigger --subsystem-match=input --subsystem-match=hidraw --subsystem-match=misc || true' \
        '    fi;;' 'esac' 'exit 0' > "$ROOT/DEBIAN/$s"
    chmod 755 "$ROOT/DEBIAN/$s"
done
sed -i '$d' "$ROOT/DEBIAN/postinst"                   # its `exit 0`: the helper restart comes before it
cat >> "$ROOT/DEBIAN/postinst" <<'EOF'
if [ "$1" = configure ]; then
    for d in /run/user/[0-9]*; do
        [ -S "$d/systemd/private" ] && u=$(stat -c %U "$d") || continue
        timeout 10 systemctl --user -M "$u@" daemon-reload >/dev/null 2>&1 || true
        timeout 10 systemctl --user -M "$u@" try-restart 'app-tablet\x2dhover@autostart.service' >/dev/null 2>&1 || true
    done
fi
exit 0
EOF
(cd "$ROOT" && find . -type f ! -path './DEBIAN/*' -printf '%P\0' | sort -z | xargs -0 md5sum > DEBIAN/md5sums)
chmod 644 "$ROOT/DEBIAN/control" "$ROOT/DEBIAN/conffiles" "$ROOT/DEBIAN/md5sums"
dpkg-deb --root-owner-group -Zxz --build "$ROOT" "$OUT/${PKG}_${VER}_all.deb" >/dev/null
echo "$OUT/${PKG}_${VER}_all.deb"
