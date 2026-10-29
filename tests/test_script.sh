#!/bin/bash
# Drives tablet-precision.sh through on / move / resize / suspend / resume /
# off / heal with stubbed busctl + notify-send, a fake pen reader and a fake
# overlay. The stub keeps outputArea per product, like KWin, so the pen
# ledger is tested across a bus switch and a logout. No KWin, no GUI.
# Usage: test_script.sh <scratch dir> <path to tablet-precision.sh>
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
S=$1/script-test
SRC=$2
rm -rf "$S"; mkdir -p "$S/bin" "$S/rd" "$S/conf"
cp "$SRC" "$S/bin/tablet-precision.sh"
cp "$HERE/fake/tablet-overlay.py" "$HERE/fake/tablet-pen-pos.py" "$S/bin/"
chmod +x "$S/bin/"* "$HERE/stubs/"*
export PATH="$HERE/stubs:$PATH" XDG_RUNTIME_DIR="$S/rd" XDG_CONFIG_HOME="$S/conf" XDG_STATE_HOME="$S/state"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$S/no-bus"   # kwriteconfig6 --notify writes and exits 0 on a dead bus: nothing reaches the live session
export STUB_LOG="$S/busctl.log" FAKE_LOG="$S/overlay.log" STUB_AREAS="$S/kwin"
printf 'SCALE=0.29\nDIM=0.10\nHOVER_MASK=0x80\n' > "$S/conf/tabprec.conf"
T="$S/bin/tablet-precision.sh"
RD="$S/rd/tabprec"
LEDGERS="$S/state/tabprec"
FAILS=0
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; FAILS=$((FAILS + 1)); fi; }
sets() { grep -c 'set-property' "$STUB_LOG"; }
SET='busctl --user set-property org.kde.KWin /org/kde/KWin/InputDevice/event5 org.kde.KWin.InputDevice outputArea (dddd)'

# 1: OFF -> ON. SCALE 0.29 of 2560 = 742 x 490 (aspect 224/148), pen at the centre -> 909 475.
"$T" toggle; sleep 0.4
check "1 base mapping saved"            '[ "$(cat "$RD/saved-area")" = "0 0 1 1" ]'
check "1 area file = px + fractions"     '[ "$(cat "$RD/area")" = "909 475 742 490 0.10 2560 1440 0.355078 0.329861 0.289844 0.340278" ]'
check "1 mapping set once"              '[ "$(sets)" -eq 1 ]'
check "1 outputArea fractions"          'grep -q "set-property.*outputArea (dddd) 0.355078 0.329861 0.289844 0.340278" "$STUB_LOG"'
check "1 overlay spawned with --fifo"   'grep -q "^SPAWN 909 475 742 490 0.10 --fifo $RD/overlay.fifo$" "$FAKE_LOG"'
check "1 fifo exists"                   '[ -p "$RD/overlay.fifo" ]'
check "1 pen sysname cached"            '[ "$(cat "$RD/pen")" = "event5" ]'
PID1=$(cat "$RD/overlay.pid")

# 2: fresh relocate marker + press -> MOVE (pen at 200 300 -> 142 198), still ON, overlay moved through the pipe.
touch "$RD/relocate"
FAKE_PEN="200 300 2560 1440" "$T" toggle; sleep 0.4
check "2 still on, base mapping kept"   '[ "$(cat "$RD/saved-area")" = "0 0 1 1" ]'
check "2 marker consumed"               '[ ! -f "$RD/relocate" ]'
check "2 mapping set again (2 calls)"   '[ "$(sets)" -eq 2 ]'
check "2 area file = 142 198 742 490 …" '[ "$(cat "$RD/area")" = "142 198 742 490 0.10 2560 1440 0.055469 0.137500 0.289844 0.340278" ]'
check "2 pipe line 142 198 742 490 0.10" 'grep -qx "142 198 742 490 0.10" "$FAKE_LOG"'
check "2 overlay NOT respawned"         '[ "$(cat "$RD/overlay.pid")" = "$PID1" ] && [ "$(grep -c SPAWN "$FAKE_LOG")" -eq 1 ]'

# 3: ring resize to SCALE 0.40 -> 1024 x 677 around the area's own centre (513, 443) -> 1 105, through
#    the pipe. The pen sits far away (2000 1000): a resize must not look at it.
sed -i 's/^SCALE=.*/SCALE=0.40/' "$S/conf/tabprec.conf"
FAKE_PEN="2000 1000 2560 1440" "$T" resize; sleep 0.4
check "3 resize keeps the centre: pipe line 1 105 1024 677 0.10" 'grep -qx "1 105 1024 677 0.10" "$FAKE_LOG"'
check "3 area file = 1 105 1024 677 …"  '[ "$(cat "$RD/area")" = "1 105 1024 677 0.10 2560 1440 0.000391 0.072917 0.400000 0.470139" ]'
check "3 resize did not respawn"        '[ "$(cat "$RD/overlay.pid")" = "$PID1" ] && [ "$(grep -c SPAWN "$FAKE_LOG")" -eq 1 ]'
check "3 mapping set (3 calls)"         '[ "$(sets)" -eq 3 ]'
check "3 resize ignores the pen: no line near 2000 1000" '! grep -q "^1[0-9][0-9][0-9] [0-9]* 1024 677" "$FAKE_LOG"'

# 3a: the same SCALE again (a ring tick queued behind the one that did the work): nothing happens.
"$T" resize; sleep 0.3
check "3a resize with the width already on screen: no mapping call, no pipe line" '[ "$(sets)" -eq 3 ] && [ "$(wc -l < "$FAKE_LOG")" -eq 3 ]'

# 3b: suspend -> the saved base mapping while still ON; resume -> the area's fractions again; overlay untouched.
"$T" suspend; sleep 0.3
check "3b suspend: base mapping 0 0 1 1, still on" '[ "$(tail -1 "$STUB_LOG")" = "$SET 0 0 1 1" ] && [ -f "$RD/saved-area" ]'
"$T" resume; sleep 0.3
check "3b resume: area re-mapped 0.000391 0.072917 0.400000 0.470139" '[ "$(tail -1 "$STUB_LOG")" = "$SET 0.000391 0.072917 0.400000 0.470139" ]'
check "3b overlay untouched (1 spawn, 3 log lines)" '[ "$(grep -c SPAWN "$FAKE_LOG")" -eq 1 ] && [ "$(wc -l < "$FAKE_LOG")" -eq 3 ]'
check "3b mapping set (5 calls)"        '[ "$(sets)" -eq 5 ]'

# 3c: an area file from before the fractions were recorded (7 fields): resume computes them.
echo "142 198 742 490 0.10 2560 1440" > "$RD/area"
"$T" resume; sleep 0.3
check "3c resume from a 7-field area file: 0.055469 0.137500 0.289844 0.340278" '[ "$(tail -1 "$STUB_LOG")" = "$SET 0.055469 0.137500 0.289844 0.340278" ] && [ "$(sets)" -eq 6 ]'

# 3d: grow to SCALE 0.80 from that area (centre 513 443): 2048 x 1353 cannot stay centred there - clamped to 0 0.
sed -i 's/^SCALE=.*/SCALE=0.80/' "$S/conf/tabprec.conf"
"$T" resize; sleep 0.4
check "3d resize clamped to the screen: 0 0 2048 1353" 'grep -qx "0 0 2048 1353 0.10" "$FAKE_LOG"'
check "3d area file rewritten with fractions" '[ "$(cat "$RD/area")" = "0 0 2048 1353 0.10 2560 1440 0.000000 0.000000 0.800000 0.939583" ] && [ "$(sets)" -eq 7 ]'

# U: UNU-03 - the screen changes while the mode is ON. KWin applies the FRACTIONS; the pixels (area file,
#    overlay, the next drag) must follow the live screen. State here: ON, area 0 0 2048 1353 on 2560x1440.
sed -i 's/^SCALE=.*/SCALE=0.50/' "$S/conf/tabprec.conf"
FAKE_PEN="1000 500 1920 1080" "$T" resize; sleep 0.4
check "U1 screen now 1920x1080, SCALE 0.50: area redone for it (288 191 960 634, screen fields 1920 1080)" \
    '[ "$(cut -d" " -f1-7 "$RD/area")" = "288 191 960 634 0.10 1920 1080" ]'
FAKE_PEN="1000 500 1600 900" "$T" resize; sleep 0.4
check "U2 screen now 1600x900, SCALE unchanged: the area file still follows (screen fields 1600 900, width 800)" \
    '[ "$(cut -d" " -f6-7 "$RD/area")" = "1600 900" ] && [ "$(cut -d" " -f3 "$RD/area")" = "800" ]'
check "U2 ... and the overlay got the new pixels through the pipe" 'tail -1 "$FAKE_LOG" | grep -q " 800 [0-9]* 0.10$"'
"$T" suspend; sleep 0.3
FAKE_PEN="1000 500 1920 1080" "$T" resume; sleep 0.4
check "U3 resume after the screen grew to 1920x1080: the area file follows (1920 1080, width 960)" \
    '[ "$(cut -d" " -f6-7 "$RD/area")" = "1920 1080" ] && [ "$(cut -d" " -f3 "$RD/area")" = "960" ]'
s4=$(sets); FAKE_PEN="1000 500 1920 1080" "$T" resize; sleep 0.3
check "U4 screen unchanged and SCALE matches: a resize does nothing (no new mapping call)" '[ "$(sets)" -eq "$s4" ]'
sed -i 's/^SCALE=.*/SCALE = 0.60   # spaced and commented (GCP)/' "$S/conf/tabprec.conf"
FAKE_PEN="1000 500 1920 1080" "$T" resize; sleep 0.3
check "U5 the toggle itself reads 'SCALE = 0.60 # note': width 1152 on 1920 (the old shell sourced it and read the default)" \
    '[ "$(cut -d" " -f3 "$RD/area")" = "1152" ]'
sed -i 's/^SCALE.*/SCALE=0.80/' "$S/conf/tabprec.conf"
FAKE_PEN="1000 500 2560 1440" "$T" resize; sleep 0.3          # a state the next section can start from
U_EXTRA=$(( $(sets) - 7 ))                                      # mapping calls section U added

# 4: STALE marker (10 s old) + press -> OFF, not move.
touch -d '10 seconds ago' "$RD/relocate"
"$T" toggle; sleep 0.4
check "4 off: state, area, marker gone" '[ ! -f "$RD/saved-area" ] && [ ! -f "$RD/area" ] && [ ! -f "$RD/relocate" ]'
check "4 base mapping restored"         '[ "$(tail -1 "$STUB_LOG")" = "$SET 0 0 1 1" ] && [ "$(sets)" -eq $((8 + U_EXTRA)) ]'
"$T" suspend; "$T" resume; "$T" resize; sleep 0.3
check "4 suspend/resume/resize are no-ops when off" '[ "$(sets)" -eq $((8 + U_EXTRA)) ]'
check "4 overlay killed"                '[ ! -d "/proc/$PID1" ]'
check "4 pid file removed"              '[ ! -f "$RD/overlay.pid" ]'

# 5: ON again (a second overlay, reusing the pipe), then OFF with no marker.
"$T" toggle; sleep 0.4
PID2=$(cat "$RD/overlay.pid")
check "5 on: new overlay alive"         '[ "$PID2" != "$PID1" ] && [ -d "/proc/$PID2" ] && [ "$(grep -c SPAWN "$FAKE_LOG")" -eq 2 ]'
"$T" toggle; sleep 0.4
check "5 off again"                     '[ ! -f "$RD/saved-area" ] && [ ! -d "/proc/$PID2" ]'

# 6: runs are serialized: a toggle waits for the lock another run holds; the overlay it spawns
#    must not inherit the lock (or the OFF toggle would wait 5 s and give up).
( flock 9; sleep 1.2 ) 9>"$RD/lock" &
sleep 0.2; t0=$(date +%s%N); "$T" toggle; t1=$(date +%s%N); wait
check "6 toggle waited for the lock (>= 0.8 s)" '[ $(( (t1 - t0) / 1000000 )) -ge 800 ] && [ -f "$RD/saved-area" ]'
PID3=$(cat "$RD/overlay.pid")
t0=$(date +%s%N); "$T" toggle; t1=$(date +%s%N); sleep 0.4
check "6 off again in under 2 s: the overlay did not hold the lock" '[ $(( (t1 - t0) / 1000000 )) -lt 2000 ] && [ ! -f "$RD/saved-area" ] && [ ! -d "/proc/$PID3" ]'

# The invariant from here on: after every run that changes the mapping, kcminputrc names the mapping
# the pen must have with precision mode OFF. KWin loads that file whenever a device appears, and the
# stub does too, so no rectangle the toggle sets can outlive an unplug, a bus switch or a logout.
# `kc <product>` reads the file. `appear <product>` makes that pen turn up fresh, which is what an
# unplug, a cable swap or a logout does - the stub then seeds it from kcminputrc, exactly like KWin.
kc()     { kreadconfig6 --file kcminputrc --group Libinput --group 1386 --group "${1:-864}" \
                        --group "Wacom Intuos Pro M Pen" --key OutputArea 2>/dev/null; }
appear() { rm -f "$STUB_AREAS/area-${1:-864}"
           STUB_PRODUCT="${1:-864}" busctl --user get-property a b c outputArea >/dev/null; }

# 7: the 2026-09-10 bug. ON over Bluetooth (product 864), the cable goes in while ON (855), OFF on the
#    USB pen, Bluetooth back. This design PREVENTS it instead of repairing it afterwards.
"$T" toggle; sleep 0.4
R=$(cut -d' ' -f8-11 "$RD/area")
check "7 on: the pen takes the rectangle, kcminputrc still names the whole screen" \
    '[ "$(cat "$STUB_AREAS/area-864")" = "$R" ] && [ -z "$(kc 864)" ]'
check "7 on: no ledger, because the write-back was proved" '[ ! -f "$LEDGERS/1386-864" ]'
appear 855
check "7 the cable goes in while ON: the USB pen turns up on the whole screen" \
    '[ "$(cat "$STUB_AREAS/area-855")" = "0 0 1 1" ]'
STUB_PRODUCT=855 "$T" toggle; sleep 0.4
check "7 off on the USB pen: precision mode ends" '[ ! -f "$RD/saved-area" ]'
appear 864
check "7 Bluetooth back: on the whole screen, with no heal and no ledger" \
    '[ "$(cat "$STUB_AREAS/area-864")" = "0 0 1 1" ] && [ ! -f "$LEDGERS/1386-864" ]'

# 8: the next toggle ON after such a bus switch saves the whole screen as the base, never a rectangle.
"$T" toggle; sleep 0.4
STUB_PRODUCT=855 "$T" toggle; sleep 0.4
appear 864
"$T" toggle 2>/dev/null; sleep 0.4
check "8 toggle ON after the bus switch saves 0 0 1 1 as the base" '[ "$(cat "$RD/saved-area")" = "0 0 1 1" ]'
"$T" toggle; sleep 0.4
check "8 off: 864 on the whole screen, kcminputrc names it (absent or 0,0,1,1: the same to KWin)" \
    '[ "$(cat "$STUB_AREAS/area-864")" = "0 0 1 1" ] && { [ -z "$(kc 864)" ] || [ "$(kc 864)" = "0,0,1,1" ]; }'

# 9: a logout while ON. $RD dies with the session and KWin restarts, so both pens turn up fresh and
#    read kcminputrc - which names the whole screen. Nothing has to repair anything.
"$T" toggle; sleep 0.4
kill "$(cat "$RD/overlay.pid")"; sleep 0.1; rm -rf "$RD"
appear 864
check "9 a logout while ON: the pen comes back on the whole screen" \
    '[ "$(cat "$STUB_AREAS/area-864")" = "0 0 1 1" ] && [ ! -f "$LEDGERS/1386-864" ]'

# 10: a base that is NOT the whole screen (a tablet area set in System Settings). The write-back must
#     put THAT back, not the whole screen, or precision mode would silently widen the user's own area.
kwriteconfig6 --file kcminputrc --group Libinput --group 1386 --group 864 \
              --group "Wacom Intuos Pro M Pen" --key OutputArea "0,0,0.5,1"
appear 864
"$T" toggle; sleep 0.4
check "10 a custom base: kcminputrc keeps it while the mode is on" '[ "$(kc 864)" = "0,0,0.5,1" ]'
appear 864
check "10 the pen comes back on the custom base, not the whole screen" \
    '[ "$(cat "$STUB_AREAS/area-864")" = "0 0 0.5 1" ]'
"$T" toggle 2>/dev/null; sleep 0.4           # off again: test 11 starts with the mode OFF
kwriteconfig6 --file kcminputrc --group Libinput --group 1386 --group 864 \
              --group "Wacom Intuos Pro M Pen" --key OutputArea --delete
appear 864

# 11: the write-back cannot be proved (no kwriteconfig6 on this machine). The old repair path is the
#     fallback: a ledger is written, and heal puts the pen right when it comes back.
#     kcminputrc must genuinely disagree with the base, or there is nothing for the write-back to fix.
kwriteconfig6 --file kcminputrc --group Libinput --group 1386 --group 864 \
              --group "Wacom Intuos Pro M Pen" --key OutputArea "0.5,0.5,0.2,0.2"
echo "0 0 1 1" > "$STUB_AREAS/area-864"     # KWin's memory says the whole screen, the file says a rectangle
NOKW="$S/nokw"; mkdir -p "$NOKW"
printf '#!/bin/bash\nexit 127\n' > "$NOKW/kwriteconfig6"; chmod +x "$NOKW/kwriteconfig6"
PATH="$NOKW:$PATH" "$T" toggle 2>/dev/null; sleep 0.4
R=$(cut -d' ' -f8-11 "$RD/area")
check "11 the write-back cannot be proved: a ledger is written instead" \
    '[ "$(cat "$LEDGERS/1386-864")" = "0 0 1 1 $R" ]'
kill "$(cat "$RD/overlay.pid")" 2>/dev/null; sleep 0.1; rm -rf "$RD"
"$T" heal 2>/dev/null
check "11 heal repairs from the ledger and spends it" \
    '[ "$(cat "$STUB_AREAS/area-864")" = "0 0 1 1" ] && [ ! -f "$LEDGERS/1386-864" ]'
kwriteconfig6 --file kcminputrc --group Libinput --group 1386 --group 864 \
              --group "Wacom Intuos Pro M Pen" --key OutputArea --delete

# 12: heal while ON re-maps a pen that has just turned up, so the mode survives a cable swap.
"$T" toggle; sleep 0.4
R=$(cut -d' ' -f8-11 "$RD/area")
appear 855
STUB_PRODUCT=855 "$T" heal 2>/dev/null
check "12 heal while ON: the pen that turned up takes the area on record" '[ "$(cat "$STUB_AREAS/area-855")" = "$R" ]'
check "12 heal while ON leaves no ledger" '[ ! -f "$LEDGERS/1386-855" ]'
STUB_PRODUCT=855 "$T" toggle; sleep 0.4

# 13: heal with no pen listed (the tablet left again): 2 s of retries, then exit 1 without a notice.
t0=$(date +%s%N); STUB_NO_PEN=1 "$T" heal; rc=$?; t1=$(date +%s%N)
check "13 heal without a pen: exit 1 after >= 1.8 s of retries" '[ "$rc" -eq 1 ] && [ $(( (t1 - t0) / 1000000 )) -ge 1800 ]'

# 13b: those retries hold no lock (HLA-01). The daemon starts heal when a tablet appears. A key press or the
#      ghost's `where` in the next 2 s used to wait behind it, with the daemon's input loop frozen meanwhile.
STUB_NO_PEN=1 "$T" heal 2>/dev/null & hpid=$!
sleep 0.3
t0=$(date +%s%N); out=$("$T" where); t1=$(date +%s%N)
wait "$hpid"; rc=$?
check "13b a run during heal's retries does not wait for them (< 1 s)" '[ $(( (t1 - t0) / 1000000 )) -lt 1000 ] && [ "$(echo "$out" | wc -w)" -eq 7 ]'
check "13b heal still gives up after its retries (exit 1)" '[ "$rc" -eq 1 ]'
check "no notifications (no errors)"    '! grep -q notify-send "$STUB_LOG"'

# 14: the tablet is switched off while precision mode is on (the 2026-09-12 hardware test). OFF must still
#     end the mode and close the overlay: the keyboard shortcut and Tablet Center's button are then the
#     only ways left, and kcminputrc already names the base, so there is nothing live to restore.
appear 864                                  # test 12 ended OFF on the USB pen: the Bluetooth pen comes back fresh, on the base
"$T" toggle; sleep 0.4
opid=$(cat "$RD/overlay.pid")
n=$(grep -c notify-send "$STUB_LOG")
STUB_NO_PEN=1 "$T" toggle; rc=$?; sleep 0.4
check "14 OFF with the tablet switched off: the mode ends" '[ "$rc" -eq 0 ] && [ ! -f "$RD/saved-area" ] && [ ! -f "$RD/area" ]'
check "14 OFF with the tablet switched off: the overlay closes" '! kill -0 "$opid" 2>/dev/null && [ ! -f "$RD/overlay.pid" ]'
check "14 OFF with the tablet switched off: no error notice" '[ "$(grep -c notify-send "$STUB_LOG")" -eq "$n" ]'
STUB_NO_PEN=1 "$T" toggle 2>/dev/null; sleep 0.2
check "14 ON with the tablet switched off still refuses, with one notice" '[ ! -f "$RD/saved-area" ] && [ "$(grep -c notify-send "$STUB_LOG")" -eq $((n + 1)) ]'

# 15: the tablet gone with the mode on: the daemon pauses it after its grace. The overlay closes without a pen, and the
#     tablet back within RECONNECT seconds resumes the SAME area - a new placement would use a pen that still reads
#     0,0 after the reconnect and land in the corner. Later than RECONNECT, or RECONNECT=0, the pause is dropped.
appear 864                                  # test 14 left the tablet switched off: it comes back on the base
"$T" toggle; sleep 0.4
A=$(cut -d' ' -f1-4 "$RD/area"); F=$(cut -d' ' -f8-11 "$RD/area"); opid=$(cat "$RD/overlay.pid")
STUB_NO_PEN=1 "$T" pause "$(date +%s)"; sleep 0.4
check "15 pause without a pen: the mode ends, the overlay closes" '[ ! -f "$RD/saved-area" ] && [ ! -f "$RD/area" ] && ! kill -0 "$opid" 2>/dev/null'
check "15 pause keeps the base and the area for a reconnect" '[ -f "$RD/paused" ] && [ "$(sed -n 2p "$RD/paused")" = "0 0 1 1" ] && [ "$(sed -n 3p "$RD/paused" | cut -d" " -f1-4)" = "$A" ]'
appear 864
FAKE_PEN="2500 1400 2560 1440" "$T" heal 2>/dev/null; sleep 0.4
check "15 the tablet back in time: resumed at the SAME area, not at the pen" '[ -f "$RD/saved-area" ] && [ "$(cut -d" " -f1-4 "$RD/area")" = "$A" ] && [ "$(cut -d" " -f8-11 "$RD/area")" = "$F" ]'
check "15 resumed: the overlay is back and the pause record is spent" '[ -f "$RD/overlay.pid" ] && kill -0 "$(cat "$RD/overlay.pid")" 2>/dev/null && [ ! -f "$RD/paused" ]'
check "15 resumed: kcminputrc still names the whole screen" '[ -z "$(kc 864)" ]'
STUB_NO_PEN=1 "$T" pause "$(( $(date +%s) - 120 ))"; sleep 0.4
appear 864
"$T" heal 2>/dev/null; sleep 0.4
check "15 the tablet back too late: the pause is dropped, the mode stays off" '[ ! -f "$RD/saved-area" ] && [ ! -f "$RD/paused" ]'
"$T" toggle; sleep 0.4
STUB_NO_PEN=1 "$T" pause "$(date +%s)"; sleep 0.4
appear 864
FAKE_PEN="2500 1400 2560 1440" "$T" toggle 2>/dev/null; sleep 0.4
check "15 the key pressed after a reconnect resumes the pause, not a corner area" '[ -f "$RD/saved-area" ] && [ "$(cut -d" " -f1-4 "$RD/area")" = "$A" ]'
"$T" toggle; sleep 0.4
echo 'RECONNECT=0' >> "$S/conf/tabprec.conf"
"$T" toggle; sleep 0.4
STUB_NO_PEN=1 "$T" pause "$(date +%s)"; sleep 0.4
appear 864
"$T" heal 2>/dev/null; sleep 0.4
check "15 RECONNECT=0: a pause never resumes" '[ ! -f "$RD/saved-area" ] && [ ! -f "$RD/paused" ]'
sed -i '/^RECONNECT=/d' "$S/conf/tabprec.conf"

# 16: a DIM-only change (same width) must still apply the area again - the no-op guard used to
#     compare width alone and skip a change that only touched dim strength.
"$T" toggle; sleep 0.4
n_before_dim=$(wc -l < "$FAKE_LOG"); sets_before_dim=$(sets)
sed -i 's/^DIM=.*/DIM=0.33/' "$S/conf/tabprec.conf"
"$T" resize; sleep 0.3
check "16 DIM-only change still applies the area again" \
    '[ "$(sets)" -gt "'"$sets_before_dim"'" ] && [ "$(wc -l < "$FAKE_LOG")" -gt "'"$n_before_dim"'" ] && tail -1 "$FAKE_LOG" | grep -q " 0.33$"'
"$T" toggle; sleep 0.4

# 17: the soft cap on SCALE (conf SCALE_MIN / SCALE_MAX, defaults 0.05 / 0.80; 0 = no cap on that side).
#     `where` prints the rectangle and changes nothing. 0.95 of 2560 = 2432 wide would be 1607 high:
#     the screen height ends it at 2179 x 1440. A SCALE near 0 with no floor is one pixel, never empty.
sed -i 's/^SCALE=.*/SCALE=0.95/' "$S/conf/tabprec.conf"
check "17 no cap keys: 0.95 stops at the default 0.80 -> 2048 x 1353" '[ "$("$T" where | cut -d" " -f3,4)" = "2048 1353" ]'
echo 'SCALE_MAX=0' >> "$S/conf/tabprec.conf"
check "17 SCALE_MAX=0: no cap, the screen height ends it -> 2179 x 1440" '[ "$("$T" where | cut -d" " -f3,4)" = "2179 1440" ]'
sed -i 's/^SCALE=.*/SCALE=0.0001/' "$S/conf/tabprec.conf"; echo 'SCALE_MIN=0' >> "$S/conf/tabprec.conf"
check "17 SCALE_MIN=0: no floor, one pixel is the least -> 1 x 1" '[ "$("$T" where | cut -d" " -f3,4)" = "1 1" ]'

echo "failures: $FAILS"
[ "$FAILS" -eq 0 ]
