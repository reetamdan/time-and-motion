"""Local evidence: real source frames and source-compressed clips at known source times.

Evidence is extracted from the camera's original source file, never re-encoded from the whole
video:

* snapshot - one decoded frame at (or immediately after) the requested source time, written as JPEG.
  The actual frame PTS is read back from ffmpeg (showinfo with -copyts) and recorded.
* clip     - a stream copy (``-c copy``) around the requested time. Stream copy can only start on a
  keyframe, so the recorded ``source_start_s`` is the keyframe at or before the requested start, and
  ``source_end_s`` comes from probing the written clip. Requested and actual bounds are both stored.

Source time here is the camera epoch's source/PTS timeline, which for file replay is the file's
presentation timeline minus the stream start time (recorded per record).

Every record carries its run, camera, epoch, source time, trigger (``operator_marker`` is a manual,
labelled operator action - not an analytics event), file checksum and size, and the detector/run
provenance. Retention removes the oldest records beyond the age, count, or size limits, and deletes
only files inside the configured evidence root.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable
import uuid

from ..configuration import AppConfig
from ..db.repository import SQLiteRepository
from ..timeutils import iso_now

LOGGER = logging.getLogger("cctv.evidence")
OPERATOR_MARKER = "operator_marker"
OPERATOR_LABEL = "OPERATOR MARKER - manual evidence request; not an analytics or alert detection"
SOURCE_TIMELINE_BASIS = ("camera epoch source timeline (pipeline PTS seconds), mapped onto the source file by aligning "
                         "the epoch's first decoded frame")
SHOWINFO_PTS = re.compile(r"pts_time:\s*([0-9.]+)")


class EvidenceUnavailable(RuntimeError):
    """Evidence cannot be produced; the message names the exact missing capability or input."""


@dataclass(frozen=True, slots=True)
class ToolResult:
    returncode: int | None
    stdout: str
    stderr: str


Runner = Callable[[list[str], float], ToolResult]


def _run(command: list[str], timeout: float) -> ToolResult:
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return ToolResult(None, "", f"timed out after {timeout:.0f} s")
    except OSError as exc:
        return ToolResult(None, "", f"{type(exc).__name__}: {exc}")
    return ToolResult(result.returncode, result.stdout.decode(errors="replace"), result.stderr.decode(errors="replace"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class MediaTools:
    """ffmpeg/ffprobe seam (replaced in unit tests)."""

    def __init__(self, timeout: float, runner: Runner | None = None, ffmpeg: str | None = None,
                 ffprobe: str | None = None) -> None:
        self.timeout = timeout
        self._run = runner or _run
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg")
        self.ffprobe = ffprobe or shutil.which("ffprobe")
        self._keyframes: dict[tuple[str, int, int], tuple[float, list[float], float]] = {}

    def unavailable_reason(self) -> str | None:
        missing = [name for name, path in (("ffmpeg", self.ffmpeg), ("ffprobe", self.ffprobe)) if not path]
        return f"{' and '.join(missing)} not found on PATH (required for evidence extraction)" if missing else None

    def version(self) -> str | None:
        result = self._run([str(self.ffmpeg), "-hide_banner", "-version"], self.timeout)
        return result.stdout.splitlines()[0][:120] if result.returncode == 0 and result.stdout else None

    def stream_timing(self, source: Path) -> tuple[float, list[float], float]:
        """(stream start_time, keyframe PTS times, first decoded frame PTS) of the first video stream;
        memoized per file version."""
        stat = source.stat()
        key = (str(source), stat.st_size, stat.st_mtime_ns)
        if key in self._keyframes:
            return self._keyframes[key]
        start = self._run([str(self.ffprobe), "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=start_time", "-of", "csv=p=0", str(source)], self.timeout)
        frames = self._run([str(self.ffprobe), "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
                            "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(source)], self.timeout)
        first = self._run([str(self.ffprobe), "-v", "error", "-select_streams", "v:0", "-read_intervals", "%+#1",
                           "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(source)], self.timeout)
        if start.returncode != 0 or frames.returncode != 0 or first.returncode != 0:
            raise EvidenceUnavailable(f"ffprobe could not read the source timing: {(start.stderr or frames.stderr or first.stderr)[:200]}")
        start_time = float(start.stdout.strip().split(",")[0] or 0)
        keyframes = sorted(float(value.strip().rstrip(",")) for value in frames.stdout.split() if value.strip().rstrip(","))
        first_pts = float(first.stdout.split()[0].rstrip(",")) if first.stdout.split() else start_time
        self._keyframes[key] = (start_time, keyframes, first_pts)
        return start_time, keyframes, first_pts

    def snapshot(self, source: Path, pts_time: float, output: Path) -> float:
        """Decode the frame at/after pts_time; return the actual frame PTS (file timeline)."""
        result = self._run([str(self.ffmpeg), "-hide_banner", "-nostdin", "-y", "-copyts", "-ss", f"{pts_time:.6f}",
                            "-i", str(source), "-frames:v", "1", "-vf", "showinfo", "-q:v", "3", str(output)],
                           self.timeout)
        match = SHOWINFO_PTS.search(result.stderr)
        if result.returncode != 0 or not output.is_file() or match is None:
            raise EvidenceUnavailable(f"ffmpeg snapshot failed (exit {result.returncode}): {result.stderr.strip()[-300:]}")
        return float(match.group(1))

    def clip(self, source: Path, start: float, duration: float, output: Path) -> None:
        result = self._run([str(self.ffmpeg), "-hide_banner", "-nostdin", "-y", "-ss", f"{start:.6f}", "-i", str(source),
                            "-t", f"{duration:.6f}", "-map", "0:v:0", "-c", "copy", "-movflags", "+faststart",
                            "-avoid_negative_ts", "make_zero", str(output)], self.timeout)
        if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
            raise EvidenceUnavailable(f"ffmpeg clip copy failed (exit {result.returncode}): {result.stderr.strip()[-300:]}")

    def duration(self, path: Path) -> float:
        result = self._run([str(self.ffprobe), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(path)], self.timeout)
        try:
            return float(result.stdout.strip())
        except ValueError as exc:
            raise EvidenceUnavailable(f"ffprobe could not read the clip duration: {result.stderr[:200]}") from exc


class EvidenceService:
    def __init__(self, config: AppConfig, repository: SQLiteRepository, tools: MediaTools | None = None) -> None:
        self.config = config
        self.repository = repository
        self.root = config.runtime.evidence_root.resolve()
        self.tools = tools or MediaTools(config.runtime.evidence.tool_timeout_seconds)
        self._source_hashes: dict[tuple[str, int, int], str] = {}

    # ------------------------------------------------------------------ capture

    def _source_sha256(self, source: Path) -> str:
        stat = source.stat()
        key = (str(source), stat.st_size, stat.st_mtime_ns)
        if key not in self._source_hashes:
            self._source_hashes[key] = _sha256(source)
        return self._source_hashes[key]

    def capability(self) -> dict[str, Any]:
        reason = self.tools.unavailable_reason()
        return {"available": reason is None, "unavailable_reason": reason, "root": str(self.root),
                "ffmpeg": self.tools.ffmpeg, "ffprobe": self.tools.ffprobe}

    def capture_marker(self, *, run_id: str, camera_id: str, camera_epoch: str, source_time_s: float,
                       note: str | None, provenance: dict[str, Any], trigger: str = OPERATOR_MARKER,
                       alert_key: str | None = None, pipeline_origin_s: float | None = None,
                       frame_number: int | None = None) -> dict[str, Any]:
        """Snapshot + keyframe-aligned stream-copy clip at source_time_s. Blocking; call from a worker thread.

        The pipeline's PTS and the file's presentation timestamps can differ by a constant (e.g. the
        H.264 B-frame reorder delay: cam01's first pipeline PTS is 0.0667 s, ffmpeg's is 0.0), so the
        two timelines are aligned on the epoch's first decoded frame (pipeline_origin_s <-> the file's
        first frame PTS). Without an origin the timelines are assumed equal and that is recorded.
        """
        reason = self.tools.unavailable_reason()
        if reason:
            raise EvidenceUnavailable(reason)
        camera = self.config.camera_map[camera_id]
        source = camera.source_path
        if camera.source_type != "file" or source is None or not source.is_file():
            raise EvidenceUnavailable("Evidence extraction needs the camera's local source file; live (RTSP) sources have no "
                                      "recording buffer in this version" if camera.source_type == "rtsp" else
                                      "Evidence extraction needs the camera's local source file; this camera has none")
        start_time, keyframes, first_frame_pts = self.tools.stream_timing(source)
        if pipeline_origin_s is None:
            offset, basis = start_time, "no pipeline origin recorded; file PTS = stream start_time + source time (assumed)"
        else:
            offset = first_frame_pts - pipeline_origin_s
            basis = "aligned on the epoch's first decoded frame: file PTS = source time - pipeline origin + file first-frame PTS"
        settings = self.config.runtime.evidence
        group = str(uuid.uuid4())
        directory = self.root / run_id / camera_id / group
        directory.mkdir(parents=True, exist_ok=False)
        file_pts = source_time_s + offset
        snapshot_path = directory / "snapshot.jpg"
        clip_path = directory / "clip.mp4"
        try:
            actual_frame_pts = self.tools.snapshot(source, file_pts, snapshot_path)
            requested_start = max(first_frame_pts, file_pts - settings.clip_pre_seconds)
            requested_end = file_pts + settings.clip_post_seconds
            keyframe = max((k for k in keyframes if k <= requested_start + 1e-6), default=first_frame_pts)
            self.tools.clip(source, requested_start, requested_end - requested_start, clip_path)
            clip_duration = self.tools.duration(clip_path)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        common = {
            "run_id": run_id, "camera_id": camera_id, "camera_epoch": camera_epoch,
            "timestamp_basis": SOURCE_TIMELINE_BASIS, "label": OPERATOR_LABEL if trigger == OPERATOR_MARKER else trigger,
            "source_marked_s": source_time_s,
        }
        base_metadata = {
            "group_id": group, "trigger": trigger, "note": (note or "")[:500] or None, "alert_key": alert_key,
            "source_label": camera.source_label, "source_sha256": self._source_sha256(source),
            "stream_start_time_s": start_time, "requested_source_time_s": source_time_s,
            "timeline_mapping": {"basis": basis, "file_pts_minus_source_s": offset, "pipeline_origin_s": pipeline_origin_s,
                                 "file_first_frame_pts_s": first_frame_pts},
            "pipeline_frame_number": frame_number,
            "tool": self.tools.version(), "provenance": provenance,
            "scope": "Real source media from the configured file at a recorded source time; counts or detections are "
                     "not burned into the media",
        }
        snapshot_id = self.repository.register_evidence({
            **common, "path": str(snapshot_path.relative_to(self.root)), "media_type": "image/jpeg",
            "kind": f"{trigger}_snapshot", "checksum": _sha256(snapshot_path), "codec": "mjpeg",
            "size_bytes": snapshot_path.stat().st_size,
            "source_start_s": actual_frame_pts - offset, "source_end_s": actual_frame_pts - offset,
            "start_at": iso_now(), "end_at": None,
            "metadata": {**base_metadata, "actual_frame_source_s": actual_frame_pts - offset, "actual_frame_file_pts_s": actual_frame_pts,
                         "extraction": "accurate decode of the first frame at/after the requested time"},
        })
        clip_start = keyframe - offset
        clip_id = self.repository.register_evidence({
            **common, "path": str(clip_path.relative_to(self.root)), "media_type": "video/mp4",
            "kind": f"{trigger}_clip", "checksum": _sha256(clip_path), "codec": "h264 (stream copy)",
            "size_bytes": clip_path.stat().st_size,
            "source_start_s": clip_start, "source_end_s": clip_start + clip_duration,
            "start_at": iso_now(), "end_at": None,
            "metadata": {**base_metadata, "requested_source_start_s": requested_start - offset,
                         "requested_source_end_s": requested_end - offset,
                         "keyframe_aligned_start_s": clip_start, "clip_duration_s": clip_duration,
                         "extraction": "stream copy (no re-encode); starts on the keyframe at or before the requested start"},
        })
        self.enforce_retention()
        return {"group_id": group, "snapshot": self.describe(self.repository.get_evidence(snapshot_id)),
                "clip": self.describe(self.repository.get_evidence(clip_id))}

    # ------------------------------------------------------------------ read

    def resolve(self, record: dict[str, Any]) -> Path | None:
        supplied = Path(record["path"])
        candidate = (supplied if supplied.is_absolute() else self.root / supplied).resolve()
        return candidate if candidate.is_relative_to(self.root) else None

    def describe(self, record: dict[str, Any] | None) -> dict[str, Any] | None:
        if record is None:
            return None
        path = self.resolve(record)
        available = bool(path and path.is_file() and path.stat().st_size > 0)
        metadata = record.get("metadata_json") or {}
        return {
            "id": record["id"], "run_id": record["run_id"], "camera_id": record["camera_id"],
            "camera_epoch": record.get("camera_epoch"), "kind": record["kind"], "media_type": record["media_type"],
            "label": record.get("label"), "trigger": metadata.get("trigger"), "group_id": metadata.get("group_id"),
            "note": metadata.get("note"), "timestamp_basis": record.get("timestamp_basis"),
            "source_marked_s": record.get("source_marked_s"), "source_start_s": record.get("source_start_s"),
            "source_end_s": record.get("source_end_s"), "checksum": record.get("checksum"),
            "size_bytes": record.get("size_bytes"), "created_at": record["created_at"],
            "file_available": available,
            "unavailable_reason": None if available else (
                "Evidence path is outside the evidence root" if path is None else "Evidence file is missing or empty"),
            "url": f"/api/v1/evidence/{record['id']}", "metadata": metadata,
        }

    def list(self, **filters: Any) -> list[dict[str, Any]]:
        return [self.describe(record) for record in self.repository.list_evidence(**filters)]  # type: ignore[misc]

    # ------------------------------------------------------------------ retention

    def enforce_retention(self, now: datetime | None = None) -> dict[str, Any]:
        """Remove the oldest records beyond the age, count, or byte limits (files inside the root only)."""
        retention = self.config.analytics.retention
        max_records = self.config.runtime.evidence.max_records
        max_bytes = retention.max_evidence_mib * 1024 * 1024
        cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=retention.evidence_days)).isoformat()
        records = self.repository.evidence_for_retention()  # oldest first
        total_bytes = sum(record["size_bytes"] or 0 for record in records)
        remaining = len(records)
        removed: list[str] = []
        for record in records:
            expired = record["created_at"] < cutoff
            if not (expired or remaining > max_records or total_bytes > max_bytes):
                break
            path = self.resolve(record)
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                    if path.parent != self.root and not any(path.parent.iterdir()):
                        path.parent.rmdir()
                except OSError as exc:
                    LOGGER.warning("Could not remove evidence file %s: %s", path, exc)
                    continue
            self.repository.delete_evidence(record["id"])
            removed.append(record["id"])
            remaining -= 1
            total_bytes -= record["size_bytes"] or 0
        return {"removed": removed, "remaining_records": remaining, "remaining_bytes": total_bytes}

    def usage(self) -> dict[str, Any]:
        retention = self.config.analytics.retention
        return {**self.repository.evidence_usage(), "max_records": self.config.runtime.evidence.max_records,
                "max_bytes": retention.max_evidence_mib * 1024 * 1024, "max_age_days": retention.evidence_days}

