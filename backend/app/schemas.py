"""Versioned API response schemas shared with the frontend contract."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Health(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    CALIBRATION_WARNING = "CALIBRATION WARNING"


class Calibration(str, Enum):
    VALID = "VALID"
    MISSING = "MISSING"
    INVALID = "INVALID"
    STALE = "STALE"


class Quality(str, Enum):
    OBSERVED = "OBSERVED"
    ESTIMATED = "ESTIMATED"
    UNAVAILABLE = "UNAVAILABLE"


class AlertSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertStatus(str, Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class RunStatus(str, Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    DEGRADED = "DEGRADED"
    # Stop requested but release of an owned camera container was not confirmed; retry stop.
    STOP_FAILED = "STOP_FAILED"


class Congestion(str, Enum):
    NORMAL = "NORMAL"
    BUSY = "BUSY"
    CONGESTED = "CONGESTED"
    SEVERELY_CONGESTED = "SEVERELY CONGESTED"
    UNAVAILABLE = "UNAVAILABLE"


class Camera(BaseModel):
    id: str
    name: str
    floor_id: str
    enabled: bool
    health: Health = Health.OFFLINE
    calibration_status: Calibration = Calibration.MISSING
    calibration_revision: str | None = None
    source_type: Literal["file", "rtsp"]
    source_label: str
    epoch: str | None = None
    visible_count: int | None = None
    source_fps: float | None = None
    analytics_fps: float | None = None
    frame_age_ms: float | None = None
    frame_age_basis: str | None = None
    dropped_frames: int = 0
    last_good_age_ms: float | None = None
    unavailable_reason: str | None = None
    preview_url: str | None  # null for live sources (no local file to preview)
    # Time-model disclosure (backend/app/analytics/timebase.py).
    replay_mode: Literal["paced", "unpaced"] | None = None
    analytics_fps_basis: str | None = None
    source_position_s: float | None = None
    source_segment: int | None = None
    source_time_basis: str | None = None
    source_discontinuities: int = 0
    backend_queue_ms: float | None = None
    persisted_count_samples: int = 0
    camera_line_crossings: int = 0
    # How the source ended without an operator stop (null while running or after a stop).
    stream_outcome: Literal["CLEAN_EOF", "ABNORMAL_TERMINATION", "CONSUMER_FAILED"] | None = None
    stream_outcome_detail: str | None = None
    frames_at_detection_limit: int = 0
    detection_limit: int | None = None
    # Live (RTSP) sources: reconnect attempts since the last frame and when the next one is due.
    reconnect_attempt: int = 0
    next_reconnect_at: str | None = None
    evidence_available: bool = True
    evidence_unavailable_reason: str | None = None


class CountHistorySample(BaseModel):
    camera_epoch: str
    source_segment: int
    bucket_index: int
    source_time_s: float
    frame_number: int | None = None
    visible_count: int
    visible_mean: float
    visible_max: int
    frames: int
    replay_mode: str | None = None
    received_at_utc: str
    count_basis: str
    # Downsampled bins: buckets covered and buckets that actually had frames (1/1 when not downsampled).
    bucket_span: int = 1
    buckets_present: int = 1


class CameraCountHistory(BaseModel):
    camera_id: str
    run_id: str | None = None
    camera_epoch: str | None = None
    time_basis: str
    receive_time_basis: str
    sample_seconds: float
    samples: list[CountHistorySample] = Field(default_factory=list)
    unavailable_reason: str | None = None
    window: Literal["recent", "range"] = "recent"
    total_samples: int = 0
    returned_samples: int = 0
    segments: int = 0
    first_source_s: float | None = None
    last_source_s: float | None = None
    next_cursor: str | None = None
    downsample_factor: int = 1


class CameraCrossing(BaseModel):
    event_id: str
    source_segment: int
    line_id: str
    coordinate_space: Literal["image_pixels"]
    direction: Literal["IN", "OUT"]
    track_key: str
    source_time_s: float
    received_at_utc: str


class CameraCrossings(BaseModel):
    camera_id: str
    run_id: str | None = None
    camera_epoch: str | None = None
    time_basis: str
    configured_image_lines: list[str] = Field(default_factory=list)
    crossings: list[CameraCrossing] = Field(default_factory=list)
    unavailable_reason: str | None = None


class Zone(BaseModel):
    id: str
    floor_id: str
    name: str
    kind: Literal["dining", "queue", "entrance", "stairs", "corridor", "blind"]
    polygon: list[tuple[float, float]]
    capacity: float | None = None
    occupancy: int | None = None
    congestion: Congestion = Congestion.UNAVAILABLE
    queue_size: int | None = None


class FloorCamera(BaseModel):
    id: str
    name: str
    position: tuple[float, float]
    coverage_polygon: list[tuple[float, float]]
    health: Health
    calibration_status: Calibration


class FloorPortal(BaseModel):
    id: str
    name: str
    position: tuple[float, float]
    direction: Literal["UP", "DOWN", "BOTH"]


class Floor(BaseModel):
    id: str
    name: str
    level: int
    width: int = 1000
    height: int = 600
    visible_occupancy: int | None = None
    estimated_occupancy: int | None = None
    quality: Quality = Quality.UNAVAILABLE
    zones: list[Zone]
    cameras: list[FloorCamera]
    walls: list[list[tuple[float, float]]]
    portals: list[FloorPortal]


class Alert(BaseModel):
    id: str
    stable_key: str
    severity: AlertSeverity
    category: str
    title: str
    message: str
    scope_type: str
    scope_id: str | None
    status: AlertStatus
    triggered_at: str
    acknowledged_at: str | None = None
    measurements: dict[str, Any] = Field(default_factory=dict)
    rule_revision: str
    evidence_id: str | None = None


class SystemMetrics(BaseModel):
    timestamp: str
    scope: Literal["windows-host", "wsl2", "linux-host", "container", "unavailable"]
    gpu_name: str | None = None
    gpu_utilization: float | None = None
    vram_used_mib: float | None = None
    vram_total_mib: float | None = None
    gpu_temperature_c: float | None = None
    nvdec_utilization: float | None = None
    cpu_utilization: float | None = None
    ram_used_mb: float | None = None
    ram_total_mb: float | None = None
    process_rss_mb: float | None = None
    unavailable_reason: str | None = None


class KPIs(BaseModel):
    building_expected: int | None = None
    building_visible: int | None = None
    building_estimated: int | None = None
    unresolved_transitions: int | None = None
    discrepancy: int | None = None
    quality: Quality = Quality.UNAVAILABLE
    entries: int | None = None
    exits: int | None = None
    queue_total: int | None = None
    max_wait_seconds: float | None = None
    stair_up_per_min: float | None = None
    stair_down_per_min: float | None = None
    congested_zones: int = 0
    healthy_cameras: int = 0
    enabled_cameras: int


class Notice(BaseModel):
    severity: AlertSeverity
    title: str
    message: str


class AssociationRecord(BaseModel):
    id: str
    run_id: str
    portal_id: str | None = None
    source_track_key: str
    candidate_track_key: str | None = None
    temporary_global_track_id: str | None = None
    status: Literal["UNASSOCIATED", "CANDIDATE", "CONFIRMED", "UNKNOWN", "EXPIRED", "REVOKED"]
    score: float | None = None
    margin: float | None = None
    decision_reason: str
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    updated_at: str
    supersedes_id: str | None = None


class Snapshot(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str | None
    run_status: RunStatus
    generated_at: str
    sequence: int
    watermark: int
    cameras: list[Camera]
    floors: list[Floor]
    alerts: list[Alert]
    kpis: KPIs
    associations: list[AssociationRecord]
    system_metrics: SystemMetrics
    notices: list[Notice]


class HistoryPoint(BaseModel):
    timestamp: str
    value: float | None
    quality: Quality
    run_id: str | None


class HistoryResponse(BaseModel):
    metric: str
    points: list[HistoryPoint]
    gaps: list[dict[str, Any]] = Field(default_factory=list)
    next_cursor: str | None = None


class ReadinessCheck(BaseModel):
    name: str
    ready: bool
    detail: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: list[ReadinessCheck]
