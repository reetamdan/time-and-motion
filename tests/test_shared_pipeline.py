"""Several file cameras in one DeepStream pipeline: routing, detach, whole-pipeline failure, identities."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

import pytest

from backend.app.configuration import load_configuration
from backend.app.perception import service_maker
from backend.app.perception.base import PerceptionCleanupError, PerceptionStartError, ResourceIdentity
from backend.app.perception.service_maker import FRAME_PREFIX, ContainerCleanup, ServiceMakerSessions

from .support import PROJECT_ROOT
from .test_service_maker import CollectingSink, object_record

CHILD = """
import json, sys, time
prefix, frames = sys.argv[1], json.loads(sys.argv[2])
for record in frames:
    sys.stdout.write(prefix + json.dumps(record) + "\\n")
    sys.stdout.flush()
if len(sys.argv) > 3:
    sys.stderr.write(sys.argv[3] + "\\n")
    sys.stderr.flush()
time.sleep(60)
"""


def record(camera_id: str, epoch: str, frame_number: int) -> dict:
    pts_ns = 1_000_000_000 + frame_number * 133_000_000
    return {"schema_version": "1.1", "camera_id": camera_id, "camera_epoch": epoch, "frame_number": frame_number,
            "pts_ns": pts_ns, "timestamp": pts_ns / 1e9, "frame_width": 1920, "frame_height": 1080,
            "source_width": 1920, "source_height": 1080, "bbox_format": "xywh", "objects": [object_record()]}


def manager_with(tmp_path: Path, monkeypatch, frames: list[dict], stderr: str | None = None):
    config = load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT)
    config.project_root = tmp_path  # per-camera files go under tmp_path/backend/var/perception
    cameras = [config.camera_map["cam-01"], config.camera_map["cam-01"].model_copy(update={"id": "cam-02"})]
    manager = ServiceMakerSessions(config)
    commands, removed = [], []

    def command(members, name, replay_mode="paced"):
        commands.append((members, name))
        return [sys.executable, "-u", "-c", CHILD, FRAME_PREFIX, json.dumps(frames), *([stderr] if stderr else [])]

    def remove(name, request_stop=True):
        removed.append(name)
        for session in list(manager._sessions.values()):
            if session.container_name == name and session.process.poll() is None:
                session.process.kill()
        return ContainerCleanup(name, True)
    monkeypatch.setattr(manager, "_pipeline_command", command)
    monkeypatch.setattr(manager, "_ensure_container_absent", remove)
    return manager, cameras, commands, removed


def test_frames_reach_their_own_camera_and_the_last_stop_releases_the_container(tmp_path, monkeypatch) -> None:
    frames = [record(c, e, n) for n in range(3) for c, e in (("cam-01", "e1"), ("cam-02", "e2"))]
    manager, cameras, commands, removed = manager_with(tmp_path, monkeypatch, frames)
    sinks = [CollectingSink(), CollectingSink()]

    async def scenario() -> None:
        results = await manager.start_pipeline([(cameras[0], "e1", sinks[0]), (cameras[1], "e2", sinks[1])], "unpaced")
        assert results == [None, None]
        assert [m[0].id for m in commands[0][0]] == ["cam-01", "cam-02"] and commands[0][1] == "aegis-batch-e1"
        for sink in sinks:
            for _ in range(3):
                await asyncio.wait_for(sink.arrived.get(), timeout=5)
        assert [(b.camera_id, b.camera_epoch, b.frame_number) for b in sinks[0].batches] == [("cam-01", "e1", n) for n in range(3)]
        assert [(b.camera_id, b.camera_epoch, b.frame_number) for b in sinks[1].batches] == [("cam-02", "e2", n) for n in range(3)]
        process = manager._sessions[("cam-01", "e1")].process
        assert manager._sessions[("cam-02", "e2")].process is process  # one container for both

        await manager.stop_camera("cam-01", "e1")  # detached: the other camera still uses the pipeline
        assert removed == [] and process.poll() is None and ("cam-02", "e2") in manager._sessions
        await manager.stop_camera("cam-02", "e2")  # the last camera releases the shared container
        assert removed == ["aegis-batch-e1"] and manager._sessions == {} and manager._pipelines == {}
        assert (tmp_path / "backend" / "var" / "perception" / "cam-01" / "e1.log").is_file()

    asyncio.run(scenario())


def test_a_pipeline_that_cannot_start_fails_every_camera_and_is_released(tmp_path, monkeypatch) -> None:
    manager, cameras, _, removed = manager_with(tmp_path, monkeypatch, [],
                                                stderr="Unable to set the pipeline to the playing state.")

    async def scenario() -> None:
        results = await manager.start_pipeline([(cameras[0], "e1", CollectingSink()), (cameras[1], "e2", CollectingSink())])
        assert all(isinstance(r, PerceptionStartError) and r.resource_allocated and r.cleanup_confirmed for r in results)
        assert results[0] is not results[1]  # each camera gets its own error object
        assert "cam-01, cam-02" in str(results[0]) and "could not start" in str(results[0])
        assert removed == ["aegis-batch-e1"] and manager._sessions == {} and manager._pipelines == {}

    asyncio.run(scenario())


def test_incompatible_cameras_are_refused_before_launch(tmp_path, monkeypatch) -> None:
    manager, cameras, commands, _ = manager_with(tmp_path, monkeypatch, [])
    other = cameras[1].model_copy(update={"analysis_frame_interval": 4})

    async def scenario() -> None:
        results = await manager.start_pipeline([(cameras[0], "e1", CollectingSink()), (other, "e2", CollectingSink())])
        assert all(isinstance(r, PerceptionStartError) and not r.resource_allocated for r in results)
        assert "same loop and analysis_frame_interval" in str(results[0]) and commands == []

    asyncio.run(scenario())


def test_shared_container_identity_is_adopted_after_a_restart_but_nothing_else(tmp_path, monkeypatch) -> None:
    manager, _, _, removed = manager_with(tmp_path, monkeypatch, [])
    identity = manager.pipeline_identity([("cam-01", "e1"), ("cam-02", "e2")])
    assert identity.name == "aegis-batch-e1" and manager.pipeline_identity([("cam-01", "e1")]) == manager.resource_identity("cam-01", "e1")
    manager.adopt("cam-01", "e1", identity)
    manager.adopt("cam-02", "e2", identity)

    async def scenario() -> None:
        await manager.stop_camera("cam-01", "e1")
        await manager.stop_camera("cam-02", "e2")
        assert removed == ["aegis-batch-e1", "aegis-batch-e1"] and manager.unresolved_cleanups() == {}

    asyncio.run(scenario())
    for name in ("aegis-other-thing", "someone-batch-e1"):
        with pytest.raises(PerceptionCleanupError):
            manager.adopt("cam-01", "e1", ResourceIdentity("container", name, identity.runtime))
    with pytest.raises(PerceptionCleanupError):
        manager.adopt("cam-01", "e1", ResourceIdentity("container", "aegis-batch-e1", "docker@elsewhere"))


def test_pipeline_command_repeats_the_per_camera_arguments_in_order(monkeypatch) -> None:
    monkeypatch.setattr(service_maker, "host_route", lambda: "linux-docker")
    config = load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT)
    manager = ServiceMakerSessions(config)
    first = config.camera_map["cam-01"].model_copy(update={"analysis_frame_interval": 4, "loop": True})
    second = first.model_copy(update={"id": "cam-02"})
    var = PROJECT_ROOT / "backend" / "var" / "perception"
    members = [(c, e, var / c.id / f"{e}.jsonl", var / c.id / f"{e}.summary.json") for c, e in ((first, "e1"), (second, "e2"))]
    command = manager._pipeline_command(members, "aegis-batch-e1")

    def values(flag):
        return [command[i + 1] for i, part in enumerate(command) if part == flag]
    assert "DEEPSTREAM_CONTAINER_NAME=aegis-batch-e1" in command
    assert values("--camera-id") == ["cam-01", "cam-02"] and values("--camera-epoch") == ["e1", "e2"]
    assert len(values("--source")) == 2 and values("--output")[1].endswith("/cam-02/e2.jsonl")
    assert values("--run-summary")[0].endswith("/cam-01/e1.summary.json")
    assert values("--analysis-frame-interval") == ["4"] and command.count("--loop") == 1
    single = manager._command(first, "e1", *members[0][2:])
    assert values("--infer-config") == [single[single.index("--infer-config") + 1]]
    assert single.count("--source") == 1 and f"DEEPSTREAM_CONTAINER_NAME={manager.container_name('cam-01', 'e1')}" in single
