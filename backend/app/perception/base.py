"""Clean boundary for a real observation-producing DeepStream or other local adapter."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Protocol

from ..analytics.types import Observation
from ..configuration import CameraConfig


class PerceptionUnavailable(RuntimeError):
    pass


class PerceptionCleanupError(RuntimeError):
    """An owned runtime resource (e.g. a camera container) could not be confirmed released."""


class PerceptionStartError(PerceptionUnavailable):
    """Camera start failed. resource_allocated says whether anything (e.g. a container) was launched.

    False: the failure happened before launch, so there is nothing to release.
    True: something was launched; the caller must reconcile it through stop_camera.
    """

    def __init__(self, message: str, *, resource_allocated: bool, cleanup_confirmed: bool | None = None) -> None:
        super().__init__(message)
        self.resource_allocated = resource_allocated
        self.cleanup_confirmed = cleanup_confirmed


@dataclass(frozen=True, slots=True)
class ResourceIdentity:
    """Exact identity of a runtime resource an adapter launches for one camera epoch."""

    kind: str  # e.g. "container"
    name: str  # exact name, e.g. "aegis-cam-01-<epoch>"
    runtime: str  # e.g. "docker@wsl:Ubuntu-22.04"


@dataclass(frozen=True, slots=True)
class AdapterAvailability:
    name: str
    available: bool
    detail: str
    blockers: tuple[str, ...] = ()
    runtime_detected: bool = False
    # Structured detail (artifact identity checks, capability-cache state); never used as a substitute for available.
    diagnostics: dict = field(default_factory=dict, compare=False, hash=False)


@dataclass(frozen=True, slots=True)
class ObservationBatch:
    """One decoded frame's observations with both time domains (see analytics/timebase.py).

    received_at is backend receive time (time.monotonic) and drives freshness only.
    source_time is the frame's source/event time (PTS seconds within the epoch); when present,
    observation timestamps are in that domain. received_at_utc is presentation-only.
    """

    camera_id: str
    camera_epoch: str
    observations: tuple[Observation, ...]
    received_at: float
    source_fps: float | None = None
    analytics_fps: float | None = None
    frame_age_ms: float | None = None
    frame_age_basis: str | None = None
    dropped_frames: int = 0
    metadata: dict[str, object] = field(default_factory=dict)
    source_time: float | None = None
    source_pts_ns: int | None = None
    source_segment: int = 0
    source_discontinuity: str | None = None
    frame_number: int | None = None
    received_at_utc: str | None = None
    replay_mode: str | None = None


# Terminal stream outcomes reported through ObservationSink.end_of_stream. An operator stop or a
# cancelled consumer reports nothing: the stop path owns those.
CLEAN_EOF = "CLEAN_EOF"  # every accepted frame drained, reader EOF without error, extractor exited 0
ABNORMAL_TERMINATION = "ABNORMAL_TERMINATION"  # reader error, non-zero exit, or EOF while the process still runs
CONSUMER_FAILED = "CONSUMER_FAILED"  # the backend consumer raised; remaining frames were not processed


class ObservationSink(Protocol):
    async def emit(self, batch: ObservationBatch) -> None: ...

    async def end_of_stream(self, camera_epoch: str, outcome: str, detail: str | None = None) -> None: ...


class PerceptionAdapter(ABC):
    @abstractmethod
    def availability(self) -> AdapterAvailability:
        """Inspect actual runtime/artifact availability; never infer it from configuration alone.

        May block (e.g. container probes): async callers must run it in a worker thread.
        """

    def cached_availability(self) -> AdapterAvailability | None:
        """Last real availability result without probing (None if never probed). Must not block."""
        return self.availability()

    @abstractmethod
    async def start_camera(
        self, camera: CameraConfig, camera_epoch: str, sink: ObservationSink, replay_mode: str = "paced"
    ) -> None:
        """Start one source and deliver only genuinely decoded/detected observations.

        replay_mode applies to file sources: "paced" releases frames on the source timeline,
        "unpaced" processes as fast as possible (evaluation/benchmark).
        """

    @abstractmethod
    async def stop_camera(self, camera_id: str, camera_epoch: str) -> None:
        """Release the camera epoch's resources; raise PerceptionCleanupError if release is not confirmed.

        Must be safe to call for an epoch whose start failed, or that is unknown (nothing to release).
        """

    def resource_identity(self, camera_id: str, camera_epoch: str) -> ResourceIdentity | None:
        """Identity of the resource start_camera would launch, known before launch (None: no external resource)."""
        return None

    async def adopt_resource(self, camera_id: str, camera_epoch: str, identity: ResourceIdentity | None) -> None:
        """Take back ownership of a resource recorded by a previous backend process so stop_camera can release it."""
        return None

    @abstractmethod
    async def shutdown(self) -> None: ...
