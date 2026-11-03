#!/usr/bin/env python3
"""Reproduce review findings 1 and 2 against the code as it exists (no new interfaces used)."""
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.app.configuration import load_configuration  # noqa: E402
from backend.app.db.repository import SQLiteRepository  # noqa: E402
from backend.app.perception.base import AdapterAvailability, PerceptionAdapter, PerceptionCleanupError  # noqa: E402
from backend.app.services.runtime import CameraRuntimeSupervisor  # noqa: E402
from backend.app.services.sequence import SequenceState  # noqa: E402
import yaml  # noqa: E402

containers: set[str] = set()


class LeakyAdapter(PerceptionAdapter):
    def __init__(self, start_fails=False, stop_fails=0):
        self.start_fails, self.stop_fails, self.stop_calls = start_fails, stop_fails, []

    def availability(self):
        return AdapterAvailability("repro", True, "repro", runtime_detected=True)

    async def start_camera(self, camera, epoch, sink, replay_mode="paced"):
        containers.add(f"aegis-{camera.id}-{epoch}")  # resource launched...
        if self.start_fails:
            raise RuntimeError("no metadata before startup deadline")  # ...then startup fails

    async def stop_camera(self, camera_id, epoch):
        self.stop_calls.append(epoch)
        if self.stop_fails:
            self.stop_fails -= 1
            raise PerceptionCleanupError("still present")
        containers.discard(f"aegis-{camera_id}-{epoch}")

    async def shutdown(self):
        return None


def make(tmp, adapter, db):
    cfg_dir = tmp / "configs"
    if not cfg_dir.exists():
        shutil.copytree(ROOT / "configs", cfg_dir)
        clip = tmp / "cam01.mp4"; clip.write_bytes(b"x")
        cams = yaml.safe_load((cfg_dir / "cameras.yaml").read_text()); cams["cameras"][0]["source"] = str(clip)
        (cfg_dir / "cameras.yaml").write_text(yaml.safe_dump(cams))
    config = load_configuration(cfg_dir, ROOT)
    repo = SQLiteRepository(db); repo.migrate(); repo.seed_configuration(config)
    return CameraRuntimeSupervisor(config, repo, adapter, SequenceState()), repo


async def case1(tmp):
    containers.clear()
    adapter = LeakyAdapter(start_fails=True)
    sup, _ = make(tmp, adapter, tmp / "c1.db")
    await sup.start(); after_start = sup.current_run()[1]
    await sup.stop()
    return {"status_after_start": after_start, "status_after_stop": sup.current_run()[1],
            "adapter_stop_calls": len(adapter.stop_calls), "containers_left": sorted(containers)}


async def case2(tmp):
    containers.clear()
    sup, repo = make(tmp, LeakyAdapter(stop_fails=1), tmp / "c2.db")
    await sup.start(); run_id = sup.current_run()[0]
    await sup.stop(); before = repo.get_run(run_id)["status"]
    adapter2 = LeakyAdapter()
    sup2, repo2 = make(tmp, adapter2, tmp / "c2.db")
    if hasattr(sup2, 'recover'):
        await sup2.recover()  # post-fix restart path
    else:
        sup2.initialize()  # pre-fix restart path
    persisted = repo2.get_run(run_id)["status"]
    try:
        await sup2.start()
    except Exception as exc:  # post-fix: start is refused while a resource is unresolved
        result_start_error = f'{type(exc).__name__}: {exc}'
    else:
        result_start_error = None
    return {"status_before_restart": before, "persisted_after_initialize": persisted,
            "adapter_stop_calls_on_restart": len(adapter2.stop_calls), "new_run_status": sup2.current_run()[1],
            "new_run_is_different": sup2.current_run()[0] != run_id, "start_error": result_start_error, "containers_left": sorted(containers)}


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=False)
    tmp = Path(tempfile.mkdtemp())
    result = {"finding_1_startup_failure": asyncio.run(case1(tmp)), "finding_2_restart": asyncio.run(case2(tmp))}
    r1, r2 = result["finding_1_startup_failure"], result["finding_2_restart"]
    result["reproduced"] = {
        "finding_1": r1["status_after_stop"] == "STOPPED" and r1["adapter_stop_calls"] == 0 and bool(r1["containers_left"]),
        "finding_2": r2["status_before_restart"] == "STOP_FAILED" and r2["persisted_after_initialize"] == "STOPPED"
                     and r2["adapter_stop_calls_on_restart"] == 0 and r2["new_run_status"] == "RUNNING",
    }
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
