import asyncio, sys, time, subprocess, uuid
from pathlib import Path
ROOT = Path.cwd(); sys.path.insert(0, str(ROOT))
from backend.app.configuration import load_configuration
from backend.app.perception import service_maker as sm

T0 = [0.0]
def mark(msg): print(f"{time.monotonic()-T0[0]:7.3f}s {msg}", flush=True)

class Sink:
    def __init__(self): self.n = 0
    async def __call__(self, *a, **k): self.n += 1
    def __getattr__(self, name):
        async def f(*a, **k): self.n += 1
        return f

orig_stop = sm.ServiceMakerSessions._stop_container
def stop(self, name):
    mark("docker stop begin"); orig_stop(self, name); mark("docker stop end")
sm.ServiceMakerSessions._stop_container = stop
orig_wait = subprocess.Popen.wait
def wait(self, timeout=None):
    mark(f"process.wait({timeout}) begin")
    try:
        return orig_wait(self, timeout)
    finally:
        mark(f"process.wait end rc={self.returncode}")
subprocess.Popen.wait = wait

async def main():
    config = load_configuration(ROOT / "configs", ROOT)
    m = sm.ServiceMakerSessions(config)
    cam = config.camera_map["cam-01"]; epoch = "stopprobe-" + uuid.uuid4().hex[:8]
    sink = Sink()
    T0[0] = time.monotonic()
    await m.start_camera(cam, epoch, sink); mark("first frame")
    await asyncio.sleep(0.5); mark(f"stopping; sink calls={sink.n}")
    s = m._sessions[(cam.id, epoch)]
    t = time.monotonic(); await m.stop_camera(cam.id, epoch)
    mark(f"stop_camera total {time.monotonic()-t:.3f}s task_done={s.task.done()} reader_alive={s.reader.is_alive()}")
asyncio.run(main())
