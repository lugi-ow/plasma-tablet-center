#!/bin/bash
# tests/run_all.sh - every gate in one go: compile every Python file,
# syntax-check every shell script, the map gate (tests/check_map.py), then the
# eight rigs - test_script.sh (the toggle with a stubbed busctl, a fake pen
# reader and a fake overlay), test_size.sh (the ring script with a stub toggle
# and a fake preview), test_hover.py (the daemon with a named pipe as the
# pad, a fake pen, fake overlays and a fake toggle), test_profiles.py
# (tablet_profiles.py, Qt-free, with stubbed kreadconfig6/kwriteconfig6/qdbus6
# wrapping the real binaries), test_install.sh (packaging/build-deb.sh's .deb
# unpacked into a scratch root, its first start on a scratch HOME), test_conf.py (one conf line shape in every
# reader), test_warp.py (the virtual mouse when KWin's list cannot be matched) and test_center.py (the Pad buttons tab offscreen,
# skipped without PyQt6 like the import smoke). Run from anywhere; needs python3
# and bash - no tablet, no KWin, no window. Exit 0 = all green.
set -o pipefail
cd "$(dirname "$0")/.." || exit 1
S="${TMPDIR:-/tmp}/tabprec-tests"        # the rigs wipe their own subfolders
red=0

echo "== py_compile"
python3 -m py_compile *.py tests/*.py tests/fake/*.py || red=1
echo "== import smoke"
# py_compile cannot see a name imported from the wrong Qt module (shipped once,
# 2026-09-14: QLocalServer from QtCore). Skipped cleanly on a box without PyQt6.
if python3 -c "import PyQt6" 2>/dev/null; then
  mkdir -p "$S/imp" && QT_QPA_PLATFORM=offscreen XDG_CONFIG_HOME="$S/imp" \
  XDG_DATA_HOME="$S/imp" XDG_RUNTIME_DIR="$S/imp" python3 -c "
import importlib.util
for n in ('tablet_profiles', 'tablet_center'):
    s = importlib.util.spec_from_file_location(n, n + '.py')
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
print('imports OK')" || red=1
else
  echo "PyQt6 absent - skipped"
fi
echo "== bash -n"
for f in *.sh tests/*.sh packaging/*.sh; do bash -n "$f" || red=1; done
echo "== map"
python3 tests/check_map.py || red=1
echo "== toggle rig"
bash tests/test_script.sh "$S" "$PWD/tablet-precision.sh" 2>&1 | grep -E '^(FAIL|failures)' || red=1
echo "== ring rig"
bash tests/test_size.sh "$S" "$PWD/tablet-precision-size.sh" 2>&1 | grep -E '^(FAIL|failures)' || red=1
echo "== hover rig"
python3 tests/test_hover.py "$S" "$PWD/tablet-hover.py" 2>/dev/null | grep -E '^(FAIL|failures)' || red=1
echo "== pressure rig"
python3 tests/test_pressure.py "$S" "$PWD" 2>&1 | grep -E '^(FAIL|failures|cpu)' || red=1
echo "== profiles rig"
python3 tests/test_profiles.py "$S" "$PWD" 2>&1 | grep -E '^(FAIL|failures)' || red=1
echo "== pen driver rig"
python3 tests/test_pen_driver.py 2>&1 | grep -E '^(FAIL|failures)' || red=1
echo "== pen wait rig"
bash tests/test_pen_wait.sh "$S" "$PWD/tablet-precision.sh" 2>&1 | grep -E '^(FAIL|SKIP|failures)' || red=1
echo "== transition rig"
python3 tests/test_transition.py "$S" "$PWD" 2>&1 | grep -E '^(FAIL|failures)' || red=1
echo "== conf rig"
python3 tests/test_conf.py "$S" "$PWD" 2>&1 | grep -E '^(FAIL|failures)' || red=1
echo "== warp rig"
python3 tests/test_warp.py "$S" "$PWD" 2>&1 | grep -E '^(FAIL|failures)' || red=1
echo "== per-app rig"
python3 tests/test_perapp.py "$S" "$PWD" 2>&1 | grep -E '^(FAIL|failures)' || red=1
echo "== install rig"
bash tests/test_install.sh "$S" 2>&1 | grep -E '^(FAIL|failures)' || red=1
echo "== center rig"
if python3 -c "import PyQt6" 2>/dev/null; then
  python3 tests/test_center.py "$S" "$PWD" 2>&1 | grep -E '^(FAIL|failures)' || red=1
else
  echo "PyQt6 absent - skipped"
fi

echo
[ "$red" -eq 0 ] && echo "ALL GATES GREEN" || echo "GATES RED"
exit "$red"
