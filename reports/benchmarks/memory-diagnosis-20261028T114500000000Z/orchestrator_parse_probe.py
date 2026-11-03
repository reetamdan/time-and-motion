import sys, psutil, os, time
sys.argv=[sys.argv[0]]+sys.argv[1:]
sys.path.insert(0, "scripts/benchmark")
import importlib.util
spec = importlib.util.spec_from_file_location("rc", "scripts/benchmark/run_capacity.py")
rc = importlib.util.module_from_spec(spec); spec.loader.exec_module(rc)
from pathlib import Path
p = Path(sys.argv[1])
proc = psutil.Process()
base = proc.memory_info().rss
peak = [base]
import threading
stop = False
def watch():
    while not stop:
        peak[0] = max(peak[0], proc.memory_info().rss); time.sleep(0.05)
t = threading.Thread(target=watch, daemon=True); t.start()
t0=time.time()
rows = rc.read_jsonl(p)
m = rc.feed_metrics(rows, (120, 720), None, 0)
stop=True; t.join()
print("file_MB", round(p.stat().st_size/2**20,1), "base_MB", round(base/2**20), "peak_MB", round(peak[0]/2**20), "rows", len(rows), "s", round(time.time()-t0,1))
