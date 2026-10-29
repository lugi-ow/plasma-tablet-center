#!/usr/bin/env python3
"""add_markers.py SRC_DIR [OUT_DIR] - put `# ── chunk: <name>` markers
(`// ── chunk:` in QML) above every top-level def/class, bash function and
script mode of the toolkit, plus the hand-placed block markers listed below
(add new block markers there). A marker goes above the comment block that sits
directly on its target, never above the file header. Idempotent: re-run after
adding functions; existing markers are left alone. With OUT_DIR the marked
files are written there and SRC_DIR is left untouched (tests/stage_public.sh
uses that to stage the public tree). check_map.py imports mark(): a file it
would add a marker to has a def, function or mode with none."""
import os
import re
import sys

FILES = ("tablet-precision.sh", "tablet-pen-pos.py", "tablet-overlay.py", "tablet-overlay.qml",
         "tablet-hover.py", "tablet-precision-size.sh", "tablet-size-preview.py",
         "tablet-pointer-warp.py", "tablet-pad-probe.py", "tablet_center.py", "tablet_profiles.py")
# file -> [(line prefix that anchors the block, chunk name)]
BLOCKS = {
    "tablet-precision.sh": [("export LC_ALL=C.UTF-8", "config-and-paths"), ('[ "$MODE" = heal ] && find_pen', "run-lock")],
    "tablet-overlay.py": [("USAGE = ", "args"), ("app = QGuiApplication", "window"),
                          ("if follow or fifo:", "input-source"), ("    def on_input", "on_input")],
    "tablet-overlay.qml": [("Window {", "overlay-window"), ("    Item {", "fading-item"),
                           ("        Rectangle { x: 0;                 y: 0;", "dim-bands"),
                           ("        Rectangle {   // precision mode", "solid-border"),
                           ("        Shape {", "waiting-border"), ("    NumberAnimation {", "flow-animation")],
    "tablet-hover.py": [("FAMILIES = {", "FAMILIES"), ("MODELS = {", "MODELS")],
    "tablet-precision-size.sh": [('exec 8>"$RD/conf.lock"', "tick-lock"),
                                 ('if [ -f "$MARK" ]', "drag-guard"), ("STEP=$(awk", "step"),
                                 ("# rewrite SCALE/DIM in place", "conf-write"),
                                 ('if [ -f "$RD/saved-area" ]', "resize-or-preview")],
    "tablet-size-preview.py": [("app = QGuiApplication", "window"), ("quit_timer = QTimer()", "timers"),
                               ("try:", "first-show")],
    "tablet-pointer-warp.py": [("UI_DEV_CREATE, UI_DEV_DESTROY", "uinput-constants")],
    "tablet-pad-probe.py": [("BT_PEN_REGION_END", "quiet-reports")],
    "tablet_center.py": [("CONF = Path(", "paths")],
}
CASE_STAR = {"tablet-precision.sh": "toggle"}      # the `*)` mode's name
PY_DEF = re.compile(r"^(def|class)\s+(\w+)")
SH_FN = re.compile(r"^(\w+)\(\)\s*\{")
SH_CASE = re.compile(r"^(\w+|\*)\)")


def header_end(lines):
    """Index of the first line that is not the shebang / leading comment block."""
    for i, line in enumerate(lines):
        if line.startswith("#!") or line.startswith("#") or line.startswith("//"):
            continue
        return i
    return len(lines)


def mark(lines, fname):
    ext = fname.rsplit(".", 1)[1]
    cm = "//" if ext == "qml" else "#"
    targets = []                                   # (line index, indent, name)
    for i, line in enumerate(lines):
        if ext == "py":
            m = PY_DEF.match(line)
            if m:
                targets.append((i, "", m.group(2)))
        elif ext == "sh":
            m = SH_FN.match(line)
            if m:
                targets.append((i, "", m.group(1)))
            else:
                m = SH_CASE.match(line)
                if m:
                    name = CASE_STAR.get(fname) if m.group(1) == "*" else m.group(1)
                    if name:
                        targets.append((i, "", name))
        for prefix, name in BLOCKS.get(fname, []):
            if line.startswith(prefix):
                targets.append((i, line[:len(line) - len(line.lstrip())], name))
    head = header_end(lines)
    added = 0
    for i, indent, name in sorted(set(targets), reverse=True):
        j = i
        while j - 1 > head and lines[j - 1].startswith(indent + cm) and not lines[j - 1].startswith(indent + cm + " ── chunk:"):
            j -= 1                                 # above the comment block on the target
        marker = f"{indent}{cm} ── chunk: {name}"
        if j - 1 >= 0 and lines[j - 1].strip() == marker.strip():
            continue                               # already there
        lines.insert(j, marker)
        added += 1
    return added


def main(src, out=None):
    """Mark every file of FILES under src, in place or into out."""
    for fname in FILES:
        path = os.path.join(src, fname)
        if not os.path.exists(path):
            print(f"{fname}: missing")
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        lines = text.split("\n")
        added = mark(lines, fname)
        dest = os.path.join(out, fname) if out else path
        if out:
            os.makedirs(out, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        total = sum(1 for l in lines if "── chunk:" in l)
        print(f"{fname}: +{added} markers ({total} total)")


if __name__ == "__main__":                      # importable by check_map.py: mark() is the gate's rule too
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
