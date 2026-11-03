"""Calibration-gated facility analytics with controlled synthetic inputs (no real calibration implied)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from backend.app.analytics.facility import FacilityAnalytics
from backend.app.analytics.types import Observation, TrackKey
from backend.app.configuration import ConfigurationError, load_configuration
from backend.app.db.repository import SQLiteRepository
from backend.app.perception.base import ObservationBatch
from backend.app.services.runtime import CameraRuntimeSupervisor
from backend.app.services.sequence import SequenceState

from .support import PROJECT_ROOT, SuppliedObservationAdapter, copy_configuration, update_yaml, write_calibration

SX, SY = 1920 / 1000, 1080 / 600  # floor unit -> pixel for the synthetic scaling calibration


def calibrated_config(tmp_path: Path, cameras=("cam-01",), meters_per_unit: float | None = None, **calibration):
    directory = copy_configuration(tmp_path)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"unit-test-video-fixture")
    refs = {camera: write_calibration(directory, camera, **calibration) for camera in cameras}

    def cameras_update(document):
        for item in document["cameras"]:
            if item["id"] in refs:
                item.update(enabled=True, source=str(clip), calibration_status="VALID", calibration_ref=refs[item["id"]],
                            calibration_revision=calibration.get("revision", "rev-1"), floor_id="floor-1")
    update_yaml(directory, "cameras", cameras_update)
    if meters_per_unit:
        update_yaml(directory, "floors", lambda d: d["floors"][0].update(meters_per_unit=meters_per_unit))
    return directory, load_configuration(directory, PROJECT_ROOT)


def person(camera: str, epoch: str, track: str, floor_x: float, floor_y: float, t: float) -> Observation:
    return Observation(key=TrackKey(camera, epoch, track), timestamp=t,
                       metadata={"image_bottom_center_pixels": [floor_x * SX, floor_y * SY]})


def batch(camera, epoch, t, observations, frame=0, size=(1920, 1080)) -> ObservationBatch:
    return ObservationBatch(camera_id=camera, camera_epoch=epoch, observations=tuple(observations),
                            received_at=time.monotonic(), source_fps=30.0, analytics_fps=30.0, frame_age_ms=None,
                            frame_age_basis="unit", dropped_frames=0,
                            metadata={"frame_width": size[0], "frame_height": size[1]},
                            source_time=t, source_pts_ns=int(t * 1e9), source_segment=0, frame_number=frame)


def supervisor_for(config, tmp_path):
    repository = SQLiteRepository(tmp_path / "facility.db")
    repository.migrate()
    repository.seed_configuration(config)
    adapter = SuppliedObservationAdapter()
    return CameraRuntimeSupervisor(config, repository, adapter, SequenceState()), repository, adapter


# ------------------------------------------------------------------ configuration


def test_valid_calibration_requires_a_matching_validating_file(tmp_path: Path) -> None:
    directory = copy_configuration(tmp_path)
    update_yaml(directory, "cameras", lambda d: d["cameras"][0].update(calibration_status="VALID", calibration_revision="rev-1"))
    with pytest.raises(ConfigurationError, match="no calibration_ref"):
        load_configuration(directory, PROJECT_ROOT)
    ref = write_calibration(directory, revision="rev-2")
    update_yaml(directory, "cameras", lambda d: d["cameras"][0].update(calibration_ref=ref))
    with pytest.raises(ConfigurationError, match="revision"):
        load_configuration(directory, PROJECT_ROOT)
    ref = write_calibration(directory, revision="rev-1", error=5.0)  # held-out points disagree by 5 units
    update_yaml(directory, "cameras", lambda d: d["cameras"][0].update(calibration_ref=ref))
    with pytest.raises(ConfigurationError, match="validation error"):
        load_configuration(directory, PROJECT_ROOT)


def test_calibration_projection_rejects_points_off_the_floor(tmp_path: Path) -> None:
    _, config = calibrated_config(tmp_path)
    facility = FacilityAnalytics(config)
    facility.reset_camera("cam-01")
    facility.observe_frame_size("cam-01", 1920, 1080)
    inside = person("cam-01", "e", "1", 100, 100, 0)
    outside = Observation(key=TrackKey("cam-01", "e", "2"), timestamp=0, metadata={"image_bottom_center_pixels": [-500, 50]})
    projected = facility.project("cam-01", (inside, outside), 0.0)
    assert [p.key.local_track_id for p in projected] == ["1"]
    assert projected[0].position == pytest.approx((100, 100))


# ------------------------------------------------------------------ runtime


def test_zone_occupancy_capabilities_and_resolution_invalidation(tmp_path: Path) -> None:
    _, config = calibrated_config(tmp_path)
    supervisor, repository, adapter = supervisor_for(config, tmp_path)

    async def scenario() -> None:
        await supervisor.start()
        epoch = supervisor.states()["cam-01"].epoch
        run_id = supervisor.current_run()[0]
        people = [person("cam-01", epoch, str(i), 60 + 20 * i, 100, 0.0) for i in range(5)]  # entrance-f1 interior
        people.append(person("cam-01", epoch, "far", 600, 400, 0.0))  # outside every cam-01 zone
        await adapter.emit(batch("cam-01", epoch, 0.0, people))
        zones = {row["scope_id"]: row for row in repository.latest_analytics(run_id, "occupancy") if row["scope_type"] == "zone"}
        assert zones["entrance-f1"]["visible"] == 5 and zones["entrance-f1"]["quality"] == "OBSERVED"
        capabilities = supervisor.capabilities()
        assert capabilities["cameras"]["cam-01"]["floor_positions"]["available"]
        assert capabilities["zones"]["entrance-f1"]["occupancy"]["available"]
        assert "not running: cam-02" in capabilities["zones"]["queue-f1"]["occupancy"]["reason"]
        await supervisor.stop()

        # A new epoch whose frames are 1280x720: the calibration was measured on 1920x1080 -> invalid.
        await supervisor.start()
        epoch = supervisor.states()["cam-01"].epoch
        run_id = supervisor.current_run()[0]
        people = [person("cam-01", epoch, str(i), 60 + 20 * i, 100, 0.0) for i in range(6)]
        await adapter.emit(batch("cam-01", epoch, 0.0, people, size=(1280, 720)))
        capability = supervisor.capabilities()["cameras"]["cam-01"]["floor_positions"]
        assert not capability["available"] and "resolution/crop/zoom changed" in capability["reason"]
        assert repository.latest_analytics(run_id, "occupancy") == []  # nothing measured, nothing persisted as 0
        alerts = [a["stable_key"] for a in repository.list_alerts(run_id=run_id)]
        assert any(key.startswith(f"geometry-invalid:cam-01:{epoch}") for key in alerts)
        assert supervisor.states()["cam-01"].visible_count == 6  # camera-local count is preserved
        await supervisor.stop()

    asyncio.run(scenario())


def test_image_line_on_a_different_frame_size_is_disabled(tmp_path: Path) -> None:
    directory = copy_configuration(tmp_path)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"unit-test-video-fixture")
    update_yaml(directory, "cameras", lambda d: d["cameras"][0].update(source=str(clip)))
    update_yaml(directory, "analytics", lambda d: d["boundaries"].append({
        "id": "door-line", "camera_id": "cam-01", "start": [100, 500], "end": [1800, 500], "positive_direction": "IN",
        "owner": "cam-01", "coordinate_space": "image_pixels", "frame_size": [1920, 1080]}))
    config = load_configuration(directory, PROJECT_ROOT)
    supervisor, repository, adapter = supervisor_for(config, tmp_path)

    async def scenario() -> None:
        await supervisor.start()
        epoch = supervisor.states()["cam-01"].epoch
        await adapter.emit(batch("cam-01", epoch, 0.0, [], size=(1280, 720)))
        lines = supervisor.capabilities()["cameras"]["cam-01"]["image_line_crossings"]
        assert not lines["available"] and "drawn on 1920x1080" in lines["reason"]
        assert supervisor._analytics[("cam-01", epoch)].image_lines == ()
        await supervisor.stop()

    asyncio.run(scenario())


def test_floor_boundary_crossing_feeds_entries_and_the_ledger(tmp_path: Path) -> None:
    _, config = calibrated_config(tmp_path)
    supervisor, repository, adapter = supervisor_for(config, tmp_path)

    async def scenario() -> None:
        await supervisor.start()
        epoch = supervisor.states()["cam-01"].epoch
        run_id = supervisor.current_run()[0]
        for step in range(21):  # walks across north-entry-line (y = 180) between x 65..250 over 2 s
            t = step * 0.1
            await adapter.emit(batch("cam-01", epoch, t, [person("cam-01", epoch, "7", 150, 140 + 4 * step, t)], frame=step))
        assert repository.count_crossings(run_id) == 1
        building = [r for r in repository.latest_analytics(run_id, "occupancy") if r["scope_type"] == "building"]
        assert building and building[0]["expected"] in (1, -1)  # one entry or one exit, by line orientation
        assert building[0]["quality"] == "ESTIMATED"  # boundary coverage is not declared complete
        await supervisor.stop()

    asyncio.run(scenario())


def test_congestion_needs_persistence_and_recovers_with_hysteresis(tmp_path: Path) -> None:
    _, config = calibrated_config(tmp_path)
    supervisor, repository, adapter = supervisor_for(config, tmp_path)

    async def scenario() -> None:
        await supervisor.start()
        epoch = supervisor.states()["cam-01"].epoch
        run_id = supervisor.current_run()[0]
        crowd = lambda t: [person("cam-01", epoch, str(i), 40 + (i % 20) * 12, 50 + (i // 20) * 60, t) for i in range(45)]
        t, frame = 0.0, 0
        while t < 4.5:  # above the CONGESTED threshold (40) but for less than persist_seconds (5)
            await adapter.emit(batch("cam-01", epoch, t, crowd(t), frame=frame))
            t, frame = t + 0.5, frame + 1
        keys = [a["stable_key"] for a in repository.list_alerts(run_id=run_id)]
        assert "congestion:entrance-f1" not in keys
        while t < 7.0:
            await adapter.emit(batch("cam-01", epoch, t, crowd(t), frame=frame))
            t, frame = t + 0.5, frame + 1
        alert = next(a for a in repository.list_alerts(run_id=run_id) if a["stable_key"] == "congestion:entrance-f1")
        assert alert["status"] == "OPEN" and alert["measurements"]["occupancy"] == 45.0
        stop_at = t + 9.0
        while t < stop_at:  # empty zone for longer than recover_seconds (8)
            await adapter.emit(batch("cam-01", epoch, t, [], frame=frame))
            t, frame = t + 0.5, frame + 1
        alert = next(a for a in repository.list_alerts(run_id=run_id) if a["stable_key"] == "congestion:entrance-f1")
        assert alert["status"] == "RESOLVED"
        await supervisor.stop()

    asyncio.run(scenario())


def test_overlap_fusion_counts_one_person_seen_by_two_cameras(tmp_path: Path) -> None:
    _, config = calibrated_config(tmp_path, cameras=("cam-02", "cam-03"))
    facility = FacilityAnalytics(config)
    for camera in ("cam-02", "cam-03"):
        facility.reset_camera(camera)
        facility.observe_frame_size(camera, 1920, 1080)
    zone = config.zone_map["dining-f1"]
    same = [person("cam-02", "e2", "1", 500, 400, 1.0), person("cam-03", "e3", "9", 500.2, 400.1, 1.0)]
    facility.update("cam-02", 1.0, (same[0],))
    facility.update("cam-03", 1.0, (same[1],))
    unscaled = facility.zone_result(zone)
    assert unscaled.visible is None and "meters_per_unit" in unscaled.reason  # no metric scale, no fusion

    _, scaled = calibrated_config(tmp_path / "scaled", cameras=("cam-02", "cam-03"), meters_per_unit=0.05)
    facility = FacilityAnalytics(scaled)
    for camera in ("cam-02", "cam-03"):
        facility.reset_camera(camera)
        facility.observe_frame_size(camera, 1920, 1080)
    facility.update("cam-02", 1.0, (same[0], person("cam-02", "e2", "2", 800, 500, 1.0)))
    facility.update("cam-03", 1.0, (same[1],))
    result = facility.zone_result(scaled.zone_map["dining-f1"])
    assert result.visible == 2  # the shared person is deduplicated; the second person is counted once


def test_fit_calibration_recovers_a_known_homography_and_validates(tmp_path: Path) -> None:
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "config"))
    from fit_calibration import fit_homography
    from backend.app.analytics.calibration import apply_homography

    truth = ((0.5, 0.1, 20.0), (0.02, 0.6, 10.0), (0.0001, 0.0002, 1.0))  # a perspective homography
    pixels = [(100, 900), (1800, 950), (1700, 300), (200, 250), (960, 600), (500, 400)]
    points = [[u, v, *apply_homography(truth, u, v)] for u, v in pixels]
    fitted = fit_homography(points)
    for u, v, x, y in points:
        assert apply_homography(tuple(map(tuple, fitted)), u, v) == pytest.approx((x, y), abs=1e-6)
    with pytest.raises(ValueError, match="degenerate"):
        fit_homography([[i, i, i, i] for i in range(5)])
