"""Queue membership state machine with join/leave dwell and bounded occlusion."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from .types import TrackKey


@dataclass(frozen=True, slots=True)
class QueuePolicy:
    join_dwell_seconds: float = 2.0
    leave_dwell_seconds: float = 2.0
    max_occlusion_seconds: float = 3.0
    min_join_movement: float = 2.0
    require_movement_to_join: bool = True

    def __post_init__(self) -> None:
        if min(self.join_dwell_seconds, self.leave_dwell_seconds, self.max_occlusion_seconds,
                self.min_join_movement) < 0:
            raise ValueError("Queue durations and distances must be non-negative")


@dataclass(frozen=True, slots=True)
class QueueSample:
    key: TrackKey
    timestamp: float
    inside: bool
    observed: bool = True
    moving: bool = False
    position: tuple[float, float] | None = None
    service_completed: bool = False

    def __post_init__(self) -> None:
        if not math.isfinite(self.timestamp):
            raise ValueError("Queue sample timestamp must be finite")


@dataclass(frozen=True, slots=True)
class QueueTransition:
    key: TrackKey
    transition: Literal["JOINED", "LEFT", "EXPIRED"]
    timestamp: float
    wait_seconds: float | None
    wait_basis: Literal["OBSERVED_SERVICE_COMPLETION", "UNAVAILABLE"]


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    queue_size: int | None
    joins: int
    leaves: int
    expirations: int
    occluded_members: int
    unresolved_members: int
    max_wait_seconds: float | None
    quality: Literal["OBSERVED", "ESTIMATED", "UNAVAILABLE"]


@dataclass(slots=True)
class _MemberState:
    last_timestamp: float
    last_seen: float
    candidate_since: float | None = None
    candidate_position: tuple[float, float] | None = None
    moved_during_join: bool = False
    member: bool = False
    joined_at: float | None = None
    outside_since: float | None = None
    expired: bool = False


class QueueTracker:
    def __init__(self, policy: QueuePolicy = QueuePolicy()) -> None:
        self.policy = policy
        self._states: dict[TrackKey, _MemberState] = {}
        self.joins = 0
        self.leaves = 0
        self.expirations = 0

    def update(self, sample: QueueSample) -> QueueTransition | None:
        state = self._states.get(sample.key)
        if state is None:
            if not sample.observed:
                return None
            state = _MemberState(sample.timestamp, sample.timestamp)
            self._states[sample.key] = state
        if sample.timestamp < state.last_timestamp:
            return None
        state.last_timestamp = sample.timestamp
        if not sample.observed:
            return None
        state.last_seen = sample.timestamp
        state.expired = False

        if state.member:
            if sample.service_completed:
                return self._leave(sample.key, state, sample.timestamp, observed_service=True)
            if sample.inside:
                state.outside_since = None
                return None
            if state.outside_since is None:
                state.outside_since = sample.timestamp
            if sample.timestamp - state.outside_since >= self.policy.leave_dwell_seconds:
                return self._leave(sample.key, state, sample.timestamp, observed_service=False)
            return None

        if not sample.inside:
            state.candidate_since = None
            state.candidate_position = None
            state.moved_during_join = False
            return None
        state.outside_since = None
        if state.candidate_since is None:
            state.candidate_since = sample.timestamp
            state.candidate_position = sample.position
            state.moved_during_join = sample.moving
        if sample.moving:
            state.moved_during_join = True
        if sample.position is not None and state.candidate_position is not None:
            if math.dist(sample.position, state.candidate_position) >= self.policy.min_join_movement:
                state.moved_during_join = True
        dwell_complete = sample.timestamp - state.candidate_since >= self.policy.join_dwell_seconds
        movement_complete = state.moved_during_join or not self.policy.require_movement_to_join
        if dwell_complete and movement_complete:
            state.member = True
            state.joined_at = state.candidate_since
            state.candidate_since = None
            self.joins += 1
            return QueueTransition(sample.key, "JOINED", sample.timestamp, None, "UNAVAILABLE")
        return None

    process = update

    def _leave(
        self, key: TrackKey, state: _MemberState, timestamp: float, observed_service: bool
    ) -> QueueTransition:
        wait = timestamp - state.joined_at if observed_service and state.joined_at is not None else None
        state.member = False
        state.joined_at = None
        state.outside_since = None
        state.candidate_since = None
        self.leaves += 1
        return QueueTransition(
            key, "LEFT", timestamp, wait,
            "OBSERVED_SERVICE_COMPLETION" if wait is not None else "UNAVAILABLE",
        )

    def snapshot(self, now: float) -> QueueSnapshot:
        occluded = 0
        unresolved = sum(state.expired for state in self._states.values())
        current_waits: list[float] = []
        for state in self._states.values():
            if not state.member:
                continue
            absence = now - state.last_seen
            if absence > self.policy.max_occlusion_seconds:
                state.member = False
                state.expired = True
                state.joined_at = None
                self.expirations += 1
                unresolved += 1
                continue
            if absence > 0:
                occluded += 1
            if state.joined_at is not None:
                current_waits.append(now - state.joined_at)
        if not self._states:
            return QueueSnapshot(None, self.joins, self.leaves, self.expirations, 0, unresolved, None, "UNAVAILABLE")
        queue_size = sum(state.member for state in self._states.values()) if not unresolved else None
        quality = "UNAVAILABLE" if unresolved else ("ESTIMATED" if occluded else "OBSERVED")
        return QueueSnapshot(
            queue_size=queue_size,
            joins=self.joins,
            leaves=self.leaves,
            expirations=self.expirations,
            occluded_members=occluded,
            unresolved_members=unresolved,
            max_wait_seconds=max(current_waits) if current_waits and not unresolved else None,
            quality=quality,
        )
