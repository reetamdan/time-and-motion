from __future__ import annotations

import asyncio
import queue
import time
from pathlib import Path

import pytest

from backend.app.analytics.camera_local import CameraLocalAnalytics, ImageLine
from backend.app.analytics.crossing import CrossingPolicy
from backend.app.analytics.timebase import ReplayPacer, SourceTime, SourceTimeline
from backend.app.analytics.types import Observation, TrackKey
from backend.app.configuration import load_configuration
from backend.app.db.repository import SQLiteRepository
from backend.app.perception.base import ObservationBatch
from backend.app.perception.service_maker import ServiceMakerSessions, _Session
from backend.app.services.runtime import CameraRuntimeSupervisor
from backend.app.services.sequence import SequenceState

from .support import PROJECT_ROOT, SuppliedObservationAdapter, copy_configuration, update_yaml

FPS = 30.0
LINE = ImageLine("test-line", (0.0, 500.0), (1920.0, 500.0), "IN", "cam-01", (1920, 1080),
                 CrossingPolicy(dead_band=8, debounce_seconds=2, confirmation_seconds=0.15, min_track_seconds=0.4,
                                min_track_observations=3, min_displacement=2, stale_after_ms=1500))


# ---------------------------------------------------------------- timeline / pacer

def test_source_timeline_segments_on_backward_and_forward_jumps() -> None:
    timeline = SourceTimeline(max_forward_gap_seconds=2.0)
    assert timeline.observe(0).segment == 0
    assert timeline.observe(33_366_667).discontinuity is None
    backward = timeline.observe(10_000_000)
    assert (backward.segment, backward.discontinuity) == (1, "BACKWARD_OR_REPEATED_PTS")
    repeated = timeline.observe(10_000_000)
    assert repeated.segment == 2
    jump = timeline.observe(10_000_000 + 3_000_000_000)
    assert (jump.segment, jump.discontinuity) == (3, "FORWARD_JUMP")
    with pytest.raises(ValueError):
        timeline.observe(-1)


def test_pacer_follows_source_time_and_reanchors_per_segment() -> None:
    paced = ReplayPacer("paced")
    assert paced.delay(SourceTime(10.0, 10_000_000_000, 0), host_now=100.0) == 0.0
    assert paced.delay(SourceTime(11.0, 11_000_000_000, 0), host_now=100.2) == pytest.approx(0.8)
    assert paced.delay(SourceTime(11.0, 11_000_000_000, 0), host_now=101.5) == 0.0  # late frames are not delayed
    # New segment: no wait derived from the old anchor (no huge/negative waits across a discontinuity).
    assert paced.delay(SourceTime(0.5, 500_000_000, 1), host_now=102.0) == 0.0
    assert paced.delay(SourceTime(1.0, 1_000_000_000, 1), host_now=102.0) == pytest.approx(0.5)
    unpaced = ReplayPacer("unpaced")
    assert unpaced.delay(SourceTime(0.0, 0, 0), 0.0) == 0.0
    assert unpaced.delay(SourceTime(99.0, 99_000_000_000, 0), 0.0) == 0.0
    with pytest.raises(ValueError):
        ReplayPacer("live")  # type: ignore[arg-type]


# ---------------------------------------------------------------- deterministic fixture

def walker_frames(epoch: str, frames: int = 120, pts_offset_ns: int = 0):
    """Source-time frames: track 7 walks down across y=500, track 8 stays above it."""
    result = []
    for n in range(frames):
        t = pts_offset_ns / 1e9 + n / FPS
        observations = (
            Observation(TrackKey("cam-01", epoch, "7"), t,
                        metadata={"image_bottom_center_pixels": [900.0, 300.0 + n * 4.0]}),
            Observation(TrackKey("cam-01", epoch, "8"), t,
                        metadata={"image_bottom_center_pixels": [400.0, 200.0]}),
        )
        result.append((n, int(round(t * 1e9)), t, observations))
    return result


def make_supervisor(tmp_path: Path, with_line: bool = True):
    directory = copy_configuration(tmp_path)
    clip = tmp_path / "cam01.mp4"
    clip.write_bytes(b"unit-test-video-fixture")
    update_yaml(directory, "cameras", lambda document: document["cameras"][0].update(source=str(clip)))
    if with_line:
        update_yaml(directory, "analytics", lambda document: document["boundaries"].append({
            "id": "test-line", "camera_id": "cam-01", "start": [0, 500], "end": [1920, 500],
            "positive_direction": "IN", "owner": "cam-01", "enabled": True,
            "coordinate_space": "image_pixels", "frame_size": [1920, 1080], "purpose": "unit test",
        }))
    config = load_configuration(directory, PROJECT_ROOT)
    repository = SQLiteRepository(tmp_path / f"time-{with_line}.db")
    repository.migrate()
    repository.seed_configuration(config)
    adapter = SuppliedObservationAdapter()
    return CameraRuntimeSupervisor(config, repository, adapter, SequenceState()), repository, adapter


async def feed(adapter, epoch, frames, *, wall_step: float, discontinuity_at: int | None = None,
               start_monotonic: float | None = None) -> None:
    received = time.monotonic() if start_monotonic is None else start_monotonic
    timeline = SourceTimeline()
    for n, pts_ns, t, observations in frames:
        source = timeline.observe(pts_ns)
        await adapter.emit(ObservationBatch(
            camera_id="cam-01", camera_epoch=epoch, observations=observations, received_at=received,
            source_time=source.seconds, source_pts_ns=pts_ns, source_segment=source.segment,
            source_discontinuity=source.discontinuity, frame_number=n, received_at_utc="2026-10-26T00:00:00.000Z",
            replay_mode="unit",
        ))
        received += wall_step


def persisted(repository, run_id, epoch):
    strip = lambda rows, drop: [{k: v for k, v in row.items() if k not in drop} for row in rows]  # noqa: E731
    return (strip(repository.camera_count_history(run_id, "cam-01", epoch), {"received_at_utc", "camera_epoch"}),
            strip(repository.camera_crossings(run_id, "cam-01", epoch), {"received_at_utc", "event_id", "track_key"}))


# ---------------------------------------------------------------- wall-clock independence

def test_same_source_observations_at_different_wall_speeds_give_identical_analytics(tmp_path: Path) -> None:
    results = []
    for label, wall_step in (("fast", 0.0001), ("realtime", 1 / FPS), ("slow", 0.5)):
        supervisor, repository, adapter = make_supervisor(tmp_path / label)

        async def run():
            await supervisor.start()
            run_id = supervisor.current_run()[0]
            epoch = supervisor.states()["cam-01"].epoch
            # received_at only drives freshness of the displayed state; analytics must not depend on it.
            await feed(adapter, epoch, walker_frames(epoch), wall_step=wall_step)
            await supervisor.stop()
            return persisted(repository, run_id, epoch)

        results.append(asyncio.run(run()))
    samples, crossings = results[0]
    assert len(samples) == 8  # 120 frames = 4.0 s of source time at 0.5 s buckets
    assert [round(s["source_time_s"], 6) for s in samples] == [round(i * 0.5, 6) for i in range(8)]
    assert all(s["visible_count"] == 2 and s["frames"] == 15 for s in samples)
    assert len(crossings) == 1 and crossings[0]["direction"] in ("IN", "OUT")
    assert results[1] == results[0] and results[2] == results[0]


def test_replaying_identical_frames_is_idempotent(tmp_path: Path) -> None:
    supervisor, repository, adapter = make_supervisor(tmp_path)

    async def run():
        await supervisor.start()
        run_id = supervisor.current_run()[0]
        epoch = supervisor.states()["cam-01"].epoch
        frames = walker_frames(epoch)
        await feed(adapter, epoch, frames, wall_step=0.001)
        first = persisted(repository, run_id, epoch)
        # Re-deliver the same source frames into the same epoch (e.g. transport retry): no duplicates.
        supervisor._analytics[("cam-01", epoch)] = CameraLocalAnalytics("cam-01", epoch, 0.5, (LINE,))
        await feed(adapter, epoch, frames, wall_step=0.001)
        await supervisor.stop()
        return first, persisted(repository, run_id, epoch)

    first, second = asyncio.run(run())
    assert first[1] and second[1] == first[1]
    assert second[0][: len(first[0])] == first[0]
    assert len(second[0]) == len(first[0]) + 1  # only the final bucket, flushed at stop, is new


def test_new_epoch_has_independent_history_and_track_identity(tmp_path: Path) -> None:
    supervisor, repository, adapter = make_supervisor(tmp_path)

    async def run():
        await supervisor.start()
        run1 = supervisor.current_run()[0]
        epoch1 = supervisor.states()["cam-01"].epoch
        await feed(adapter, epoch1, walker_frames(epoch1, 30), wall_step=0.001)
        await supervisor.stop()
        await supervisor.start()
        run2 = supervisor.current_run()[0]
        epoch2 = supervisor.states()["cam-01"].epoch
        # Same numeric tracker IDs and the same PTS values restart in the new epoch.
        await feed(adapter, epoch2, walker_frames(epoch2, 30), wall_step=0.001)
        await supervisor.stop()
        return run1, epoch1, run2, epoch2

    run1, epoch1, run2, epoch2 = asyncio.run(run())
    assert epoch1 != epoch2
    h1 = repository.camera_count_history(run1, "cam-01", epoch1)
    h2 = repository.camera_count_history(run2, "cam-01", epoch2)
    assert h1 and h2 and {r["camera_epoch"] for r in h1} == {epoch1} and {r["camera_epoch"] for r in h2} == {epoch2}
    assert repository.camera_count_history(run2, "cam-01", epoch1) == []


def test_discontinuity_resets_state_so_no_crossing_spans_it(tmp_path: Path) -> None:
    supervisor, repository, adapter = make_supervisor(tmp_path)
    epoch_holder = {}

    async def run():
        await supervisor.start()
        run_id = supervisor.current_run()[0]
        epoch = supervisor.states()["cam-01"].epoch
        epoch_holder["epoch"] = epoch
        # Track 7 approaches the line (y 300..476) then the PTS jumps back (a source reset); after the
        # jump it appears below the line. Bridging the discontinuity would fabricate a crossing.
        before = [(n, int(n / FPS * 1e9), n / FPS,
                   (Observation(TrackKey("cam-01", epoch, "7"), n / FPS, metadata={"image_bottom_center_pixels": [900.0, 300.0 + n * 4.0]}),))
                  for n in range(45)]
        after = [(45 + n, int(n / FPS * 1e9), n / FPS,
                  (Observation(TrackKey("cam-01", epoch, "7"), n / FPS, metadata={"image_bottom_center_pixels": [900.0, 700.0 + n]}),))
                 for n in range(45)]
        await feed(adapter, epoch, before + after, wall_step=0.001)
        state = supervisor.states()["cam-01"]
        await supervisor.stop()
        return run_id, epoch, state

    run_id, epoch, state = asyncio.run(run())
    assert state.source_discontinuities == 1 and state.source_segment == 1
    samples, crossings = persisted(repository, run_id, epoch)
    assert crossings == []
    assert {s["source_segment"] for s in samples} == {0, 1}
    alerts = repository.list_alerts(run_id=run_id)
    assert any(a["title"] == "Source timestamp discontinuity" for a in alerts)


def test_unconfirmed_tracks_are_not_counted_or_crossed(tmp_path: Path) -> None:
    analytics = CameraLocalAnalytics("cam-01", "e", 0.5, (LINE,))
    crossings = []
    for n in range(120):
        t = n / FPS
        shadow = Observation(TrackKey("cam-01", "e", "9"), t, confirmed=False, tracking_state="SHADOW",
                             metadata={"image_bottom_center_pixels": [900.0, 300.0 + n * 4.0]})
        result = analytics.process_frame(t, 0, (shadow,), n)
        crossings += result.crossings
        assert all(s.visible_count == 0 for s in result.samples)
    assert crossings == []


def test_stale_track_gap_cannot_complete_a_crossing() -> None:
    analytics = CameraLocalAnalytics("cam-01", "e", 0.5, (LINE,))
    key = TrackKey("cam-01", "e", "7")
    events = []
    for n in range(20):  # above the line
        t = n / FPS
        events += analytics.process_frame(t, 0, (Observation(key, t, metadata={"image_bottom_center_pixels": [900.0, 400.0]}),), n).crossings
    # 3 s of source time without the track (> 1.5 s stale window), then it reappears below the line.
    for n in range(20, 110):
        events += analytics.process_frame(n / FPS, 0, (), n).crossings
    for n in range(110, 140):
        t = n / FPS
        events += analytics.process_frame(t, 0, (Observation(key, t, metadata={"image_bottom_center_pixels": [900.0, 650.0]}),), n).crossings
    assert events == []


# ---------------------------------------------------------------- real consumer loop pacing


class ExitedProcess:
    """An extractor process that already exited with the given code."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode


def test_consumer_paces_on_source_time_only_in_paced_mode(tmp_path: Path) -> None:
    config = load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT)
    camera = config.camera_map["cam-01"]

    class Sink:
        def __init__(self):
            self.batches = []

        async def emit(self, batch):
            self.batches.append(batch)

    def records(n_frames: int):
        return [{"frame_number": n, "pts_ns": int(n / FPS * 1e9), "objects": [], "timestamp": n / FPS,
                 "frame_width": 1920, "frame_height": 1080} for n in range(n_frames)]

    async def consume(mode: str):
        manager = ServiceMakerSessions(config)
        session = _Session("cam-01", f"epoch-{mode}", "aegis-x", ExitedProcess(), tmp_path / "x.jsonl", None, asyncio.Event(),  # type: ignore[arg-type]
                           frame_queue=queue.Queue(), replay_mode=mode)
        for record in records(16):  # 0.5 s of source time
            session.frame_queue.put(record)
        session.reader_done.set()
        sink = Sink()
        started = time.perf_counter()
        await manager._consume(session, camera, sink)
        return time.perf_counter() - started, sink.batches

    paced_seconds, paced = asyncio.run(consume("paced"))
    unpaced_seconds, unpaced = asyncio.run(consume("unpaced"))
    assert paced_seconds >= 0.45  # 15 frame intervals of 1/30 s
    assert unpaced_seconds < 0.3
    assert [b.source_time for b in paced] == [b.source_time for b in unpaced]
    assert {b.replay_mode for b in paced} == {"paced"} and {b.replay_mode for b in unpaced} == {"unpaced"}
    assert all(b.received_at_utc and b.source_segment == 0 for b in paced)


# ---------------------------------------------------------------- API

def test_count_history_and_crossings_api(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from backend.app.main import create_app

    app = create_app(database_path=tmp_path / "api.db")
    with TestClient(app) as client:
        services = app.state.services
        run = services.repository.create_run(services.config.configuration_revision)
        services.repository.start_camera_session(run["id"], "cam-01", "epoch-api", "SOURCE_AVAILABLE", "unit")
        services.repository.record_count_samples(run["id"], [{
            "camera_id": "cam-01", "camera_epoch": "epoch-api", "source_segment": 0, "bucket_index": 3,
            "source_time_s": 1.5, "frame_number": 45, "visible_count": 4, "visible_mean": 3.5, "visible_max": 5,
            "frames": 15, "replay_mode": "paced", "received_at_utc": "2026-10-26T00:00:01.000Z", "count_basis": "unit",
        }])
        body = client.get("/api/v1/cameras/cam-01/count-history").json()
        assert body["camera_epoch"] == "epoch-api" and body["samples"][0]["visible_count"] == 4
        assert "source PTS" in body["time_basis"] and "not UTC capture" in body["time_basis"]
        crossings = client.get("/api/v1/cameras/cam-01/line-crossings").json()
        assert crossings["crossings"] == [] and crossings["unavailable_reason"] == "No image_pixels line is configured for this camera"
        assert client.get("/api/v1/cameras/nope/count-history").status_code == 404
        assert client.post("/api/v1/demo/start?replay_mode=live").status_code == 422


def test_controls_the_same_motion_without_gap_or_reset_does_cross() -> None:
    # Positive control for the stale-gap test: continuous observations crossing y=500 produce one event.
    analytics = CameraLocalAnalytics("cam-01", "e", 0.5, (LINE,))
    key = TrackKey("cam-01", "e", "7")
    events = []
    for n in range(60):
        t = n / FPS
        y = 400.0 if n < 20 else 650.0 if n >= 30 else 400.0 + (n - 20) * 25.0
        events += analytics.process_frame(t, 0, (Observation(key, t, metadata={"image_bottom_center_pixels": [900.0, y]}),), n).crossings
    assert len(events) == 1
    # Positive control for the discontinuity test: same positions, continuous PTS (one segment).
    analytics = CameraLocalAnalytics("cam-01", "e", 0.5, (LINE,))
    events = []
    positions = [300.0 + n * 4.0 for n in range(45)] + [700.0 + n for n in range(45)]
    for n, y in enumerate(positions):
        t = n / FPS
        events += analytics.process_frame(t, 0, (Observation(key, t, metadata={"image_bottom_center_pixels": [900.0, y]}),), n).crossings
    assert len(events) == 1
