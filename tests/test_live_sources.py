"""Live (RTSP) sources: configuration-only switch, credential handling, loss/reconnect with bounded backoff.

Controlled tests with a supplied-observation adapter; the real RTSP path is exercised separately by
scripts/validate_rtsp_source.py against a local controlled RTSP server.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from backend.app.configuration import load_configuration
from backend.app.db.repository import SQLiteRepository
from backend.app.perception import service_maker
from backend.app.perception.base import ObservationBatch, PerceptionUnavailable
from backend.app.perception.service_maker import ServiceMakerSessions, redact, resolve_rtsp_source
from backend.app.services.runtime import CameraRuntimeSupervisor
from backend.app.services.sequence import SequenceState

from .support import PROJECT_ROOT, SuppliedObservationAdapter, copy_configuration, update_yaml

URL = "rtsp://${AEGIS_TEST_RTSP_USER}:${AEGIS_TEST_RTSP_PASS}@127.0.0.1:8554/cam01"
ENV = {"AEGIS_TEST_RTSP_USER": "operator", "AEGIS_TEST_RTSP_PASS": "s3cr3t-pass"}


def test_credentials_resolve_at_launch_and_are_redactable() -> None:
    url, secrets = resolve_rtsp_source(URL, ENV)
    assert url == "rtsp://operator:s3cr3t-pass@127.0.0.1:8554/cam01"
    message = f"rtspsrc: could not open {url} (user operator:s3cr3t-pass)"
    cleaned = redact(message, secrets)
    assert "s3cr3t-pass" not in cleaned and "[redacted]" in cleaned
    with pytest.raises(PerceptionUnavailable, match="AEGIS_TEST_RTSP_PASS") as error:
        resolve_rtsp_source(URL, {"AEGIS_TEST_RTSP_USER": "operator"})
    assert "operator" not in str(error.value)  # names the missing variable, never echoes values


def config_with_live_camera(tmp_path: Path, **health):
    directory = copy_configuration(tmp_path)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"unit-test-video-fixture")

    def cameras(document):
        document["cameras"][0].update(source=str(clip))
        document["cameras"][1].update(enabled=True, source_type="rtsp", source=URL)
    update_yaml(directory, "cameras", cameras)
    if health:
        update_yaml(directory, "analytics", lambda d: d["health"].update(**health))
    return load_configuration(directory, PROJECT_ROOT)


def test_live_command_uses_url_without_pacing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(service_maker, "host_route", lambda: "linux-docker")
    monkeypatch.setattr(service_maker, "runtime_prefix", lambda distro: ())
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    config = config_with_live_camera(tmp_path)
    manager = ServiceMakerSessions(config)
    command = manager._command(config.camera_map["cam-02"], "e", PROJECT_ROOT / "backend" / "var" / "o.jsonl",
                               PROJECT_ROOT / "backend" / "var" / "s.json", "paced")
    assert command[command.index("--source") + 1] == "rtsp://operator:s3cr3t-pass@127.0.0.1:8554/cam01"
    assert "--pace" not in command  # a live source is already real time
    assert config.camera_map["cam-02"].source_label == "RTSP 127.0.0.1"


def batch(camera: str, epoch: str, t: float) -> ObservationBatch:
    return ObservationBatch(camera_id=camera, camera_epoch=epoch, observations=(), received_at=time.monotonic(),
                            source_fps=30.0, analytics_fps=30.0, frame_age_ms=None, frame_age_basis="unit", dropped_frames=0,
                            metadata={"frame_width": 1920, "frame_height": 1080}, source_time=t, source_pts_ns=int(t * 1e9),
                            source_segment=0, frame_number=int(t * 30))


def supervisor_for(config, tmp_path, adapter):
    repository = SQLiteRepository(tmp_path / "live.db")
    repository.migrate()
    repository.seed_configuration(config)
    return CameraRuntimeSupervisor(config, repository, adapter, SequenceState()), repository


async def wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


def test_live_loss_reconnects_with_new_epoch_and_file_camera_unaffected(tmp_path: Path) -> None:
    config = config_with_live_camera(tmp_path, reconnect_backoff_seconds=0.05, max_reconnect_backoff_seconds=0.2)
    adapter = SuppliedObservationAdapter()
    supervisor, repository = supervisor_for(config, tmp_path, adapter)

    async def scenario() -> None:
        await supervisor.start()
        run_id = supervisor.current_run()[0]
        live, file_epoch = supervisor.states()["cam-02"].epoch, supervisor.states()["cam-01"].epoch
        await adapter.emit(batch("cam-02", live, 0.1))
        await adapter.emit(batch("cam-01", file_epoch, 0.1))
        supervisor.complete_stream("cam-02", live, "ABNORMAL_TERMINATION", "live source ended after 3 frames")
        state = supervisor.states()["cam-02"]
        assert state.health == "OFFLINE" and state.visible_count is None and state.reconnect_attempt == 1
        assert any(a["stable_key"] == "camera-reconnecting:cam-02" for a in repository.list_alerts(run_id=run_id))
        assert await wait_until(lambda: supervisor.states()["cam-02"].epoch not in (None, live))
        new_epoch = supervisor.states()["cam-02"].epoch
        assert ("cam-02", live) in adapter.stopped  # the lost epoch is released before the new one starts
        assert supervisor.states()["cam-01"].epoch == file_epoch  # the file camera never restarted
        await adapter.emit(batch("cam-02", new_epoch, 0.1))
        assert supervisor.states()["cam-02"].reconnect_attempt == 0
        alert = next(a for a in repository.list_alerts(run_id=run_id) if a["stable_key"] == "camera-reconnecting:cam-02")
        assert alert["status"] == "RESOLVED"
        await supervisor.stop()

    asyncio.run(scenario())


def test_failed_live_starts_back_off_with_a_cap_and_stop_cancels(tmp_path: Path) -> None:
    config = config_with_live_camera(tmp_path, reconnect_backoff_seconds=0.05, max_reconnect_backoff_seconds=0.1)

    class Unreachable(SuppliedObservationAdapter):
        async def start_camera(self, camera, camera_epoch, sink, replay_mode="paced") -> None:
            if camera.source_type == "rtsp":
                self.started.append((camera.id, camera_epoch))
                raise RuntimeError("rtsp server unreachable")
            await super().start_camera(camera, camera_epoch, sink, replay_mode)

    adapter = Unreachable()
    supervisor, repository = supervisor_for(config, tmp_path, adapter)

    async def scenario() -> None:
        await supervisor.start()
        assert await wait_until(lambda: sum(1 for c, _ in adapter.started if c == "cam-02") >= 4, 3)
        state = supervisor.states()["cam-02"]
        assert state.reconnect_attempt >= 3 and "reconnect attempt" in (state.unavailable_reason or "")
        alert = next(a for a in repository.list_alerts(run_id=supervisor.current_run()[0]) if a["stable_key"] == "camera-reconnecting:cam-02")
        assert alert["measurements"]["delay_seconds"] <= 0.1  # capped
        assert supervisor.states()["cam-01"].perception_started  # other cameras keep running
        await supervisor.stop()
        attempts = sum(1 for c, _ in adapter.started if c == "cam-02")
        await asyncio.sleep(0.3)
        assert sum(1 for c, _ in adapter.started if c == "cam-02") == attempts  # no reconnect after stop

    asyncio.run(scenario())


def test_stalled_live_camera_is_released_and_reconnected(tmp_path: Path) -> None:
    config = config_with_live_camera(tmp_path, reconnect_backoff_seconds=0.05, max_reconnect_backoff_seconds=0.1,
                                     live_stall_seconds=0.3)
    adapter = SuppliedObservationAdapter()
    supervisor, _ = supervisor_for(config, tmp_path, adapter)

    async def scenario() -> None:
        await supervisor.start()
        live = supervisor.states()["cam-02"].epoch
        await adapter.emit(batch("cam-02", live, 0.1))
        assert await wait_until(lambda: supervisor.states()["cam-02"].epoch not in (None, live), 4)
        assert ("cam-02", live) in adapter.stopped
        await supervisor.stop()

    asyncio.run(scenario())


def test_snapshot_reports_live_source_limits(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    config_with_live_camera(tmp_path)
    app = create_app(config_dir=tmp_path / "configs", database_path=tmp_path / "api.db", perception_adapter=SuppliedObservationAdapter(),
                     evidence_root=tmp_path / "evidence")
    with TestClient(app) as client:
        camera = next(c for c in client.get("/api/v1/facility/snapshot").json()["cameras"] if c["id"] == "cam-02")
        assert camera["preview_url"] is None and camera["evidence_available"] is False
        assert "recording buffer" in camera["evidence_unavailable_reason"]
        assert "s3cr3t" not in str(camera) and camera["source_label"] == "RTSP 127.0.0.1"
