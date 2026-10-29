#!/usr/bin/env python3
# tests/test_conf.py SCRATCH SRCDIR - GCP-01/02/03: every reader of ~/.config/tabprec.conf takes ONE line shape and
# no reader runs the file. Part 1 drives the real ring script end to end (a stub toggle and preview) on each
# conf text; part 2 reads the same texts through every parser (the shell function of BOTH scripts, the daemon,
# tablet_profiles, the size preview) and compares them. A line of Artem's own conf (tests/fixtures) must read as before.
import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SCRATCH, SRC = Path(sys.argv[1]) / "conf", Path(sys.argv[2])
shutil.rmtree(SCRATCH, ignore_errors=True)
BIN, CONFD, RD = SCRATCH / "bin", SCRATCH / "conf", SCRATCH / "rd"
for d in (BIN, CONFD, RD / "tabprec"):
    d.mkdir(parents=True)
os.environ.update(XDG_RUNTIME_DIR=str(RD), XDG_CONFIG_HOME=str(CONFD), XDG_DATA_HOME=str(SCRATCH / "data"))
CONF = CONFD / "tabprec.conf"
fails = 0


def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   {detail}"))
    fails += not ok


# the texts: (label, conf text, expected value per key as the parsers hand it on; absent = unset)
LIVE = (Path(__file__).parent / "fixtures/live-2026-09/tabprec.conf").read_text()
CASES = [
    ("live", LIVE, {"SCALE": "0.1950", "DIM": "0.00", "RING_STEP": "0.5"}),
    ("spaces", "SCALE = 0.30\nDIM\t=\t0.20\n", {"SCALE": "0.30", "DIM": "0.20"}),
    ("comment", "SCALE=0.30 # my size\nDIM=0.2#x\n# SCALE=0.9\n", {"SCALE": "0.30", "DIM": "0.2"}),
    ("quotes", 'SCALE="0.30"\nDIM=\'0.20\'\n', {"SCALE": "0.30", "DIM": "0.20"}),
    ("crlf", "SCALE=0.40\r\nDIM=0.15\r\n", {"SCALE": "0.40", "DIM": "0.15"}),
    ("indent", "   SCALE=0.25\n\tDIM=0.05\n", {"SCALE": "0.25", "DIM": "0.05"}),
    ("last wins", "SCALE=0.20\nSCALE=0.22\n", {"SCALE": "0.22"}),
    ("no newline", "SCALE=0.33", {"SCALE": "0.33"}),
    ("junk lines", "hello world\n=5\nexport SCALE=0.9\nSCALE\nSCALE=0.21\n", {"SCALE": "0.21"}),
    ("comma", "SCALE=0,30\n", {"SCALE": "0,30"}),
    ("no run", "SCALE=$(touch RAN)\nDIM=`touch RAN2`\n", {"SCALE": "$(touch RAN)", "DIM": "`touch RAN2`"}),
]
KEYS = ("SCALE", "DIM", "SCALE_MIN", "SCALE_MAX", "RECONNECT", "RING_STEP")

# ---- part 1: the real ring script, one tick up from each text (RING_STEP=1 = +0.01) ----
(BIN / "tablet-precision.sh").write_text('#!/bin/bash\necho "CALL $1" >> "$FAKE_LOG"\n')
(BIN / "tablet-size-preview.py").write_text("#!/usr/bin/env python3\n")
shutil.copy(SRC / "tablet-precision-size.sh", BIN / "tablet-precision-size.sh")
for f in BIN.iterdir():
    f.chmod(0o755)
os.environ["FAKE_LOG"] = str(SCRATCH / "calls.log")


def tick(text):
    CONF.write_bytes(text.encode())
    for gone in (RD / "tabprec/preview.pid", SCRATCH / "RAN", SCRATCH / "RAN2"):
        gone.unlink(missing_ok=True)
    subprocess.run(["bash", str(BIN / "tablet-precision-size.sh"), "up"], cwd=SCRATCH, capture_output=True, timeout=20)
    return CONF.read_text()


def scale_of(conf_text):
    return [l for l in conf_text.splitlines() if l.startswith("SCALE=")]


out = tick("RING_STEP=1\nSCALE = 0.30\nHOVER_MASK=0x80\n")
check("C1 'SCALE = 0.30' (spaces): +0.01 = 0.3100 (old shell read the default: 0.3700)", scale_of(out) == ["SCALE=0.3100"], out)
check("C1 ... and the old spaced line is replaced, not left beside the new one (one SCALE key)",
      sum(1 for l in out.splitlines() if re.match(r"\s*SCALE\s*=", l)) == 1, out)
check("C1 ... the other lines are kept", "RING_STEP=1" in out and "HOVER_MASK=0x80" in out, out)
out = tick("RING_STEP=1\nSCALE=0,30\n")
check("C2 'SCALE=0,30' (comma): 0.3100 (old ring script read 0 and clamped to 0.0500)", scale_of(out) == ["SCALE=0.3100"], out)
out = tick("RING_STEP=1\nSCALE=$(touch RAN)\n")
check("C3 a command in a conf value is NOT run (old: the file RAN appears)", not (SCRATCH / "RAN").exists(),
      "the conf was executed")
check("C3 ... and the junk SCALE falls back on the default: 0.3700", scale_of(out) == ["SCALE=0.3700"], out)
out = tick("RING_STEP=1\nSCALE=0.30 # note\nDIM=0.2\n")
check("C4 a trailing comment still reads: 0.3100", scale_of(out) == ["SCALE=0.3100"], out)
out = tick("RING_STEP=1\r\nSCALE=0.30\r\n")
check("C5 a CRLF conf reads: 0.3100", scale_of(out) == ["SCALE=0.3100"], out)
out = tick(LIVE.replace("RING_STEP=0.5", "RING_STEP=1"))
check("C6 Artem's own conf lines: 0.1950 + 0.01 = 0.2050, every other line kept",
      scale_of(out) == ["SCALE=0.2050"] and all(l in out.splitlines() for l in LIVE.splitlines()
                                                if not l.startswith(("SCALE", "RING_STEP"))), out)

# ---- part 2: every parser on every text ----
def shell_read(script, text):
    """The script's own read_conf_keys, cut out of the file and run alone (no function = no result)."""
    body = (SRC / script).read_text()
    m = re.search(r"^# ── chunk: read_conf_keys\n(read_conf_keys\(\) \{.*?^\})", body, re.S | re.M)
    if not m:
        return None
    CONF.write_bytes(text.encode())
    drv = ("export LC_ALL=C.UTF-8\n" + m.group(1) + f'\nCONF="{CONF}"\nread_conf_keys\n'
           'for k in ' + " ".join(KEYS) + '; do [ -n "${!k+x}" ] && printf "%s=%s\\n" "$k" "${!k}"; done\n')
    r = subprocess.run(["bash", "-c", drv], capture_output=True, text=True, cwd=SCRATCH, timeout=10)
    return dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)


sys.path.insert(0, str(SRC))
import importlib.util  # noqa: E402

hspec = importlib.util.spec_from_file_location("hover", SRC / "tablet-hover.py")
hover = importlib.util.module_from_spec(hspec)
hspec.loader.exec_module(hover)
hover.CONF = CONF
import tablet_profiles as wp  # noqa: E402

wp.CONF = CONF


def profiles_read(text):
    out = {}
    for line in text.splitlines():
        kv = wp.parse_conf_line(line) if hasattr(wp, "parse_conf_line") else None
        if kv:
            out[kv[0]] = kv[1]
    return out


tree = ast.parse((SRC / "tablet-size-preview.py").read_text())
fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "read_conf")
ns = {"CONF": CONF, "re": re}
exec(compile(ast.Module([fn], []), "preview", "exec"), ns)

def body_of(script):
    m = re.search(r"^read_conf_keys\(\) \{.*?^\}", (SRC / script).read_text(), re.S | re.M)
    return m.group(0) if m else None


check("R the two scripts carry the same read_conf_keys body",
      body_of("tablet-precision.sh") is not None and body_of("tablet-precision.sh") == body_of("tablet-precision-size.sh"))

for label, text, want in CASES:
    CONF.write_bytes(text.encode())
    for name, got in (("toggle shell", shell_read("tablet-precision.sh", text)),
                      ("ring shell", shell_read("tablet-precision-size.sh", text)),
                      ("daemon", {k: v for k, v in hover.read_conf().items() if k in KEYS}),
                      ("profiles", {k: v for k, v in profiles_read(text).items() if k in KEYS})):
        check(f"R {label}: {name} reads {want}", got == want, f"got {got}")
    # the preview hands on floats: SCALE/DIM, a comma as a dot, a junk value = its default
    if "SCALE" in want and re.fullmatch(r"[0-9.,]+", want["SCALE"]):
        sc, dim = ns["read_conf"]()
        check(f"R {label}: size preview SCALE = {want['SCALE']}",
              abs(sc - float(want["SCALE"].replace(",", "."))) < 1e-9, f"got {sc}")

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
