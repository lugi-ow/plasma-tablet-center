#!/bin/bash
# tablet-precision-size.sh {up|down} - one ring tick: step the precision area size (conf SCALE) by RING_STEP points.
# Part of plasma-wacom-center (MIT). Structure and contracts: PROJECT_MAP.md; mechanisms: TECHNICAL.md.

export LC_ALL=C.UTF-8
CONF="${XDG_CONFIG_HOME:-$HOME/.config}/tabprec.conf"
RD="${XDG_RUNTIME_DIR:-/tmp}/tabprec"
mkdir -p "$RD"
# ── chunk: tick-lock
# One tick at a time over the conf. Its OWN lock, never the toggle's $RD/lock, because this script
# CALLS the toggle. The conf is read AFTER it, so a tick queued behind another steps the newest
# SCALE, and the read-modify-write below cannot interleave with another tick - two overlapping ticks
# used to lose a step, and could leave a conf holding only SCALE and DIM.
exec 8>"$RD/conf.lock"; flock -w 5 8 || exit 0   # exit 0: a tick that cannot get in must not spawn a preview either
[ -f "$CONF" ] && . "$CONF"
SCALE=${SCALE:-0.36}
DIM=${DIM:-0.10}
DIR=$(cd "$(dirname "$0")" && pwd)
MARK="$RD/relocate"

# ── chunk: drag-guard
if [ -f "$MARK" ] && [ $(( $(date +%s) - $(stat -c %Y "$MARK" 2>/dev/null || echo 0) )) -le 3 ]; then
    exit 0                   # a relocation is being aimed: the size stays
fi

# ── chunk: step
STEP=$(awk -v p="${RING_STEP:-0.5}" 'BEGIN{ if (p + 0 <= 0) p = 0.5; printf "%.4f", p / 100 }')   # points per tick, from the conf
[ "$1" = "down" ] && STEP="-$STEP"
# the soft cap (conf SCALE_MIN / SCALE_MAX, 0 = none on that side); without one the full screen width and 0.001
case "${SCALE_MIN:-}" in ""|*[!0-9.]*) SCALE_MIN=0.05;; esac
case "${SCALE_MAX:-}" in ""|*[!0-9.]*) SCALE_MAX=0.80;; esac
SCALE=$(awk -v s="$SCALE" -v d="$STEP" -v lo="$SCALE_MIN" -v hi="$SCALE_MAX" 'BEGIN{
    s += d; if (hi + 0 > 0 && s > hi) s = hi; if (lo + 0 > 0 && s < lo) s = lo;
    if (s > 1) s = 1; if (s < 0.001) s = 0.001;
    printf "%.4f", s}')
# ── chunk: conf-write
# rewrite SCALE/DIM in place and keep every other line (touch-preview keys)
REST=$(grep -v -E '^[[:space:]]*(SCALE|DIM)=' "$CONF" 2>/dev/null)
TMP="$CONF.tmp.$$"
{ printf 'SCALE=%s\nDIM=%s\n' "$SCALE" "$DIM" && { [ -z "$REST" ] || printf '%s\n' "$REST"; }; } > "$TMP" &&
    mv -f "$TMP" "$CONF" || rm -f "$TMP"   # atomic: no crash and no full disk can leave half a conf. The group's status
    # is the printfs' own (an empty REST is simply nothing to add), so a write that failed is never moved into place
exec 8>&-                                  # written: let the next tick in before the toggle runs

# ── chunk: resize-or-preview
if [ -f "$RD/saved-area" ]; then
    "$DIR/tablet-precision.sh" resize          # precision mode on: the area itself, around its centre
elif ! { [ -f "$RD/preview.pid" ] && kill -0 "$(cat "$RD/preview.pid")" 2>/dev/null; }; then
    nohup python3 "$DIR/tablet-size-preview.py" > "$RD/preview.log" 2>&1 &   # off: the centred preview
    echo $! > "$RD/preview.pid"
fi
