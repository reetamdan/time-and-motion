"""Explicit stale-data policy shared by event-producing analytics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

from .types import Observation


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    max_age_ms: float = 1500
    allowed_future_ms: float = 100

    def __post_init__(self) -> None:
        if self.max_age_ms <= 0 or self.allowed_future_ms < 0:
            raise ValueError("Invalid freshness bounds")

    def evaluate(self, observation: Observation, now: float) -> "FreshnessResult":
        age_ms = (now - observation.timestamp) * 1000
        if age_ms > self.max_age_ms:
            return FreshnessResult(False, age_ms, "Observation is stale")
        if age_ms < -self.allowed_future_ms:
            return FreshnessResult(False, age_ms, "Observation timestamp is implausibly in the future")
        if not observation.confirmed or observation.tracking_state.upper() in {
            "LOST", "ENDED", "SHADOW", "PREDICTED", "TENTATIVE"
        }:
            return FreshnessResult(False, age_ms, "Track is not a confirmed current visual observation")
        return FreshnessResult(True, max(0.0, age_ms), None)


@dataclass(frozen=True, slots=True)
class FreshnessResult:
    fresh: bool
    age_ms: float
    unavailable_reason: str | None


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class FreshValue(Generic[T]):
    """Retains a stale last value for diagnostics without presenting it as a current measurement."""

    value: T | None
    observed_at: float | None
    quality: Literal["OBSERVED", "ESTIMATED", "UNAVAILABLE", "STALE"]
    stale: bool
    unavailable_reason: str | None = None

    @property
    def contract_value(self) -> T | None:
        return self.value if not self.stale and self.quality != "UNAVAILABLE" else None


def resolve_fresh_value(
    value: T | None,
    observed_at: float | None,
    now: float,
    policy: FreshnessPolicy,
) -> FreshValue[T]:
    if value is None or observed_at is None:
        return FreshValue(None, observed_at, "UNAVAILABLE", True, "No observation is available")
    age_ms = (now - observed_at) * 1000
    if age_ms > policy.max_age_ms or age_ms < -policy.allowed_future_ms:
        return FreshValue(value, observed_at, "STALE", True, "Last observation is outside the freshness window")
    return FreshValue(value, observed_at, "OBSERVED", False)
