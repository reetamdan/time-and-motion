"""Camera-local, source-time analytics for one camera epoch.

Everything here runs on source (event) time only, so the same observations produce the
same results whether replay is paced, unpaced or delivered in bursts. Wall-clock/receive
time is deliberately not an input.

Outputs:
* visible-count history samples, one per history_sample_seconds of source time;
* directional crossings of image_pixels boundaries (camera-local, not facility portals).

A new source segment (PTS discontinuity) closes the open history bucket and resets
crossing state, so no duration or crossing spans a discontinuity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .crossing import Boundary, CrossingEvent, CrossingPolicy, DirectionalCrossing, IdempotencyCache
from .types import Observation

COUNT_BASIS = "tracked person detections in the frame (provisional detector; uncalibrated camera-local count)"


@dataclass(frozen=True, slots=True)
class CountSample:
    camera_id: str
    camera_epoch: str
    source_segment: int
    bucket_index: int
    source_time: float  # start of the bucket's first frame, source seconds
    frame_number: int | None
    visible_count: int  # count on the first frame of the bucket
    visible_mean: float
    visible_max: int
    frames: int


@dataclass(frozen=True, slots=True)
class ImageLine:
    id: str
    start: tuple[float, float]
    end: tuple[float, float]
    positive_direction: str
    owner: str
    frame_size: tuple[int, int]
    policy: CrossingPolicy


@dataclass(slots=True)
class _Bucket:
    segment: int
    index: int
    source_time: float
    frame_number: int | None
    first_count: int
    total: int = 0
    frames: int = 0
    maximum: int = 0


@dataclass(slots=True)
class FrameResult:
    samples: list[CountSample] = field(default_factory=list)
    crossings: list[tuple[ImageLine, CrossingEvent]] = field(default_factory=list)
    segment_reset: bool = False


class CameraLocalAnalytics:
    def __init__(self, camera_id: str, camera_epoch: str, sample_seconds: float,
                 image_lines: tuple[ImageLine, ...] = ()) -> None:
        if sample_seconds <= 0:
            raise ValueError("sample_seconds must be positive")
        self.camera_id = camera_id
        self.camera_epoch = camera_epoch
        self.sample_seconds = sample_seconds
        self.image_lines = image_lines
        self._segment: int | None = None
        self._bucket: _Bucket | None = None
        self._idempotency = IdempotencyCache()
        self._crossings: dict[str, DirectionalCrossing] = {}
        self._last_source_time: float | None = None

    def disable_lines(self, line_ids: set[str]) -> None:
        """Stop counting on lines whose geometry no longer matches the frames (e.g. resolution changed)."""
        self.image_lines = tuple(line for line in self.image_lines if line.id not in line_ids)
        for line_id in line_ids:
            self._crossings.pop(line_id, None)

    def _reset_segment(self, segment: int) -> None:
        self._segment = segment
        self._last_source_time = None
        self._crossings = {
            line.id: DirectionalCrossing(
                Boundary(f"{line.id}@seg{segment}", line.start, line.end, line.positive_direction,  # type: ignore[arg-type]
                         line.owner, line.policy),
                self._idempotency,
            )
            for line in self.image_lines
        }

    def _close_bucket(self) -> CountSample | None:
        bucket, self._bucket = self._bucket, None
        if bucket is None or bucket.frames == 0:
            return None
        return CountSample(self.camera_id, self.camera_epoch, bucket.segment, bucket.index, bucket.source_time,
                           bucket.frame_number, bucket.first_count, bucket.total / bucket.frames, bucket.maximum, bucket.frames)

    def flush(self) -> list[CountSample]:
        sample = self._close_bucket()
        return [sample] if sample else []

    def process_frame(self, source_time: float, segment: int, observations: tuple[Observation, ...],
                      frame_number: int | None = None) -> FrameResult:
        if not math.isfinite(source_time):
            raise ValueError("source_time must be finite")
        result = FrameResult()
        if self._segment != segment:
            closed = self._close_bucket()
            if closed:
                result.samples.append(closed)
            result.segment_reset = self._segment is not None
            self._reset_segment(segment)
        elif self._last_source_time is not None and source_time <= self._last_source_time:
            # Out-of-order frame inside a segment cannot be applied to time-ordered state.
            return result
        self._last_source_time = source_time

        current = [o for o in observations if o.key.camera_id == self.camera_id and o.key.camera_epoch == self.camera_epoch
                   and o.confirmed and o.tracking_state.upper() == "CONFIRMED"]
        count = len({o.key for o in current})
        index = int(math.floor(source_time / self.sample_seconds))
        if self._bucket is not None and self._bucket.index != index:
            closed = self._close_bucket()
            if closed:
                result.samples.append(closed)
        if self._bucket is None:
            self._bucket = _Bucket(segment, index, source_time, frame_number, count)
        bucket = self._bucket
        bucket.total += count
        bucket.frames += 1
        bucket.maximum = max(bucket.maximum, count)

        for line in self.image_lines:
            detector = self._crossings[line.id]
            for observation in current:
                point = observation.metadata.get("image_bottom_center_pixels")
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    continue
                pixel = Observation(key=observation.key, timestamp=source_time, position=(float(point[0]), float(point[1])),
                                    confirmed=True, tracking_state="CONFIRMED", continuity_id=observation.continuity_id)
                event = detector.update(pixel, now=source_time)
                if event is not None:
                    result.crossings.append((line, event))
        return result
