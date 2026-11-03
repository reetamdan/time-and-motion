"""Multi-camera lifecycle: concurrent isolated starts, independent epochs, failure isolation, cleanup."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from backend.app.configuration import load_configuration
from backend.app.db.repository import SQLiteRepository
from backend.app.perception.base import (
    AdapterAvailability, ObservationBatch, ObservationSink, PerceptionAdapter, PerceptionCleanupError, ResourceIdentity,
)
from backend.app.services.runtime import CameraRuntimeSupervisor
from backend.app.services.sequence import SequenceState

from .support import PROJECT_ROOT, copy_configuration, update_yaml

CAMERAS = ["cam-01", "cam-02", "cam-03", "cam-04"]


class MultiAdapter(PerceptionAdapter):
    def __init__(self, delays: dict[str, float] | None = None, fail: set[str] | None = None,
                 refuse_stop: set[str] | None = None) -> None:
        self.delays = delays or {}
        self.fail = fail or set()
        self.refuse_stop = refuse_stop or set()
        self.running: dict[str, str] = {}
        self.sinks: dict[str, ObservationSink] = {}
        self.started_at: dict[str, float] = {}

    def availability(self) -> AdapterAvailability:
        return AdapterAvailability("multi", True, "test", runtime_detected=True)

    def resource_identity(self, camera_id: str, camera_epoch: str) -> ResourceIdentity:
        return ResourceIdentity("container", f"aegis-{camera_id}-{camera_epoch}", "fake")

    async def start_camera(self, camera, camera_epoch, sink, replay_mode="paced") -> None:
        await asyncio.sleep(self.delays.get(camera.id, 0))
        if camera.id in self.fail:
            from backend.app.perception.base import PerceptionStartError
            raise PerceptionStartError("source failed before launch", resource_allocated=False)
        self.running[camera.id] = camera_epoch
        self.sinks[camera.id] = sink
        self.started_at[camera.id] = time.monotonic()

    async def stop_camera(self, camera_id: str, camera_epoch: str) -> None:
        if camera_id in self.refuse_stop:
            self.refuse_stop.discard(camera_id)
            raise PerceptionCleanupError(f"aegis-{camera_id}-{camera_epoch} still present")
        self.running.pop(camera_id, None)

    async def shutdown(self) -> None:
        return None


def four_cameras(tmp_path: Path):
    directory = copy_configuration(tmp_path)
    clip = tmp_path / "dup.mp4"
    clip.write_bytes(b"unit-test-video-fixture")

    def enable(document):
        for camera in document["cameras"]:
            camera.update(enabled=True, source=str(clip))
    update_yaml(directory, "cameras", enable)
    config = load_configuration(directory, PROJECT_ROOT)
    repository = SQLiteRepository(tmp_path / "multi.db")
    repository.migrate()
    repository.seed_configuration(config)
    return config, repository


def batch(camera: str, epoch: str, frame: int, *, at_limit: bool = False) -> ObservationBatch:
    return ObservationBatch(camera_id=camera, camera_epoch=epoch, observations=(), received_at=time.monotonic(),
                            source_fps=30.0, analytics_fps=30.0, frame_age_ms=None, frame_age_basis="unit",
                            dropped_frames=0, metadata={"detection_limit": 300, "at_detection_limit": at_limit},
                            source_time=frame / 30, source_pts_ns=int(frame / 30 * 1e9), source_segment=0,
                            frame_number=frame)


def test_slow_camera_does_not_delay_the_others(tmp_path: Path) -> None:
    config, repository = four_cameras(tmp_path)
    adapter = MultiAdapter(delays={"cam-01": 0.6})
    supervisor = CameraRuntimeSupervisor(config, repository, adapter, SequenceState())

    async def scenario() -> None:
        began = time.monotonic()
        await supervisor.start()
        assert supervisor.current_run()[1] == "RUNNING"
        others = [adapter.started_at[c] - began for c in CAMERAS if c != "cam-01"]
        assert max(others) < 0.3  # not serialized behind cam-01's 0.6 s start
        epochs = {c: supervisor.states()[c].epoch for c in CAMERAS}
        assert len(set(epochs.values())) == 4  # independent epochs
        await supervisor.stop()

    asyncio.run(scenario())


def test_one_failed_camera_is_isolated_and_others_keep_streaming(tmp_path: Path) -> None:
    config, repository = four_cameras(tmp_path)
    adapter = MultiAdapter(fail={"cam-03"})
    supervisor = CameraRuntimeSupervisor(config, repository, adapter, SequenceState())

    async def scenario() -> None:
        await supervisor.start()
        states = supervisor.states()
        assert supervisor.current_run()[1] == "DEGRADED"
        assert states["cam-03"].health == "OFFLINE" and "Source initialization failed" in states["cam-03"].unavailable_reason
        for camera in ("cam-01", "cam-02", "cam-04"):
            await adapter.sinks[camera].emit(batch(camera, states[camera].epoch, 1))
        states = supervisor.states()
        assert all(states[c].perception_started and states[c].source_position_s is not None for c in ("cam-01", "cam-02", "cam-04"))
        await supervisor.stop()
        assert supervisor.current_run()[1] == "STOPPED" and adapter.running == {}

    asyncio.run(scenario())


def test_partial_cleanup_failure_keeps_only_that_camera_owned(tmp_path: Path) -> None:
    config, repository = four_cameras(tmp_path)
    adapter = MultiAdapter(refuse_stop={"cam-02"})
    supervisor = CameraRuntimeSupervisor(config, repository, adapter, SequenceState())

    async def scenario() -> None:
        await supervisor.start()
        await supervisor.stop()
        assert supervisor.current_run()[1] == "STOP_FAILED"
        assert set(supervisor.cleanup_failures()) == {"cam-02"}
        assert set(adapter.running) == {"cam-02"}
        assert [r["camera_id"] for r in repository.unreleased_resources()] == ["cam-02"]
        await supervisor.stop()
        assert supervisor.current_run()[1] == "STOPPED" and adapter.running == {}

    asyncio.run(scenario())


def test_detection_limit_is_counted_and_alerted_once_per_epoch(tmp_path: Path) -> None:
    config, repository = four_cameras(tmp_path)
    adapter = MultiAdapter()
    supervisor = CameraRuntimeSupervisor(config, repository, adapter, SequenceState())

    async def scenario() -> None:
        await supervisor.start()
        epoch = supervisor.states()["cam-01"].epoch
        for frame, at_limit in ((1, False), (2, True), (3, True)):
            await adapter.sinks["cam-01"].emit(batch("cam-01", epoch, frame, at_limit=at_limit))
        state = supervisor.states()["cam-01"]
        assert state.frames_at_detection_limit == 2 and state.detection_limit == 300
        run_id = supervisor.current_run()[0]
        keys = [a["stable_key"] for a in repository.list_alerts(run_id=run_id)]
        assert keys.count(f"detection-limit:cam-01:{epoch}") == 1
        assert supervisor.states()["cam-02"].frames_at_detection_limit == 0
        await supervisor.stop()

    asyncio.run(scenario())
