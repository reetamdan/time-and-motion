"""Explainable count/density congestion with persistence and recovery hysteresis."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

Level = Literal["NORMAL", "BUSY", "CONGESTED", "SEVERELY CONGESTED", "UNAVAILABLE"]


@dataclass(frozen=True, slots=True)
class CongestionRule:
    busy: float
    congested: float
    severe: float
    metric: Literal["count", "density", "capacity_ratio"] = "count"
    persist_seconds: float = 5.0
    recover_seconds: float = 5.0
    recovery_hysteresis: float = 0.15
    revision: str = "congestion-v1"

    def __post_init__(self) -> None:
        if not 0 < self.busy < self.congested < self.severe:
            raise ValueError("Congestion thresholds must be positive and strictly increase")
        if self.persist_seconds < 0 or self.recover_seconds < 0:
            raise ValueError("Persistence windows cannot be negative")
        if not 0 <= self.recovery_hysteresis < 1:
            raise ValueError("Recovery hysteresis must be in [0, 1)")

    @property
    def thresholds(self) -> dict[str, float]:
        return {"BUSY": self.busy, "CONGESTED": self.congested, "SEVERELY CONGESTED": self.severe}


@dataclass(frozen=True, slots=True)
class CongestionTransition:
    previous: Level
    current: Level
    timestamp: float
    value: float | None
    metric: str
    measurements: dict[str, float]
    thresholds: dict[str, float]
    rule_revision: str


class CongestionMonitor:
    _rank = {"NORMAL": 0, "BUSY": 1, "CONGESTED": 2, "SEVERELY CONGESTED": 3}

    def __init__(self, rule: CongestionRule) -> None:
        self.rule = rule
        self.level: Level = "NORMAL"
        self._candidate: Level | None = None
        self._candidate_since: float | None = None

    def _raw_level(self, value: float) -> Level:
        if value >= self.rule.severe:
            return "SEVERELY CONGESTED"
        if value >= self.rule.congested:
            return "CONGESTED"
        if value >= self.rule.busy:
            return "BUSY"
        return "NORMAL"

    def update(
        self, value: float, timestamp: float, measurements: dict[str, float] | None = None
    ) -> CongestionTransition | None:
        if not math.isfinite(value) or value < 0 or not math.isfinite(timestamp):
            raise ValueError("Congestion inputs must be finite and non-negative")
        raw = self._raw_level(value)
        current_rank = self._rank.get(self.level, 0)
        raw_rank = self._rank[raw]
        if raw == self.level:
            self._clear_candidate()
            return None
        if raw_rank < current_rank:
            current_threshold = self.rule.thresholds[self.level]
            recovery_boundary = current_threshold * (1 - self.rule.recovery_hysteresis)
            if value > recovery_boundary:
                self._clear_candidate()
                return None
        if self._candidate != raw:
            self._candidate = raw
            self._candidate_since = timestamp
        required = self.rule.persist_seconds if raw_rank > current_rank else self.rule.recover_seconds
        since = self._candidate_since if self._candidate_since is not None else timestamp
        if timestamp - since < required:
            return None
        previous = self.level
        self.level = raw
        self._clear_candidate()
        return CongestionTransition(
            previous=previous,
            current=raw,
            timestamp=timestamp,
            value=value,
            metric=self.rule.metric,
            measurements={"value": value, **(measurements or {})},
            thresholds=self.rule.thresholds,
            rule_revision=self.rule.revision,
        )

    def update_occupancy(
        self, occupancy: int, timestamp: float, area_m2: float | None = None,
        capacity: float | None = None,
    ) -> CongestionTransition | None:
        if self.rule.metric == "count":
            return self.update(float(occupancy), timestamp, {"occupancy": float(occupancy)})
        if self.rule.metric == "density":
            if area_m2 is None or not math.isfinite(area_m2) or area_m2 <= 0:
                return self.mark_unavailable(timestamp)
            density = occupancy / area_m2
            return self.update(density, timestamp, {"occupancy": float(occupancy), "area_m2": area_m2})
        if capacity is None or not math.isfinite(capacity) or capacity <= 0:
            return self.mark_unavailable(timestamp)
        return self.update(occupancy / capacity, timestamp, {"occupancy": float(occupancy), "capacity": capacity})

    def mark_unavailable(self, timestamp: float) -> CongestionTransition | None:
        if self.level == "UNAVAILABLE":
            return None
        previous = self.level
        self.level = "UNAVAILABLE"
        self._clear_candidate()
        return CongestionTransition(
            previous, "UNAVAILABLE", timestamp, None, self.rule.metric, {}, self.rule.thresholds, self.rule.revision
        )

    def _clear_candidate(self) -> None:
        self._candidate = None
        self._candidate_since = None
