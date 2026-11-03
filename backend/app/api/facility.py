"""Facility, history, alerts, associations, evidence, preview, and provenance APIs."""

from __future__ import annotations

import asyncio

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..errors import APIError
from ..analytics.history import downsample
from ..analytics.timebase import RECEIVE_TIME_BASIS, SOURCE_TIME_BASIS
from ..schemas import (
    Alert, AlertSeverity, AlertStatus, AssociationRecord, CameraCountHistory, CameraCrossings, HistoryResponse, Snapshot,
)
from ..timeutils import to_iso
from .dependencies import services
from ..services.evidence import EvidenceUnavailable
from .files import stream_file

router = APIRouter(prefix="/api/v1", tags=["facility"])

HISTORY_METRICS = {
    "occupancy", "building_expected", "building_visible", "building_estimated", "discrepancy",
    "queue_total", "max_wait_seconds", "entries", "exits", "stair_up_per_min", "stair_down_per_min",
    "cpu_utilization", "gpu_utilization", "vram_used_mib",
}


@router.get("/cameras")
async def cameras(request: Request) -> list[dict[str, object]]:
    return (await asyncio.to_thread(services(request).snapshots.snapshot))["cameras"]  # type: ignore[return-value]


def _camera_scope(request: Request, camera_id: str, run_id: str | None, epoch: str | None) -> tuple[str | None, str | None, str | None]:
    service = services(request)
    if camera_id not in service.config.camera_map:
        raise APIError(404, "CAMERA_NOT_FOUND", "Unknown camera", {"camera_id": camera_id})
    if run_id is None:
        latest = service.repository.latest_run()
        run_id = latest["id"] if latest else None
    if run_id is None:
        return None, None, "No run has been recorded"
    if epoch is None:
        epoch = service.repository.latest_camera_epoch(run_id, camera_id)
    if epoch is None:
        return run_id, None, "No camera epoch was started in this run"
    return run_id, epoch, None


def _cursor(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    try:
        segment, bucket = (int(part) for part in value.split(":"))
    except ValueError as exc:
        raise APIError(422, "INVALID_CURSOR", "Cursor must be '<segment>:<bucket_index>'", {"after": value}) from exc
    return segment, bucket


@router.get("/cameras/{camera_id}/count-history", response_model=CameraCountHistory)
async def camera_count_history(
    request: Request, camera_id: str, run_id: str | None = Query(default=None),
    epoch: str | None = Query(default=None), limit: int = Query(default=2000, ge=1, le=10000),
    window: Literal["recent", "range"] = Query(default="recent"),
    from_source_s: float | None = Query(default=None), to_source_s: float | None = Query(default=None),
    after: str | None = Query(default=None), max_points: int | None = Query(default=None, ge=10, le=10000),
) -> dict[str, object]:
    """Persisted visible-count samples on the camera epoch's source timeline (not wall-clock).

    window=recent (default) returns the newest `limit` samples, so a long run keeps updating;
    window=range pages forward from `after` / source-time bounds. max_points downsamples within
    source segments only (see analytics/history.py); gaps are never interpolated.
    """
    service = services(request)
    run_id, epoch, reason = _camera_scope(request, camera_id, run_id, epoch)
    samples: list[dict[str, object]] = []
    stats: dict[str, object] = {"total_samples": 0, "segments": 0, "first_source_s": None, "last_source_s": None,
                                "next_cursor": None}
    if epoch:
        samples, stats = await asyncio.to_thread(
            service.repository.camera_count_history_window, run_id, camera_id, epoch, limit=limit, window=window,
            from_source_s=from_source_s, to_source_s=to_source_s, after=_cursor(after))
        if not stats["total_samples"]:
            reason = "No count samples persisted for this epoch yet"
    points, factor = downsample(samples, max_points)
    return {"camera_id": camera_id, "run_id": run_id, "camera_epoch": epoch, "time_basis": SOURCE_TIME_BASIS,
            "receive_time_basis": RECEIVE_TIME_BASIS, "sample_seconds": service.config.runtime.replay.history_sample_seconds,
            "samples": points, "unavailable_reason": reason, "window": window, "returned_samples": len(samples),
            "downsample_factor": factor, **stats}


@router.get("/cameras/{camera_id}/line-crossings", response_model=CameraCrossings)
async def camera_line_crossings(
    request: Request, camera_id: str, run_id: str | None = Query(default=None),
    epoch: str | None = Query(default=None), limit: int = Query(default=2000, ge=1, le=10000),
) -> dict[str, object]:
    """Camera-local image-space line crossings. These are not facility entries/exits."""
    service = services(request)
    lines = [line.id for line in service.supervisor.image_lines(camera_id)] if camera_id in service.config.camera_map else []
    run_id, epoch, reason = _camera_scope(request, camera_id, run_id, epoch)
    if not lines:
        reason = "No image_pixels line is configured for this camera"
    crossings = service.repository.camera_crossings(run_id, camera_id, epoch, limit) if epoch else []
    return {"camera_id": camera_id, "run_id": run_id, "camera_epoch": epoch, "time_basis": SOURCE_TIME_BASIS,
            "configured_image_lines": lines, "crossings": crossings, "unavailable_reason": reason}


@router.get("/floors")
async def floors(request: Request) -> list[dict[str, object]]:
    return (await asyncio.to_thread(services(request).snapshots.snapshot))["floors"]  # type: ignore[return-value]


@router.get("/zones")
async def zones(request: Request) -> list[dict[str, object]]:
    floors = (await asyncio.to_thread(services(request).snapshots.snapshot))["floors"]
    return [zone for floor in floors for zone in floor["zones"]]  # type: ignore[index]


@router.get("/facility/snapshot", response_model=Snapshot)
async def snapshot(request: Request) -> dict[str, object]:
    return await asyncio.to_thread(services(request).snapshots.snapshot)


@router.get("/analytics/history", response_model=HistoryResponse)
async def history(
    request: Request,
    metric: str,
    from_time: datetime | None = Query(default=None, alias="from"),
    to_time: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=200, ge=1, le=1000),
    cursor: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
) -> dict[str, object]:
    if metric not in HISTORY_METRICS:
        raise APIError(400, "INVALID_METRIC", "Unsupported history metric", {"supported": sorted(HISTORY_METRICS)})
    for value in (from_time, to_time):
        if value is not None and value.tzinfo is None:
            raise APIError(422, "TIMEZONE_REQUIRED", "History bounds must include a timezone")
    if from_time and to_time and from_time >= to_time:
        raise APIError(422, "INVALID_TIME_RANGE", "The from bound must precede the to bound")
    offset = 0
    if cursor is not None:
        try:
            offset = int(cursor)
        except ValueError as exc:
            raise APIError(422, "INVALID_CURSOR", "History cursor must be a non-negative integer") from exc
        if offset < 0 or str(offset) != cursor:
            raise APIError(422, "INVALID_CURSOR", "History cursor must be a non-negative integer")
    service = services(request)
    result = service.repository.history(
        metric,
        to_iso(from_time) if from_time else None,
        to_iso(to_time) if to_time else None,
        limit,
        offset,
        run_id,
    )
    return HistoryResponse.model_validate(result).model_dump(mode="json")


@router.get("/alerts", response_model=list[Alert])
async def alerts(
    request: Request,
    status: AlertStatus | None = None,
    severity: AlertSeverity | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    run_id: str | None = None,
) -> list[dict[str, object]]:
    records = services(request).repository.list_alerts(
        status=status.value if status else None,
        severity=severity.value if severity else None,
        run_id=run_id,
        limit=limit,
        offset=offset,
    )
    return [Alert.model_validate(record).model_dump(mode="json") for record in records]


@router.post("/alerts/{alert_id}/acknowledge", response_model=Alert)
async def acknowledge(request: Request, alert_id: str) -> dict[str, object]:
    service = services(request)
    before = service.repository.get_alert(alert_id)
    if before is None:
        raise APIError(404, "ALERT_NOT_FOUND", "Alert does not exist", {"alert_id": alert_id})
    alert = service.repository.acknowledge_alert(alert_id)
    if alert is None:
        raise APIError(404, "ALERT_NOT_FOUND", "Alert does not exist", {"alert_id": alert_id})
    if before["status"] != alert["status"]:
        service.sequence.bump()
    return Alert.model_validate(alert).model_dump(mode="json")


@router.get("/associations", response_model=list[AssociationRecord])
async def associations(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    run_id: str | None = None,
    status: Literal["UNASSOCIATED", "CANDIDATE", "CONFIRMED", "UNKNOWN", "EXPIRED", "REVOKED"] | None = None,
) -> list[dict[str, object]]:
    records = services(request).repository.list_associations(
        run_id=run_id, status=status, limit=limit, offset=offset
    )
    return [AssociationRecord.model_validate(record).model_dump(mode="json") for record in records]


@router.get("/runs/{run_id}/provenance")
async def run_provenance(request: Request, run_id: str) -> dict[str, object]:
    record = await asyncio.to_thread(services(request).repository.get_run_provenance, run_id)
    if record is None:
        raise APIError(404, "PROVENANCE_NOT_FOUND", "No provenance was recorded for this run", {"run_id": run_id})
    return {"run_id": run_id, "recorded_at": record["recorded_at"], **record["provenance_json"]}


@router.get("/versions")
async def versions(request: Request) -> dict[str, object]:
    return services(request).snapshots.versions()


@router.get("/cameras/{camera_id}/detections")
async def camera_detections(
    request: Request, camera_id: str, after_frame: int | None = Query(default=None, ge=-1),
    limit: int = Query(default=90, ge=0, le=150),
) -> dict[str, object]:
    """Recent tracked person boxes of the current epoch, for drawing over the source preview.

    Display only: counts and history come from the camera-local analytics, never from these boxes.
    Each frame carries its file-timeline position so the browser can align the boxes with the
    preview video; boxes are in pipeline frame pixels (``frame_width`` x ``frame_height``).
    """
    service = services(request)
    if camera_id not in service.config.camera_map:
        raise APIError(404, "CAMERA_NOT_FOUND", "Camera does not exist", {"camera_id": camera_id})
    return service.supervisor.detections(camera_id, after_frame, limit)


@router.get("/cameras/{camera_id}/preview")
async def preview(request: Request, camera_id: str):
    service = services(request)
    camera = service.config.camera_map.get(camera_id)
    if camera is None:
        raise APIError(404, "CAMERA_NOT_FOUND", "Camera does not exist", {"camera_id": camera_id})
    if not camera.enabled:
        raise APIError(409, "CAMERA_DISABLED", "Camera is disabled", {"camera_id": camera_id})
    if camera.source_type != "file" or camera.source_path is None:
        raise APIError(409, "PREVIEW_UNAVAILABLE", "No local source-file preview is available for this camera")
    state = service.supervisor.states()[camera_id]
    label = None if state.perception_started else "SOURCE PREVIEW · ANALYTICS UNAVAILABLE"
    return stream_file(
        request, camera.source_path, media_type=None,
        unavailable_code="SOURCE_UNAVAILABLE", preview_label=label,
    )


class EvidenceMarkerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=500)
    # Source time on the camera epoch's timeline; defaults to the latest consumed frame's source time.
    source_time_s: float | None = Field(default=None, ge=0)


@router.post("/cameras/{camera_id}/evidence-markers", status_code=201)
async def create_evidence_marker(request: Request, camera_id: str, body: EvidenceMarkerRequest | None = None) -> dict[str, object]:
    """Operator evidence marker: a real snapshot + source clip at a source time this epoch has reached.

    Labelled as a manual operator action; it is not an analytics detection and raises only an INFO
    operator-marker alert that links the evidence.
    """
    service = services(request)
    body = body or EvidenceMarkerRequest()
    camera = service.config.camera_map.get(camera_id)
    if camera is None:
        raise APIError(404, "CAMERA_NOT_FOUND", "Unknown camera", {"camera_id": camera_id})
    capability = service.evidence.capability()
    if not capability["available"]:
        raise APIError(503, "EVIDENCE_TOOL_UNAVAILABLE", capability["unavailable_reason"])
    run_id, run_status, _ = service.supervisor.current_run()
    state = service.supervisor.states()[camera_id]
    if run_id is None or run_status in ("STOPPED", "IDLE") or state.epoch is None:
        raise APIError(409, "NO_ACTIVE_EPOCH", "Evidence markers need a camera epoch in the current run",
                       {"camera_id": camera_id, "run_status": run_status})
    reached = state.source_position_s
    if reached is None:
        raise APIError(409, "NO_SOURCE_TIME", "This camera epoch has not consumed a frame yet; no source time exists",
                       {"camera_id": camera_id})
    source_time = reached if body.source_time_s is None else body.source_time_s
    if source_time > reached:
        raise APIError(422, "SOURCE_TIME_NOT_REACHED", "Requested source time is beyond what this epoch has processed",
                       {"requested": source_time, "reached": reached})
    if state.source_origin_s is not None and source_time < state.source_origin_s:
        raise APIError(422, "SOURCE_TIME_BEFORE_EPOCH", "Requested source time precedes this epoch's first frame",
                       {"requested": source_time, "origin": state.source_origin_s})
    if (state.source_segment or 0) != 0:
        raise APIError(409, "SOURCE_TIMELINE_DISCONTINUOUS",
                       "This epoch's source timeline has a discontinuity; mapping it onto the file is ambiguous",
                       {"segment": state.source_segment})
    # A looping file restarts its own timeline on every pass: align on that pass's first file frame.
    origin, refusal = service.supervisor.evidence_origin(camera_id, source_time)
    if refusal:
        raise APIError(409, "SOURCE_LOOP_UNMAPPED", refusal, {"requested": source_time})
    provenance = await asyncio.to_thread(service.repository.get_run_provenance, run_id)
    detector = (provenance or {}).get("provenance_json", {}).get("detector") or {}
    try:
        result = await asyncio.to_thread(
            service.evidence.capture_marker, run_id=run_id, camera_id=camera_id, camera_epoch=state.epoch,
            source_time_s=source_time, note=body.note, pipeline_origin_s=origin,
            frame_number=state.source_frame_number if body.source_time_s is None else None,
            provenance={"detector_id": detector.get("id"), "detector_acceptance": detector.get("acceptance"),
                        "replay_mode": state.replay_mode, "source_position_reached_s": reached,
                        "replay_pass_when_marked": state.loop_index if state.loop else None})
    except EvidenceUnavailable as exc:
        raise APIError(503, "EVIDENCE_UNAVAILABLE", str(exc)) from exc
    snapshot, clip = result["snapshot"], result["clip"]
    alert = await asyncio.to_thread(
        service.repository.open_alert, run_id, f"operator-marker:{result['group_id']}", "INFO", "operator_marker",
        "Operator evidence marker", f"{camera.name}: manual marker at source {source_time:.2f} s"
        + (f" - {body.note}" if body.note else ""), "camera", camera_id,
        {"source_time_s": source_time, "clip_source_start_s": clip["source_start_s"],
         "clip_source_end_s": clip["source_end_s"], "evidence_ids": [snapshot["id"], clip["id"]]},
        "operator-marker-v1", snapshot["id"])
    service.sequence.bump()
    return {**result, "alert_id": alert["id"]}


@router.get("/evidence")
async def list_evidence(
    request: Request, camera_id: str | None = Query(default=None), run_id: str | None = Query(default=None),
    group: str | None = Query(default=None), limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    service = services(request)
    items = await asyncio.to_thread(service.evidence.list, camera_id=camera_id, run_id=run_id, group=group,
                                    limit=limit, offset=offset)
    return {"items": items, "usage": await asyncio.to_thread(service.evidence.usage),
            "capability": service.evidence.capability()}


@router.get("/evidence/{evidence_id}/meta")
async def evidence_meta(request: Request, evidence_id: str) -> dict[str, object]:
    service = services(request)
    record = await asyncio.to_thread(service.repository.get_evidence, evidence_id)
    if record is None:
        raise APIError(404, "EVIDENCE_NOT_FOUND", "Evidence does not exist", {"evidence_id": evidence_id})
    return service.evidence.describe(record)  # type: ignore[return-value]


@router.get("/evidence/{evidence_id}")
async def evidence(request: Request, evidence_id: str):
    service = services(request)
    record = service.repository.get_evidence(evidence_id)
    if record is None:
        raise APIError(404, "EVIDENCE_NOT_FOUND", "Evidence does not exist", {"evidence_id": evidence_id})
    candidate = service.evidence.resolve(record)
    if candidate is None:
        raise APIError(403, "EVIDENCE_PATH_FORBIDDEN", "Evidence path is outside the authorized local evidence root")
    return stream_file(
        request, candidate, media_type=record["media_type"], unavailable_code="EVIDENCE_UNAVAILABLE"
    )
