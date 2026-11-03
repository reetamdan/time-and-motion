"""Explicit time domains for camera observations.

Two domains are kept separate and never substituted for each other:

* Source (event) time: the media presentation timeline of one camera epoch, from the
  decoder's PTS. Crossings, dwell, queue durations, speeds and source-time history use it.
  It is relative media time, not a UTC capture time; a file clip carries no trustworthy
  capture clock, so none is invented.
* Receive (process) time: when the backend accepted a frame, as a monotonic value for
  freshness/elapsed calculations and a UTC stamp for presentation. It describes transport
  and processing, never when the scene happened.

A source timeline is scoped to (camera, epoch). A PTS that goes backwards or jumps forward
by more than the configured gap starts a new *segment*; time-dependent analytics reset at a
segment or epoch boundary rather than bridging the discontinuity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ReplayMode = Literal["paced", "unpaced"]
REPLAY_MODES: tuple[ReplayMode, ...] = ("paced", "unpaced")

SOURCE_TIME_BASIS = "source PTS seconds within the camera epoch (relative media time, not UTC capture time)"
RECEIVE_TIME_BASIS = "backend receive time (monotonic for freshness; UTC for presentation only)"
ANALYTICS_FPS_BASIS = (
    "Frames consumed per second of backend host time over a 2 s window. In paced replay it tracks the "
    "source rate; in unpaced replay it is local processing throughput. It is not a live-camera frame rate "
    "or a multi-camera capacity measurement."
)


@dataclass(frozen=True, slots=True)
class SourceTime:
    seconds: float
    pts_ns: int
    segment: int
    discontinuity: str | None = None


class SourceTimeline:
    """Maps one epoch's PTS stream to source seconds plus a discontinuity segment index."""

    def __init__(self, max_forward_gap_seconds: float = 2.0) -> None:
        if max_forward_gap_seconds <= 0:
            raise ValueError("max_forward_gap_seconds must be positive")
        self._max_gap_ns = int(max_forward_gap_seconds * 1e9)
        self._previous_pts: int | None = None
        self.segment = 0

    def observe(self, pts_ns: int) -> SourceTime:
        if isinstance(pts_ns, bool) or not isinstance(pts_ns, int) or pts_ns < 0:
            raise ValueError("pts_ns must be a non-negative integer")
        discontinuity = None
        previous = self._previous_pts
        if previous is not None:
            if pts_ns <= previous:
                discontinuity = "BACKWARD_OR_REPEATED_PTS"
            elif pts_ns - previous > self._max_gap_ns:
                discontinuity = "FORWARD_JUMP"
            if discontinuity:
                self.segment += 1
        self._previous_pts = pts_ns
        return SourceTime(pts_ns / 1e9, pts_ns, self.segment, discontinuity)


class ReplayPacer:
    """Schedules frame release on the source timeline (paced) or not at all (unpaced).

    The anchor is re-established at each segment so a discontinuity never produces a
    huge or negative wait.
    """

    def __init__(self, mode: ReplayMode) -> None:
        if mode not in REPLAY_MODES:
            raise ValueError(f"Unsupported replay mode {mode!r}")
        self.mode = mode
        self._anchor: tuple[int, float, float] | None = None  # (segment, source seconds, host seconds)

    def delay(self, source: SourceTime, host_now: float) -> float:
        """Seconds to wait before releasing this frame (0 for unpaced or late frames)."""
        if self.mode == "unpaced":
            return 0.0
        if self._anchor is None or self._anchor[0] != source.segment:
            self._anchor = (source.segment, source.seconds, host_now)
            return 0.0
        _, source_start, host_start = self._anchor
        return max(0.0, (source.seconds - source_start) - (host_now - host_start))
