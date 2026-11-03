from backend.app.analytics.fusion import FusionObservation, FusionPolicy, OverlapFusion

from .support import key


def fused(camera: str, local: str, x: float = 1, timestamp: float = 1, calibrated: bool = True):
    return FusionObservation(
        key=key(camera=camera, local=local), floor_id="floor-1", timestamp=timestamp,
        world_position=(x, 2), overlap_group="dining-overlap", calibration_valid=calibrated,
    )


def test_controlled_overlap_counts_one_person_once() -> None:
    result = OverlapFusion("run-1").fuse([fused("cam-02", "11"), fused("cam-03", "4", 1.2)])
    assert result.visible_hypotheses == 1 and result.occupancy_contribution == 1
    assert result.quality == "OBSERVED"
    assert len(result.groups[0].constituent_track_keys) == 2
    assert set(result.groups[0].camera_ids) == {"cam-02", "cam-03"}


def test_same_camera_simultaneous_tracks_cannot_share_a_group() -> None:
    result = OverlapFusion("run-1").fuse([fused("cam-02", "11"), fused("cam-02", "12")])
    assert result.visible_hypotheses == 2
    assert all(len(group.camera_ids) == 1 for group in result.groups)


def test_time_distance_and_overlap_gates_prevent_false_merges() -> None:
    engine = OverlapFusion("run-1", FusionPolicy(max_time_delta_ms=100, max_world_distance_m=0.5))
    distant = engine.fuse([fused("cam-02", "1", 0), fused("cam-03", "2", 5)])
    assert distant.visible_hypotheses == 2
    misaligned = engine.fuse([fused("cam-02", "1", timestamp=0), fused("cam-03", "2", timestamp=1)])
    assert misaligned.visible_hypotheses == 2
    different_overlap = fused("cam-03", "2")
    different_overlap = FusionObservation(
        different_overlap.key, different_overlap.floor_id, different_overlap.timestamp,
        different_overlap.world_position, "another-overlap",
    )
    assert engine.fuse([fused("cam-02", "1"), different_overlap]).visible_hypotheses == 2


def test_uncalibrated_overlap_has_no_authoritative_occupancy() -> None:
    result = OverlapFusion("run-1").fuse([
        fused("cam-02", "1", calibrated=False), fused("cam-03", "2", calibrated=False),
    ])
    assert result.visible_hypotheses == 2
    assert result.occupancy_contribution is None and result.quality == "UNAVAILABLE"
