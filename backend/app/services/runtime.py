"""Isolated per-camera demo lifecycle and real observation intake."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..analytics.camera_local import COUNT_BASIS, CameraLocalAnalytics, CountSample, ImageLine
from ..analytics.facility import FACILITY_EMIT_SECONDS, FacilityAnalytics, capability_matrix
from ..analytics.crossing import CrossingPolicy
from ..analytics.freshness import FreshnessPolicy
from ..analytics.timebase import REPLAY_MODES
from ..configuration import AppConfig, CameraConfig
from ..db.repository import Repository
from ..perception.base import (
    ObservationBatch, PerceptionAdapter, PerceptionCleanupError, PerceptionStartError, ResourceIdentity,
)
from ..timeutils import iso_now, to_iso
from .sequence import SequenceState

LOGGER = logging.getLogger("cctv.runtime")
# Recent per-frame tracked boxes kept per camera for the dashboard overlay (about 5 s at 30 fps).
DETECTION_BUFFER_FRAMES = 150
# Loop origins kept per looping camera (older passes can no longer be mapped for evidence).
MAX_LOOP_ORIGINS = 64


@dataclass(slots=True)
class CameraRuntimeState:
    health: str = "OFFLINE"
    epoch: str | None = None
    source_available: bool = False
    visible_count: int | None = None
    source_fps: float | None = None
    analytics_fps: float | None = None
    frame_age_ms: float | None = None
    frame_age_basis: str | None = None
    dropped_frames: int = 0
    last_good_monotonic: float | None = None
    unavailable_reason: str | None = "Demo run not started"
    perception_started: bool = False
    replay_mode: str | None = None
    source_position_s: float | None = None
    source_segment: int | None = None
    source_discontinuities: int = 0
    backend_queue_ms: float | None = None
    persisted_count_samples: int = 0
    camera_line_crossings: int = 0
    # CLEAN_EOF / ABNORMAL_TERMINATION / CONSUMER_FAILED once the source ended without an operator stop.
    stream_outcome: str | None = None
    stream_outcome_detail: str | None = None
    # Frames whose person detections reached the detector's per-class top-k (output may be truncated).
    frames_at_detection_limit: int = 0
    detection_limit: int | None = None
    # Source time of this epoch's first consumed frame and the latest frame number: they map the
    # pipeline PTS timeline onto the source file's own timestamps (evidence extraction).
    source_origin_s: float | None = None
    source_frame_number: int | None = None
    # Live sources: reconnect attempts since the last frame, and when the next attempt is due (UTC ISO).
    reconnect_attempt: int = 0
    next_reconnect_at: str | None = None
    # Continuous file replay: frames per pass of the file (frame n is file frame n mod loop_frames),
    # the current pass, and the source time of each pass's first file frame (maps passes onto the file).
    loop: bool = False
    loop_frames: int | None = None
    loop_index: int | None = None
    loop_origins: dict[int, float] = field(default_factory=dict)
    loop_ends: dict[int, float] = field(default_factory=dict)
    # Position of the latest frame on the source file's own timeline (seconds from its first frame).
    file_position_s: float | None = None
    # Recent tracked person boxes (pipeline pixels) for the dashboard overlay; never used for counts.
    frame_size: tuple[int, int] | None = None
    detections: deque = field(default_factory=lambda: deque(maxlen=DETECTION_BUFFER_FRAMES))


class CameraRuntimeSupervisor:
    def __init__(
        self, config: AppConfig, repository: Repository, adapter: PerceptionAdapter, sequence: SequenceState,
        frame_counter: Callable[[Path], int | None] | None = None,
    ) -> None:
        self.config = config
        # Counts the video frames of a file source (ffprobe); needed only to map looped replay onto the file.
        self._frame_counter = frame_counter
        self.repository = repository
        self.adapter = adapter
        self.sequence = sequence
        self._states = {camera.id: self._initial_state(camera) for camera in config.cameras}
        self._state_lock = threading.RLock()
        self._lifecycle_lock = asyncio.Lock()
        self._run_id: str | None = None
        self._display_run_id: str | None = None
        self._run_status = "IDLE"
        self._perception_active = False
        self._started: dict[str, str] = {}
        # camera_id -> detail for owned resources whose release could not be confirmed.
        self._cleanup_failures: dict[str, str] = {}
        self._freshness = FreshnessPolicy(config.analytics.health.stale_after_ms)
        self._replay_mode = config.runtime.replay.mode
        self._analytics: dict[tuple[str, str], CameraLocalAnalytics] = {}
        # Calibration-gated facility analytics for the active run (None when no run is active).
        self._facility: FacilityAnalytics | None = None
        self._zone_state: dict[str, tuple[int | None, str]] = {}
        self._floor_state: dict[str, tuple[int | None, str]] = {}
        self._building_recorded = False
        self._facility_task: asyncio.Task | None = None
        # Live-source reconnects (camera_id -> pending task) and the stall watchdog.
        self._reconnect_tasks: dict[str, asyncio.Task] = {}
        self._reconnect_attempts: dict[str, int] = {}
        self._watchdog_task: asyncio.Task | None = None
        # Pipeline loads in flight (replay.startup_concurrency); created per run inside its event loop.
        self._start_slots: asyncio.Semaphore | None = None

    @staticmethod
    def _file_source_available(camera: CameraConfig) -> bool:
        if camera.source_type != "file" or camera.source_path is None:
            return False
        try:
            return camera.source_path.is_file() and camera.source_path.stat().st_size > 0
        except OSError:
            return False

    @classmethod
    def _initial_state(cls, camera: CameraConfig) -> CameraRuntimeState:
        available = cls._file_source_available(camera)
        reason = None
        if camera.source_type == "file" and not available:
            reason = "Authorized source clip not installed"
        elif available:
            reason = "Demo run not started; source-only preview is available"
        else:
            reason = "Demo run not started"
        return CameraRuntimeState(source_available=available, unavailable_reason=reason)

    def initialize(self) -> None:
        raise RuntimeError("initialize() is replaced by 'await recover()', which reconciles owned resources first")

    async def recover(self) -> None:
        """Reconcile resources recorded by a previous backend process before anything new can start.

        Every unreleased ownership row is adopted by the adapter and given one bounded release attempt.
        A run is marked STOPPED only once all of its resources are confirmed released; otherwise it is
        restored here as STOP_FAILED (restart-blocking, retried by the operator's stop).
        """
        async with self._lifecycle_lock:
            for row in self.repository.unreleased_resources():
                identity = (ResourceIdentity(row["resource_kind"], row["resource_name"], row["runtime"])
                            if row["resource_name"] else None)
                await self._release(row["run_id"], row["camera_id"], row["camera_epoch"], identity, adopt=True)
            unresolved = self.repository.unreleased_resources()
            unresolved_runs = {row["run_id"] for row in unresolved}
            abandoned = self.repository.latest_run(active_only=True)
            while abandoned is not None and abandoned["id"] not in unresolved_runs:
                reason = "Backend process restarted; owned resources reconciled and confirmed released"
                self.repository.update_run_status(abandoned["id"], "STOPPED", reason)
                self.repository.end_camera_sessions(abandoned["id"])
                abandoned = self.repository.latest_run(active_only=True)
            if not unresolved:
                return
            run_id = unresolved[-1]["run_id"]
            failures = {row["camera_id"]: f"{row['resource_name'] or 'resource'} not confirmed released after restart: "
                                          f"{row['last_error'] or 'unknown'}" for row in unresolved}
            for row in unresolved:
                self.repository.update_run_status(row["run_id"], "STOP_FAILED",
                                                  "Backend restarted with unconfirmed resource cleanup")
                self.repository.open_alert(
                    row["run_id"], f"camera-cleanup-failed:{row['camera_id']}", "CRITICAL", "camera",
                    "Camera stop not confirmed", failures[row["camera_id"]][:500], "camera", row["camera_id"],
                    {"retry": "POST /api/v1/demo/stop", "resource": row["resource_name"]}, "runtime-v1",
                )
            with self._state_lock:
                self._run_id = run_id
                self._display_run_id = run_id
                self._run_status = "STOP_FAILED"
                self._cleanup_failures = failures
                for row in unresolved:
                    self._started[row["camera_id"]] = row["camera_epoch"]
                    state = self._states[row["camera_id"]]
                    state.health = "OFFLINE"
                    state.epoch = row["camera_epoch"]
                    state.perception_started = False
                    state.unavailable_reason = f"Stop not confirmed: {failures[row['camera_id']][:200]}"
            self.sequence.bump()

    async def _release(self, run_id: str, camera_id: str, epoch: str, identity: ResourceIdentity | None = None,
                       adopt: bool = False) -> str | None:
        """One bounded release attempt through the adapter; returns None on confirmed release, else the error."""
        try:
            if adopt:
                await self.adapter.adopt_resource(camera_id, epoch, identity)
            await self.adapter.stop_camera(camera_id, epoch)
        except Exception as exc:  # PerceptionCleanupError or an unknown outcome: not a confirmed release
            detail = str(exc) if isinstance(exc, PerceptionCleanupError) else f"{type(exc).__name__}: {exc}"
            self.repository.update_resource_state(run_id, camera_id, epoch, "UNRESOLVED", detail[:500], attempted=True)
            return detail
        self.repository.update_resource_state(run_id, camera_id, epoch, "RELEASED", None, attempted=True)
        return None

    def current_run(self) -> tuple[str | None, str, bool]:
        with self._state_lock:
            return self._display_run_id, self._run_status, self._perception_active

    def states(self) -> dict[str, CameraRuntimeState]:
        with self._state_lock:
            return copy.deepcopy(self._states)

    def availability(self):
        """Fresh probe; may block. Use from a worker thread."""
        return self.adapter.availability()

    def cached_availability(self):
        """Last probe result without blocking (None if never probed)."""
        return self.adapter.cached_availability()

    @staticmethod
    def _safe_source_error(camera: CameraConfig, error: Exception) -> str:
        detail = str(error)
        sensitive_values = [camera.source]
        if camera.source_path is not None:
            sensitive_values.append(str(camera.source_path))
        for value in sensitive_values:
            if value:
                detail = detail.replace(value, camera.source_label)
        return detail[:300]

    def cleanup_failures(self) -> dict[str, str]:
        with self._state_lock:
            return dict(self._cleanup_failures)

    def replay_mode(self) -> str:
        return self._replay_mode

    def image_lines(self, camera_id: str) -> tuple[ImageLine, ...]:
        crossing = self.config.analytics.crossing
        policy = CrossingPolicy(
            dead_band=crossing.dead_band, debounce_seconds=crossing.debounce_seconds,
            confirmation_seconds=crossing.confirmation_seconds, min_track_seconds=crossing.min_track_seconds,
            min_track_observations=crossing.min_track_observations, min_displacement=crossing.min_displacement,
            stale_after_ms=crossing.stale_after_ms,
        )
        return tuple(
            ImageLine(b.id, tuple(b.start), tuple(b.end), b.positive_direction, b.owner, tuple(b.frame_size), policy)  # type: ignore[arg-type]
            for b in self.config.analytics.boundaries
            if b.enabled and b.camera_id == camera_id and b.coordinate_space == "image_pixels" and b.frame_size
        )

    async def start(self, replay_mode: str | None = None) -> None:
        async with self._lifecycle_lock:
            if self._run_id is not None:
                if self._run_status == "STOP_FAILED":
                    # Never presented as a successful start: the run's cleanup must be confirmed by stop first.
                    names = ", ".join(sorted(self._cleanup_failures)) or "unknown camera"
                    raise PerceptionCleanupError(
                        f"Start refused: run {self._run_id} is STOP_FAILED; cleanup not confirmed for {names}. "
                        "Retry POST /api/v1/demo/stop first")
                # A healthy active run (STARTING/RUNNING/DEGRADED) is idempotent: no new epoch.
                return
            unreleased = self.repository.unreleased_resources()
            if unreleased:
                names = ", ".join(sorted({row["resource_name"] or row["camera_id"] for row in unreleased}))
                raise PerceptionCleanupError(f"Start refused: owned resources not confirmed released: {names}")
            mode = replay_mode or self.config.runtime.replay.mode
            if mode not in REPLAY_MODES:
                raise ValueError(f"Unsupported replay mode {mode!r}")
            self._replay_mode = mode
            run = self.repository.create_run(self.config.configuration_revision)
            run_id = run["id"]
            self._facility = FacilityAnalytics(self.config)
            self._zone_state = {}
            self._floor_state = {}
            self._building_recorded = False
            with self._state_lock:
                self._run_id = run_id
                self._display_run_id = run_id
                self._run_status = "STARTING"
                self._perception_active = False
            # Container probes block for seconds; keep the event loop (and every camera consumer) running.
            availability = await asyncio.to_thread(self.adapter.availability)
            self.repository.record_run_provenance(run_id, self.provenance(availability, mode))
            # Pipelines start concurrently, at most replay.startup_concurrency loading at once (a slot frees at
            # the first frame or a failed start): a slow or failing pipeline never blocks the others. A pipeline
            # is one camera, or up to replay.cameras_per_pipeline file cameras batched together.
            self._start_slots = asyncio.Semaphore(self.config.runtime.replay.startup_concurrency)
            await asyncio.gather(*(
                self._start_group_isolated(run_id, group, availability.available, availability.name)
                for group in self.pipeline_groups(availability.available)
            ))
            if not availability.available:
                self.repository.open_alert(
                    run_id, "perception-unavailable", "WARNING", "perception", "Perception runtime unavailable",
                    availability.detail, "system", None,
                    {"blockers": list(availability.blockers), "runtime_detected": availability.runtime_detected},
                    "runtime-v1",
                )
            states = self.states()
            enabled = [camera for camera in self.config.cameras if camera.enabled]
            healthy = all(states[camera.id].perception_started for camera in enabled)
            status = "RUNNING" if healthy and enabled else "DEGRADED"
            with self._state_lock:
                self._run_status = status
            self.repository.update_run_status(run_id, status)
            if self.config.zones or self.config.portals:
                # Facility results must expire even when no frame arrives to trigger evaluation.
                self._facility_task = asyncio.create_task(self._facility_ticker(run_id))
            if any(camera.enabled and camera.source_type == "rtsp" for camera in self.config.cameras):
                self._watchdog_task = asyncio.create_task(self._live_watchdog(run_id))
            self.sequence.bump()

    # ------------------------------------------------------------------ live sources (RTSP)

    def _schedule_reconnect(self, run_id: str, camera_id: str, reason: str) -> None:
        """Bounded exponential backoff (health.reconnect_backoff_seconds doubling up to the max); other cameras unaffected."""
        if self._run_id != run_id or camera_id in self._reconnect_tasks or self._run_status == "STOP_FAILED":
            return
        health = self.config.analytics.health
        attempt = self._reconnect_attempts.get(camera_id, 0) + 1
        self._reconnect_attempts[camera_id] = attempt
        delay = min(health.max_reconnect_backoff_seconds, health.reconnect_backoff_seconds * 2 ** (attempt - 1))
        due = time.time() + delay
        with self._state_lock:
            state = self._states.get(camera_id)
            if state is not None:
                state.health = "OFFLINE"
                state.visible_count = None
                state.reconnect_attempt = attempt
                state.next_reconnect_at = to_iso(datetime.fromtimestamp(due, timezone.utc))
                state.unavailable_reason = f"Live source lost ({reason[:160]}); reconnect attempt {attempt} in {delay:g} s"
        camera = self.config.camera_map[camera_id]
        self.repository.open_alert(
            run_id, f"camera-reconnecting:{camera_id}", "WARNING", "camera_offline", "Live camera reconnecting",
            f"{camera.name}: {reason[:300]}; attempt {attempt} in {delay:g} s (backoff capped at {health.max_reconnect_backoff_seconds:g} s)",
            "camera", camera_id, {"attempt": attempt, "delay_seconds": delay}, "runtime-v1")
        self._reconnect_tasks[camera_id] = asyncio.create_task(self._reconnect(run_id, camera_id, delay))
        self.sequence.bump()

    async def _reconnect(self, run_id: str, camera_id: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            async with self._lifecycle_lock:
                if self._run_id != run_id or self._run_status not in ("RUNNING", "DEGRADED"):
                    return
                old_epoch = self._started.get(camera_id)
                if old_epoch is not None:
                    self._flush_analytics(run_id, camera_id, old_epoch)
                    failure = await self._release(run_id, camera_id, old_epoch)
                    if failure is not None:
                        # The previous container is not confirmed gone: never start a second one beside it.
                        self._cleanup_failures[camera_id] = failure
                        self.repository.open_alert(
                            run_id, f"camera-cleanup-failed:{camera_id}", "CRITICAL", "camera", "Camera stop not confirmed",
                            failure[:500], "camera", camera_id, {"retry": "POST /api/v1/demo/stop"}, "runtime-v1")
                        return
                    self._started.pop(camera_id, None)
                    if self._facility is not None:
                        self._facility.remove_camera(camera_id)
                availability = self.adapter.cached_availability() or await asyncio.to_thread(self.adapter.availability)
                await self._start_camera_isolated(run_id, self.config.camera_map[camera_id], availability.available, availability.name)
        finally:
            self._reconnect_tasks.pop(camera_id, None)
        state = self.states().get(camera_id)
        if state is not None and not state.perception_started and self._run_id == run_id:
            self._schedule_reconnect(run_id, camera_id, state.unavailable_reason or "reconnect failed")

    async def _live_watchdog(self, run_id: str) -> None:
        """A live camera that stops delivering frames (stalled connection, no EOS) is released and reconnected."""
        stall = self.config.analytics.health.live_stall_seconds
        while True:
            await asyncio.sleep(1.0)
            if self._run_id != run_id:
                return
            now = time.monotonic()
            for camera in self.config.cameras:
                if camera.source_type != "rtsp" or camera.id in self._reconnect_tasks:
                    continue
                with self._state_lock:
                    state = self._states.get(camera.id)
                    stalled = (state is not None and state.perception_started and state.stream_outcome is None
                               and state.last_good_monotonic is not None and now - state.last_good_monotonic > stall)
                    epoch = state.epoch if state else None
                if stalled and epoch:
                    self.complete_stream(camera.id, epoch, "ABNORMAL_TERMINATION", f"no frame for more than {stall:g} s")

    async def _facility_ticker(self, run_id: str) -> None:
        while True:
            await asyncio.sleep(FACILITY_EMIT_SECONDS)
            if self._run_id != run_id or self._facility is None:
                return
            try:
                if self._facility_evaluate(run_id, None):
                    self.sequence.bump()
            except Exception:
                LOGGER.exception("Facility evaluation tick failed")

    def pipeline_groups(self, adapter_available: bool) -> list[list[CameraConfig]]:
        """Cameras in launch units: alone, or up to cameras_per_pipeline compatible file cameras together.

        Only enabled file cameras whose clip is present and that share loop and analysis_frame_interval
        are batched, in cameras.yaml order; everything else (live, disabled, missing) starts alone.
        """
        size = min(self.config.runtime.replay.cameras_per_pipeline, self.adapter.max_cameras_per_pipeline())
        if size <= 1 or not adapter_available:
            return [[camera] for camera in self.config.cameras]
        groups: list[list[CameraConfig]] = []
        pending: dict[tuple[bool, int], list[CameraConfig]] = {}
        for camera in self.config.cameras:
            if camera.enabled and camera.source_type == "file" and self._file_source_available(camera):
                bucket = pending.setdefault((camera.loop, camera.analysis_frame_interval), [])
                bucket.append(camera)
                if len(bucket) == size:
                    groups.append(pending.pop((camera.loop, camera.analysis_frame_interval)))
            else:
                groups.append([camera])
        groups.extend(pending.values())
        return groups

    async def _start_group_isolated(
        self, run_id: str, cameras: list[CameraConfig], adapter_available: bool, adapter_name: str
    ) -> None:
        if len(cameras) == 1:
            await self._start_camera_isolated(run_id, cameras[0], adapter_available, adapter_name)
            return
        try:
            async with self._start_slots if self._start_slots is not None else contextlib.nullcontext():
                await self._start_shared(run_id, cameras, adapter_name)
        except Exception as exc:
            # Bookkeeping failed before the per-camera results: every camera of the pipeline is reported.
            for camera in cameras:
                if not self._states[camera.id].perception_started:
                    self._camera_start_failed(run_id, camera, exc)

    async def _start_shared(self, run_id: str, cameras: list[CameraConfig], adapter_name: str) -> None:
        """Start file cameras that share one pipeline; each succeeds or fails as its own camera."""
        epochs = [str(uuid.uuid4()) for _ in cameras]
        identity = self.adapter.pipeline_identity([(camera.id, epoch) for camera, epoch in zip(cameras, epochs)])
        states = [await self._prepare_launch(run_id, camera, epoch, adapter_name, True, identity)
                  for camera, epoch in zip(cameras, epochs)]
        starts = [(camera, epoch, _ObservationSink(self, camera.id, epoch)) for camera, epoch in zip(cameras, epochs)]
        try:
            results = await self.adapter.start_cameras(starts, self._replay_mode)
        except asyncio.CancelledError as exc:
            for camera, epoch in zip(cameras, epochs):
                await asyncio.shield(self._reconcile_failed_start(run_id, camera, epoch, exc))
            raise
        except Exception as exc:
            results = [RuntimeError(f"{type(exc).__name__}: {exc}") for _ in cameras]
        for camera, epoch, state, result in zip(cameras, epochs, states, results):
            if result is None:
                self._mark_started(run_id, camera, epoch, state)
                self._publish_state(run_id, camera, epoch, state)
            else:
                await self._reconcile_failed_start(run_id, camera, epoch, result)
                self._camera_start_failed(run_id, camera, result)

    async def _start_camera_isolated(
        self, run_id: str, camera: CameraConfig, adapter_available: bool, adapter_name: str
    ) -> None:
        try:
            if self._start_slots is None:
                await self._start_camera(run_id, camera, adapter_available, adapter_name)
            else:
                async with self._start_slots:
                    await self._start_camera(run_id, camera, adapter_available, adapter_name)
        except Exception as exc:
            self._camera_start_failed(run_id, camera, exc)

    def _camera_start_failed(self, run_id: str, camera: CameraConfig, exc: BaseException) -> None:
        # A source/adapter failure is isolated to this camera (or its shared pipeline); other sources continue.
        state = CameraRuntimeState(
            health="OFFLINE", source_available=False,
            epoch=getattr(exc, "camera_epoch", None),
            unavailable_reason=f"Source initialization failed: {self._safe_source_error(camera, exc)}",
        )
        with self._state_lock:
            self._states[camera.id] = state
        self.repository.update_camera_health(
            run_id, camera.id, None, "OFFLINE", camera.calibration_status, state.unavailable_reason
        )
        self.repository.open_alert(
            run_id, f"camera-initialization:{camera.id}", "CRITICAL", "camera", "Camera initialization failed",
            f"{camera.name}: {state.unavailable_reason}", "camera", camera.id,
            {"source_label": camera.source_label}, "runtime-v1",
        )
        if camera.source_type == "rtsp" and not isinstance(exc, asyncio.CancelledError):
            self._schedule_reconnect(run_id, camera.id, state.unavailable_reason or "start failed")

    async def _start_camera(
        self, run_id: str, camera: CameraConfig, adapter_available: bool, adapter_name: str
    ) -> None:
        if not camera.enabled:
            with self._state_lock:
                self._states[camera.id] = CameraRuntimeState(unavailable_reason="Camera disabled in configuration")
            self.repository.update_camera_health(
                run_id, camera.id, None, "OFFLINE", camera.calibration_status, "Camera disabled in configuration"
            )
            return
        source_available = True
        reason: str | None = None
        if camera.source_type == "file":
            source_available = self._file_source_available(camera)
            if not source_available:
                reason = "Authorized source clip not installed"
        elif not adapter_available:
            source_available = False
            reason = "RTSP source/perception adapter is unavailable"
        if not source_available:
            with self._state_lock:
                self._states[camera.id] = CameraRuntimeState(source_available=False, unavailable_reason=reason)
            self.repository.update_camera_health(run_id, camera.id, None, "OFFLINE", camera.calibration_status, reason)
            self.repository.open_alert(
                run_id, f"camera-offline:{camera.id}", "CRITICAL", "camera_offline", "Camera source unavailable",
                f"{camera.name}: {reason}", "camera", camera.id,
                {"source_type": camera.source_type, "source_label": camera.source_label}, "runtime-v1",
            )
            return

        epoch = str(uuid.uuid4())
        identity = self.adapter.resource_identity(camera.id, epoch) if adapter_available else None
        state = await self._prepare_launch(run_id, camera, epoch, adapter_name, adapter_available, identity)
        if adapter_available:
            try:
                await self.adapter.start_camera(camera, epoch, _ObservationSink(self, camera.id, epoch), self._replay_mode)
            except asyncio.CancelledError as exc:
                # Cancelled mid-start (shutdown/client cancel): a resource may already exist. Reconcile it
                # to completion (shielded) before propagating, so nothing is left owned-but-untracked.
                await asyncio.shield(self._reconcile_failed_start(run_id, camera, epoch, exc))
                raise
            except Exception as exc:
                await self._reconcile_failed_start(run_id, camera, epoch, exc)
                raise
            self._mark_started(run_id, camera, epoch, state)
        self._publish_state(run_id, camera, epoch, state)

    async def _prepare_launch(self, run_id: str, camera: CameraConfig, epoch: str, adapter_name: str, launch: bool,
                              identity: ResourceIdentity | None) -> CameraRuntimeState:
        """Everything before the adapter starts a camera: state, session row, analytics, durable ownership."""
        state = CameraRuntimeState(
            health="DEGRADED", epoch=epoch, source_available=True,
            unavailable_reason="Source available; perception runtime unavailable (source preview only)",
        )
        if camera.loop and camera.source_type == "file":
            state.loop = True
            state.loop_frames = await asyncio.to_thread(self._count_frames, camera)
        self.repository.start_camera_session(run_id, camera.id, epoch, "SOURCE_AVAILABLE", adapter_name)
        if launch:
            self._analytics[(camera.id, epoch)] = CameraLocalAnalytics(
                camera.id, epoch, self.config.runtime.replay.history_sample_seconds, self.image_lines(camera.id)
            )
            state.replay_mode = self._replay_mode
            # Ownership is durable *before* launch: a failed start or a crash cannot orphan the resource.
            # Cameras sharing a pipeline all record the shared resource.
            self.repository.record_resource(run_id, camera.id, epoch, adapter_name, identity.kind if identity else None,
                                            identity.name if identity else None, identity.runtime if identity else None)
        return state

    def _mark_started(self, run_id: str, camera: CameraConfig, epoch: str, state: CameraRuntimeState) -> None:
        self.repository.update_resource_state(run_id, camera.id, epoch, "ACTIVE")
        if self._facility is not None:
            self._facility.reset_camera(camera.id, epoch)
        state.perception_started = True
        state.health = "HEALTHY" if camera.calibration_status == "VALID" else "CALIBRATION WARNING"
        state.unavailable_reason = (
            None if camera.calibration_status == "VALID" else "Calibration missing; geometry-dependent analytics unavailable"
        )
        with self._state_lock:
            self._started[camera.id] = epoch
            self._perception_active = True

    def _publish_state(self, run_id: str, camera: CameraConfig, epoch: str, state: CameraRuntimeState) -> None:
        with self._state_lock:
            self._states[camera.id] = state
        self.repository.update_camera_health(
            run_id, camera.id, epoch, state.health, camera.calibration_status, state.unavailable_reason
        )

    async def _reconcile_failed_start(self, run_id: str, camera: CameraConfig, epoch: str, error: BaseException) -> None:
        """Release whatever a failed start may have launched; keep it owned and retryable if unconfirmed."""
        self._analytics.pop((camera.id, epoch), None)
        setattr(error, "camera_epoch", epoch)
        if isinstance(error, PerceptionStartError) and not error.resource_allocated:
            self.repository.update_resource_state(run_id, camera.id, epoch, "RELEASED", "Start failed before launch")
            return
        failure = await self._release(run_id, camera.id, epoch)
        if failure is None:
            return
        with self._state_lock:
            self._started[camera.id] = epoch  # stop/reset retry it through the normal path
            self._cleanup_failures[camera.id] = failure
        self.repository.open_alert(
            run_id, f"camera-cleanup-failed:{camera.id}", "CRITICAL", "camera", "Camera stop not confirmed",
            f"Startup failed after launch and cleanup is unconfirmed: {failure}"[:500], "camera", camera.id,
            {"retry": "POST /api/v1/demo/stop"}, "runtime-v1",
        )
        error.args = (f"{error}; launched resource cleanup NOT confirmed (retry with stop)",)

    def _count_frames(self, camera: CameraConfig) -> int | None:
        if self._frame_counter is None or camera.source_path is None:
            return None
        try:
            frames = self._frame_counter(camera.source_path)
        except Exception as exc:  # the loop still runs; only file mapping of later passes is unavailable
            LOGGER.warning("Could not count the frames of %s: %s", camera.source_label, exc)
            return None
        return frames if isinstance(frames, int) and frames > 0 else None

    def evidence_origin(self, camera_id: str, source_time: float) -> tuple[float | None, str | None]:
        """(pipeline origin of the file pass containing source_time, refusal reason).

        Without looping this is the epoch's first frame. A looping file restarts its own timeline on
        every pass, so the origin is the source time of that pass's first file frame.
        """
        with self._state_lock:
            state = self._states[camera_id]
            if not state.loop:
                return state.source_origin_s, None
            if not state.loop_frames:
                return None, ("The looping source's frame count is unknown (ffprobe), so its replay passes cannot be "
                              "mapped onto the file")
            # Only a pass with an observed first frame and a reached end can map this time.
            # Picking the nearest preceding origin silently maps a missing pass onto an older one.
            candidates = [origin for index, origin in state.loop_origins.items()
                          if origin <= source_time + 1e-9
                          and source_time <= state.loop_ends.get(index, origin) + 1e-9]
            if not candidates:
                return None, "That source time is outside the observed, retained replay passes mapped onto the file"
            return max(candidates), None

    def detections(self, camera_id: str, after_frame: int | None = None, limit: int = 90,
                   after_epoch: str | None = None) -> dict[str, Any]:
        """Recent tracked person boxes for the dashboard overlay (display only; counts come from analytics)."""
        with self._state_lock:
            state = self._states[camera_id]
            if after_epoch is not None and after_epoch != state.epoch:
                after_frame = None  # frame numbers restart at zero in a new epoch
            frames = [frame for frame in state.detections if after_frame is None or frame["frame_number"] > after_frame]
            age = (time.monotonic() - state.last_good_monotonic) * 1000 if state.last_good_monotonic is not None else None
            return {
                "camera_id": camera_id, "camera_epoch": state.epoch, "replay_mode": state.replay_mode,
                "loop": state.loop, "loop_index": state.loop_index,
                "frame_width": state.frame_size[0] if state.frame_size else None,
                "frame_height": state.frame_size[1] if state.frame_size else None,
                "latest_age_ms": age,
                "box_format": "[x, y, width, height, track_id] in pipeline frame pixels (tracker box when available)",
                "frames": frames[-limit:] if limit > 0 else [],
            }

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            run_id = self._run_id
            if run_id is None:
                return
            task, self._facility_task = self._facility_task, None
            if task is not None:
                task.cancel()
            watchdog, self._watchdog_task = self._watchdog_task, None
            if watchdog is not None:
                watchdog.cancel()
            for reconnect in list(self._reconnect_tasks.values()):
                reconnect.cancel()
            self._reconnect_tasks.clear()
            self._reconnect_attempts.clear()
            failures: dict[str, str] = {}
            # Started cameras plus any durable ownership row of this run that is not yet released
            # (e.g. a start interrupted between launch and tracking): stop never reports STOPPED over them.
            targets = dict(self._started)
            for row in self.repository.unreleased_resources():
                if row["run_id"] == run_id:
                    targets.setdefault(row["camera_id"], row["camera_epoch"])
            for camera_id, epoch in targets.items():
                self._flush_analytics(run_id, camera_id, epoch)
                if self._facility is not None:
                    self._facility.remove_camera(camera_id)
                owner_run = self._resource_run(camera_id, epoch) or run_id
                failure = await self._release(owner_run, camera_id, epoch)
                if failure is not None:
                    failures[camera_id] = failure
                    continue
                self._started.pop(camera_id, None)
                if camera_id in self._cleanup_failures:
                    self.repository.resolve_alert_by_key(run_id, f"camera-cleanup-failed:{camera_id}", "Cleanup confirmed on retry")
            with self._state_lock:
                self._cleanup_failures = failures
            if failures:
                for camera_id, detail in failures.items():
                    self.repository.open_alert(
                        run_id, f"camera-cleanup-failed:{camera_id}", "CRITICAL", "camera", "Camera stop not confirmed",
                        detail[:500], "camera", camera_id, {"retry": "POST /api/v1/demo/stop"}, "runtime-v1",
                    )
                reason = "Cleanup not confirmed for " + ", ".join(sorted(failures))
                self.repository.update_run_status(run_id, "STOP_FAILED", reason)
                with self._state_lock:
                    # The run stays open (and start() refuses) until a retry confirms release.
                    self._run_status = "STOP_FAILED"
                    for camera_id, detail in failures.items():
                        state = self._states[camera_id]
                        state.health = "OFFLINE"
                        state.perception_started = False
                        state.visible_count = None
                        state.unavailable_reason = f"Stop not confirmed: {detail[:200]}"
                self.sequence.bump()
                return
            self.repository.end_camera_sessions(run_id)
            self.repository.update_run_status(run_id, "STOPPED", "Operator stopped demo")
            with self._state_lock:
                self._run_id = None
                self._run_status = "STOPPED"
                for camera in self.config.cameras:
                    available = self._states[camera.id].source_available
                    self._states[camera.id] = CameraRuntimeState(
                        source_available=available,
                        unavailable_reason="Demo run stopped; source-only preview is available" if available else "Demo run stopped",
                    )
            self.sequence.bump()

    def provenance(self, availability, replay_mode: str) -> dict:
        """What this run actually used: recorded identities, recomputed hashes, and their comparison."""
        models = self.config.models
        selected = models.selected
        profile = selected.runtime_profile if selected else None
        return {
            "schema_version": 1,
            "configuration_revision": self.config.configuration_revision,
            "configuration_files": self.config.file_revisions,
            "replay_mode": replay_mode,
            "adapter": {"name": availability.name, "available": availability.available,
                        "blockers": list(availability.blockers), "runtime_detected": availability.runtime_detected},
            "selection_mode": models.selection_mode,
            "detector": None if selected is None else {
                "id": selected.id, "acceptance": selected.acceptance, "revision": selected.revision,
                "evaluation_ref": selected.evaluation_ref, "precision": selected.precision,
                "input_shape": selected.input_shape, "class_map": {str(k): v for k, v in selected.class_map.items()},
                "profile": None if profile is None else {
                    "person_class_id": profile.person_class_id, "person_label": profile.person_label,
                    "pipeline_frame": [profile.pipeline_width, profile.pipeline_height],
                    "tracker_frame": [profile.tracker_width, profile.tracker_height],
                    "source_geometry_policy": profile.source_geometry_policy,
                    "preprocessing": profile.preprocessing, "parser": profile.parser.model_dump(mode="json"),
                },
            },
            "tracker": {"name": models.tracker.name, "revision": models.tracker.revision,
                        "expected_sha256": models.tracker.sha256},
            "image": models.pipeline.image if models.pipeline else None,
            "artifact_checks": availability.diagnostics.get("artifacts", []),
            "capability": availability.diagnostics.get("capability"),
            "cameras": {camera.id: {"enabled": camera.enabled, "source_label": camera.source_label,
                                    "calibration_status": camera.calibration_status,
                                    "calibration_revision": camera.calibration_revision}
                        for camera in self.config.cameras},
        }

    def capabilities(self) -> dict:
        """Exactly which analytics are available now, and the reason for each that is not."""
        with self._state_lock:
            running = {camera_id for camera_id, state in self._states.items() if state.perception_started}
            facility = self._facility
            geometry = dict(facility.geometry) if facility is not None else {}
        freshness = facility.freshness() if facility is not None else {}
        return capability_matrix(self.config, geometry, running, freshness)

    def _facility_frame(self, run_id: str, batch: ObservationBatch, camera, observations) -> None:
        """Geometry gating on the first frame, then calibrated facility analytics (no-op when uncalibrated)."""
        facility = self._facility
        if facility is None or batch.source_time is None:
            return
        width, height = batch.metadata.get("frame_width"), batch.metadata.get("frame_height")
        for problem in facility.observe_frame_size(batch.camera_id, width if isinstance(width, int) else None,
                                                   height if isinstance(height, int) else None):
            self.repository.open_alert(
                run_id, f"geometry-invalid:{batch.camera_id}:{batch.camera_epoch}:{abs(hash(problem)) % 10**8}", "WARNING",
                "calibration", "Camera geometry invalid for current frames", f"{camera.name}: {problem}"[:500],
                "camera", batch.camera_id, {"frame_size": [width, height]}, "runtime-v1")
        geometry = facility.geometry.get(batch.camera_id)
        analytics = self._analytics.get((batch.camera_id, batch.camera_epoch))
        if geometry is not None and geometry.invalid_lines and analytics is not None:
            analytics.disable_lines(set(geometry.invalid_lines))
        for event in facility.update(batch.camera_id, batch.source_time, observations, received_at=batch.received_at,
                                     epoch=batch.camera_epoch, segment=batch.source_segment):
            occurred = batch.received_at_utc or iso_now()
            envelope = {
                "event_id": event["event_id"], "event_type": "facility_boundary_crossing", "run_id": run_id,
                "producer_id": f"facility:{batch.camera_id}", "producer_sequence": batch.frame_number or 0,
                "idempotency_key": f"{run_id}:{event['event_id']}", "occurred_at": occurred, "emitted_at": iso_now(),
                "camera_id": batch.camera_id, "camera_epoch": batch.camera_epoch,
                "payload": {**event, "coordinate_space": "floor_map", "occurred_at_basis": "backend receive time (UTC); "
                            "source_time_s is event time"},
            }
            self.repository.record_crossing(envelope, {"id": event["event_id"], "boundary_id": event["boundary_id"],
                                                       "direction": event["direction"], "track_key": event["track_key"]})
        # Throttled per camera on that camera's own source timeline (timelines of different cameras differ).
        if facility.due(batch.source_time, batch.camera_id):
            self._facility_evaluate(run_id, batch.source_time)

    def _facility_evaluate(self, run_id: str, source_now: float | None) -> bool:
        """Evaluate facility figures now (receive-time freshness) and persist what changed."""
        facility = self._facility
        if facility is None:
            return False
        result = facility.evaluate(time.monotonic(), source_now)
        stamp = iso_now()
        changed = False
        for zone_id, zone in result["zones"].items():
            key = (zone.visible, zone.quality)
            if zone.visible is None and zone_id not in self._zone_state:
                continue  # never available in this run: nothing to supersede, nothing to persist
            if zone.visible is not None or self._zone_state.get(zone_id) != key:
                self.repository.record_occupancy(run_id, "zone", zone_id, stamp, visible=zone.visible,
                                                 quality=zone.quality if zone.visible is not None else "UNAVAILABLE",
                                                 coverage={"cameras": list(zone.cameras), "reason": zone.reason})
                changed |= self._zone_state.get(zone_id) != key
                self._zone_state[zone_id] = key
        for floor_id, floor in result["floors"].items():
            key = (floor.visible, floor.quality)
            if floor.visible is None and floor_id not in self._floor_state:
                continue
            if floor.visible is not None or self._floor_state.get(floor_id) != key:
                self.repository.record_occupancy(run_id, "floor", floor_id, stamp, visible=floor.visible,
                                                 quality=floor.quality if floor.visible is not None else "UNAVAILABLE",
                                                 coverage=floor.as_dict())
                changed |= self._floor_state.get(floor_id) != key
                self._floor_state[floor_id] = key
        for zone_id, queue in result["queues"].items():
            if queue.quality != "UNAVAILABLE" or zone_id in self._zone_state:
                self.repository.record_queue(run_id, zone_id, stamp, queue_size=queue.queue_size, joins=queue.joins,
                                             leaves=queue.leaves, max_wait_seconds=queue.max_wait_seconds,
                                             quality=queue.quality, details={"occluded": queue.occluded_members,
                                                                             "unresolved": queue.unresolved_members})
        for zone, transition in result["congestion_transitions"]:
            changed = True
            self.repository.record_congestion(run_id, zone.id, stamp, level=transition.current, value=transition.value,
                                              units=transition.metric, measurements=transition.measurements,
                                              thresholds=transition.thresholds, rule_revision=transition.rule_revision,
                                              quality="OBSERVED" if transition.current != "UNAVAILABLE" else "UNAVAILABLE")
            alert_key = f"congestion:{zone.id}"
            if transition.current in ("CONGESTED", "SEVERELY CONGESTED"):
                self.repository.open_alert(
                    run_id, alert_key, "CRITICAL" if transition.current == "SEVERELY CONGESTED" else "WARNING",
                    "congestion", f"{zone.name} {transition.current.lower()}",
                    f"{zone.name}: {transition.metric} {transition.value} reached {transition.current} "
                    f"(persisted {self.config.analytics.congestion.persist_seconds} s)", "zone", zone.id,
                    {**transition.measurements, "thresholds": transition.thresholds}, transition.rule_revision)
            elif transition.previous in ("CONGESTED", "SEVERELY CONGESTED"):
                self.repository.resolve_alert_by_key(run_id, alert_key, f"Recovered to {transition.current}")
        ledger = result["ledger"]
        if ledger is not None:
            self._building_recorded = True
            self.repository.record_occupancy(
                run_id, "building", "facility", stamp, expected=ledger.expected, visible=result["building_visible"],
                estimated=ledger.estimated, discrepancy=ledger.discrepancy, allocation_discrepancy=ledger.allocation_discrepancy,
                quality=ledger.quality, balances={"floors": ledger.floor_balances, "entries": ledger.external_entries,
                                                  "exits": ledger.external_exits, "anomalies": list(ledger.anomalies)})
        elif self._building_recorded:
            # A boundary camera went stale/ended: the last building figure is superseded, not kept.
            self._building_recorded = False
            changed = True
            self.repository.record_occupancy(run_id, "building", "facility", stamp, quality="UNAVAILABLE",
                                             coverage={"reason": "a facility boundary camera is not current",
                                                       "freshness": result["freshness"]})
        for flow in result["flows"]:
            self.repository.record_flow(run_id, flow["portal_id"], flow["direction"], stamp,
                                        rate_per_minute=flow["rate_per_minute"], window_seconds=flow["window_seconds"],
                                        observed_count=flow["observed_count"], inferred_count=flow["inferred_count"],
                                        quality="ESTIMATED")
        for decision in facility.take_decisions():
            changed = True
            self.repository.record_association({**decision, "run_id": run_id})
        return changed

    def _resource_run(self, camera_id: str, epoch: str) -> str | None:
        rows = [row for row in self.repository.unreleased_resources()
                if row["camera_id"] == camera_id and row["camera_epoch"] == epoch]
        return rows[0]["run_id"] if rows else None

    async def reset(self) -> None:
        await self.stop()
        if self.cleanup_failures():
            raise PerceptionCleanupError("Reset refused: camera cleanup is not confirmed")
        self.repository.reset_run_scoped()
        with self._state_lock:
            self._display_run_id = None
            self._run_status = "IDLE"
            self._perception_active = False
            self._states = {camera.id: self._initial_state(camera) for camera in self.config.cameras}
        self.sequence.bump()

    async def shutdown(self) -> None:
        await self.stop()
        try:
            await self.adapter.shutdown()
        except PerceptionCleanupError as exc:
            # Process exit cannot be blocked; the failure is logged for operator inspection.
            logging.getLogger("cctv.runtime").error("Shutdown left unconfirmed cleanup: %s", exc)

    def _persist_samples(self, run_id: str, samples: list[CountSample], replay_mode: str | None) -> int:
        if not samples:
            return 0
        received = iso_now()
        return self.repository.record_count_samples(run_id, [
            {"camera_id": sample.camera_id, "camera_epoch": sample.camera_epoch, "source_segment": sample.source_segment,
             "bucket_index": sample.bucket_index, "source_time_s": sample.source_time, "frame_number": sample.frame_number,
             "visible_count": sample.visible_count, "visible_mean": sample.visible_mean, "visible_max": sample.visible_max,
             "frames": sample.frames, "replay_mode": replay_mode, "received_at_utc": received, "count_basis": COUNT_BASIS}
            for sample in samples
        ])

    def _flush_analytics(self, run_id: str, camera_id: str, epoch: str) -> None:
        analytics = self._analytics.pop((camera_id, epoch), None)
        if analytics is None:
            return
        try:
            inserted = self._persist_samples(run_id, analytics.flush(), self._replay_mode)
            with self._state_lock:
                state = self._states.get(camera_id)
                if state is not None and state.epoch == epoch:
                    state.persisted_count_samples += inserted
        except Exception:
            LOGGER.exception("Could not persist final count history for %s/%s", camera_id, epoch)

    def complete_stream(self, camera_id: str, epoch: str, outcome: str, detail: str | None = None) -> None:
        """Source ended without an operator stop. Idempotent per (camera, epoch).

        CLEAN_EOF: every accepted frame was drained, so the final partial history bucket is complete and
        is persisted now (the run stays active; the camera goes stale by the normal freshness rule).
        Other outcomes keep the open bucket for the stop flush and raise an alert.
        """
        with self._state_lock:
            state = self._states.get(camera_id)
            run_id = self._run_id
            if state is None or state.epoch != epoch or run_id is None or state.stream_outcome is not None:
                return
            state.stream_outcome = outcome
            state.stream_outcome_detail = detail
        if self._facility is not None:
            # The camera's facility contribution ends with its stream, whatever its age.
            self._facility.end_camera(camera_id, outcome)
            self._facility_evaluate(run_id, None)
        if outcome == "CLEAN_EOF":
            analytics = self._analytics.pop((camera_id, epoch), None)
            inserted = self._persist_samples(run_id, analytics.flush(), self._replay_mode) if analytics else 0
            with self._state_lock:
                if state.epoch == epoch:
                    state.persisted_count_samples += inserted
        else:
            camera = self.config.camera_map[camera_id]
            self.repository.open_alert(
                run_id, f"camera-stream-ended:{camera_id}:{epoch}", "WARNING", "camera", "Camera stream ended abnormally",
                f"{camera.name}: {outcome}: {detail or 'no detail'}"[:500], "camera", camera_id,
                {"outcome": outcome}, "runtime-v1",
            )
            if camera.source_type == "rtsp":
                self._schedule_reconnect(run_id, camera_id, f"{outcome}: {detail or 'no detail'}")
        self.sequence.bump()

    def update_observations(self, batch: ObservationBatch) -> None:
        with self._state_lock:
            state = self._states.get(batch.camera_id)
            if state is None or state.epoch != batch.camera_epoch or not state.perception_started:
                return
            run_id = self._run_id
            camera = self.config.camera_map[batch.camera_id]
            # Observation timestamps are source time when the batch carries it; freshness of the
            # frame's own observations is evaluated in that same domain.
            observation_now = batch.source_time if batch.source_time is not None else batch.received_at
            fresh_observations = tuple(
                observation
                for observation in batch.observations
                if observation.key.camera_id == batch.camera_id
                and observation.key.camera_epoch == batch.camera_epoch
                and self._freshness.evaluate(observation, observation_now).fresh
            )
            state.visible_count = len({observation.key for observation in fresh_observations})
            state.source_fps = batch.source_fps
            state.analytics_fps = batch.analytics_fps
            state.frame_age_ms = batch.frame_age_ms
            state.frame_age_basis = batch.frame_age_basis
            state.dropped_frames = batch.dropped_frames
            # Receive-time domain: drives freshness/staleness of the camera's displayed values.
            state.last_good_monotonic = batch.received_at
            if batch.camera_id in self._reconnect_attempts:
                # First frame after a reconnect: the source is back; the backoff starts over next time.
                self._reconnect_attempts.pop(batch.camera_id, None)
                state.reconnect_attempt = 0
                state.next_reconnect_at = None
                reconnected = True
            else:
                reconnected = False
            state.health = "HEALTHY" if camera.calibration_status == "VALID" else "CALIBRATION WARNING"
            state.unavailable_reason = None if camera.calibration_status == "VALID" else "Calibration missing; geometry-dependent analytics unavailable"
            state.source_position_s = batch.source_time
            if state.source_origin_s is None and batch.source_time is not None and batch.source_segment == 0:
                state.source_origin_s = batch.source_time
            state.source_frame_number = batch.frame_number
            state.source_segment = batch.source_segment if batch.source_time is not None else None
            state.replay_mode = batch.replay_mode or state.replay_mode
            queue_ms = batch.metadata.get("backend_queue_ms")
            state.backend_queue_ms = float(queue_ms) if isinstance(queue_ms, (int, float)) else None
            limit = batch.metadata.get("detection_limit")
            at_limit = bool(batch.metadata.get("at_detection_limit"))
            state.detection_limit = limit if isinstance(limit, int) else state.detection_limit
            first_truncation = at_limit and state.frames_at_detection_limit == 0
            state.frames_at_detection_limit += at_limit
            self._track_file_position(state, batch, fresh_observations)
            analytics = self._analytics.get((batch.camera_id, batch.camera_epoch))
        if run_id is not None:
            self._facility_frame(run_id, batch, camera, fresh_observations)
        if analytics is not None and batch.source_time is not None and run_id is not None:
            result = analytics.process_frame(batch.source_time, batch.source_segment, fresh_observations, batch.frame_number)
            inserted = self._persist_samples(run_id, result.samples, batch.replay_mode)
            recorded = 0
            for line, event in result.crossings:
                crossing = {
                    "event_id": event.event_id, "idempotency_key": event.idempotency_key, "camera_id": batch.camera_id,
                    "camera_epoch": batch.camera_epoch, "source_segment": batch.source_segment, "line_id": line.id,
                    "coordinate_space": "image_pixels", "direction": event.direction, "track_key": str(event.track_key),
                    "source_time_s": event.occurred_at, "received_at_utc": batch.received_at_utc or iso_now(),
                }
                envelope = {
                    "event_id": event.event_id, "event_type": "camera_line_crossing", "run_id": run_id,
                    "producer_id": f"camera-local:{batch.camera_id}", "producer_sequence": batch.frame_number or 0,
                    "idempotency_key": event.idempotency_key, "occurred_at": crossing["received_at_utc"],
                    "emitted_at": iso_now(), "camera_id": batch.camera_id, "camera_epoch": batch.camera_epoch,
                    "payload": {**crossing, "occurred_at_basis": "backend receive time (UTC); source_time_s is event time",
                                "scope": "camera-local image line; not a facility portal or building entry/exit"},
                }
                recorded += self.repository.record_camera_crossing(run_id, crossing, envelope)
            if reconnected:
                self.repository.resolve_alert_by_key(run_id, f"camera-reconnecting:{batch.camera_id}", "Live source frames resumed")
            if first_truncation:
                self.repository.open_alert(
                    run_id, f"detection-limit:{batch.camera_id}:{batch.camera_epoch}", "WARNING", "perception",
                    "Detector output may be truncated",
                    f"{camera.name}: frame {batch.frame_number} reached the detector top-k of {state.detection_limit} "
                    "person detections; counts in such frames are lower bounds", "camera", batch.camera_id,
                    {"detection_limit": state.detection_limit, "frame_number": batch.frame_number}, "runtime-v1",
                )
            if batch.source_discontinuity:
                self.repository.open_alert(
                    run_id, f"source-time-discontinuity:{batch.camera_id}:{batch.camera_epoch}:{batch.source_segment}",
                    "WARNING", "camera", "Source timestamp discontinuity",
                    f"{camera.name}: {batch.source_discontinuity} at frame {batch.frame_number}; time-based analytics restarted",
                    "camera", batch.camera_id, {"segment": batch.source_segment}, "runtime-v1",
                )
            with self._state_lock:
                if state.epoch == batch.camera_epoch:
                    state.persisted_count_samples += inserted
                    state.camera_line_crossings += recorded
                    if batch.source_discontinuity:
                        state.source_discontinuities += 1
        self.sequence.bump()


    @staticmethod
    def _track_file_position(state: CameraRuntimeState, batch: ObservationBatch, observations) -> None:
        """Map the frame onto the file timeline (looping aware) and keep its boxes for the overlay."""
        source_time, frame_number = batch.source_time, batch.frame_number
        origin = state.source_origin_s if (state.source_segment or 0) == 0 else None
        if (state.source_segment or 0) != 0:
            state.loop_origins.clear()
            state.loop_ends.clear()
        elif state.loop and state.loop_frames and isinstance(frame_number, int) and source_time is not None:
            index, offset = divmod(frame_number, state.loop_frames)
            state.loop_index = index
            if offset == 0:
                state.loop_origins[index] = source_time
                for old in sorted(state.loop_origins)[:-MAX_LOOP_ORIGINS]:
                    state.loop_origins.pop(old, None)
                    state.loop_ends.pop(old, None)
            origin = state.loop_origins.get(index)
            if origin is not None:
                state.loop_ends[index] = source_time
        elif state.loop:
            origin = None  # frame count unknown: wraps cannot be located, so no pass is mapped onto the file
        state.file_position_s = source_time - origin if source_time is not None and origin is not None else None
        width, height = batch.metadata.get("frame_width"), batch.metadata.get("frame_height")
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            state.frame_size = (width, height)
        boxes = []
        for observation in observations:
            box = observation.metadata.get("tracker_bbox_xywh_pixels") or observation.metadata.get("bbox_xywh_pixels")
            if isinstance(box, dict):
                boxes.append([round(float(box["x"]), 1), round(float(box["y"]), 1), round(float(box["width"]), 1),
                              round(float(box["height"]), 1), observation.key.local_track_id])
        state.detections.append({
            "frame_number": frame_number, "source_time_s": source_time,
            "file_position_s": None if state.file_position_s is None else round(state.file_position_s, 4),
            "loop_index": state.loop_index, "boxes": boxes,
        })


class _ObservationSink:
    def __init__(self, supervisor: CameraRuntimeSupervisor, camera_id: str, epoch: str) -> None:
        self.supervisor = supervisor
        self.camera_id = camera_id
        self.epoch = epoch

    async def emit(self, batch: ObservationBatch) -> None:
        if batch.camera_id != self.camera_id or batch.camera_epoch != self.epoch:
            raise ValueError("Observation batch camera/epoch does not match its source session")
        self.supervisor.update_observations(batch)

    async def end_of_stream(self, camera_epoch: str, outcome: str, detail: str | None = None) -> None:
        if camera_epoch != self.epoch:
            raise ValueError("End-of-stream epoch does not match its source session")
        self.supervisor.complete_stream(self.camera_id, camera_epoch, outcome, detail)
