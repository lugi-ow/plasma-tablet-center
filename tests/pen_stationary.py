# pen_stationary.py - VM ONLY: the cursor must not jump when the real toggle goes ON with Left-handed on and the helper running. Compares tablet-precision-old.sh (copy of the pre-fix script) with tablet-precision.sh.
import sys, subprocess, time, os
os.environ["DISPLAY"]=":0"; import glob; os.environ["XAUTHORITY"]=glob.glob("/run/user/1000/xauth_*")[0]
sys.path.insert(0, "tests")
import pen_gate as g
grp = ["--group","Libinput","--group","1386","--group","855","--group",g.NAME]
subprocess.run(["kwriteconfig6","--notify","--file","kcminputrc",*grp,"--key","LeftHanded","true"])
log="/tmp/t2.log"; fifo="/tmp/t2.fifo"
probe = subprocess.Popen([g.PY,"tests/pen_probe.py",log,"120"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); time.sleep(4)
st = subprocess.Popen([g.PY,"tests/pen_standin.py","--bus","usb","--fifo",fifo],stdout=subprocess.PIPE,text=True)
node = st.stdout.readline().strip(); time.sleep(2)
h = subprocess.Popen([g.PY,"tablet-pen-driver.py","--force","--source",node],stdout=subprocess.PIPE,text=True); h.stdout.readline(); time.sleep(3)
print("kwin leftHanded", g.gate_kwin(node.rsplit("/",1)[-1])["leftHanded"], "outputArea", g.gate_kwin(node.rsplit("/",1)[-1])["outputArea"])
rd = os.environ["XDG_RUNTIME_DIR"]+"/tabprec"
def pos(raw):
    pts = g.gate_drive(fifo, log, [(raw[0], raw[1], 0)], wait=0.5)
    return [round(float(v),4) for v in pts[-1][1:3]]
for script in ("tablet-precision-old.sh", "tablet-precision.sh"):
    for f in ("saved-area","area","relocate","overlay.pid"):
        try: os.remove(rd+"/"+f)
        except OSError: pass
    for raw in ((33600,22200),(8000,5000)):
        p0 = pos(raw)
        r = subprocess.run(["bash",script,"toggle"],capture_output=True,text=True); time.sleep(0.5)
        p1 = pos(raw)
        r2 = subprocess.run(["bash",script,"toggle"],capture_output=True,text=True); time.sleep(0.5)
        p2 = pos(raw)
        print(script, "raw", raw, "before", p0, "ON", p1, "OFF", p2, "delta ON-before %.4f %.4f" % (p1[0]-p0[0], p1[1]-p0[1]), "exit", r.returncode, r2.returncode)
        for f in ("saved-area","area","relocate","overlay.pid"):
            try: os.remove(rd+"/"+f)
            except OSError: pass
open(fifo,"w").write("quit\n"); h.terminate(); probe.terminate()
