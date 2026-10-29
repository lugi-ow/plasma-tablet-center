#!/usr/bin/env python3
# test_transition.py <scratch dir> <repo dir> - Qt-free rig for the first start after the rename:
# tablet_profiles.migrate_old_data copies the previous version's data folder to the new one. The fixture is shaped like
# a real folder (names with spaces, a backup folder, profiles.ini, the package mark); a scratch XDG_DATA_HOME only.
import os
import shutil
import sys
from pathlib import Path

SCRATCH = Path(sys.argv[1]) / "transition-test"
REPO = Path(sys.argv[2]).resolve()
shutil.rmtree(SCRATCH, ignore_errors=True)
(SCRATCH / "data").mkdir(parents=True)
os.environ.update({"XDG_DATA_HOME": str(SCRATCH / "data"), "XDG_CONFIG_HOME": str(SCRATCH / "config"),
                   "XDG_RUNTIME_DIR": str(SCRATCH / "run"), "HOME": str(SCRATCH / "home")})
sys.path.insert(0, str(REPO))
import tablet_profiles as tp  # noqa: E402 - env vars first: its paths read them at import

OLD, NEW = tp.OLD_DATA, tp.DATA
fails = 0


def check(label, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        fails += 1


def make_old():
    shutil.rmtree(OLD, ignore_errors=True)
    shutil.rmtree(NEW, ignore_errors=True)
    (OLD / "profiles").mkdir(parents=True)
    (OLD / "backup").mkdir()
    for n, name in enumerate(("My settings", "Profile 2", "Krita big brush")):
        body = f"[Conf]\nSCALE = 0.{n + 3}\n[Pad]\n0 = F13\n" * (n + 1) + "é\n"        # different sizes, non-ASCII
        (OLD / "profiles" / f"{name}{tp.OLD_EXT}").write_text(body, encoding="utf-8")
    (OLD / "backup" / f"Profile 2{tp.OLD_EXT}").write_text("[Conf]\nSCALE = 0.2\n")
    (OLD / "profiles.ini").write_text("[Active]\nname = Profile 2\n\n[Grid]\n1 = My settings\n2 = Profile 2\n")
    (OLD / "package-setup-done").write_text("1\n")


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


make_old()
before = snapshot(OLD)
notes = tp.migrate_old_data()
after = {k: v for k, v in snapshot(NEW).items() if k != tp.MOVE_MARK}      # the done-mark is not a copied file
check("move: 3 profiles before, 3 LISTED after", tp.list_profiles() == ["Krita big brush", "My settings", "Profile 2"])
check("move: the active profile is still Profile 2", tp.active_profile_name() == "Profile 2")
check("move: every file copied, bytes equal (6 of 6)", len(after) == 6 == len(before) and all(
    after[k.replace(tp.OLD_EXT, ".tcprofile")] == v for k, v in before.items()))
check("move: backups use the new extension, the mark moved too",
      (NEW / "backup" / "Profile 2.tcprofile").is_file() and (NEW / "package-setup-done").is_file())
check("move: no old extension in the new folder", not any(k.endswith(tp.OLD_EXT) for k in after))
check("move: the old folder is still whole, plus one note file",
      {k: v for k, v in snapshot(OLD).items() if k != tp.MOVED_NOTE} == before and (OLD / tp.MOVED_NOTE).is_file())
check("move: one note shown, 6 of 6", len(notes) == 1 and "6 of 6" in notes[0])

snap_new, snap_old = snapshot(NEW), snapshot(OLD)
check("second start: nothing changes and nothing is said",
      tp.migrate_old_data() == [] and snapshot(NEW) == snap_new and snapshot(OLD) == snap_old)

# A new folder that already has a file keeps it; the rest is copied.
make_old()
(NEW / "profiles").mkdir(parents=True)
(NEW / "profiles" / "My settings.tcprofile").write_text("newer\n")
notes = tp.migrate_old_data()
check("merge: the file already there is kept, the other 5 copied",
      (NEW / "profiles" / "My settings.tcprofile").read_text() == "newer\n" and len(snapshot(NEW)) == 7 and "5 of 6" in notes[0])

# A damaged copy: the second file reads back wrong. The old folder stays untouched, the new one holds nothing of ours.
make_old()
before = snapshot(OLD)
orig_copy2 = tp.shutil.copy2
calls = []


def bad_copy2(src, dst, **kw):
    calls.append(src)
    r = orig_copy2(src, dst, **kw)
    if len(calls) == 2:
        Path(dst).write_bytes(b"damaged")
    return r


tp.shutil.copy2 = bad_copy2
notes = tp.migrate_old_data()
tp.shutil.copy2 = orig_copy2
check("damaged: the old folder is untouched (no note file, same bytes)", snapshot(OLD) == before)
check("damaged: nothing of the copy is left, no .part file", snapshot(NEW) == {})
check("damaged: it says so and names the old folder", len(notes) == 2 and str(OLD) in notes[1])
notes = tp.migrate_old_data()
check("damaged: the next start tries again and succeeds", len(snapshot(NEW)) == 7 and "6 of 6" in notes[0])

# A read-only old folder (the note cannot be written): the verified copy stays, the done-mark is in the new folder.
if os.geteuid() != 0:
    make_old()
    before = snapshot(OLD)
    OLD.chmod(0o555)
    notes = tp.migrate_old_data()
    OLD.chmod(0o755)
    check("read-only old folder: the move succeeds, 6 of 6, the mark is in the new folder",
          len(notes) == 1 and "6 of 6" in notes[0] and len(snapshot(NEW)) == 7 and (NEW / tp.MOVE_MARK).is_file())
    check("read-only old folder: no note there, old bytes untouched", snapshot(OLD) == before)
    snap_new = snapshot(NEW)
    check("read-only old folder: the next start does nothing", tp.migrate_old_data() == [] and snapshot(NEW) == snap_new)

    # A first move that fails (an unreadable file), the fallback start in between, then the retry.
    make_old()
    before = snapshot(OLD)
    bad = OLD / "profiles" / f"Profile 2{tp.OLD_EXT}"
    bad.chmod(0)
    notes = tp.migrate_old_data()
    check("failed move: it says so, nothing of the copy is left", len(notes) == 2 and snapshot(NEW) == {})
    tp.ensure_first_start(None, None)             # main goes on: a fresh profiles.ini and 'My settings'
    check("failed move: the fallback ini carries the [Move] mark",
          tp._read_ini().get("Move", "fallback", fallback="") == "1" and tp.active_profile_name() == "My settings")
    snap_fb = snapshot(NEW)
    bad.chmod(0o644)
    check("failed move: the old folder is unchanged", snapshot(OLD) == before)
    notes = tp.migrate_old_data()
    check("retry: the old active name and the grid are back, the mark is gone",
          tp.active_profile_name() == "Profile 2" and tp.get_grid()[:2] == ["My settings", "Profile 2"]
          and not tp._read_ini().has_section("Move"))
    check("retry: the old 'My settings' bytes are back, 3 profiles listed",
          (NEW / "profiles" / "My settings.tcprofile").read_bytes() == before[f"profiles/My settings{tp.OLD_EXT}"]
          and tp.list_profiles() == ["Krita big brush", "My settings", "Profile 2"] and (NEW / tp.MOVE_MARK).is_file())
    snap_new = snapshot(NEW)
    check("retry: the next start does nothing", tp.migrate_old_data() == [] and snapshot(NEW) == snap_new)

    # A retry that fails again puts the fallback files back (never just unlinks a replaced file).
    make_old()
    bad = OLD / "profiles" / f"Profile 2{tp.OLD_EXT}"
    bad.chmod(0)
    tp.migrate_old_data()
    tp.ensure_first_start(None, None)
    snap_fb = snapshot(NEW)
    notes = tp.migrate_old_data()
    bad.chmod(0o644)
    check("failed retry: the fallback ini and 'My settings' are exactly as before",
          len(notes) == 2 and snapshot(NEW) == snap_fb and not (NEW / tp.MOVE_MARK).exists())

    # The old folder has no profiles.ini: the retry still drops the mark.
    make_old()
    (OLD / "profiles.ini").unlink()
    (OLD / "profiles" / f"Profile 2{tp.OLD_EXT}").chmod(0)
    tp.migrate_old_data()
    tp.ensure_first_start(None, None)
    (OLD / "profiles" / f"Profile 2{tp.OLD_EXT}").chmod(0o644)
    tp.migrate_old_data()
    check("retry without an old ini: the mark is dropped, the rest copied",
          not tp._read_ini().has_section("Move") and tp.active_profile_name() == "My settings"
          and tp.list_profiles() == ["Krita big brush", "My settings", "Profile 2"])

# No old folder at all: the fresh ini has no mark.
shutil.rmtree(OLD, ignore_errors=True)
shutil.rmtree(NEW, ignore_errors=True)
tp.ensure_first_start(None, None)
check("fresh install: no [Move] mark in the ini", not tp._read_ini().has_section("Move"))
shutil.rmtree(NEW, ignore_errors=True)

# Nothing to do: no old folder, or an empty one.
shutil.rmtree(OLD, ignore_errors=True)
shutil.rmtree(NEW, ignore_errors=True)
check("fresh install: no old folder, no new folder, no note", tp.migrate_old_data() == [] and not NEW.exists())
OLD.mkdir(parents=True)
check("empty old folder: nothing to do", tp.migrate_old_data() == [] and not NEW.exists())

# The import dialog's filter takes the old extension (the program text, not a run).
src = (REPO / "tablet_center.py").read_text()
check("import: the open dialog lists both extensions", "*.tcprofile *.wcprofile" in src)

shutil.rmtree(SCRATCH, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
