"""Live Windows/WSL/Docker Service Maker process and stdout observation consumer."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import json
import logging
import math
import os
import shutil
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from typing import Any, Callable, Mapping, TextIO
from urllib.parse import urlsplit

from ..analytics.timebase import ANALYTICS_FPS_BASIS, REPLAY_MODES, ReplayPacer, SourceTimeline
from ..analytics.types import Observation, TrackKey
from ..timeutils import iso_now
from ..configuration import AppConfig, CameraConfig, DetectorRuntimeProfile
from .base import (
    ABNORMAL_TERMINATION, CLEAN_EOF, CONSUMER_FAILED, ObservationBatch, ObservationSink, PerceptionCleanupError, PerceptionStartError, PerceptionUnavailable,
    ResourceIdentity,
)

LOGGER = logging.getLogger("cctv.perception.service_maker")
FRAME_PREFIX = "AEGIS_FRAME_V1:"
FRAME_PREFIX_BYTES = FRAME_PREFIX.encode("ascii")
FRAME_QUEUE_SIZE = 128
FRAME_SCHEMAS = {"1.0", "1.1"}
# Service Maker prints this and then blocks in its main loop without exiting (no frame will ever come),
# e.g. when nvinfer cannot deserialize the engine for lack of GPU memory. Seen in the container log.
PIPELINE_START_FAILED = b"Unable to set the pipeline to the playing state"
LOG_SCAN_SECONDS = 1.0
# Per-frame producer/read/release timestamps for end-to-end lead measurement (validation only).
RELEASE_LOG_ENV = "CCTV_RELEASE_LOG"
# Paced replay: a short buffer so backpressure reaches the extractor and queued frames stay
# close to their release time (about 0.5 s at 30 fps).
PACED_FRAME_QUEUE_SIZE = 16
SOURCE_FORWARD_GAP_SECONDS = 2.0
# docker stop grace; the container runs under --init so SIGTERM normally ends it well before this.
STOP_GRACE_SECONDS = 5
DOCKER_QUERY_TIMEOUT_SECONDS = 10
# Bounded escalation after a failed/unconfirmed stop: kill + forced removal of the exact owned container.
CLEANUP_ESCALATION_ATTEMPTS = 2
# docker run --rm removes the container asynchronously after it exits.
REMOVAL_VERIFY_SECONDS = 5.0
OWNED_CONTAINER_NAME = re.compile(r"^aegis-[A-Za-z0-9_.-]+$")
ANALYTICS_RATE_HORIZON_SECONDS = 2.0
ANALYTICS_RATE_MIN_SPAN_SECONDS = 0.5


class ArrivalRate:
    """Consumer frame rate over a sliding time window of high-resolution arrival times.

    Frames reach the backend in pipe bursts, so neither a per-frame 1/delta (undefined when
    two frames share a ~15.6 ms Windows monotonic tick) nor a fixed frame-count window
    (measures burst drain speed) reflects throughput. The rate is reported only once the
    retained arrivals span at least min_span seconds; until then it is unknown (None).
    """

    def __init__(self, horizon: float = ANALYTICS_RATE_HORIZON_SECONDS,
                 min_span: float = ANALYTICS_RATE_MIN_SPAN_SECONDS) -> None:
        self._horizon = horizon
        self._min_span = min_span
        self._arrivals: deque[float] = deque()

    def add(self, arrived_at: float) -> float | None:
        self._arrivals.append(arrived_at)
        while arrived_at - self._arrivals[0] > self._horizon:
            self._arrivals.popleft()
        span = arrived_at - self._arrivals[0]
        return (len(self._arrivals) - 1) / span if span >= self._min_span else None


def host_route() -> str:
    """Where this backend runs relative to the container runtime.

    windows-wsl   Windows backend; docker is reached through ``wsl.exe -d <distro> -e`` (arguments passed verbatim, no login-shell re-parsing) (validated route).
    linux-docker  backend inside WSL (or native Linux, e.g. the future A6000 host) calling docker directly.
    unsupported   no reachable container runtime from this process.
    """
    if os.name == "nt":
        return "windows-wsl"
    if shutil.which("docker"):
        return "linux-docker"
    return "unsupported"


RTSP_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_rtsp_source(template: str, environ: Mapping[str, str] | None = None) -> tuple[str, list[str]]:
    """Expand ``${VAR}`` credential references from the environment at launch time.

    Returns the URL and the secret strings (resolved values, userinfo, full URL) to redact from any
    text that may reach logs, alerts, or API responses. A missing variable is named, never guessed.
    """
    environ = os.environ if environ is None else environ
    missing = [name for name in RTSP_VARIABLE.findall(template) if name not in environ]
    if missing:
        raise PerceptionUnavailable(f"RTSP credential variable(s) not set: {', '.join(sorted(set(missing)))}")
    url = RTSP_VARIABLE.sub(lambda match: environ[match.group(1)], template)
    parsed = urlsplit(url)
    secrets = [environ[name] for name in RTSP_VARIABLE.findall(template) if environ[name]]
    if parsed.username or parsed.password:
        secrets += [value for value in (parsed.password, f"{parsed.username}:{parsed.password}") if value]
    if parsed.query:
        secrets.append(parsed.query)
    secrets.append(url)
    return url, sorted(set(secrets), key=len, reverse=True)


def redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def runtime_prefix(distro: str) -> tuple[str, ...]:
    """Command prefix that reaches the docker host: wsl.exe from Windows, nothing from Linux."""
    return ("wsl.exe", "-d", distro, "-e") if os.name == "nt" else ()


def wsl_path(path: Path) -> str:
    """Convert a Windows project path without invoking a shell or guessing the cwd."""
    value = str(path.resolve())
    if os.name != "nt":
        return value
    if len(value) < 3 or value[1] != ":":
        raise ValueError(f"Cannot convert path to WSL: {value}")
    return f"/mnt/{value[0].lower()}/{value[2:].replace(chr(92), '/').lstrip('/')}"


def workspace_path(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("Service Maker paths must remain inside the project workspace") from exc
    return "/workspace" if not relative.parts else f"/workspace/{relative.as_posix()}"


@dataclass(frozen=True, slots=True)
class DockerResult:
    command: tuple[str, ...]
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and self.error is None

    def as_dict(self) -> dict[str, Any]:
        return {"command": list(self.command[-4:]), "returncode": self.returncode, "timed_out": self.timed_out,
                "stderr": self.stderr.strip()[:300], "error": self.error}


@dataclass(slots=True)
class ContainerCleanup:
    container_name: str
    confirmed_absent: bool
    attempts: list[dict[str, Any]] = field(default_factory=list)
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class PersonClass:
    """The detector's own (class_id, label) for a person; normalized to the application's person class."""

    class_id: int
    label: str

    @classmethod
    def of(cls, profile: DetectorRuntimeProfile) -> "PersonClass":
        return cls(profile.person_class_id, profile.person_label)


def geometry_check(profile: DetectorRuntimeProfile, record: dict[str, Any]) -> tuple[str | None, str | None]:
    """(error, disclosure) for the first frame's source/pipeline geometry under the profile's policy."""
    source = (record.get("source_width"), record.get("source_height"))
    pipeline = (record.get("frame_width"), record.get("frame_height"))
    expected = (profile.pipeline_width, profile.pipeline_height)
    if pipeline != expected:
        return f"pipeline frame {pipeline} differs from the profile's {expected}", None
    if not all(isinstance(v, int) and v > 0 for v in source):
        return f"source geometry {source} was not reported by the extractor", None
    if source == expected:
        return None, None
    if profile.source_geometry_policy == "require_exact":
        return (f"source {source[0]}x{source[1]} is not the {expected[0]}x{expected[1]} input this profile's "
                "preprocessing contract requires; refusing to stretch it"), None
    source_aspect, pipeline_aspect = source[0] / source[1], expected[0] / expected[1]
    kind = "aspect ratio changed" if abs(source_aspect - pipeline_aspect) > 0.01 else "uniform rescale"
    return None, f"source {source[0]}x{source[1]} rescaled by nvstreammux to {expected[0]}x{expected[1]} ({kind})"


@dataclass(slots=True)
class _Session:
    camera_id: str
    camera_epoch: str
    container_name: str
    process: subprocess.Popen[bytes]
    output_path: Path
    log_handle: TextIO
    stop_event: asyncio.Event
    frame_queue: queue.Queue[dict[str, Any]] = field(default_factory=lambda: queue.Queue(maxsize=FRAME_QUEUE_SIZE))
    first_frame_event: threading.Event = field(default_factory=threading.Event)
    reader_done: threading.Event = field(default_factory=threading.Event)
    reader: threading.Thread | None = None
    malformed_frames: int = 0
    reader_error: str | None = None
    frames_consumed: int = 0
    frames_discarded_after_stop: int = 0
    task: asyncio.Task[None] | None = None
    replay_mode: str = "paced"
    end_outcome: str | None = None
    geometry_disclosure: str | None = None
    release_log: TextIO | None = None
    live: bool = False
    secrets: list[str] = field(default_factory=list)


class ServiceMakerSessions:
    """Own one container/process/consumer per camera epoch."""

    def __init__(self, config: AppConfig, on_launch_failure: Callable[[str], None] | None = None) -> None:
        self.config = config
        self._sessions: dict[tuple[str, str], _Session] = {}
        self._unresolved: dict[tuple[str, str], ContainerCleanup] = {}
        self._on_launch_failure = on_launch_failure

    def profile(self) -> tuple[str, DetectorRuntimeProfile]:
        selected = self.config.models.selected
        if selected is None or selected.runtime_profile is None:
            raise PerceptionUnavailable("The selected detector has no runtime_profile in configs/models.yaml")
        return selected.id, selected.runtime_profile

    def _command(self, camera: CameraConfig, epoch: str, output: Path, summary: Path,
                 replay_mode: str = "paced") -> list[str]:
        if host_route() == "unsupported":
            raise PerceptionUnavailable("No container runtime is reachable: run the backend on Windows (wsl.exe bridge) "
                                        "or inside WSL/Linux with docker on PATH")
        root = self.config.project_root
        wrapper = root / "scripts" / "deepstream.sh"
        live = camera.source_type == "rtsp"
        if live:
            source_argument, _ = resolve_rtsp_source(camera.source)
        else:
            if camera.source_path is None:
                raise PerceptionUnavailable("The file source has no resolved local path")
            source_argument = workspace_path(camera.source_path, root)
        pipeline = self.config.models.pipeline
        tracker = self.config.models.tracker
        if pipeline is None or tracker.config_path is None:
            raise PerceptionUnavailable("Pipeline and tracker identities are not configured")
        _, profile = self.profile()
        return [
            *runtime_prefix(self.config.runtime.telemetry.wsl_distro),
            "env", f"DEEPSTREAM_CONTAINER_NAME={self.container_name(camera.id, epoch)}",
            "bash", wsl_path(wrapper), "python3", workspace_path(pipeline.extractor_path, root),
            "--source", source_argument,
            "--infer-config", workspace_path(profile.infer_config_path, root),
            "--tracker-config", workspace_path(tracker.config_path, root),
            "--tracker-lib", tracker.library,
            "--width", str(profile.pipeline_width), "--height", str(profile.pipeline_height),
            "--tracker-width", str(profile.tracker_width), "--tracker-height", str(profile.tracker_height),
            "--output", workspace_path(output, root),
            "--run-summary", workspace_path(summary, root),
            *(["--output-max-mib", f"{self.config.runtime.replay.frame_log_max_mib:g}"]
              if self.config.runtime.replay.frame_log_max_mib else []),
            "--camera-id", camera.id,
            "--camera-epoch", epoch,
            # Source-side pacing (v2 extractor) only for paced FILE replay; live sources are already real time.
            *(["--pace", "source", "--pace-lead-ms", f"{pipeline.pace_lead_ms:g}"]
              if replay_mode == "paced" and pipeline.extractor_pacing == "source" and not live else []),
            # Continuous file replay (DeepStream file_loop): PTS and frame numbers stay monotonic across wraps.
            *(["--loop"] if camera.loop and not live else []),
            *(["--analysis-frame-interval", str(camera.analysis_frame_interval)]
              if camera.analysis_frame_interval > 1 else []),
        ]

    def container_name(self, camera_id: str, epoch: str) -> str:
        """The exact, deterministic container name this backend uses for one camera epoch."""
        return f"aegis-{self._safe_name(camera_id)}-{self._safe_name(epoch)}"

    def resource_identity(self, camera_id: str, epoch: str) -> ResourceIdentity:
        return ResourceIdentity("container", self.container_name(camera_id, epoch),
                                f"docker@wsl:{self.config.runtime.telemetry.wsl_distro}")

    def adopt(self, camera_id: str, epoch: str, identity: ResourceIdentity | None) -> None:
        """Re-own a container recorded by a previous backend process so stop_camera can release it.

        Only the exact name this backend would have generated for (camera, epoch) on this runtime is
        accepted; anything else is refused rather than guessed.
        """
        expected = self.resource_identity(camera_id, epoch)
        if identity is None or identity != expected:
            raise PerceptionCleanupError(f"Refusing to adopt {identity!r}: expected {expected!r}")
        key = (camera_id, epoch)
        if key not in self._sessions:
            self._unresolved[key] = ContainerCleanup(identity.name, False, [], "Adopted after backend restart")

    @staticmethod
    def _safe_name(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]", "-", value)

    async def start_camera(
        self, camera: CameraConfig, camera_epoch: str, sink: ObservationSink, replay_mode: str = "paced"
    ) -> None:
        if replay_mode not in REPLAY_MODES:
            raise PerceptionStartError(f"Unsupported replay mode {replay_mode!r}", resource_allocated=False)
        key = (camera.id, camera_epoch)
        blocked = [k for k in self._unresolved if k[0] == camera.id]
        if blocked:
            raise PerceptionStartError(
                f"Previous container for {camera.id} (epoch {blocked[0][1]}) is not confirmed removed; stop/retry cleanup first",
                resource_allocated=False,
            )
        if key in self._sessions:
            raise PerceptionStartError("This camera perception epoch is already active", resource_allocated=False)
        try:
            _, profile = self.profile()
        except PerceptionUnavailable as exc:
            raise PerceptionStartError(str(exc), resource_allocated=False) from exc
        directory = self.config.project_root / "backend" / "var" / "perception" / camera.id
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / f"{camera_epoch}.jsonl"
        summary = directory / f"{camera_epoch}.summary.json"
        log_path = directory / f"{camera_epoch}.log"
        try:
            command = self._command(camera, camera_epoch, output, summary, replay_mode)
            secrets = resolve_rtsp_source(camera.source)[1] if camera.source_type == "rtsp" else []
        except PerceptionUnavailable as exc:
            raise PerceptionStartError(str(exc), resource_allocated=False) from exc
        container_name = self.container_name(camera.id, camera_epoch)
        log_handle = log_path.open("w", encoding="utf-8", newline="\n")
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log_handle)
        except OSError as exc:
            log_handle.close()
            # wsl.exe never started, so no container can exist.
            raise PerceptionStartError(f"Could not launch the pinned Service Maker container: {exc}",
                                       resource_allocated=False) from exc
        session = _Session(
            camera.id, camera_epoch, container_name, process, output, log_handle, asyncio.Event(),
            frame_queue=queue.Queue(maxsize=PACED_FRAME_QUEUE_SIZE if replay_mode == "paced" else FRAME_QUEUE_SIZE),
            replay_mode=replay_mode, live=camera.source_type == "rtsp", secrets=secrets,
        )
        if os.environ.get(RELEASE_LOG_ENV) == "1":
            session.release_log = (directory / f"{camera_epoch}.release.jsonl").open("w", encoding="utf-8", newline="\n")
        self._sessions[key] = session
        try:
            self._start_reader(session)
            await self._wait_for_metadata(session)
            first = session.frame_queue.queue[0] if session.frame_queue.queue else None
            if first is not None:
                error, session.geometry_disclosure = geometry_check(profile, first)
                if error:
                    raise PerceptionUnavailable(f"Source geometry rejected: {error}")
        except asyncio.CancelledError:
            # Cancelled after launch: release (shielded) and keep the record if unconfirmed, then propagate.
            cleanup = await asyncio.shield(self._finish(session))
            self._sessions.pop(key, None)
            if not cleanup.confirmed_absent:
                self._unresolved[key] = cleanup
            raise
        except Exception as exc:
            # The wrapper was launched, so a container may exist: release it before reporting failure.
            cleanup = await self._finish(session)
            self._sessions.pop(key, None)
            if not cleanup.confirmed_absent:
                self._unresolved[key] = cleanup
            if self._on_launch_failure is not None and not str(exc).startswith("Source geometry rejected"):
                self._on_launch_failure(str(exc))
            raise PerceptionStartError(str(exc), resource_allocated=True,
                                       cleanup_confirmed=cleanup.confirmed_absent) from exc
        session.task = asyncio.create_task(self._consume(session, camera, sink, PersonClass.of(profile)), name=f"observations:{camera.id}:{camera_epoch}")

    def _start_reader(self, session: _Session) -> None:
        if session.process.stdout is None:
            raise PerceptionUnavailable("Service Maker stdout pipe was not created")
        session.reader = threading.Thread(
            target=self._read_stdout,
            args=(session,),
            name=f"service-maker-stdout:{session.camera_id}:{session.camera_epoch}",
            daemon=True,
        )
        session.reader.start()

    async def _wait_for_metadata(self, session: _Session) -> None:
        deadline = time.monotonic() + self.config.runtime.replay.startup_timeout_seconds
        log_offset, next_scan = 0, 0.0
        while time.monotonic() < deadline:
            if session.first_frame_event.is_set():
                return
            if time.monotonic() >= next_scan:
                # A pipeline that never reaches PLAYING fails now, not at the deadline, so its GPU memory
                # and start slot go to the next camera.
                next_scan = time.monotonic() + LOG_SCAN_SECONDS
                log_offset, failed = self._scan_log(session.log_handle, log_offset)
                if failed:
                    raise PerceptionUnavailable(
                        "Service Maker pipeline could not start (GStreamer could not reach PLAYING; on this "
                        "runtime usually GPU memory during model engine load): "
                        + redact(self._log_errors(session.log_handle), session.secrets))
            if session.reader_done.is_set():
                return_code = session.process.poll()
                detail = redact(self._log_tail(session.log_handle), session.secrets)
                raise PerceptionUnavailable(
                    "Service Maker stdout ended before valid frame metadata arrived "
                    f"(exit {return_code}, malformed_frames={session.malformed_frames}, "
                    f"reader_error={session.reader_error!r}): {detail}"
                )
            await asyncio.sleep(0.02)
        raise PerceptionUnavailable(
            "Service Maker produced no valid stdout frame metadata within the startup deadline "
            f"(malformed_frames={session.malformed_frames}, reader_error={session.reader_error!r})"
        )

    @staticmethod
    def _validate_frame(session: _Session, payload: bytes) -> dict[str, Any]:
        record = json.loads(payload)
        if not isinstance(record, dict):
            raise ValueError("Frame payload is not a JSON object")
        if (
            record.get("schema_version") not in FRAME_SCHEMAS
            or record.get("camera_id") != session.camera_id
            or record.get("camera_epoch") != session.camera_epoch
            or record.get("bbox_format") != "xywh"
        ):
            raise ValueError("Unexpected observation schema, camera, or epoch")
        if type(record.get("frame_number")) is not int:
            raise ValueError("frame_number must be an integer")
        if type(record.get("pts_ns")) is not int:
            raise ValueError("pts_ns must be an integer")
        if not isinstance(record.get("objects"), list):
            raise ValueError("objects must be a list")
        return record

    @staticmethod
    def _append_log(session: _Session, message: str) -> None:
        try:
            session.log_handle.write(message)
            if not message.endswith("\n"):
                session.log_handle.write("\n")
            session.log_handle.flush()
        except (OSError, ValueError) as exc:
            LOGGER.warning("Could not append Service Maker log for %s: %s", session.camera_id, exc)

    def _read_stdout(self, session: _Session) -> None:
        stream = session.process.stdout
        try:
            if stream is None:
                raise RuntimeError("Service Maker stdout pipe is unavailable")
            while True:
                line = stream.readline()
                if not line:
                    break
                if session.stop_event.is_set():
                    # Keep draining until EOF after a stop: wsl.exe relays container stdout and
                    # cannot exit while blocked writing into a full, unread pipe.
                    session.frames_discarded_after_stop += line.startswith(FRAME_PREFIX_BYTES)
                    continue
                if not line.startswith(FRAME_PREFIX_BYTES):
                    self._append_log(session, line.decode("utf-8", errors="replace").rstrip("\r\n"))
                    continue
                read_at = time.perf_counter()
                read_unix_ns = time.time_ns()
                try:
                    record = self._validate_frame(session, line[len(FRAME_PREFIX_BYTES):])
                    record["_backend_read_perf"] = read_at
                    record["_backend_read_unix_ns"] = read_unix_ns
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    session.malformed_frames += 1
                    self._append_log(
                        session,
                        f"Skipping malformed Service Maker stdout frame for {session.camera_id}: {exc}",
                    )
                    continue
                while not session.stop_event.is_set():
                    try:
                        session.frame_queue.put(record, timeout=0.25)
                    except queue.Full:
                        continue
                    session.first_frame_event.set()
                    break
        except Exception as exc:
            session.reader_error = f"{type(exc).__name__}: {exc}"
            LOGGER.exception("Service Maker stdout reader failed for %s", session.camera_id)
            self._append_log(session, f"Service Maker stdout reader failed: {session.reader_error}")
        finally:
            session.reader_done.set()

    @staticmethod
    def _scan_log(handle: TextIO, offset: int) -> tuple[int, bool]:
        """Read the container log from ``offset``; return the new offset and whether start failed."""
        start = max(0, offset - len(PIPELINE_START_FAILED))  # re-read a marker split across two scans
        try:
            handle.flush()
            with open(handle.name, "rb") as reader:
                reader.seek(start)
                chunk = reader.read()
        except (OSError, ValueError):
            return offset, False
        return start + len(chunk), PIPELINE_START_FAILED in chunk

    @staticmethod
    def _log_errors(handle: TextIO) -> str:
        """The container log's distinct ERROR lines (the cause), else its tail."""
        try:
            lines = Path(handle.name).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return "No readable container log detail"
        errors = list(dict.fromkeys(line.strip() for line in lines if line.lstrip().startswith("ERROR")))
        return " | ".join(errors[:4])[-800:] if errors else ServiceMakerSessions._log_tail(handle)

    @staticmethod
    def _log_tail(handle: TextIO) -> str:
        try:
            handle.flush()
            path = Path(handle.name)
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            return " | ".join(line.strip() for line in lines[-8:] if line.strip())[-800:]
        except OSError:
            return "No readable container log detail"

    @staticmethod
    def _observation(record: dict[str, Any], camera: CameraConfig, epoch: str, source_time: float,
                     segment: int = 0, *, person: PersonClass) -> Observation | None:
        obj = record
        if obj.get("label") != person.label or obj.get("class_id") != person.class_id:
            return None
        track_id = obj.get("track_id")
        if not isinstance(track_id, str) or not track_id.isdigit():
            return None
        box = obj.get("tracker_bbox") or obj.get("bbox")
        if not isinstance(box, dict):
            return None
        coordinates = [box.get(key) for key in ("x", "y", "width", "height")]
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) for value in coordinates):
            return None
        if box["width"] <= 0 or box["height"] <= 0:
            return None
        return Observation(
            key=TrackKey(camera.id, epoch, track_id),
            # Source/event time (PTS seconds within the epoch), never host receive time.
            timestamp=source_time,
            # Image pixels are not floor coordinates: position/world_position stay empty without calibration.
            position=None,
            world_position=None,
            metadata={
                "source_segment": segment,
                # Normalized application class; the detector's own id is kept for provenance.
                "label": "person",
                "detector_class_id": person.class_id,
                "confidence": obj.get("confidence"),
                "tracker_confidence": obj.get("tracker_confidence"),
                "bbox_xywh_pixels": obj.get("bbox"),
                "tracker_bbox_xywh_pixels": obj.get("tracker_bbox"),
                "image_bottom_center_pixels": [box["x"] + box["width"] / 2, box["y"] + box["height"]],
                "geometry_status": "UNCALIBRATED_PIXEL_EVIDENCE_ONLY",
            },
        )

    @staticmethod
    def _object_summary(objects: list[Any], person: PersonClass) -> dict[str, Any]:
        """What the person-track filter removed, so nothing is dropped silently."""
        classes: dict[str, int] = {}
        untracked_person = 0
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            name = f"{obj.get('class_id')}:{obj.get('label')}"
            classes[name] = classes.get(name, 0) + 1
            if obj.get("class_id") == person.class_id and obj.get("label") == person.label and obj.get("track_id") is None:
                untracked_person += 1
        return {"detector_classes": classes, "untracked_person_detections": untracked_person}

    async def _consume(self, session: _Session, camera: CameraConfig, sink: ObservationSink,
                       person: PersonClass | None = None) -> None:
        profile = self.profile()[1]
        person = person or PersonClass.of(profile)
        detection_limit = profile.detection_limit_per_class
        timeline = SourceTimeline(SOURCE_FORWARD_GAP_SECONDS)
        pacer = ReplayPacer(session.replay_mode)  # type: ignore[arg-type]
        arrival_rate = ArrivalRate()
        previous_pts: int | None = None
        previous_frame: int | None = None
        dropped_frames = 0
        try:
            while not session.stop_event.is_set():
                try:
                    record = session.frame_queue.get_nowait()
                except queue.Empty:
                    if session.reader_done.is_set():
                        break
                    await asyncio.sleep(0.005)
                    continue
                frame_number = record["frame_number"]
                pts_ns = record["pts_ns"]
                objects = record["objects"]
                source = timeline.observe(pts_ns)
                wait = pacer.delay(source, time.perf_counter())
                while wait > 0 and not session.stop_event.is_set():
                    await asyncio.sleep(min(wait, 0.05))
                    wait = pacer.delay(source, time.perf_counter())
                if session.stop_event.is_set():
                    break
                released = time.perf_counter()
                released_unix_ns = time.time_ns()
                received_at = time.monotonic()
                emitted_unix_ns = record.get("emitted_unix_ns")
                queue_depth = session.frame_queue.qsize()
                observations = tuple(
                    observation
                    for obj in objects
                    if isinstance(obj, dict)
                    for observation in [self._observation(obj, camera, session.camera_epoch, source.seconds, source.segment,
                                                          person=person)]
                    if observation is not None
                )
                if previous_frame is not None and frame_number > previous_frame + 1:
                    dropped_frames += frame_number - previous_frame - 1
                # Consecutive analysed frames are analysis_frame_interval source frames apart.
                source_fps = (
                    camera.analysis_frame_interval * 1e9 / (pts_ns - previous_pts)
                    if previous_pts is not None and pts_ns > previous_pts and source.discontinuity is None else None
                )
                read_at = record.get("_backend_read_perf")
                batch = ObservationBatch(
                    camera_id=camera.id,
                    camera_epoch=session.camera_epoch,
                    observations=observations,
                    received_at=received_at,
                    source_fps=source_fps,
                    analytics_fps=arrival_rate.add(released),
                    frame_age_ms=None,
                    frame_age_basis="No capture clock for file replay; source PTS and backend receive time are separate domains",
                    dropped_frames=dropped_frames,
                    metadata={
                        "frame_number": frame_number,
                        "source_pts_ns": pts_ns,
                        "source_timestamp_seconds": record.get("timestamp"),
                        "frame_width": record.get("frame_width"),
                        "frame_height": record.get("frame_height"),
                        "object_count_before_person_track_filter": len(objects),
                        **self._object_summary(objects, person),
                        "detection_limit": detection_limit,
                        "at_detection_limit": detection_limit is not None and sum(
                            1 for obj in objects if isinstance(obj, dict) and obj.get("class_id") == person.class_id
                        ) >= detection_limit,
                        "profile": self.config.models.selected_model,
                        "geometry_disclosure": session.geometry_disclosure,
                        "backend_queue_ms": (released - read_at) * 1000 if isinstance(read_at, float) else None,
                        # Producer wall clock (container) -> backend release (host); clocks not skew-corrected.
                        "producer_to_release_ms": (released_unix_ns - emitted_unix_ns) / 1e6
                        if type(emitted_unix_ns) is int else None,
                        "analytics_fps_basis": ANALYTICS_FPS_BASIS,
                    },
                    source_time=source.seconds,
                    source_pts_ns=pts_ns,
                    source_segment=source.segment,
                    source_discontinuity=source.discontinuity,
                    frame_number=frame_number,
                    received_at_utc=iso_now(),
                    replay_mode=session.replay_mode,
                )
                await sink.emit(batch)
                if session.release_log is not None:
                    # Written after the analytics update so the record proves the frame was consumed:
                    # read (stdout) -> released (pacer) -> analytics_done, all on the backend host clock.
                    session.release_log.write(json.dumps({
                        "frame_number": frame_number, "pts_ns": pts_ns, "emitted_unix_ns": emitted_unix_ns,
                        "read_unix_ns": record.get("_backend_read_unix_ns"), "released_unix_ns": released_unix_ns,
                        "analytics_done_unix_ns": time.time_ns(), "queue_depth": queue_depth}) + "\n")
                session.frames_consumed += 1
                previous_pts = pts_ns
                previous_frame = frame_number
        except asyncio.CancelledError:
            raise  # cancellation is part of a stop; the stop path flushes and releases
        except Exception as exc:
            LOGGER.exception("Service Maker observation consumer failed for %s", camera.id)
            await self._report_end(sink, session, CONSUMER_FAILED, f"{type(exc).__name__}: {exc}")
            return
        if session.stop_event.is_set():
            return  # operator stop: no end-of-stream outcome
        outcome, detail = await self._classify_end(session)
        await self._report_end(sink, session, outcome, detail)

    async def _classify_end(self, session: _Session) -> tuple[str, str | None]:
        """Called after the reader hit EOF and every accepted frame was emitted."""
        if session.reader_error:
            return ABNORMAL_TERMINATION, f"stdout reader failed: {session.reader_error}"
        try:
            returncode = await asyncio.to_thread(session.process.wait, 10)
        except subprocess.TimeoutExpired:
            return ABNORMAL_TERMINATION, "stdout reached EOF but the extractor process is still running"
        if returncode != 0:
            return ABNORMAL_TERMINATION, f"extractor exited with code {returncode}"
        if session.live:
            # A live camera has no natural end: its stream ending is a source loss, never a complete history.
            return ABNORMAL_TERMINATION, f"live source ended after {session.frames_consumed} frames"
        return CLEAN_EOF, f"{session.frames_consumed} frames drained; extractor exited 0"

    @staticmethod
    async def _report_end(sink: ObservationSink, session: _Session, outcome: str, detail: str | None) -> None:
        session.end_outcome = outcome
        report = getattr(sink, "end_of_stream", None)
        if report is None:
            return
        try:
            await report(session.camera_epoch, outcome, detail)
        except Exception:
            LOGGER.exception("End-of-stream handling failed for %s/%s", session.camera_id, session.camera_epoch)

    def unresolved_cleanups(self) -> dict[tuple[str, str], ContainerCleanup]:
        """Owned containers whose removal could not be confirmed; retried by the next stop."""
        return dict(self._unresolved)

    async def stop_camera(self, camera_id: str, camera_epoch: str) -> None:
        key = (camera_id, camera_epoch)
        session = self._sessions.get(key)
        if session is not None:
            cleanup = await self._finish(session)
            # Session tracking is released only once the outcome is known; failures stay recorded below.
            self._sessions.pop(key, None)
        elif key in self._unresolved:
            cleanup = await asyncio.to_thread(self._ensure_container_absent, self._unresolved[key].container_name, True)
        else:
            return
        if cleanup.confirmed_absent:
            self._unresolved.pop(key, None)
            return
        self._unresolved[key] = cleanup
        raise PerceptionCleanupError(
            f"Container {cleanup.container_name} for {camera_id}/{camera_epoch} was not confirmed removed: {cleanup.detail}"
        )

    async def _finish(self, session: _Session) -> ContainerCleanup:
        session.stop_event.set()
        try:
            cleanup = await asyncio.to_thread(
                self._ensure_container_absent, session.container_name, session.process.poll() is None
            )
        except Exception as exc:  # never let an unexpected error skip the local cleanup below
            cleanup = ContainerCleanup(session.container_name, False, [], f"{type(exc).__name__}: {exc}")
        if session.task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(session.task), timeout=3)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                session.task.cancel()
                await asyncio.gather(session.task, return_exceptions=True)
        try:
            await asyncio.to_thread(session.process.wait, 5)
        except subprocess.TimeoutExpired:
            # The wrapper is only the docker client. If the container is not confirmed gone, the
            # unresolved-cleanup record (not this process) tracks the remaining resource.
            try:
                session.process.kill()
            except OSError as exc:
                LOGGER.warning("Service Maker wrapper kill failed for %s: %s", session.container_name, exc)
            try:
                await asyncio.to_thread(session.process.wait, 5)
            except (OSError, subprocess.TimeoutExpired) as exc:
                LOGGER.warning("Service Maker wrapper did not exit cleanly for %s: %s", session.container_name, exc)
        except OSError as exc:
            LOGGER.warning("Service Maker wrapper wait failed for %s: %s", session.container_name, exc)
        if session.process.stdout is not None:
            try:
                session.process.stdout.close()
            except (OSError, ValueError) as exc:
                LOGGER.warning("Service Maker stdout close failed for %s: %s", session.container_name, exc)
        if session.reader is not None and session.reader.ident is not None:
            await asyncio.to_thread(session.reader.join, 5)
            if session.reader.is_alive():
                LOGGER.error("Service Maker stdout reader did not join for %s", session.container_name)
        if not cleanup.confirmed_absent:
            self._append_log(session, f"Container cleanup unresolved: {cleanup.detail}; attempts={cleanup.attempts}")
        session.log_handle.close()
        if session.release_log is not None:
            session.release_log.close()
        return cleanup

    def _docker(self, arguments: list[str], timeout: float) -> DockerResult:
        """Single seam for every Docker call (the fault-injection point in tests)."""
        command = (*runtime_prefix(self.config.runtime.telemetry.wsl_distro), "docker", *arguments)
        try:
            result = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return DockerResult(command, None, timed_out=True)
        except OSError as exc:
            return DockerResult(command, None, error=f"{type(exc).__name__}: {exc}")
        return DockerResult(command, result.returncode, (result.stdout or b"").decode(errors="replace"),
                            (result.stderr or b"").decode(errors="replace"))

    def _container_present(self, container_name: str) -> bool | None:
        """Exact-name presence check; None when Docker could not answer."""
        # Plain substring filter (no regex/glob characters: wsl.exe arguments may be shell re-parsed);
        # exactness is enforced below by comparing whole names.
        result = self._docker(["ps", "-a", "--filter", f"name={container_name}", "--format", "{{.Names}}"],
                              DOCKER_QUERY_TIMEOUT_SECONDS)
        if not result.ok:
            return None
        return container_name in {line.strip().lstrip("/") for line in result.stdout.splitlines()}

    def _wait_absent(self, container_name: str, attempts: list[dict[str, Any]]) -> bool | None:
        deadline = time.monotonic() + REMOVAL_VERIFY_SECONDS
        while True:
            present = self._container_present(container_name)
            if present is False:
                return False
            if time.monotonic() >= deadline:
                attempts.append({"step": "verify", "present": present})
                return present
            time.sleep(0.25)

    def _ensure_container_absent(self, container_name: str, request_stop: bool = True) -> ContainerCleanup:
        """Stop, verify, and if needed kill/remove exactly one owned container. Never touches other names."""
        if not OWNED_CONTAINER_NAME.fullmatch(container_name):
            return ContainerCleanup(container_name, False, [], "Refusing to operate on a container name this backend does not own")
        if host_route() == "unsupported":
            return ContainerCleanup(container_name, True, [], "No container runtime on this platform")
        attempts: list[dict[str, Any]] = []
        if request_stop:
            # --init makes SIGTERM effective; the explicit grace keeps Docker's own SIGKILL inside the timeout.
            stop = self._docker(["stop", "--time", str(STOP_GRACE_SECONDS), container_name], STOP_GRACE_SECONDS + 10)
            attempts.append({"step": "stop", **stop.as_dict()})
            if not stop.ok and "No such container" in stop.stderr:
                # --rm already removed it (normal after a clean end of stream); absence is still verified below.
                LOGGER.info("docker stop %s: container already gone", container_name)
            elif not stop.ok:
                LOGGER.warning("docker stop %s failed: %s", container_name, stop.as_dict())
        present = self._wait_absent(container_name, attempts)
        for attempt in range(CLEANUP_ESCALATION_ATTEMPTS):
            if present is False:
                break
            kill = self._docker(["kill", container_name], DOCKER_QUERY_TIMEOUT_SECONDS)
            remove = self._docker(["rm", "-f", container_name], DOCKER_QUERY_TIMEOUT_SECONDS)
            attempts.append({"step": f"escalate-{attempt + 1}", "kill": kill.as_dict(), "rm": remove.as_dict()})
            present = self._wait_absent(container_name, attempts)
        if present is False:
            return ContainerCleanup(container_name, True, attempts)
        detail = "container still present" if present else "Docker did not answer the presence check"
        LOGGER.error("Unresolved cleanup for %s: %s", container_name, detail)
        return ContainerCleanup(container_name, False, attempts, detail)

    async def shutdown(self) -> None:
        failures = []
        for key in list(self._sessions) + [k for k in self._unresolved if k not in self._sessions]:
            try:
                await self.stop_camera(*key)
            except PerceptionCleanupError as exc:
                failures.append(str(exc))
        if failures:
            raise PerceptionCleanupError("; ".join(failures))
