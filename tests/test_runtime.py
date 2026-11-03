from __future__ import annotations

import asyncio
import time

from backend.app.analytics.types import Observation
from backend.app.configuration import load_configuration
from backend.app.db.repository import SQLiteRepository
from backend.app.perception.base import ObservationBatch
from backend.app.services.environment import EnvironmentService
from backend.app.services.runtime import CameraRuntimeSupervisor
from backend.app.services.sequence import SequenceState
from backend.app.services.telemetry import TelemetryCollector, TelemetryService

from .support import PROJECT_ROOT, SuppliedObservationAdapter, UnavailableAdapter, copy_configuration, key, update_yaml, write_calibration
from .support import requires_local_artifacts


def test_available_source_is_isolated_from_missing_sources(tmp_path) -> None:
    directory = copy_configuration(tmp_path)
    clip = tmp_path / "cam01.mp4"
    clip.write_bytes(b"unit-test-video-fixture")
    update_yaml(directory, "cameras", lambda document: document["cameras"][0].update(source=str(clip)))
    config = load_configuration(directory, PROJECT_ROOT)
    repository = SQLiteRepository(tmp_path / "runtime.db")
    repository.migrate()
    repository.seed_configuration(config)
    adapter = SuppliedObservationAdapter()
    sequence = SequenceState()
    supervisor = CameraRuntimeSupervisor(config, repository, adapter, sequence)

    async def lifecycle() -> None:
        await supervisor.start()
        states = supervisor.states()
        assert states["cam-01"].perception_started
        assert states["cam-01"].health == "CALIBRATION WARNING"
        for camera_id in ("cam-02", "cam-03", "cam-04"):
            assert states[camera_id].health == "OFFLINE"
            assert states[camera_id].visible_count is None
            assert states[camera_id].unavailable_reason == "Camera disabled in configuration"
        assert [camera_id for camera_id, _ in adapter.started] == ["cam-01"]
        run_id, status, perception_active = supervisor.current_run()
        assert run_id and status == "RUNNING" and perception_active
        assert repository.list_alerts(run_id=run_id) == []
        epoch = states["cam-01"].epoch
        now = time.monotonic()
        await adapter.emit(ObservationBatch(
            camera_id="cam-01", camera_epoch=epoch,
            observations=(Observation(key=key(camera="cam-01", epoch=epoch), timestamp=now),),
            received_at=now, source_fps=24, analytics_fps=8, dropped_frames=2,
        ))
        updated = supervisor.states()
        assert updated["cam-01"].visible_count == 1
        assert updated["cam-01"].source_fps == 24 and updated["cam-01"].dropped_frames == 2
        assert updated["cam-02"].visible_count is None
        await supervisor.stop()
        assert supervisor.states()["cam-01"].visible_count is None
        await supervisor.start()
        assert supervisor.states()["cam-01"].epoch != epoch
        await supervisor.reset()
        assert supervisor.current_run() == (None, "IDLE", False)
        assert repository.list_alerts() == []

    asyncio.run(lifecycle())


@requires_local_artifacts
def test_readiness_reports_actual_missing_sources_models_and_perception(config, repository, monkeypatch) -> None:
    collector = TelemetryCollector(config.runtime.telemetry)
    telemetry = TelemetryService(collector)
    environment = EnvironmentService(config, repository, UnavailableAdapter(), telemetry)
    ready, checks = environment.readiness()
    by_name = {check["name"]: check for check in checks}
    assert not ready
    assert by_name["configuration"]["ready"] and by_name["persistence"]["ready"]
    assert by_name["sources"]["ready"]
    assert by_name["models"]["ready"] and not by_name["perception"]["ready"]
    sources = environment.source_report()
    assert len(sources) == 4
    by_id = {source["id"]: source for source in sources}
    assert by_id["cam-01"]["available"] and by_id["cam-01"]["unavailable_reason"] is None
    for camera_id in ("cam-02", "cam-03", "cam-04"):
        assert not by_id[camera_id]["available"]
        assert by_id[camera_id]["unavailable_reason"] == "Camera is disabled"
    assert all("source" not in source or "data/demo" not in str(source) for source in sources)


def test_adapter_failure_redacts_source_and_uses_availability_name(tmp_path) -> None:
    import sqlite3

    directory = copy_configuration(tmp_path)
    secret_source = "rtsp://user:secret@camera.local/stream?token=secret"
    update_yaml(directory, "cameras", lambda document: document["cameras"][0].update(
        source_type="rtsp", source=secret_source,
    ))
    config = load_configuration(directory, PROJECT_ROOT)
    repository = SQLiteRepository(tmp_path / "redacted-runtime.db")
    repository.migrate()
    repository.seed_configuration(config)

    class FailingAdapter(SuppliedObservationAdapter):
        async def start_camera(self, camera, camera_epoch, sink, replay_mode="paced") -> None:
            raise RuntimeError(f"Could not initialize {camera.source}")

    supervisor = CameraRuntimeSupervisor(config, repository, FailingAdapter(), SequenceState())

    async def lifecycle() -> None:
        await supervisor.start()
        run_id, status, _ = supervisor.current_run()
        assert run_id and status == "DEGRADED"
        alert = next(
            alert for alert in repository.list_alerts(run_id=run_id)
            if alert["category"] == "camera" and alert["scope_id"] == "cam-01"
        )
        assert secret_source not in alert["message"] and "secret" not in alert["message"]
        assert "RTSP camera.local" in alert["message"]
        assert "secret" not in supervisor.states()["cam-01"].unavailable_reason
        with sqlite3.connect(repository.database_path) as connection:
            adapter_name = connection.execute(
                "SELECT adapter_name FROM camera_sessions WHERE run_id = ? AND camera_id = 'cam-01'", (run_id,),
            ).fetchone()[0]
        assert adapter_name == "unit-supplied"
        await supervisor.shutdown()

    asyncio.run(lifecycle())


class CleanupFailingAdapter(SuppliedObservationAdapter):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    async def stop_camera(self, camera_id: str, camera_epoch: str) -> None:
        from backend.app.perception.base import PerceptionCleanupError
        if self.failures > 0:
            self.failures -= 1
            raise PerceptionCleanupError(f"Container aegis-{camera_id}-{camera_epoch} was not confirmed removed")
        await super().stop_camera(camera_id, camera_epoch)


def test_unconfirmed_cleanup_is_stop_failed_not_stopped_and_retry_recovers(tmp_path) -> None:
    from backend.app.perception.base import PerceptionCleanupError
    import pytest

    directory = copy_configuration(tmp_path)
    clip = tmp_path / "cam01.mp4"
    clip.write_bytes(b"unit-test-video-fixture")
    update_yaml(directory, "cameras", lambda document: document["cameras"][0].update(source=str(clip)))
    config = load_configuration(directory, PROJECT_ROOT)
    repository = SQLiteRepository(tmp_path / "runtime.db")
    repository.migrate()
    repository.seed_configuration(config)
    adapter = CleanupFailingAdapter(failures=2)  # initial stop + the retry inside reset() both fail
    supervisor = CameraRuntimeSupervisor(config, repository, adapter, SequenceState())

    async def lifecycle() -> None:
        await supervisor.start()
        run_id, _, _ = supervisor.current_run()
        epoch = supervisor.states()["cam-01"].epoch
        await supervisor.stop()
        assert supervisor.current_run()[1] == "STOP_FAILED"
        assert repository.get_run(run_id)["status"] == "STOP_FAILED"
        assert "cam-01" in supervisor.cleanup_failures()
        state = supervisor.states()["cam-01"]
        assert state.health == "OFFLINE" and state.visible_count is None
        assert "Stop not confirmed" in state.unavailable_reason
        alerts = repository.list_alerts(run_id=run_id)
        cleanup_alerts = [a for a in (alerts["items"] if isinstance(alerts, dict) else alerts) if a["stable_key"] == "camera-cleanup-failed:cam-01"]
        assert cleanup_alerts and cleanup_alerts[0]["status"] != "RESOLVED"
        # start() must not silently launch a new epoch over an unconfirmed container.
        with pytest.raises(PerceptionCleanupError, match="STOP_FAILED"):
            await supervisor.start()
        assert adapter.started == [("cam-01", epoch)]
        with pytest.raises(PerceptionCleanupError):
            await supervisor.reset()
        # A retry that confirms release completes the stop and resolves the alert.
        await supervisor.stop()
        assert supervisor.current_run()[1] == "STOPPED"
        assert repository.get_run(run_id)["status"] == "STOPPED"
        assert supervisor.cleanup_failures() == {}
        assert adapter.stopped == [("cam-01", epoch)]
        resolved = repository.get_alert(cleanup_alerts[0]["id"])
        assert resolved["status"] == "RESOLVED"

    asyncio.run(lifecycle())


def test_blocking_availability_probe_never_runs_on_the_event_loop(tmp_path) -> None:
    import threading

    from backend.app.services.snapshots import SnapshotService
    from backend.app.services.telemetry import TelemetryService

    directory = copy_configuration(tmp_path)
    clip = tmp_path / "cam01.mp4"
    clip.write_bytes(b"unit-test-video-fixture")
    update_yaml(directory, "cameras", lambda document: document["cameras"][0].update(source=str(clip)))
    config = load_configuration(directory, PROJECT_ROOT)
    repository = SQLiteRepository(tmp_path / "probe.db")
    repository.migrate()
    repository.seed_configuration(config)

    class SlowProbeAdapter(SuppliedObservationAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.probe_threads: list[int] = []
            self._last = None

        def availability(self):
            # Stands in for the real ~16 s of container probes.
            self.probe_threads.append(threading.get_ident())
            time.sleep(0.2)
            self._last = super().availability()
            return self._last

        def cached_availability(self):
            return self._last

    adapter = SlowProbeAdapter()
    sequence = SequenceState()
    supervisor = CameraRuntimeSupervisor(config, repository, adapter, sequence)
    telemetry = TelemetryService(TelemetryCollector(config.runtime.telemetry))
    snapshots = SnapshotService(config, repository, supervisor, telemetry, sequence)

    async def lifecycle() -> None:
        loop_thread = threading.get_ident()
        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        await supervisor.start()
        beat.cancel()
        assert adapter.probe_threads and loop_thread not in adapter.probe_threads
        assert ticks >= 5  # the loop kept running while the probe blocked its worker thread
        probes = len(adapter.probe_threads)
        snapshots.snapshot()
        assert len(adapter.probe_threads) == probes  # snapshots read the cached result only
        await supervisor.stop()

    asyncio.run(lifecycle())


def test_entries_exits_are_unknown_not_zero_without_a_calibrated_boundary(tmp_path) -> None:
    from backend.app.services.snapshots import SnapshotService
    from backend.app.services.telemetry import TelemetryService

    def build(calibration: str):
        directory = copy_configuration(tmp_path / calibration)
        clip = tmp_path / calibration / "cam01.mp4"
        clip.write_bytes(b"unit-test-video-fixture")
        ref = write_calibration(directory) if calibration == "VALID" else None
        update_yaml(directory, "cameras", lambda document: document["cameras"][0].update(
            source=str(clip), calibration_status=calibration, calibration_ref=ref,
            calibration_revision="rev-1" if calibration == "VALID" else None))
        config = load_configuration(directory, PROJECT_ROOT)
        repository = SQLiteRepository(tmp_path / calibration / "entries.db")
        repository.migrate()
        repository.seed_configuration(config)
        sequence = SequenceState()
        supervisor = CameraRuntimeSupervisor(config, repository, SuppliedObservationAdapter(), sequence)
        snapshots = SnapshotService(config, repository, supervisor, TelemetryService(TelemetryCollector(config.runtime.telemetry)), sequence)
        return supervisor, snapshots

    async def kpis(calibration: str):
        supervisor, snapshots = build(calibration)
        await supervisor.start()
        result = snapshots.snapshot()["kpis"]
        await supervisor.stop()
        return result

    uncalibrated = asyncio.run(kpis("MISSING"))
    assert uncalibrated["entries"] is None and uncalibrated["exits"] is None  # perception active, nothing measurable
    calibrated = asyncio.run(kpis("VALID"))
    assert calibrated["entries"] == 0 and calibrated["exits"] == 0  # measurable: zero crossings is a real value
