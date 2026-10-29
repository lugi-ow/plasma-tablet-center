#!/bin/bash
# The pen driver's grab window: a helper that is taking the pen holds pen-driver.lock, and its socket may come a moment
# later. Drives tablet-precision.sh with PEN_DRIVER=1, a stubbed busctl and a fake helper (the lock at once, the socket
# after a delay). The toggle waits for the socket (bounded) only while the lock is held: no helper = no wait (D2).
# Usage: test_pen_wait.sh <scratch dir> <path to tablet-precision.sh>
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
S=$1/pen-wait-test
SRC=$2
rm -rf "$S"; mkdir -p "$S/bin" "$S/rd/tabprec" "$S/conf"
cp "$SRC" "$S/bin/tablet-precision.sh"
cp "$HERE/fake/tablet-overlay.py" "$HERE/fake/tablet-pen-pos.py" "$S/bin/"
chmod +x "$S/bin/"* "$HERE/stubs/"*
export PATH="$HERE/stubs:$PATH" XDG_RUNTIME_DIR="$S/rd" XDG_CONFIG_HOME="$S/conf" XDG_STATE_HOME="$S/state"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$S/no-bus"
unset DISPLAY WAYLAND_DISPLAY PEN_DRIVER DRIVER_WAIT
export STUB_LOG="$S/busctl.log" FAKE_LOG="$S/overlay.log" STUB_AREAS="$S/kwin"
T="$S/bin/tablet-precision.sh"
RD="$S/rd/tabprec"
SOCK="$RD/pen-driver.sock"
FAILS=0
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; FAILS=$((FAILS + 1)); fi; }
areas() { grep -c 'set-property.*outputArea' "$STUB_LOG"; }   # KWin outputArea writes so far
now_ms() { local t=${EPOCHREALTIME/[.,]/}; echo $(( t / 1000 )); }
conf() { printf 'SCALE=0.29\nDIM=0.10\nHOVER_MASK=0x80\n%s' "$1" > "$S/conf/tabprec.conf"; }
# a fake driver: it holds the helper's lock at once, after $1 s it binds the socket, writes every datagram it gets to $2 for up to $3 s
driver() { python3 -c '
import fcntl, os, socket, sys, time
lock = open(sys.argv[3].replace(".sock", ".lock"), "w"); fcntl.flock(lock, fcntl.LOCK_EX)
time.sleep(float(sys.argv[1]))
s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM); s.bind(sys.argv[3]); s.settimeout(float(sys.argv[4]))
try:
    while True:
        open(sys.argv[2], "a").write(s.recv(200).decode() + "\n")
except socket.timeout:
    pass
os.unlink(sys.argv[3])' "$1" "$2" "$SOCK" "$3" 2>/dev/null & }

python3 -c 'import socket,sys; socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM).bind(sys.argv[1])' "$S/probe.sock" 2>/dev/null || { echo "SKIP (this sandbox cannot create a unix socket; the VM proof covers it)"; echo "failures: 0"; exit 0; }
rm -f "$S/probe.sock"

# 1: PEN_DRIVER=1, the socket appears 1.5 s after the press. ON then OFF both reach the driver; KWin is never written.
conf $'PEN_DRIVER=1\n'; : > "$STUB_LOG"
: > "$S/got1"; driver 1.5 "$S/got1" 2.5
t0=$(now_ms); "$T" toggle 2>"$S/err1"; t1=$(now_ms); sleep 0.4
check "1 ON inside the re-grab window: the driver gets a rectangle"   '[ "$(wc -l < "$S/got1" 2>/dev/null || echo 0)" -ge 1 ] && head -1 "$S/got1" | grep -Eq "^0\.[0-9]+ 0\.[0-9]+ 0\.[0-9]+ 0\.[0-9]+$"'
check "1 KWin outputArea writes for the original: 0"                  '[ "$(areas)" -eq 0 ]'
check "1 the press waited for the socket (1.2-2.9 s)"                 '[ $((t1 - t0)) -ge 1200 ] && [ $((t1 - t0)) -le 2900 ]'
check "1 no 'waited' warning when the socket came in time"            '! grep -q "pen driver" "$S/err1"'
"$T" toggle; sleep 0.4
check "1 OFF: the driver gets full"                                   'tail -1 "$S/got1" | grep -qx full'
check "1 OFF: KWin still never written"                               '[ "$(areas)" -eq 0 ] && [ ! -f "$RD/saved-area" ]'
wait; rm -f "$SOCK"

# 2: PEN_DRIVER=1, a helper takes the pen (lock held) but its socket never comes: past the bound (DRIVER_WAIT=1) the
# KWin path runs, plus one stderr line. Then no helper at all (D2): no wait, even with DRIVER_WAIT=3.
conf $'PEN_DRIVER=1\nDRIVER_WAIT=3\n'; : > "$STUB_LOG"
t0=$(now_ms); "$T" toggle 2>"$S/err2b"; t1=$(now_ms); sleep 0.3
check "2 no helper (no lock held): ON is not delayed (under 1 s), KWin path, no warning" '[ $((t1 - t0)) -lt 1000 ] && [ "$(areas)" -eq 1 ] && ! grep -q "pen driver" "$S/err2b"'
"$T" toggle 2>/dev/null; sleep 0.3
conf $'PEN_DRIVER=1\nDRIVER_WAIT=1\n'; : > "$STUB_LOG"
python3 -c 'import fcntl, sys, time; f = open(sys.argv[1], "w"); fcntl.flock(f, fcntl.LOCK_EX); time.sleep(2.5)' "$RD/pen-driver.lock" &
sleep 0.2; t0=$(now_ms); "$T" toggle 2>"$S/err2"; t1=$(now_ms); sleep 0.4
check "2 past the bound: KWin written once (today's path)"            '[ "$(areas)" -eq 1 ]'
check "2 the wait was about 1 s (0.9-2.2 s)"                          '[ $((t1 - t0)) -ge 900 ] && [ $((t1 - t0)) -le 2200 ]'
check "2 one journal line names the driver"                           '[ "$(grep -c "pen driver" "$S/err2")" -eq 1 ]'
"$T" toggle; sleep 0.4
check "2 OFF through KWin: base restored (2 writes), state gone"      '[ "$(areas)" -eq 2 ] && [ ! -f "$RD/saved-area" ]'
wait

# 3: the tablet is gone (no pen) and PEN_DRIVER=1: OFF must not wait for a driver that has nothing to grab.
conf $'PEN_DRIVER=1\nDRIVER_WAIT=2\n'; : > "$STUB_LOG"
"$T" toggle 2>/dev/null; sleep 0.4
t0=$(now_ms); STUB_NO_PEN=1 "$T" toggle 2>/dev/null; t1=$(now_ms)
check "3 OFF with the tablet gone: no wait (under 1.5 s)"             '[ $((t1 - t0)) -lt 1500 ] && [ ! -f "$RD/saved-area" ]'

# 4: PEN_DRIVER off: no socket, no wait in the path, today's code (KWin written).
conf $'DRIVER_WAIT=3\n'; : > "$STUB_LOG"
t0=$(now_ms); "$T" toggle 2>"$S/err4"; t1=$(now_ms); sleep 0.4
check "4 switch off: ON is not delayed (under 1 s)"                   '[ $((t1 - t0)) -lt 1000 ]'
check "4 switch off: KWin written once, no driver line"               '[ "$(areas)" -eq 1 ] && ! grep -q "pen driver" "$S/err4"'
t0=$(now_ms); "$T" toggle 2>/dev/null; t1=$(now_ms)
check "4 switch off: OFF is not delayed either"                       '[ $((t1 - t0)) -lt 1000 ] && [ "$(areas)" -eq 2 ]'

# 5: DRIVER_WAIT=08 is eight seconds, not an octal error (bound shortened to prove it: 08 must parse, 0 must not wait or warn)
conf $'PEN_DRIVER=1\nDRIVER_WAIT=0\n'; : > "$STUB_LOG"
t0=$(now_ms); "$T" toggle 2>"$S/err5"; t1=$(now_ms); sleep 0.3
check "5 DRIVER_WAIT=0: no wait, no warning, KWin path"               '[ $((t1 - t0)) -lt 1000 ] && [ "$(areas)" -eq 1 ] && ! grep -q "pen driver" "$S/err5"'
"$T" toggle 2>/dev/null; sleep 0.3
conf $'PEN_DRIVER=1\nDRIVER_WAIT=08\n'; : > "$STUB_LOG"; : > "$S/got5"; driver 1.0 "$S/got5" 2
"$T" toggle 2>"$S/err5b"; sleep 0.3
check "5 DRIVER_WAIT=08 parses (no shell error) and the driver gets the rectangle" '! grep -qi "value too great\|syntax" "$S/err5b" && [ "$(areas)" -eq 0 ] && [ -s "$S/got5" ]'
"$T" toggle 2>/dev/null; wait; rm -f "$SOCK"

# 6: a drag while ON (the daemon brackets it with suspend / resume). Old code: suspend wrote KWin's area for the
# original pen and the driver kept the cursor in the old area while the overlay roamed. Now the driver gets full.
conf $'PEN_DRIVER=1\n'; : > "$STUB_LOG"; : > "$S/got6"; driver 0 "$S/got6" 3
sleep 0.3; "$T" toggle 2>/dev/null; sleep 0.2
"$T" suspend 2>/dev/null; sleep 0.2
check "6 suspend while ON: the driver gets full"                      '[ "$(sed -n 2p "$S/got6")" = full ]'
"$T" resume 2>/dev/null; sleep 0.2
check "6 resume: the driver gets the same area again"                 '[ -n "$(sed -n 3p "$S/got6")" ] && [ "$(sed -n 3p "$S/got6")" = "$(head -1 "$S/got6")" ]'
check "6 the drag never wrote KWin"                                   '[ "$(areas)" -eq 0 ]'
"$T" toggle 2>/dev/null; wait; rm -f "$SOCK"

# 7: R2-1, a rectangle stranded on the grabbed original. ON while no helper lives maps the ORIGINAL pen through KWin; the
# helper then grabs and runs its post-grab hook (`grabbed`); OFF goes to the helper; the helper stops (the rescue line).
# Old code: the original keeps the rectangle (mode OFF, pen confined), and the next ON saves it as the base.
conf $'PEN_DRIVER=1\nDRIVER_WAIT=0\n'; : > "$STUB_LOG"; : > "$S/got7"
rm -f "$S/kwin/area-864"                                       # a fresh tablet: case 3's OFF with the pen gone left the stub's
kwriteconfig6 --file kcminputrc --group Libinput --group 1386 --group 864 --group "Wacom Intuos Pro M Pen" --key OutputArea --delete 2>/dev/null   # original on its rectangle
"$T" toggle 2>/dev/null; sleep 0.3                             # ON, no helper: the KWin path writes the original
driver 0 "$S/got7" 1.5; sleep 0.3                              # the helper grabs: its socket is up...
"$T" grabbed 2>/dev/null                                       # ...and it runs its hook (old code: no such verb, exit 2)
"$T" toggle 2>/dev/null; wait                                  # OFF to the helper; the helper ends and unlinks
check "7 OFF reached the helper"                               '[ "$(tail -1 "$S/got7")" = full ]'
check "7 helper stopped: the original pen is on the base"      '[ "$(cat "$S/kwin/area-864" 2>/dev/null)" = "0 0 1 1" ]'
"$T" toggle 2>/dev/null                                        # the next ON, helper gone: KWin path, base read from the original
check "7 the next ON saves the true base, kcminputrc no rectangle" '[ "$(cat "$RD/saved-area")" = "0 0 1 1" ] && case "$(kreadconfig6 --file kcminputrc --group Libinput --group 1386 --group 864 --group "Wacom Intuos Pro M Pen" --key OutputArea 2>/dev/null)" in ""|0,0,1,1) true;; *) false;; esac'
"$T" toggle 2>/dev/null

echo "failures: $FAILS"
[ "$FAILS" -eq 0 ]
