import asyncio, sys, time, uuid, json
from pathlib import Path
ROOT = Path.cwd(); sys.path.insert(0, str(ROOT))
from backend.app.configuration import load_configuration
from backend.app.perception import service_maker as sm
arr = []
class Sink:
    async def emit(self, batch):
        arr.append((time.perf_counter(), batch.metadata["frame_number"], batch.metadata["source_pts_ns"], batch.analytics_fps))
async def main():
    config = load_configuration(ROOT / "configs", ROOT)
    m = sm.ServiceMakerSessions(config); cam = config.camera_map["cam-01"]; ep = "arrivals-" + uuid.uuid4().hex[:6]
    t0 = time.perf_counter()
    await m.start_camera(cam, ep, Sink())
    s = m._sessions[(cam.id, ep)]
    while not s.task.done(): await asyncio.sleep(0.1)
    await m.stop_camera(cam.id, ep)
    ts = [a[0] - t0 for a in arr]
    print("frames", len(arr), "first_at", round(ts[0], 2), "last_at", round(ts[-1], 2), "mean_rate", round((len(ts) - 1) / (ts[-1] - ts[0]), 1))
    # per-250ms histogram
    bins = {}
    for t in ts: bins[int(t * 4)] = bins.get(int(t * 4), 0) + 1
    print("frames per 250ms:", [bins.get(b, 0) for b in range(min(bins), max(bins) + 1)])
    gaps = sorted(b - a for a, b in zip(ts, ts[1:]))
    print("inter-arrival ms p50", round(gaps[len(gaps)//2]*1e3, 3), "p90", round(gaps[int(len(gaps)*.9)]*1e3, 3), "max", round(gaps[-1]*1e3, 1))
    fps = [a[3] for a in arr if a[3] is not None]
    print("reported analytics_fps min/median/max", round(min(fps),1), round(sorted(fps)[len(fps)//2],1), round(max(fps),1))
asyncio.run(main())
