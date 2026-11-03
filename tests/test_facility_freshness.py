"""Facility freshness, overlapping-zone aggregation, and stair-transition wiring (controlled synthetic inputs).

Calibrations are synthetic scaling fixtures; nothing here implies real calibration or footage.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from backend.app.analytics import facility as facility_module
from backend.app.analytics.facility import FacilityAnalytics
from backend.app.analytics.types import Observation, TrackKey
from backend.app.configuration import load_configuration
from backend.app.db.repository import SQLiteRepository
from backend.app.perception.base import ObservationBatch
from backend.app.services import runtime as runtime_module
from backend.app.services.runtime import CameraRuntimeSupervisor
from backend.app.services.sequence import SequenceState

from .support import PROJECT_ROOT, SuppliedObservationAdapter, copy_configuration, update_yaml, write_calibration

SX, SY = 1920 / 1000, 1080 / 600


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def zone(zone_id, floor, polygon, cameras, kind="dining", overlap=None):
    return {"id": zone_id, "floor_id": floor, "name": zone_id, "kind": kind, "polygon": polygon, "capacity": None,
            "area_m2": None, "authoritative_camera_ids": list(cameras), "overlap_group": overlap, "rule_ref": None}


def build(tmp_path: Path, cameras: dict[str, str], zones: list | None = None, keep_portals: bool = False,
          meters_per_unit: float | None = None, stale_after_ms: int | None = None):
    directory = copy_configuration(tmp_path)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"unit-test-video-fixture")
    refs = {camera: write_calibration(directory, camera, floor_id=floor) for camera, floor in cameras.items()}

    def cameras_update(document):
        for item in document["cameras"]:
            if item["id"] in refs:
                item.update(enabled=True, source=str(clip), calibration_status="VALID", calibration_ref=refs[item["id"]],
                            calibration_revision="rev-1", floor_id=cameras[item["id"]], replay_group="demo-session")
            else:
                item.update(enabled=False)
    update_yaml(directory, "cameras", cameras_update)
    if zones is not None:
        update_yaml(directory, "zones", lambda d: d.update(zones=zones))
    if not keep_portals:
        update_yaml(directory, "portals", lambda d: d.update(portals=[]))
    if meters_per_unit:
        update_yaml(directory, "floors", lambda d: [f.update(meters_per_unit=meters_per_unit) for f in d["floors"]])
    if stale_after_ms:
        update_yaml(directory, "analytics", lambda d: d["health"].update(stale_after_ms=stale_after_ms))
    return load_configuration(directory, PROJECT_ROOT)


def person(camera: str, track: str, x: float, y: float, t: float, epoch: str = "e") -> Observation:
    return Observation(key=TrackKey(camera, epoch, track), timestamp=t,
                       metadata={"image_bottom_center_pixels": [x * SX, y * SY]})


def started(config, clock, *cameras) -> FacilityAnalytics:
    facility = FacilityAnalytics(config, clock=clock)
    for camera in cameras:
        facility.reset_camera(camera, "e")
        facility.observe_frame_size(camera, 1920, 1080)
    return facility


RECT_A = [[100, 100], [500, 100], [500, 400], [100, 400]]
RECT_INNER = [[200, 150], [300, 150], [300, 250], [200, 250]]  # nested inside RECT_A
RECT_B = [[400, 100], [800, 100], [800, 400], [400, 400]]  # partially overlaps RECT_A (x 400..500)
FLOOR2 = [[100, 100], [900, 100], [900, 500], [100, 500]]


# ------------------------------------------------------------------ freshness


def test_ten_second_old_observation_is_unavailable_not_counted(tmp_path: Path) -> None:
    """Reviewer repro: last observation at 0 s, evaluation at 10 s, limit 1.5 s -> not count=1/OBSERVED."""
    config = build(tmp_path, {"cam-01": "floor-1"}, [zone("a", "floor-1", RECT_A, ["cam-01"])])
    clock = Clock()
    facility = started(config, clock, "cam-01")
    facility.update("cam-01", 0.0, (person("cam-01", "1", 150, 150, 0.0),), epoch="e", segment=0)
    fresh = facility.zone_result(config.zone_map["a"])
    assert (fresh.visible, fresh.quality) == (1, "OBSERVED")
    clock.now = 10.0
    stale = facility.zone_result(config.zone_map["a"])
    assert stale.visible is None and stale.quality == "UNAVAILABLE"
    assert "stale" in stale.reason and "10.0 s ago" in stale.reason and "1.5 s" in stale.reason
    result = facility.evaluate()
    assert result["floors"]["floor-1"].visible is None and result["building_visible"] is None


def test_recovery_after_stale_and_epoch_reset(tmp_path: Path) -> None:
    config = build(tmp_path, {"cam-01": "floor-1"}, [zone("a", "floor-1", RECT_A, ["cam-01"])])
    clock = Clock()
    facility = started(config, clock, "cam-01")
    facility.update("cam-01", 0.0, (person("cam-01", "1", 150, 150, 0.0),), epoch="e", segment=0)
    clock.now = 10.0
    assert facility.zone_result(config.zone_map["a"]).visible is None
    facility.update("cam-01", 10.0, (person("cam-01", "1", 150, 150, 10.0), person("cam-01", "2", 160, 160, 10.0)),
                    epoch="e", segment=0)
    assert facility.zone_result(config.zone_map["a"]).visible == 2
    facility.reset_camera("cam-01", "e2")  # new epoch: nothing carried over until its first frame
    reason = facility.zone_result(config.zone_map["a"]).reason
    assert "no calibrated frame in this epoch" in reason


def test_partial_camera_failure_degrades_only_affected_zones(tmp_path: Path) -> None:
    config = build(tmp_path, {"cam-01": "floor-1", "cam-02": "floor-1"},
                   [zone("a", "floor-1", RECT_A, ["cam-01"]), zone("q", "floor-1", [[600, 450], [900, 450], [900, 550], [600, 550]],
                                                                   ["cam-02"], kind="queue")])
    clock = Clock()
    facility = started(config, clock, "cam-01", "cam-02")
    facility.update("cam-01", 0.0, (person("cam-01", "1", 150, 150, 0.0),), epoch="e", segment=0)
    facility.update("cam-02", 0.0, (person("cam-02", "1", 700, 500, 0.0),), epoch="e", segment=0)
    result = facility.evaluate()
    assert result["floors"]["floor-1"].visible == 2
    facility.end_camera("cam-02", "ABNORMAL_TERMINATION")
    clock.now = 0.5  # cam-01 still fresh
    result = facility.evaluate()
    assert result["zones"]["a"].visible == 1 and result["zones"]["a"].quality == "OBSERVED"
    assert result["zones"]["q"].visible is None and "stream ended (ABNORMAL_TERMINATION)" in result["zones"]["q"].reason
    assert result["queues"]["q"].quality == "UNAVAILABLE" and result["queues"]["q"].queue_size is None
    floor = result["floors"]["floor-1"]
    assert floor.visible is None and "zone coverage incomplete" in floor.reason and floor.zones_unavailable == ["q"]


def test_all_cameras_stale_makes_every_facility_figure_unavailable(tmp_path: Path) -> None:
    config = build(tmp_path, {"cam-01": "floor-1", "cam-02": "floor-1"},
                   [zone("a", "floor-1", RECT_A, ["cam-01"]), zone("b", "floor-1", RECT_B, ["cam-02"])])
    clock = Clock()
    facility = started(config, clock, "cam-01", "cam-02")
    for camera in ("cam-01", "cam-02"):
        facility.update(camera, 0.0, (person(camera, "1", 150, 150, 0.0),), epoch="e", segment=0)
    clock.now = 2.0
    result = facility.evaluate()
    assert all(z.visible is None and "stale" in z.reason for z in result["zones"].values())
    assert result["floors"]["floor-1"].visible is None and result["building_visible"] is None
    assert result["ledger"] is None
    assert all(reason and "stale" in reason for reason in result["freshness"].values())


def test_source_discontinuity_resets_time_dependent_state(tmp_path: Path) -> None:
    config = build(tmp_path, {"cam-01": "floor-1"}, [zone("q", "floor-1", RECT_A, ["cam-01"], kind="queue")])
    clock = Clock()
    facility = started(config, clock, "cam-01")
    for step in range(10):  # 1.8 s of dwell in segment 0 (join dwell is 2 s)
        t = step * 0.2
        clock.now = t
        facility.update("cam-01", t, (person("cam-01", "1", 150 + step * 5, 150, t),), epoch="e", segment=0)
    assert facility._queues["q"]._states  # candidate dwell timer running
    clock.now = 2.0
    facility.update("cam-01", 0.1, (person("cam-01", "1", 200, 150, 0.1),), epoch="e", segment=1)
    tracker = facility._queues["q"]
    assert tracker.joins == 0
    state = next(iter(tracker._states.values()))
    assert state.candidate_since == pytest.approx(0.1)  # dwell restarted on the new segment's timeline


# ------------------------------------------------------------------ overlapping-zone aggregation


def test_one_person_in_two_nested_zones_counts_once_on_the_floor(tmp_path: Path) -> None:
    config = build(tmp_path, {"cam-01": "floor-1"},
                   [zone("dining", "floor-1", RECT_A, ["cam-01"]), zone("queue", "floor-1", RECT_INNER, ["cam-01"], kind="queue")])
    facility = started(config, Clock(), "cam-01")
    facility.update("cam-01", 0.0, (person("cam-01", "1", 250, 200, 0.0),), epoch="e", segment=0)
    result = facility.evaluate()
    assert result["zones"]["dining"].visible == 1 and result["zones"]["queue"].visible == 1
    floor = result["floors"]["floor-1"]
    assert (floor.visible, floor.quality, floor.multi_zone_members) == (1, "OBSERVED", 1)


def test_partially_overlapping_zones_with_two_people(tmp_path: Path) -> None:
    config = build(tmp_path, {"cam-01": "floor-1"},
                   [zone("a", "floor-1", RECT_A, ["cam-01"]), zone("b", "floor-1", RECT_B, ["cam-01"])])
    facility = started(config, Clock(), "cam-01")
    both = person("cam-01", "1", 450, 200, 0.0)  # inside A and B
    only_a = person("cam-01", "2", 150, 200, 0.0)
    facility.update("cam-01", 0.0, (both, only_a), epoch="e", segment=0)
    result = facility.evaluate()
    assert result["zones"]["a"].visible == 2 and result["zones"]["b"].visible == 1
    assert result["floors"]["floor-1"].visible == 2  # not 3


def test_overlapping_camera_views_and_zones_deduplicate_once(tmp_path: Path) -> None:
    config = build(tmp_path, {"cam-01": "floor-1", "cam-02": "floor-1"},
                   [zone("dining", "floor-1", RECT_A, ["cam-01", "cam-02"], overlap="dining-overlap"),
                    zone("queue", "floor-1", RECT_INNER, ["cam-01"], kind="queue")], meters_per_unit=0.05)
    facility = started(config, Clock(), "cam-01", "cam-02")
    facility.update("cam-01", 1.0, (person("cam-01", "1", 250, 200, 1.0),), epoch="e", segment=0)
    facility.update("cam-02", 1.0, (person("cam-02", "9", 250.2, 200.1, 1.0), person("cam-02", "4", 450, 350, 1.0)),
                    epoch="e", segment=0)
    result = facility.evaluate()
    assert result["zones"]["dining"].visible == 2  # shared person fused, second person distinct
    assert result["zones"]["queue"].visible == 1
    assert result["floors"]["floor-1"].visible == 2  # the queued diner is counted once


def test_distinct_people_in_disjoint_camera_zones_sum(tmp_path: Path) -> None:
    config = build(tmp_path, {"cam-01": "floor-1", "cam-02": "floor-1"},
                   [zone("a", "floor-1", RECT_A, ["cam-01"]),
                    zone("c", "floor-1", [[600, 450], [900, 450], [900, 550], [600, 550]], ["cam-02"])])
    facility = started(config, Clock(), "cam-01", "cam-02")
    facility.update("cam-01", 0.0, (person("cam-01", "1", 150, 150, 0.0),), epoch="e", segment=0)
    facility.update("cam-02", 0.0, (person("cam-02", "1", 700, 500, 0.0),), epoch="e", segment=0)
    assert facility.evaluate()["floors"]["floor-1"].visible == 2  # same local id on two cameras is two tracks


def test_incomplete_zone_coverage_leaves_floor_unavailable(tmp_path: Path) -> None:
    config = build(tmp_path, {"cam-01": "floor-1"},
                   [zone("a", "floor-1", RECT_A, ["cam-01"]), zone("uncovered", "floor-1", RECT_B, [])])
    facility = started(config, Clock(), "cam-01")
    facility.update("cam-01", 0.0, (person("cam-01", "1", 150, 150, 0.0),), epoch="e", segment=0)
    result = facility.evaluate()
    assert result["zones"]["a"].visible == 1
    floor = result["floors"]["floor-1"]
    assert floor.visible is None and "no authoritative camera configured" in floor.reason


def test_building_sums_deduplicated_floors(tmp_path: Path) -> None:
    config = build(tmp_path, {"cam-01": "floor-1", "cam-04": "floor-2"},
                   [zone("dining", "floor-1", RECT_A, ["cam-01"]), zone("queue", "floor-1", RECT_INNER, ["cam-01"], kind="queue"),
                    zone("upper", "floor-2", FLOOR2, ["cam-04"])])
    facility = started(config, Clock(), "cam-01", "cam-04")
    facility.update("cam-01", 0.0, (person("cam-01", "1", 250, 200, 0.0),), epoch="e", segment=0)
    facility.update("cam-04", 0.0, (person("cam-04", "1", 300, 300, 0.0), person("cam-04", "2", 600, 300, 0.0)),
                    epoch="e", segment=0)
    assert facility.evaluate()["building_visible"] == 3


# ------------------------------------------------------------------ runtime wiring


def batch(camera, epoch, t, observations, frame=0, segment=0) -> ObservationBatch:
    return ObservationBatch(camera_id=camera, camera_epoch=epoch, observations=tuple(observations),
                            received_at=time.monotonic(), source_fps=30.0, analytics_fps=30.0, frame_age_ms=None,
                            frame_age_basis="unit", dropped_frames=0, metadata={"frame_width": 1920, "frame_height": 1080},
                            source_time=t, source_pts_ns=int(t * 1e9), source_segment=segment, frame_number=frame)


def supervisor_for(config, tmp_path):
    repository = SQLiteRepository(tmp_path / "facility.db")
    repository.migrate()
    repository.seed_configuration(config)
    adapter = SuppliedObservationAdapter()
    return CameraRuntimeSupervisor(config, repository, adapter, SequenceState()), repository, adapter


def test_runtime_expires_zone_floor_without_new_frames(tmp_path: Path, monkeypatch) -> None:
    config = build(tmp_path, {"cam-01": "floor-1"},
                   [zone("dining", "floor-1", RECT_A, ["cam-01"]), zone("queue", "floor-1", RECT_INNER, ["cam-01"], kind="queue")],
                   stale_after_ms=200)
    monkeypatch.setattr(runtime_module, "FACILITY_EMIT_SECONDS", 0.05)
    supervisor, repository, adapter = supervisor_for(config, tmp_path)

    async def scenario() -> None:
        await supervisor.start()
        epoch = supervisor.states()["cam-01"].epoch
        run_id = supervisor.current_run()[0]
        await adapter.emit(batch("cam-01", epoch, 0.0, [person("cam-01", "1", 250, 200, 0.0, epoch)]))
        rows = {(r["scope_type"], r["scope_id"]): r for r in repository.latest_analytics(run_id, "occupancy")}
        assert rows[("zone", "dining")]["visible"] == 1 and rows[("zone", "queue")]["visible"] == 1
        assert rows[("floor", "floor-1")]["visible"] == 1  # de-duplicated, persisted
        await asyncio.sleep(0.6)  # no frames: only the timer can expire the contribution
        rows = {(r["scope_type"], r["scope_id"]): r for r in repository.latest_analytics(run_id, "occupancy")}
        for scope in (("zone", "dining"), ("zone", "queue"), ("floor", "floor-1")):
            assert rows[scope]["visible"] is None and rows[scope]["quality"] == "UNAVAILABLE", scope
        capability = supervisor.capabilities()["zones"]["dining"]["occupancy"]
        assert capability["available"] and not capability["current"] and "stale" in capability["current_reason"]
        await adapter.emit(batch("cam-01", epoch, 1.0, [person("cam-01", "1", 250, 200, 1.0, epoch)], frame=30))
        rows = {(r["scope_type"], r["scope_id"]): r for r in repository.latest_analytics(run_id, "occupancy")}
        assert rows[("floor", "floor-1")]["visible"] == 1  # recovered on the next fresh frame
        supervisor.complete_stream("cam-01", epoch, "CLEAN_EOF")
        rows = {(r["scope_type"], r["scope_id"]): r for r in repository.latest_analytics(run_id, "occupancy")}
        assert rows[("zone", "dining")]["visible"] is None  # ended stream is not current, whatever its age
        await supervisor.stop()

    asyncio.run(scenario())


def test_runtime_stair_transition_records_unknown_association_and_inferred_flow(tmp_path: Path, monkeypatch) -> None:
    config = build(tmp_path, {"cam-03": "floor-1", "cam-04": "floor-2"}, keep_portals=True)
    monkeypatch.setattr(runtime_module, "FACILITY_EMIT_SECONDS", 0.05)
    supervisor, repository, adapter = supervisor_for(config, tmp_path)

    async def scenario() -> None:
        await supervisor.start()
        states = supervisor.states()
        e3, e4 = states["cam-03"].epoch, states["cam-04"].epoch
        run_id = supervisor.current_run()[0]
        for step in range(6):  # walks inside stair-f1 for 0.5 s, then is gone
            t = step * 0.1
            await adapter.emit(batch("cam-03", e3, t, [person("cam-03", "5", 850, 150, t, e3)], frame=step))
            await adapter.emit(batch("cam-04", e4, t, [], frame=step))
        for step, t in enumerate((1.0, 1.6, 2.0, 3.0)):
            await adapter.emit(batch("cam-03", e3, t, [], frame=10 + step))
            await adapter.emit(batch("cam-04", e4, t, [], frame=10 + step))
        await adapter.emit(batch("cam-04", e4, 4.0, [person("cam-04", "8", 200, 200, 4.0, e4)], frame=20))
        await adapter.emit(batch("cam-03", e3, 4.0, [], frame=20))
        await asyncio.sleep(0.15)
        associations = repository.list_associations(run_id=run_id)
        assert len(associations) == 1
        decision = associations[0]
        assert decision["status"] == "UNKNOWN" and "evidence is unavailable" in decision["decision_reason"]
        assert decision["source_track_key"].startswith("cam-03:") and decision["candidate_track_key"].startswith("cam-04:")
        flows = {(r["portal_id"], r["direction"]): r for r in repository.latest_analytics(run_id, "flow")}
        up = flows[("stair-a", "UP")]
        assert up["inferred_count"] == 1 and up["observed_count"] == 0 and up["quality"] == "ESTIMATED"
        assert repository.count_unresolved_associations(run_id) == 1
        await supervisor.stop()

    asyncio.run(scenario())


def test_evaluation_throttle_is_per_camera_source_time(tmp_path: Path) -> None:
    """Two cameras on different source timelines must not re-arm evaluation on every frame."""
    config = build(tmp_path, {"cam-01": "floor-1"}, [zone("a", "floor-1", RECT_A, ["cam-01"])])
    facility = FacilityAnalytics(config, clock=Clock())
    due = 0
    for frame in range(90):  # 3 s of 30 fps from each camera, interleaved; timelines 100 s apart
        due += facility.due(100 + frame / 30, "cam-01")
        due += facility.due(frame / 30, "cam-02")
    assert due <= 8  # about one per camera per source second (was ~180 with one shared clock)
    assert facility.due(0.0, "cam-02") is True  # a backwards jump (new segment) still re-arms


def test_api_snapshot_and_persistence_agree_on_expiry(tmp_path: Path, monkeypatch) -> None:
    """Controlled input through the real HTTP API: floor/zone rows and the snapshot withdraw together."""
    from fastapi.testclient import TestClient
    from backend.app.main import create_app

    config = build(tmp_path, {"cam-01": "floor-1"},
                   [zone("dining", "floor-1", RECT_A, ["cam-01"]), zone("queue", "floor-1", RECT_INNER, ["cam-01"], kind="queue")],
                   stale_after_ms=200)
    monkeypatch.setattr(runtime_module, "FACILITY_EMIT_SECONDS", 0.05)
    adapter = SuppliedObservationAdapter()
    app = create_app(config_dir=tmp_path / "configs", database_path=tmp_path / "api.db", perception_adapter=adapter,
                     evidence_root=tmp_path / "evidence")
    with TestClient(app) as client:
        started = client.post("/api/v1/demo/start").json()
        epoch = next(c for c in started["cameras"] if c["id"] == "cam-01")["epoch"]
        client.portal.call(adapter.emit, batch("cam-01", epoch, 0.0, [person("cam-01", "1", 250, 200, 0.0, epoch)]))
        floor = next(f for f in client.get("/api/v1/facility/snapshot").json()["floors"] if f["id"] == "floor-1")
        assert floor["visible_occupancy"] == 1 and floor["quality"] == "OBSERVED"  # one person in nested zones = 1
        zones = {z["id"]: z for z in floor["zones"]}
        assert zones["dining"]["occupancy"] == 1 and zones["queue"]["occupancy"] == 1
        time.sleep(0.8)  # no frames: the facility ticker must expire the contribution
        floor = next(f for f in client.get("/api/v1/facility/snapshot").json()["floors"] if f["id"] == "floor-1")
        assert floor["visible_occupancy"] is None and floor["quality"] == "UNAVAILABLE"
        assert all(z["occupancy"] is None for z in floor["zones"])
        capability = client.get("/api/v1/system/capabilities").json()["zones"]["dining"]["occupancy"]
        assert capability["available"] and capability["current"] is False and "stale" in capability["current_reason"]
        rows = [r for r in app.state.services.repository.latest_analytics(started["run_id"], "occupancy") if r["scope_type"] == "floor"]
        assert rows and rows[0]["visible"] is None and rows[0]["quality"] == "UNAVAILABLE"
        client.post("/api/v1/demo/stop")


def test_floor_capability_reports_dedup_policy(tmp_path: Path) -> None:
    config = build(tmp_path, {"cam-01": "floor-1"}, [zone("a", "floor-1", RECT_A, ["cam-01"])])
    facility = started(config, Clock(), "cam-01")
    matrix = facility_module.capability_matrix(config, facility.geometry, {"cam-01"}, facility.freshness())
    assert matrix["floors"]["floor-1"]["visible_in_zones"]["available"]
    assert "de-duplicated" in matrix["floors"]["floor-1"]["visible_in_zones"]["aggregation"]
    assert "no zones configured" in matrix["floors"]["floor-2"]["visible_in_zones"]["reason"]
