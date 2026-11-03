"""Live Windows/WSL/Docker Service Maker process and JSONL observation consumer."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import math
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, TextIO

from ..analytics.types import Observation, TrackKey
from ..configuration import AppConfig, CameraConfig
from .base import ObservationBatch, ObservationSink, PerceptionUnavailable

LOGGER = logging.getLogger("cctv.perception.service_maker")
EXTRACTOR = "/workspace/perception/deepstream/service_maker/extract_metadata.py"
INFER_CONFIG = "/workspace/perception/deepstream/cam01_trafficcamnet_smoke_infer.txt"
TRACKER_CONFIG = "/workspace/perception/deepstream/reference/config_tracker_NvDCF_perf.yml"


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


@dataclass(slots=True)
class _Session:
    camera_id: str
    camera_epoch: str
    container_name: str
    process: subprocess.Popen[bytes]
    output_path: Path
    log_handle: TextIO
    stop_event: asyncio.Event
    metadata_event: asyncio.Event
    task: asyncio.Task[None] | None = None


class ServiceMakerSessions:
    """Own one container/process/consumer per camera epoch."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._sessions: dict[tuple[str, str], _Session] = {}

    def _command(self, camera: CameraConfig, epoch: str, output: Path, summary: Path) -> list[str]:
        if os.name != "nt":
            raise PerceptionUnavailable("The validated Service Maker bridge currently requires Windows + the pinned WSL container")
        wrapper = self.config.project_root / "scripts" / "deepstream.sh"
        source = camera.source_path
        if source is None:
            raise PerceptionUnavailable("Only a validated local file source is supported by this smoke profile")
        return [
            "wsl.exe", "-d", self.config.runtime.telemetry.wsl_distro, "--",
            "env", f"DEEPSTREAM_CONTAINER_NAME=aegis-{self._safe_name(camera.id)}-{self._safe_name(epoch)}",
            "bash", wsl_path(wrapper), "python3", EXTRACTOR,
            "--source", workspace_path(source, self.config.project_root),
            "--infer-config", INFER_CONFIG,
            "--tracker-config", TRACKER_CONFIG,
            "--output", workspace_path(output, self.config.project_root),
            "--run-summary", workspace_path(summary, self.config.project_root),
            "--camera-id", camera.id,
            "--camera-epoch", epoch,
        ]

    @staticmethod
    def _safe_name(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]", "-", value)

    async def start_camera(self, camera: CameraConfig, camera_epoch: str, sink: ObservationSink) -> None:
        key = (camera.id, camera_epoch)
        if key in self._sessions:
            raise PerceptionUnavailable("This camera perception epoch is already active")
        if self.config.models.selected_model != "trafficcamnet-smoke":
            raise PerceptionUnavailable("The validated Service Maker process profile is trafficcamnet-smoke only")
        directory = self.config.project_root / "backend" / "var" / "perception" / camera.id
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / f"{camera_epoch}.jsonl"
        summary = directory / f"{camera_epoch}.summary.json"
        log_path = directory / f"{camera_epoch}.log"
        command = self._command(camera, camera_epoch, output, summary)
        container_name = command[5].split("=", 1)[1]
        log_handle = log_path.open("w", encoding="utf-8", newline="\n")
        try:
            process = subprocess.Popen(command, stdout=log_handle, stderr=subprocess.STDOUT)
        except OSError as exc:
            log_handle.close()
            raise PerceptionUnavailable(f"Could not launch the pinned Service Maker container: {exc}") from exc
        session = _Session(
            camera.id, camera_epoch, container_name, process, output, log_handle, asyncio.Event(), asyncio.Event(),
        )
        self._sessions[key] = session
        try:
            await self._wait_for_metadata(session, camera, sink)
        except Exception:
            await self._finish(session)
            self._sessions.pop(key, None)
            raise

    async def _wait_for_metadata(
        self, session: _Session, camera: CameraConfig, sink: ObservationSink
    ) -> None:
        deadline = time.monotonic() + max(15.0, self.config.runtime.telemetry.command_timeout_seconds)
        while time.monotonic() < deadline:
            if session.metadata_event.is_set():
                return
            # WSL-mounted file sizes can lag flushed writes; consume as soon as the output exists.
            if session.task is None and session.output_path.is_file():
                session.task = asyncio.create_task(
                    self._consume(session, camera, sink), name=f"observations:{camera.id}:{camera_epoch}"
                )
            return_code = session.process.poll()
            if return_code is not None:
                detail = self._log_tail(session.log_handle)
                raise PerceptionUnavailable(f"Service Maker exited before metadata arrived (exit {return_code}): {detail}")
            await asyncio.sleep(0.05)
        raise PerceptionUnavailable("Service Maker produced no frame metadata within the startup deadline")

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
    def _observation(record: dict[str, Any], camera: CameraConfig, epoch: str, received_at: float) -> Observation | None:
        obj = record
        if obj.get("label") != "person" or obj.get("class_id") != 2:
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
            timestamp=received_at,
            position=None,
            world_position=None,
            metadata={
                "class_id": 2,
                "label": "person",
                "confidence": obj.get("confidence"),
                "tracker_confidence": obj.get("tracker_confidence"),
                "bbox_xywh_pixels": obj.get("bbox"),
                "tracker_bbox_xywh_pixels": obj.get("tracker_bbox"),
                "image_bottom_center_pixels": [box["x"] + box["width"] / 2, box["y"] + box["height"]],
                "geometry_status": "UNCALIBRATED_PIXEL_EVIDENCE_ONLY",
            },
        )

    async def _consume(self, session: _Session, camera: CameraConfig, sink: ObservationSink) -> None:
        previous_pts: int | None = None
        previous_received: float | None = None
        previous_frame: int | None = None
        dropped_frames = 0
        try:
            with session.output_path.open("r", encoding="utf-8", newline="") as handle:
                while not session.stop_event.is_set():
                    position = handle.tell()
                    line = handle.readline()
                    if not line:
                        if session.process.poll() is not None:
                            break
                        await asyncio.sleep(0.02)
                        continue
                    if not line.endswith("\n"):
                        handle.seek(position)
                        await asyncio.sleep(0.01)
                        continue
                    try:
                        record = json.loads(line)
                        if (
                            record.get("schema_version") != "1.0"
                            or record.get("camera_id") != camera.id
                            or record.get("camera_epoch") != session.camera_epoch
                            or record.get("bbox_format") != "xywh"
                        ):
                            raise ValueError("Unexpected observation schema or camera epoch")
                        frame_number = int(record["frame_number"])
                        pts_ns = int(record["pts_ns"])
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                        LOGGER.warning("Skipping invalid Service Maker frame for %s: %s", camera.id, exc)
                        continue
                    received_at = time.monotonic()
                    observations = tuple(
                        observation
                        for obj in record.get("objects", [])
                        if isinstance(obj, dict)
                        for observation in [self._observation(obj, camera, session.camera_epoch, received_at)]
                        if observation is not None
                    )
                    if previous_frame is not None and frame_number > previous_frame + 1:
                        dropped_frames += frame_number - previous_frame - 1
                    source_fps = 1e9 / (pts_ns - previous_pts) if previous_pts is not None and pts_ns > previous_pts else None
                    analytics_fps = 1.0 / (received_at - previous_received) if previous_received is not None and received_at > previous_received else None
                    batch = ObservationBatch(
                        camera_id=camera.id,
                        camera_epoch=session.camera_epoch,
                        observations=observations,
                        received_at=received_at,
                        source_fps=source_fps,
                        analytics_fps=analytics_fps,
                        frame_age_ms=None,
                        frame_age_basis="Source PTS is preserved in metadata; host arrival uses monotonic time",
                        dropped_frames=dropped_frames,
                        metadata={
                            "frame_number": frame_number,
                            "source_pts_ns": pts_ns,
                            "source_timestamp_seconds": record.get("timestamp"),
                            "frame_width": record.get("frame_width"),
                            "frame_height": record.get("frame_height"),
                            "object_count_before_person_track_filter": len(record.get("objects", [])),
                            "profile": "trafficcamnet-smoke",
                        },
                    )
                    await sink.emit(batch)
                    session.metadata_event.set()
                    previous_pts = pts_ns
                    previous_received = received_at
                    previous_frame = frame_number
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Service Maker observation consumer failed for %s", camera.id)

    async def stop_camera(self, camera_id: str, camera_epoch: str) -> None:
        key = (camera_id, camera_epoch)
        session = self._sessions.pop(key, None)
        if session is not None:
            await self._finish(session)

    async def _finish(self, session: _Session) -> None:
        session.stop_event.set()
        if session.process.poll() is None:
            await asyncio.to_thread(self._stop_container, session.container_name)
        if session.task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(session.task), timeout=3)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                session.task.cancel()
                await asyncio.gather(session.task, return_exceptions=True)
        try:
            await asyncio.to_thread(session.process.wait, 5)
        except subprocess.TimeoutExpired:
            session.process.kill()
            await asyncio.to_thread(session.process.wait, 5)
        session.log_handle.close()

    def _stop_container(self, container_name: str) -> None:
        if os.name == "nt":
            subprocess.run(
                ["wsl.exe", "-d", self.config.runtime.telemetry.wsl_distro, "--", "docker", "stop", container_name],
                capture_output=True, timeout=10, check=False,
            )

    async def shutdown(self) -> None:
        for key in list(self._sessions):
            session = self._sessions.pop(key)
            await self._finish(session)
