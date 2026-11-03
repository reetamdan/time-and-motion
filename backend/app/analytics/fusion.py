"""Calibrated overlapping-view fusion with simultaneous-track mutual exclusion."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Literal, Sequence

from .types import TrackKey


@dataclass(frozen=True, slots=True)
class FusionObservation:
    key: TrackKey
    floor_id: str
    timestamp: float
    world_position: tuple[float, float] | None
    overlap_group: str | None
    calibration_valid: bool = True
    world_uncertainty_m: float = 0.0
    timestamp_uncertainty_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class FusionPolicy:
    max_time_delta_ms: float = 250
    max_world_distance_m: float = 0.75
    ambiguity_margin_m: float = 0.05


@dataclass(frozen=True, slots=True)
class FusedGroup:
    temporary_global_track_id: str | None
    constituent_track_keys: tuple[str, ...]
    camera_ids: tuple[str, ...]
    floor_id: str
    world_position: tuple[float, float] | None
    status: Literal["CONFIRMED", "UNASSOCIATED"]


@dataclass(frozen=True, slots=True)
class FusionResult:
    groups: tuple[FusedGroup, ...]
    visible_hypotheses: int
    occupancy_contribution: int | None
    quality: Literal["OBSERVED", "ESTIMATED", "UNAVAILABLE"]
    ambiguous_track_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Candidate:
    left: int
    right: int
    distance: float


class OverlapFusion:
    def __init__(self, session_id: str, policy: FusionPolicy = FusionPolicy()) -> None:
        self.session_id = session_id
        self.policy = policy

    def _compatible(self, left: FusionObservation, right: FusionObservation) -> tuple[bool, float]:
        if left.key.camera_id == right.key.camera_id:
            return False, math.inf
        if not left.overlap_group or left.overlap_group != right.overlap_group:
            return False, math.inf
        if left.floor_id != right.floor_id or not left.calibration_valid or not right.calibration_valid:
            return False, math.inf
        if left.world_position is None or right.world_position is None:
            return False, math.inf
        time_delta_ms = abs(left.timestamp - right.timestamp) * 1000
        allowed_time = self.policy.max_time_delta_ms + left.timestamp_uncertainty_ms + right.timestamp_uncertainty_ms
        if time_delta_ms > allowed_time:
            return False, math.inf
        distance = math.dist(left.world_position, right.world_position)
        allowed_distance = self.policy.max_world_distance_m + left.world_uncertainty_m + right.world_uncertainty_m
        return distance <= allowed_distance, distance

    def fuse(self, observations: Sequence[FusionObservation]) -> FusionResult:
        ordered = sorted(observations, key=lambda item: item.key.as_string())
        candidates: list[_Candidate] = []
        ambiguous: set[str] = set()
        for left_index, left in enumerate(ordered):
            for right_index in range(left_index + 1, len(ordered)):
                compatible, distance = self._compatible(left, ordered[right_index])
                if compatible:
                    candidates.append(_Candidate(left_index, right_index, distance))
        # Two similarly plausible tracks from the same other camera are ambiguous, not a forced merge.
        for index, observation in enumerate(ordered):
            alternatives = [candidate for candidate in candidates if index in (candidate.left, candidate.right)]
            by_camera: dict[str, list[float]] = {}
            for candidate in alternatives:
                other = ordered[candidate.right if candidate.left == index else candidate.left]
                by_camera.setdefault(other.key.camera_id, []).append(candidate.distance)
            for distances in by_camera.values():
                if len(distances) > 1:
                    first, second = sorted(distances)[:2]
                    if second - first < self.policy.ambiguity_margin_m:
                        ambiguous.add(observation.key.as_string())
        candidates = [
            candidate for candidate in candidates
            if ordered[candidate.left].key.as_string() not in ambiguous
            and ordered[candidate.right].key.as_string() not in ambiguous
        ]
        candidates.sort(key=lambda item: (item.distance, ordered[item.left].key.as_string(), ordered[item.right].key.as_string()))
        clusters: list[list[int]] = [[index] for index in range(len(ordered))]
        for candidate in candidates:
            left_cluster = next(cluster for cluster in clusters if candidate.left in cluster)
            right_cluster = next(cluster for cluster in clusters if candidate.right in cluster)
            if left_cluster is right_cluster:
                continue
            cameras = [ordered[index].key.camera_id for index in left_cluster + right_cluster]
            if len(cameras) != len(set(cameras)):
                continue
            if any(
                not self._compatible(ordered[left], ordered[right])[0]
                for left in left_cluster for right in right_cluster
            ):
                continue
            merged = sorted(left_cluster + right_cluster)
            clusters = [cluster for cluster in clusters if cluster not in (left_cluster, right_cluster)]
            clusters.append(merged)
        clusters.sort(key=lambda cluster: ordered[cluster[0]].key.as_string())

        groups: list[FusedGroup] = []
        for cluster in clusters:
            members = [ordered[index] for index in cluster]
            positions = [member.world_position for member in members if member.world_position is not None]
            position = (
                (sum(point[0] for point in positions) / len(positions), sum(point[1] for point in positions) / len(positions))
                if len(positions) == len(members) else None
            )
            keys = tuple(member.key.as_string() for member in members)
            temporary_id = (
                str(uuid.uuid5(uuid.NAMESPACE_URL, f"fusion:{self.session_id}:{','.join(keys)}"))
                if len(members) > 1 else None
            )
            groups.append(FusedGroup(
                temporary_id, keys, tuple(member.key.camera_id for member in members), members[0].floor_id,
                position, "CONFIRMED" if len(members) > 1 else "UNASSOCIATED",
            ))
        uncertain_overlap = any(
            left.overlap_group and left.overlap_group == right.overlap_group
            and left.floor_id == right.floor_id and left.key.camera_id != right.key.camera_id
            and (not left.calibration_valid or not right.calibration_valid)
            for index, left in enumerate(ordered) for right in ordered[index + 1:]
        )
        quality = "UNAVAILABLE" if uncertain_overlap else ("ESTIMATED" if ambiguous else "OBSERVED")
        return FusionResult(
            groups=tuple(groups),
            visible_hypotheses=len(groups),
            occupancy_contribution=None if quality == "UNAVAILABLE" else len(groups),
            quality=quality,
            ambiguous_track_keys=tuple(sorted(ambiguous)),
        )
