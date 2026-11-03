#!/usr/bin/env python3
"""Reproduce review finding 3: the final history bucket is persisted only on operator stop, not at clean EOF."""
import asyncio
import json
import queue
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import yaml  # noqa: E402
from backend.app.configuration import load_configuration  # noqa: E402
from backend.app.db.repository import SQLiteRepository  # noqa: E402
from backend.app.perception.base import AdapterAvailability, PerceptionAdapter  # noqa: E402
from backend.app.perception.service_maker import ServiceMakerSessions, _Session  # noqa: E402
from backend.app.services.runtime import CameraRuntimeSupervisor  # noqa: E402
from backend.app.services.sequence import SequenceState  # noqa: E402


class Done:
    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


class ConsumerAdapter(PerceptionAdapter):
    """Runs the real ServiceMakerSessions._consume over 30 recorded-style frames, then a clean EOF."""

    def __init__(self, config):
        self.manager = ServiceMakerSessions(config)
        self.task = None

    def availability(self):
        return AdapterAvailability("repro", True, "repro", runtime_detected=True)

    async def start_camera(self, camera, epoch, sink, replay_mode="paced"):
        session = _Session(camera.id, epoch, "aegis-repro", Done(), Path("x"), None, asyncio.Event(),
                           frame_queue=queue.Queue(), replay_mode="unpaced")
        for n in range(30):  # 1.0 s of source time at 30 fps
            session.frame_queue.put({"frame_number": n, "pts_ns": int(n / 30 * 1e9), "objects": [
                {"class_id": 2, "label": "person", "confidence": 0.9, "tracker_confidence": 0.9, "track_id": "1",
                 "bbox": {"x": 1.0, "y": 1.0, "width": 10.0, "height": 20.0}}], "timestamp": n / 30})
        session.reader_done.set()
        self.manager._sessions[(camera.id, epoch)] = session
        self.task = asyncio.create_task(self.manager._consume(session, camera, sink))

    async def stop_camera(self, camera_id, epoch):
        return None

    async def shutdown(self):
        return None


async def main(out: Path):
    tmp = Path(tempfile.mkdtemp())
    shutil.copytree(ROOT / "configs", tmp / "configs")
    clip = tmp / "cam01.mp4"; clip.write_bytes(b"x")
    cams = yaml.safe_load((tmp / "configs" / "cameras.yaml").read_text()); cams["cameras"][0]["source"] = str(clip)
    (tmp / "configs" / "cameras.yaml").write_text(yaml.safe_dump(cams))
    config = load_configuration(tmp / "configs", ROOT)
    repo = SQLiteRepository(tmp / "eof.db"); repo.migrate(); repo.seed_configuration(config)
    adapter = ConsumerAdapter(config)
    sup = CameraRuntimeSupervisor(config, repo, adapter, SequenceState())
    await sup.start("unpaced")
    run_id, epoch = sup.current_run()[0], sup.states()["cam-01"].epoch
    await adapter.task  # clean EOF: consumer drained every frame
    before = repo.camera_count_history(run_id, "cam-01", epoch)
    await sup.stop()
    after = repo.camera_count_history(run_id, "cam-01", epoch)
    result = {"frames_delivered": 30, "sample_seconds": 0.5,
              "frames_persisted_after_eof_before_stop": sum(r["frames"] for r in before),
              "buckets_after_eof_before_stop": len(before),
              "frames_persisted_after_stop": sum(r["frames"] for r in after), "buckets_after_stop": len(after)}
    result["reproduced"] = result["frames_persisted_after_eof_before_stop"] < 30
    out.mkdir(parents=True, exist_ok=False)
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
