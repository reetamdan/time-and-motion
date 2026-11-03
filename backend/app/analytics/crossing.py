"""Directional line crossing with hysteresis, persistence, debounce, and replay idempotency."""

from __future__ import annotations

import math
import threading
import uuid
from dataclasses import dataclass, field
from typing import Literal

from .freshness import FreshnessPolicy
from .types import Observation, TrackKey


@dataclass(frozen=True, slots=True)
class CrossingPolicy:
    dead_band: float = 2.0
    debounce_seconds: float = 1.0
    confirmation_seconds: float = 0.0
    min_track_seconds: float = 0.0
    min_track_observations: int = 1
    min_displacement: float = 0.0
    retrack_debounce_seconds: float = 0.0
    stale_after_ms: float = 1500

    def __post_init__(self) -> None:
        if self.dead_band <= 0 or self.min_displacement < 0 or self.min_track_observations < 1:
            raise ValueError("Invalid crossing policy")
        if min(self.debounce_seconds, self.confirmation_seconds, self.min_track_seconds,
               self.retrack_debounce_seconds, self.stale_after_ms) < 0:
            raise ValueError("Crossing durations must be non-negative")


@dataclass(frozen=True, slots=True)
class Boundary:
    id: str
    start: tuple[float, float]
    end: tuple[float, float]
    positive_direction: Literal["IN", "OUT"] = "IN"
    owner: str = "default"
    policy: CrossingPolicy = field(default_factory=CrossingPolicy)

    def __post_init__(self) -> None:
        if self.start == self.end:
            raise ValueError("Boundary must have non-zero length")

    def signed_distance(self, point: tuple[float, float]) -> float:
        ax, ay = self.start
        bx, by = self.end
        px, py = point
        length = math.hypot(bx - ax, by - ay)
        return ((bx - ax) * (py - ay) - (by - ay) * (px - ax)) / length


@dataclass(frozen=True, slots=True)
class CrossingEvent:
    event_id: str
    idempotency_key: str
    boundary_id: str
    direction: Literal["IN", "OUT"]
    track_key: TrackKey
    continuity_id: str | None
    occurred_at: float
    owner: str


class IdempotencyCache:
    """Small process-local replay guard; durable consumers must also enforce their unique key."""

    def __init__(self) -> None:
        self._keys: set[str] = set()
        self._lock = threading.Lock()

    def check_and_add(self, key: str) -> bool:
        with self._lock:
            if key in self._keys:
                return False
            self._keys.add(key)
            return True


@dataclass(slots=True)
class _TrackState:
    first_timestamp: float
    last_timestamp: float
    observations: int = 0
    stable_side: int = 0
    candidate_side: int = 0
    candidate_since: float | None = None
    candidate_origin: tuple[float, float] | None = None
    candidate_origin_distance: float = 0.0
    last_crossing_at: float = -math.inf
    position: tuple[float, float] | None = None


class DirectionalCrossing:
    def __init__(self, boundary: Boundary, idempotency: IdempotencyCache | None = None) -> None:
        self.boundary = boundary
        self._states: dict[TrackKey, _TrackState] = {}
        self._idempotency = idempotency or IdempotencyCache()
        self._freshness = FreshnessPolicy(boundary.policy.stale_after_ms)
        self._continuity_crossings: dict[str, float] = {}

    def update(self, observation: Observation, *, now: float | None = None) -> CrossingEvent | None:
        evaluation_time = observation.timestamp if now is None else now
        if observation.position is None or not self._freshness.evaluate(observation, evaluation_time).fresh:
            return None
        state = self._states.get(observation.key)
        if state is None:
            state = _TrackState(observation.timestamp, observation.timestamp)
            self._states[observation.key] = state
        if observation.timestamp <= state.last_timestamp and state.observations:
            return None
        if state.observations and observation.timestamp - state.last_timestamp > self.boundary.policy.stale_after_ms / 1000:
            # A fresh observation after a continuity gap starts a new boundary state; it cannot complete an old crossing.
            state = _TrackState(observation.timestamp, observation.timestamp)
            self._states[observation.key] = state

        previous_position = state.position
        previous_distance = (
            self.boundary.signed_distance(previous_position)
            if previous_position is not None
            else self.boundary.signed_distance(observation.position)
        )
        state.observations += 1
        if state.observations == 1:
            state.first_timestamp = observation.timestamp
        state.last_timestamp = observation.timestamp
        state.position = observation.position

        distance = self.boundary.signed_distance(observation.position)
        if abs(distance) <= self.boundary.policy.dead_band:
            return None
        side = 1 if distance > 0 else -1
        if state.stable_side == 0:
            state.stable_side = side
            self._clear_candidate(state)
            return None
        if side == state.stable_side:
            self._clear_candidate(state)
            return None

        if state.candidate_side != side:
            state.candidate_side = side
            state.candidate_since = observation.timestamp
            state.candidate_origin = previous_position or observation.position
            state.candidate_origin_distance = previous_distance
            if self.boundary.policy.confirmation_seconds > 0:
                return None
        candidate_since = state.candidate_since
        elapsed_confirmation = observation.timestamp - (candidate_since if candidate_since is not None else observation.timestamp)
        if elapsed_confirmation < self.boundary.policy.confirmation_seconds:
            return None

        direction: Literal["IN", "OUT"] = (
            self.boundary.positive_direction
            if state.stable_side < 0 and side > 0
            else ("OUT" if self.boundary.positive_direction == "IN" else "IN")
        )
        persistent = (
            state.observations >= self.boundary.policy.min_track_observations
            and observation.timestamp - state.first_timestamp >= self.boundary.policy.min_track_seconds
        )
        origin = state.candidate_origin or observation.position
        displacement = math.dist(origin, observation.position)
        expected_sign = 1 if side > state.stable_side else -1
        movement_consistent = (distance - state.candidate_origin_distance) * expected_sign > 0
        debounce_key = observation.continuity_id or observation.key.as_string()
        last_crossing = max(state.last_crossing_at, self._continuity_crossings.get(debounce_key, -math.inf))
        debounce = max(self.boundary.policy.debounce_seconds, self.boundary.policy.retrack_debounce_seconds)
        debounced = observation.timestamp - last_crossing < debounce

        if not persistent or displacement < self.boundary.policy.min_displacement or not movement_consistent:
            # Ineligible early transitions are consumed so they cannot become delayed phantom events.
            state.stable_side = side
            self._clear_candidate(state)
            return None
        if debounced:
            # Keep the fresh opposite-side candidate; sustained evidence may complete after the debounce window.
            return None

        state.stable_side = side
        self._clear_candidate(state)
        state.last_crossing_at = observation.timestamp
        self._continuity_crossings[debounce_key] = observation.timestamp
        timestamp_key = f"{observation.timestamp:.6f}"
        idempotency_key = f"line-crossing:v1:{self.boundary.id}:{observation.key}:{direction}:{timestamp_key}"
        event = CrossingEvent(
            event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key)),
            idempotency_key=idempotency_key,
            boundary_id=self.boundary.id,
            direction=direction,
            track_key=observation.key,
            continuity_id=observation.continuity_id,
            occurred_at=observation.timestamp,
            owner=self.boundary.owner,
        )
        return event if self._idempotency.check_and_add(idempotency_key) else None

    process = update

    @staticmethod
    def _clear_candidate(state: _TrackState) -> None:
        state.candidate_side = 0
        state.candidate_since = None
        state.candidate_origin = None

    def discard_track(self, key: TrackKey) -> None:
        self._states.pop(key, None)
