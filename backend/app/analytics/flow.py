"""Bounded rolling directional flow for observed or inferred portal crossings."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class FlowEvent:
    event_id: str
    timestamp: float
    direction: Literal["UP", "DOWN"]
    observed: bool = True


@dataclass(frozen=True, slots=True)
class FlowRate:
    direction: Literal["UP", "DOWN"]
    rate_per_minute: float
    window_seconds: float
    observed_count: int
    inferred_count: int


class RollingFlow:
    def __init__(self, window_seconds: float = 60.0) -> None:
        if window_seconds <= 0:
            raise ValueError("Flow window must be positive")
        self.window_seconds = window_seconds
        self._events: deque[FlowEvent] = deque()
        self._event_ids: set[str] = set()

    def add(self, event: FlowEvent) -> bool:
        if event.event_id in self._event_ids:
            return False
        if self._events and event.timestamp < self._events[-1].timestamp:
            return False
        self._events.append(event)
        self._event_ids.add(event.event_id)
        return True

    def _prune(self, now: float) -> None:
        while self._events and self._events[0].timestamp < now - self.window_seconds:
            self._event_ids.discard(self._events.popleft().event_id)

    def rate(self, direction: Literal["UP", "DOWN"], now: float) -> FlowRate:
        self._prune(now)
        matching = [event for event in self._events if event.direction == direction]
        observed = sum(event.observed for event in matching)
        inferred = len(matching) - observed
        return FlowRate(
            direction=direction,
            rate_per_minute=len(matching) * 60.0 / self.window_seconds,
            window_seconds=self.window_seconds,
            observed_count=observed,
            inferred_count=inferred,
        )
