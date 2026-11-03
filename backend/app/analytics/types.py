"""Common observation primitives.

Observation.timestamp is event time. Real camera observations use source time (PTS seconds
within the camera epoch; see timebase.py); unit tests may use any consistent clock. Analytics
must compare timestamps only within one camera epoch/segment time domain.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class TrackKey:
    """Immutable camera-local identity; numeric tracker IDs are scoped by a new epoch."""

    camera_id: str
    camera_epoch: str
    local_track_id: str

    def __post_init__(self) -> None:
        for name in ("camera_id", "camera_epoch", "local_track_id"):
            value = str(getattr(self, name)).strip()
            if not value:
                raise ValueError(f"{name} must not be empty")
            object.__setattr__(self, name, value)

    def as_string(self) -> str:
        return f"{self.camera_id}:{self.camera_epoch}:{self.local_track_id}"

    def __str__(self) -> str:
        return self.as_string()


@dataclass(frozen=True, slots=True)
class Observation:
    key: TrackKey
    timestamp: float
    position: tuple[float, float] | None = None
    floor_id: str | None = None
    zone_id: str | None = None
    world_position: tuple[float, float] | None = None
    confirmed: bool = True
    tracking_state: str = "CONFIRMED"
    continuity_id: str | None = None
    speed_mps: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.timestamp):
            raise ValueError("Observation timestamp must be finite")
        for point in (self.position, self.world_position):
            if point is not None and any(not math.isfinite(value) for value in point):
                raise ValueError("Observation coordinates must be finite")
