#!/bin/bash
# tablet-precision.sh [toggle|resize|where|suspend|resume|heal|pause] - precision mode on Plasma 6 Wayland; the only writer of the pen mapping.
# Part of plasma-tablet-center (MIT). Structure and contracts: PROJECT_MAP.md; mechanisms: TECHNICAL.md.

# ── chunk: config-and-paths
export LC_ALL=C.UTF-8    # decimal point regardless of locale; UTF-8 keeps Qt quiet
CONF="${XDG_CONFIG_HOME:-$HOME/.config}/tabprec.conf"
MODE=${1:-toggle}
KW=org.kde.KWin
MGR=/org/kde/KWin/InputDevice
IF=org.kde.KWin.InputDevice
SCR=org.kde.kwin.Scripting
RD="${XDG_RUNTIME_DIR:-/tmp}/tabprec"
mkdir -p "$RD"
DIR=$(cd "$(dirname "$0")" && pwd)
STATE="$RD/saved-area"
FIFO="$RD/overlay.fifo"    # the live overlay reads "X Y W H DIM" lines from it
AREA="$RD/area"            # "X Y W H DIM SW SH FX FY FW FH" of the current mapping (tablet-hover.py relocates from it)
MARK="$RD/relocate"        # fresh while tablet-hover.py drags the overlay: the next press moves instead of switching off
PEN_CACHE="$RD/pen"        # the pen's KWin sysname from the last walk over the device list: one D-Bus call confirms it
PAUSED="$RD/paused"        # a pause: when the tablet went, the base, the area line (resumed within RECONNECT s)
LEDGERS="${XDG_STATE_HOME:-$HOME/.local/state}/tabprec"   # one ledger per pen, "base, last rectangle": KWin keeps a rectangle across unplugs and logouts, $RD does not survive them
pen=""
LEDGER=""
GV=""; GP=""; GN=""; GTRIED=""   # the pen's kcminputrc group path (maker, model, name), filled once by pen_group
PEN_OPTIONAL=""                  # set for OFF: the tablet may be gone, and OFF must still end the mode
TRIES=0

# ── chunk: note
note() {  # silent by design: the overlay is the feedback, only errors notify
    notify-send -a Tablet -i input-tablet -t 1800 "Precision mode" "$1" 2>/dev/null
}
# ── chunk: kload
kload() {  # busctl like every other D-Bus call here: qdbus6 is a Qt tool a box may lack, and this is the mouse fallback's only road
          busctl --user call $KW /Scripting $SCR unloadScript s "$2" >/dev/null 2>&1
          busctl --user call $KW /Scripting $SCR loadScript ss "$1" "$2" >/dev/null &&
          busctl --user call $KW /Scripting $SCR start >/dev/null; }
# ── chunk: kunload
kunload() { busctl --user call $KW /Scripting $SCR unloadScript s "$1" >/dev/null 2>&1; }

# ── chunk: find_pen
find_pen() {  # the pen's KWin sysname into $pen: the cached one when KWin still calls it a tablet tool (one D-Bus call), else a walk over every device; exits 1 when there is none (heal: after 2 s of retries, silently)
    [ -n "$pen" ] && return 0
    pen=$(cat "$PEN_CACHE" 2>/dev/null)
    if [ -n "$pen" ]; then
        case "$(busctl --user get-property $KW $MGR/$pen $IF tabletTool 2>/dev/null)" in *true*) return 0;; esac
        pen=""
    fi
    for n in $(busctl --user get-property $KW $MGR $KW.InputDeviceManager devicesSysNames 2>/dev/null \
               | tr -d '"' | cut -d' ' -f3-); do
        tool=$(busctl --user get-property $KW $MGR/$n $IF tabletTool 2>/dev/null)
        case "$tool" in *true*) pen=$n; break;; esac
    done
    if [ -z "$pen" ]; then
        if [ "$MODE" = heal ] && [ "$TRIES" -lt 8 ]; then   # the daemon saw the tablet's node: KWin may list the pen a moment later
            TRIES=$((TRIES + 1)); sleep 0.25; find_pen; return
        fi
        [ -n "$PEN_OPTIONAL" ] && return 1   # OFF with the tablet switched off: the caller ends the mode without it
        [ "$MODE" = heal ] || note "No tablet pen found (tablet asleep?)"
        exit 1
    fi
    echo "$pen" > "$PEN_CACHE"
}

# ── chunk: area_math
area_math() {  # W H X Y (px) and FX FY FW FH (fractions) for SCALE and the tablet's aspect. $1 = "pen": placed around the pen at $2 $3 so the cursor stays put; "centre": centred on $2 $3, clamped to the screen. $4 $5 = the screen size.
              # Assumes the base mapping is the full-tablet stretch: a letterboxed mapping needs the inputArea transform added.
    find_pen
    CX=$2 CY=$3 SW=$4 SH=$5
    TABSIZE=$(busctl --user get-property $KW $MGR/$pen $IF size 2>/dev/null | cut -d' ' -f2-)
    read -r W H X Y FX FY FW FH <<EOF
$(awk -v mode="$1" -v s="$SCALE" -v lo="$SCALE_MIN" -v hi="$SCALE_MAX" -v cx="$CX" -v cy="$CY" -v sw="$SW" -v sh="$SH" -v tab="${TABSIZE:-16 10}" 'BEGIN{
    split(tab, t, " "); aspect = (t[2] > 0) ? t[1] / t[2] : 1.6;
    '"$CAP"'
    w = int(sw * s + 0.5); if (w < 1) w = 1; h = int(w / aspect + 0.5);
    if (h > sh) { h = sh; w = int(h * aspect + 0.5) }
    if (h < 1) h = 1;
    if (mode == "centre") {
        x = int(cx - w / 2 + 0.5); y = int(cy - h / 2 + 0.5);
        if (x > sw - w) x = sw - w; if (y > sh - h) y = sh - h;
        if (x < 0) x = 0; if (y < 0) y = 0;
    } else { x = int(cx / sw * (sw - w) + 0.5); y = int(cy / sh * (sh - h) + 0.5) }
    printf "%d %d %d %d %.6f %.6f %.6f %.6f", w,h,x,y, x/sw,y/sh,w/sw,h/sh}')
EOF
}

# ── chunk: compute_area
compute_area() {  # the area for the pen's CURRENT position (toggle ON, a move, the ghost): pen position, then area_math pen
    if POS=$(python3 "$DIR/tablet-pen-pos.py" 2>/dev/null); then
        set -- $POS
    else
        NON=$(date +%s%N)
        printf 'print("TPREC %s " + workspace.cursorPos.x + " " + workspace.cursorPos.y + " " + workspace.virtualScreenSize.width + " " + workspace.virtualScreenSize.height);\n' "$NON" > "$RD/probe.js"
        kload "$RD/probe.js" tabprec_probe
        line=""
        for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
            line=$(journalctl --user -u plasma-kwin_wayland.service -n 200 --no-pager 2>/dev/null | grep "TPREC $NON" | tail -1)
            [ -n "$line" ] && break
            sleep 0.2
        done
        kunload tabprec_probe
        if [ -z "$line" ]; then
            note "Could not read a pointer position"
            return 1
        fi
        set -- ${line#*"TPREC $NON"}
    fi
    area_math pen "$1" "$2" "$3" "$4"
}

# ── chunk: pen_group
pen_group() {  # the pen's kcminputrc group path into $GV $GP $GN: KWin keeps every mapping it is given under [Libinput][vendor][product][name]
    [ -n "$GV" ] && return 0
    [ -n "$GTRIED" ] && return 1        # asked once per run: never read KWin again after the mapping changed
    GTRIED=1
    set -- $(busctl --user get-property $KW $MGR/$pen $IF vendor product 2>/dev/null | awk '{ print $2 }')
    GN=$(busctl --user get-property $KW $MGR/$pen $IF name 2>/dev/null | cut -d' ' -f2- | sed 's/^"//; s/"$//')
    case "${1:-}${2:-}" in ""|*[!0-9]*) return 1;; esac   # not two plain numbers: never guess a group path
    [ -n "$GN" ] || return 1
    GV="$1"; GP="$2"
    return 0
}

# ── chunk: ledger
ledger() {  # the pen's ledger file into $LEDGER, named <vendor>-<product> like KWin's own group: only unpersist's failure path writes one
    [ -n "$LEDGER" ] && return 0
    pen_group && LEDGER="$LEDGERS/$GV-$GP" || LEDGER="$LEDGERS/pen"
    return 0
}

# ── chunk: unpersist
unpersist() {  # kcminputrc must name the mapping the pen has with precision mode OFF ($1..$4). KWin writes every outputArea it is
               # given straight into that file and loads it again whenever the tablet appears, so the rectangle would otherwise
               # outlive an unplug, a bus switch, a logout and a crash. Verified on Plasma 6.6: the live mapping stays in KWin's
               # memory, so this does not leave precision mode.
    WANT=$(echo "$*" | tr ' ' ',')
    [ "$WANT" = "0,0,1,1" ] && WANT=""       # an absent key and 0,0,1,1 are the same to KWin: leave no group behind
    pen_group || { echo "tablet-precision: no vendor, product and name from KWin - the OFF mapping stays unwritten" >&2; return 1; }
    command -v kwriteconfig6 >/dev/null 2>&1 || { echo "tablet-precision: no kwriteconfig6 - the OFF mapping stays unwritten" >&2; return 1; }
    if [ -z "$WANT" ]; then
        kwriteconfig6 --notify --file kcminputrc --group Libinput --group "$GV" --group "$GP" --group "$GN" --key OutputArea --delete 2>/dev/null
    else
        kwriteconfig6 --notify --file kcminputrc --group Libinput --group "$GV" --group "$GP" --group "$GN" --key OutputArea "$WANT" 2>/dev/null
    fi
    BACK=$(kreadconfig6 --file kcminputrc --group Libinput --group "$GV" --group "$GP" --group "$GN" --key OutputArea 2>/dev/null)
    [ "$BACK" = "$WANT" ] && return 0
    echo "tablet-precision: kcminputrc says '$BACK' for $GV/$GP, wanted '${WANT:-none}' - an unplug or a logout now would keep the rectangle" >&2
    return 1
}

# ── chunk: map_area
map_area() {  # outputArea = $1..$4 in KWin's memory, then kcminputrc back to the OFF mapping. A ledger is written ONLY when that
              # write-back cannot be proved, so the old repair path still exists on a KDE that changed under us.
    pen_group                            # ask KWin for the group path BEFORE the mapping changes: every read stays ahead of the write
    if [ -f "$STATE" ]; then BASE=$(cat "$STATE")
    else BASE=$(busctl --user get-property $KW $MGR/$pen $IF outputArea 2>/dev/null | cut -d' ' -f2-)
    fi
    busctl --user set-property $KW $MGR/$pen $IF outputArea '(dddd)' "$1" "$2" "$3" "$4" || return 1
    if unpersist ${BASE:-0 0 1 1}; then
        ledger; rm -f "$LEDGER"              # the disk names the OFF mapping: there is nothing left to repair
    else
        ledger
        { mkdir -p "$LEDGERS" && echo "${BASE:-0 0 1 1} $1 $2 $3 $4" > "$LEDGER"; } 2>/dev/null ||
            echo "tablet-precision: cannot write $LEDGER either - switch precision mode off before you unplug" >&2
    fi
    return 0
}

# ── chunk: apply_area
apply_area() {  # map the computed area (W H X Y FX FY FW FH SW SH), record it, then move the live overlay or (re)spawn it
    map_area "$FX" "$FY" "$FW" "$FH" || { note "Mapping change failed"; return 1; }
    echo "$X $Y $W $H $DIM $SW $SH $FX $FY $FW $FH" > "$AREA"
    if [ -f "$RD/overlay.pid" ] && kill -0 "$(cat "$RD/overlay.pid")" 2>/dev/null && [ -p "$FIFO" ] &&
       timeout 1 sh -c 'echo "$1" > "$2"' _ "$X $Y $W $H $DIM" "$FIFO" 2>/dev/null; then
        return 0                 # the overlay is alive and took the new geometry
    fi
    [ -f "$RD/overlay.pid" ] && kill "$(cat "$RD/overlay.pid")" 2>/dev/null
    nohup python3 "$DIR/tablet-overlay.py" "$X" "$Y" "$W" "$H" "$DIM" --fifo "$FIFO" > "$RD/overlay.log" 2>&1 9>&- &   # 9>&-: the overlay must not inherit the lock
    echo $! > "$RD/overlay.pid"
}

# ── chunk: remap
remap() {  # the area on record onto the pen again (resume, heal while ON)
    [ -f "$AREA" ] || return 0
    set -- $(cat "$AREA")
    if [ -z "${11:-}" ] && [ -n "${7:-}" ]; then     # an area file from before the fractions were recorded: compute them
        set -- "$@" $(awk -v x="$1" -v y="$2" -v w="$3" -v h="$4" -v sw="$6" -v sh="$7" 'BEGIN{printf "%.6f %.6f %.6f %.6f", x/sw, y/sh, w/sw, h/sh}')
    fi
    [ -n "${11:-}" ] && map_area "$8" "$9" "${10}" "${11}"
}

# ── chunk: heal_off
heal_off() {  # mode OFF: a pen back on the rectangle its ledger names (unplugged, on the other bus or logged out while ON) gets its base again; the ledger is spent
    ledger
    [ -f "$LEDGER" ] || return 0
    set -- $(cat "$LEDGER")
    LIVE=$(busctl --user get-property $KW $MGR/$pen $IF outputArea 2>/dev/null | cut -d' ' -f2-)
    [ -n "$LIVE" ] || return 1                       # no answer from KWin: the ledger stays for the next try
    if awk -v a="$LIVE" -v b="$5 $6 $7 $8" 'BEGIN{ if (split(a, x, " ") != 4 || split(b, y, " ") != 4) exit 1
            for (i = 1; i <= 4; i++) if (x[i] - y[i] > 0.0005 || y[i] - x[i] > 0.0005) exit 1 }'; then
        busctl --user set-property $KW $MGR/$pen $IF outputArea '(dddd)' "$1" "$2" "$3" "$4" || return 1
        echo "tablet-precision: $pen came back mapped to $5 $6 $7 $8 by precision mode - base $1 $2 $3 $4 restored" >&2
    fi
    rm -f "$LEDGER"                                  # healed, or the pen was mapped by someone else since: theirs stays
}

# ── chunk: unpause
unpause() {  # a pause younger than RECONNECT seconds comes back at the SAME area: after a reconnect the pen reads 0,0 until it
             # hovers, so a new placement would land in the corner. An older pause, or RECONNECT=0, is dropped. Always 0
    [ -f "$PAUSED" ] || return 0
    GONE=$(sed -n 1p "$PAUSED"); BASEL=$(sed -n 2p "$PAUSED"); AREAL=$(sed -n 3p "$PAUSED")
    rm -f "$PAUSED"
    awk -v now="$(date +%s)" -v gone="$GONE" -v w="$RECONNECT" 'BEGIN { if (gone !~ /^[0-9]+$/ || w + 0 <= 0 || now - gone > w + 0) exit 1 }' || return 0
    set -- $AREAL
    { [ -n "${11:-}" ] && [ -n "$BASEL" ]; } || return 0
    echo "$BASEL" > "$STATE"
    X=$1 Y=$2 W=$3 H=$4 SW=$6 SH=$7 FX=$8 FY=$9 FW=${10} FH=${11}
    apply_area || { rm -f "$STATE"; return 0; }
    echo "tablet-precision: the tablet came back within $RECONNECT s - precision mode resumed" >&2
    return 0
}

# ── chunk: run-lock
# heal looks for the pen BEFORE the lock. The daemon starts heal when a tablet appears, and KWin can list
# the pen up to 2 s later: inside the lock, that wait delayed every key press and froze the daemon's calls.
[ "$MODE" = heal ] && find_pen
case "$MODE" in toggle|resize|where|suspend|resume|heal|pause) ;;   # a mistyped verb must not toggle the mode
    *) echo "usage: $(basename "$0") [toggle|resize|where|suspend|resume|heal|pause]" >&2; exit 2;; esac
exec 9>"$RD/lock"; flock -w 5 9 || exit 1   # one run at a time: a long press makes KWin's MOVE toggle and the daemon's OFF toggle land in a row
[ -f "$CONF" ] && . "$CONF"   # read AFTER the lock: a ring tick queued behind another applies the newest SCALE, not the one it was born with
SCALE=${SCALE:-0.36}; SCALE=${SCALE/,/.}; case "$SCALE" in *[!0-9.]*|*.*.*|.) SCALE=0.36;; esac   # a comma decimal (a hand edit under
DIM=${DIM:-0.10}; DIM=${DIM/,/.}; case "$DIM" in *[!0-9.]*|*.*.*|.) DIM=0.10;; esac               # a comma locale) reads as a dot; junk = the default
# The soft cap on SCALE (Tablet Center's two size-limit fields): 0 = no cap on that side. Without one the
# area still ends at the full screen width and at one pixel - a wider or an empty area maps nothing.
case "${SCALE_MIN:-}" in ""|*[!0-9.]*) SCALE_MIN=0.05;; esac
case "${SCALE_MAX:-}" in ""|*[!0-9.]*) SCALE_MAX=0.80;; esac
CAP='if (hi + 0 > 0 && s > hi) s = hi; if (lo + 0 > 0 && s < lo) s = lo; if (s > 1) s = 1;'
RECONNECT=${RECONNECT:-60}   # seconds a paused mode waits for the tablet to come back; 0 = never resume
case "$RECONNECT" in *[!0-9.]*) RECONNECT=60;; esac

# OFF must work with the tablet gone: switched off, a flat battery, the cable out with Bluetooth off.
# Otherwise find_pen exits first, the OFF branch never runs and the overlay stays on the screen with
# nothing able to close it - the pad key, the keyboard shortcut, Tablet Center and the daemon all come
# through here. Found on the hardware 2026-09-12.
[ "$MODE" = toggle ] && [ -f "$STATE" ] && PEN_OPTIONAL=1
[ "$MODE" = resize ] || [ "$MODE" = pause ] || find_pen   # resize looks the pen up only when it has something to apply; pause never does
case "$MODE" in
# ── chunk: resize
resize)    # while ON: the conf's SCALE around the area's own centre (the pen's position only when there is no area on record); a queued tick whose width AND dim already match the screen does nothing, so a burst of ticks is two resizes, not a staircase. The cursor scales with the area: the pen's place IN the area stays, not its place on the screen
    if [ -f "$STATE" ]; then
        set -- $(cat "$AREA" 2>/dev/null)
        if [ -n "${7:-}" ]; then
            TARGET_W=$(awk -v s="$SCALE" -v lo="$SCALE_MIN" -v hi="$SCALE_MAX" -v sw="$6" 'BEGIN{ '"$CAP"' w = int(sw * s + 0.5); if (w < 1) w = 1; printf "%d", w }')
            if [ "$TARGET_W" != "$3" ] || [ "$DIM" != "$5" ]; then
                CENTRE=$(awk -v x="$1" -v y="$2" -v w="$3" -v h="$4" 'BEGIN{printf "%.1f %.1f", x + w / 2, y + h / 2}')
                area_math centre $CENTRE "$6" "$7" && apply_area
            fi
        else
            compute_area && apply_area
        fi
    fi
    ;;
# ── chunk: where
where)     # prints "X Y W H DIM SW SH"; nothing changes
    compute_area && echo "$X $Y $W $H $DIM $SW $SH"
    ;;
# ── chunk: suspend
suspend)   # while ON: the base mapping for the moment, precision mode stays on (a relocation is aimed: the cursor roams)
    if [ -f "$STATE" ]; then
        set -- $(cat "$STATE")
        busctl --user set-property $KW $MGR/$pen $IF outputArea '(dddd)' "${1:-0}" "${2:-0}" "${3:-1}" "${4:-1}"
    fi
    ;;
# ── chunk: resume
resume)    # while ON: the area on record again (the relocation was cancelled)
    if [ -f "$STATE" ]; then
        remap
    fi
    ;;
# ── chunk: pause
pause)     # the tablet gone past the daemon's grace with the mode ON ($2 = when it went): end the mode without a pen, keep the area for RECONNECT s
    if [ -f "$STATE" ]; then
        PEN_OPTIONAL=1; find_pen && exit 0   # KWin still lists the pen: the tablet is here (a node the daemon could not open), nothing to pause
        { echo "${2:-$(date +%s)}"; cat "$STATE"; cat "$AREA" 2>/dev/null; } > "$PAUSED"
        rm -f "$STATE" "$AREA" "$MARK"
        if [ -f "$RD/overlay.pid" ]; then
            kill "$(cat "$RD/overlay.pid")" 2>/dev/null
            rm -f "$RD/overlay.pid"
        fi
    fi
    ;;
# ── chunk: heal
heal)      # a tablet appeared (tablet-hover.py, in the background): ON = the pen takes the area on record (a wake, or the other bus); OFF = a recent pause resumes, else heal_off
    if [ -f "$STATE" ]; then
        remap
    else
        unpause
        [ -f "$STATE" ] || heal_off
    fi
    ;;
# ── chunk: toggle
*)
    if [ ! -f "$STATE" ] && unpause && [ -f "$STATE" ]; then
        :                        # a recent pause resumed where it was: that was this press's whole job
    elif [ ! -f "$STATE" ]; then
        heal_off                 # a rectangle left by an unplug or a logout while ON is no base to save
        CUR=$(busctl --user get-property $KW $MGR/$pen $IF outputArea | cut -d' ' -f2-)
        if compute_area && apply_area; then
            echo "$CUR" > "$STATE"
        fi
    elif [ -f "$MARK" ] && [ $(( $(date +%s) - $(stat -c %Y "$MARK" 2>/dev/null || echo 0) )) -le 3 ]; then
        [ -n "$pen" ] || { note "No tablet pen found (tablet asleep?)"; exit 1; }   # a MOVE needs the pen
        rm -f "$MARK"            # relocation confirmed: re-map at the pen, the saved base mapping stays
        compute_area && apply_area
    else
        if [ -n "$pen" ]; then   # the tablet is here: its live mapping goes back to the base
            ledger
            set -- $(cat "$STATE")
            busctl --user set-property $KW $MGR/$pen $IF outputArea '(dddd)' "${1:-0}" "${2:-0}" "${3:-1}" "${4:-1}" &&
                rm -f "$LEDGER"  # the pen is on its base again: nothing left to heal
        fi                       # no pen: nothing live to restore. kcminputrc already names the base (unpersist),
                                 # KWin loads it when the tablet returns, and a fallback ledger stays for heal
        rm -f "$STATE" "$AREA" "$MARK"
        if [ -f "$RD/overlay.pid" ]; then
            kill "$(cat "$RD/overlay.pid")" 2>/dev/null
            rm -f "$RD/overlay.pid"
        fi
    fi
    ;;
esac
