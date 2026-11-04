"""Strict YAML configuration models, cross-reference checks, and provenance."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConfigurationError(ValueError):
    """Raised when configuration is structurally or referentially invalid."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


Point = tuple[float, float]
Polygon = list[Point]


def _valid_polygon(value: Polygon) -> Polygon:
    if len(value) < 3:
        raise ValueError("A polygon requires at least three points")
    if len(set(value)) < 3:
        raise ValueError("A polygon requires three distinct points")
    if any(not math.isfinite(coordinate) for point in value for coordinate in point):
        raise ValueError("Polygon coordinates must be finite")
    area = abs(
        sum(
            value[index][0] * value[(index + 1) % len(value)][1]
            - value[(index + 1) % len(value)][0] * value[index][1]
            for index in range(len(value))
        )
    ) / 2
    if area <= 0:
        raise ValueError("Polygon area must be greater than zero")
    return value


class CameraConfig(StrictModel):
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    name: str = Field(min_length=1)
    floor_id: str
    enabled: bool = True
    source_type: Literal["file", "rtsp"]
    source: str = Field(min_length=1)
    analytics_fps: float = Field(gt=0, le=120)
    preview_fps: float = Field(gt=0, le=60)
    overlap_group: str | None = None
    calibration_ref: str | None = None
    calibration_status: Literal["VALID", "MISSING", "INVALID", "STALE"] = "MISSING"
    calibration_revision: str | None = None
    replay_group: str | None = None
    timestamp_offset_ms: int = 0
    # File sources only: replay the clip continuously (demo playback) instead of ending at end of file.
    loop: bool = False
    position: Point
    coverage_polygon: Polygon
    source_path: Path | None = Field(default=None, exclude=True)

    @field_validator("coverage_polygon")
    @classmethod
    def coverage_is_valid(cls, value: Polygon) -> Polygon:
        return _valid_polygon(value)

    @model_validator(mode="after")
    def source_is_well_formed(self) -> "CameraConfig":
        if self.source_type == "rtsp":
            parsed = urlsplit(self.source)
            if parsed.scheme != "rtsp" or not parsed.hostname:
                raise ValueError("RTSP sources must be absolute rtsp:// URLs")
            if self.loop:
                raise ValueError("loop applies to file sources only; a live source has no end to loop")
        elif "://" in self.source:
            raise ValueError("File sources must be local filesystem paths")
        if self.calibration_status == "VALID" and not self.calibration_revision:
            raise ValueError("Valid calibration requires a revision")
        return self

    @property
    def source_label(self) -> str:
        if self.source_type == "file":
            return Path(self.source).name
        parsed = urlsplit(self.source)
        # Never expose credentials or query strings from RTSP URLs.
        return f"RTSP {parsed.hostname or 'source'}"


class Wall(StrictModel):
    start: Point
    end: Point

    @model_validator(mode="after")
    def nonzero_length(self) -> "Wall":
        if self.start == self.end:
            raise ValueError("Wall endpoints must differ")
        return self


class FloorConfig(StrictModel):
    id: str
    name: str
    level: int
    width: Literal[1000] = 1000
    height: Literal[600] = 600
    units: Literal["viewBox", "meters"] = "viewBox"
    fixture_status: Literal["ILLUSTRATIVE_UNCALIBRATED", "SURVEYED"]
    # Real-world scale of one map unit (from a surveyed plan); metric fusion/density need it.
    meters_per_unit: float | None = Field(default=None, gt=0)
    walls: list[Wall] = Field(default_factory=list)


class ZoneConfig(StrictModel):
    id: str
    floor_id: str
    name: str
    kind: Literal["dining", "queue", "entrance", "stairs", "corridor", "blind"]
    polygon: Polygon
    capacity: float | None = Field(default=None, gt=0)
    area_m2: float | None = Field(default=None, gt=0)
    authoritative_camera_ids: list[str] = Field(default_factory=list)
    overlap_group: str | None = None
    rule_ref: str | None = None

    @field_validator("polygon")
    @classmethod
    def polygon_is_valid(cls, value: Polygon) -> Polygon:
        return _valid_polygon(value)


class PortalEndpoint(StrictModel):
    floor_id: str
    zone_id: str
    position: Point
    camera_id: str | None = None


class PortalConfig(StrictModel):
    id: str
    name: str
    kind: Literal["stairs", "door", "corridor"] = "stairs"
    direction: Literal["UP", "DOWN", "BOTH"]
    source: PortalEndpoint
    destination: PortalEndpoint
    min_travel_seconds: float = Field(ge=0)
    max_travel_seconds: float = Field(gt=0)
    association_policy: str = "threshold_and_margin"

    @model_validator(mode="after")
    def travel_window_is_valid(self) -> "PortalConfig":
        if self.max_travel_seconds <= self.min_travel_seconds:
            raise ValueError("Maximum portal travel time must exceed minimum")
        return self


class CrossingSettings(StrictModel):
    dead_band: float = Field(default=8, gt=0)
    debounce_seconds: float = Field(default=2, ge=0)
    confirmation_seconds: float = Field(default=0.15, ge=0)
    min_track_seconds: float = Field(default=0.4, ge=0)
    min_track_observations: int = Field(default=3, ge=1)
    min_displacement: float = Field(default=2, ge=0)
    stale_after_ms: int = Field(default=1500, gt=0)


class BoundaryConfig(StrictModel):
    """A directional line. coordinate_space decides which observations may use it:

    * floor_map: facility/floor coordinates; requires a VALID camera calibration to map
      detections onto the floor, so it is inactive for uncalibrated cameras.
    * image_pixels: camera-local pixel line on the pipeline frame (frame_size). It counts
      camera-local image crossings only; it is not a facility portal and never feeds
      building entries/exits or occupancy. Only meaningful for a fixed camera view.
    """

    id: str
    camera_id: str
    start: Point
    end: Point
    positive_direction: Literal["IN", "OUT"]
    owner: str
    enabled: bool = True
    coordinate_space: Literal["floor_map", "image_pixels"] = "floor_map"
    frame_size: tuple[int, int] | None = None
    purpose: str | None = None

    @model_validator(mode="after")
    def nonzero_line(self) -> "BoundaryConfig":
        if self.start == self.end:
            raise ValueError("Boundary endpoints must differ")
        if self.coordinate_space == "image_pixels":
            if self.frame_size is None or min(self.frame_size) <= 0:
                raise ValueError("image_pixels boundaries require a positive frame_size [width, height]")
            width, height = self.frame_size
            for x, y in (self.start, self.end):
                if not (0 <= x <= width and 0 <= y <= height):
                    raise ValueError("image_pixels boundary endpoints must lie inside frame_size")
        return self


class QueueSettings(StrictModel):
    join_dwell_seconds: float = Field(default=2, ge=0)
    leave_dwell_seconds: float = Field(default=2, ge=0)
    max_occlusion_seconds: float = Field(default=3, ge=0)
    min_join_movement: float = Field(default=4, ge=0)
    overflow_threshold: int = Field(default=12, gt=0)
    stale_after_ms: int = Field(default=1500, gt=0)


class CongestionSettings(StrictModel):
    revision: str = "congestion-v1"
    metric: Literal["count", "density", "capacity_ratio"] = "count"
    busy_threshold: float = Field(gt=0)
    congested_threshold: float = Field(gt=0)
    severe_threshold: float = Field(gt=0)
    persist_seconds: float = Field(default=5, ge=0)
    recover_seconds: float = Field(default=8, ge=0)
    recovery_hysteresis: float = Field(default=0.15, ge=0, lt=1)

    @model_validator(mode="after")
    def thresholds_increase(self) -> "CongestionSettings":
        if not self.busy_threshold < self.congested_threshold < self.severe_threshold:
            raise ValueError("Congestion thresholds must strictly increase")
        return self


class OccupancySettings(StrictModel):
    initial_occupancy: int = Field(default=0, ge=0)
    boundary_coverage_complete: bool = False
    discrepancy_tolerance: int = Field(default=0, ge=0)
    discrepancy_duration_seconds: float = Field(default=5, ge=0)


class AssociationSettings(StrictModel):
    revision: str = "association-v1"
    acceptance_threshold: float = Field(default=0.75, ge=0, le=1)
    ambiguity_margin: float = Field(default=0.12, ge=0, le=1)
    max_time_delta_ms: int = Field(default=250, gt=0)
    max_world_distance_m: float = Field(default=0.75, gt=0)
    hypothesis_ttl_seconds: float = Field(default=60, gt=0)


class HealthSettings(StrictModel):
    stale_after_ms: int = Field(default=1500, gt=0)
    recovery_frames: int = Field(default=3, ge=1)
    reconnect_backoff_seconds: float = Field(default=2, gt=0)
    max_reconnect_backoff_seconds: float = Field(default=30, gt=0)
    # A live (RTSP) camera with no frame for this long is treated as lost and reconnected.
    live_stall_seconds: float = Field(default=10, gt=0)


class RetentionSettings(StrictModel):
    event_history_days: int = Field(default=7, gt=0)
    metrics_days: int = Field(default=2, gt=0)
    evidence_days: int = Field(default=7, gt=0)
    track_samples_days: int = Field(default=1, gt=0)
    hypotheses_minutes: int = Field(default=60, gt=0)
    max_database_mib: int = Field(default=1024, gt=0)
    max_evidence_mib: int = Field(default=4096, gt=0)


class AnalyticsConfig(StrictModel):
    schema_version: Literal[1]
    revision: str
    crossing: CrossingSettings
    boundaries: list[BoundaryConfig] = Field(default_factory=list)
    queue: QueueSettings
    congestion: CongestionSettings
    occupancy: OccupancySettings
    association: AssociationSettings
    health: HealthSettings
    retention: RetentionSettings


def _check_sha256(value: str | None) -> str | None:
    if value is not None and (len(value) != 64 or any(c not in "0123456789abcdef" for c in value)):
        raise ValueError("sha256 values must be 64 lowercase hex characters")
    return value


class ParserSpec(StrictModel):
    """How nvinfer turns tensors into boxes. builtin = a DeepStream-bundled parser; custom_library = a .so."""

    kind: Literal["builtin", "custom_library"]
    name: str = Field(min_length=1)
    library_path: Path | None = None
    library_sha256: str | None = None
    function: str | None = None

    @model_validator(mode="after")
    def custom_library_is_pinned(self) -> "ParserSpec":
        _check_sha256(self.library_sha256)
        if self.kind == "custom_library" and not (self.library_path and self.library_sha256 and self.function):
            raise ValueError("custom_library parsers require library_path, library_sha256, and function")
        if self.kind == "builtin" and (self.library_path or self.library_sha256 or self.function):
            raise ValueError("builtin parsers must not name a custom library")
        return self


class DetectorRuntimeProfile(StrictModel):
    """Everything the Service Maker runtime needs to run one detector, with expected identities.

    person_class_id/person_label normalize the detector's own class numbering (TrafficCamNet person
    is class 2; the RT-DETR/RF-DETR candidate parsers emit person as class 0) into the application's
    single "person" observation class. Objects of any other class are counted and reported, never
    silently relabelled.
    """

    infer_config_path: Path
    infer_config_sha256: str
    parser: ParserSpec
    person_class_id: int = Field(ge=0)
    person_label: str = Field(min_length=1)
    # nvstreammux output (pipeline frame) size; detections and image-space geometry use this space.
    pipeline_width: int = Field(gt=0)
    pipeline_height: int = Field(gt=0)
    tracker_width: int = Field(default=960, gt=0)
    tracker_height: int = Field(default=544, gt=0)
    # require_exact: the source must already be pipeline_width x pipeline_height (no resampling the
    # model's preprocessing contract was not validated for). stretch_disclosed: nvstreammux rescales
    # without padding; any aspect-ratio change is disclosed in the run provenance and frame metadata.
    source_geometry_policy: Literal["require_exact", "stretch_disclosed"]
    preprocessing: str = Field(min_length=1)
    # Per-class top-k configured in the infer config; a frame reaching it may have dropped detections.
    detection_limit_per_class: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def hashes_are_well_formed(self) -> "DetectorRuntimeProfile":
        _check_sha256(self.infer_config_sha256)
        return self


class ModelArtifact(StrictModel):
    id: str
    role: Literal["detector", "reid"]
    enabled: bool = False
    # accepted: met the applicable acceptance criteria; provisional_smoke: plumbing only;
    # candidate_unaccepted: runnable for evaluation, not accepted.
    acceptance: Literal["accepted", "provisional_smoke", "candidate_unaccepted"] = "candidate_unaccepted"
    evaluation_ref: str | None = None
    runtime_profile: DetectorRuntimeProfile | None = None
    checkpoint_path: Path
    engine_path: Path | None = None
    revision: str | None = None
    sha256: str | None = None
    engine_sha256: str | None = None
    source_repository: str | None = None
    license_ref: str | None = None
    class_map: dict[int, str] = Field(default_factory=lambda: {0: "person"})
    input_shape: list[int] = Field(default_factory=list)
    precision: str | None = None
    parser_revision: str | None = None

    @model_validator(mode="after")
    def enabled_artifacts_are_pinned(self) -> "ModelArtifact":
        _check_sha256(self.sha256)
        _check_sha256(self.engine_sha256)
        if self.runtime_profile is not None:
            label = self.class_map.get(self.runtime_profile.person_class_id)
            if label != self.runtime_profile.person_label:
                raise ValueError(
                    f"person_class_id {self.runtime_profile.person_class_id} maps to {label!r} in class_map, "
                    f"not {self.runtime_profile.person_label!r}")
            if self.engine_path is None or self.engine_sha256 is None:
                raise ValueError("A runtime profile requires a pinned engine_path and engine_sha256")
        if self.enabled:
            missing = [
                name
                for name in ("revision", "sha256", "source_repository", "license_ref", "precision", "parser_revision")
                if getattr(self, name) is None
            ]
            if not self.input_shape:
                missing.append("input_shape")
            if missing:
                raise ValueError(f"Enabled model is missing pinned fields: {', '.join(missing)}")
        return self


class TrackerConfig(StrictModel):
    name: str = "NVIDIA tracker"
    enabled: bool = False
    revision: str | None = None
    config_path: Path | None = None
    sha256: str | None = None
    library: str = "/opt/nvidia/deepstream/deepstream/lib/libnvds_nvmultiobjecttracker.so"

    @model_validator(mode="after")
    def enabled_tracker_is_pinned(self) -> "TrackerConfig":
        _check_sha256(self.sha256)
        if self.enabled and (not self.revision or not self.config_path or not self.sha256):
            raise ValueError("Enabled tracker requires revision, config_path, and sha256")
        return self


class PipelineArtifacts(StrictModel):
    """Expected identities of the runtime pieces shared by every detector profile."""

    image_reference_path: Path
    image: str = Field(pattern=r"@sha256:[0-9a-f]{64}$")
    wrapper_path: Path
    wrapper_sha256: str
    extractor_path: Path
    extractor_sha256: str
    # Frame schema the extractor emits; 1.1 adds emitted_unix_ns (extract_metadata_v2.py).
    extractor_schema_version: Literal["1.0", "1.1"] = "1.0"
    # "source": the extractor itself holds paced replay to the source timeline (v2 only), so the
    # producer cannot run ahead of presentation through the WSL pipe; unpaced runs never pace.
    extractor_pacing: Literal["none", "source"] = "none"
    pace_lead_ms: float = Field(default=200, ge=0, le=5000)
    probe_script_path: Path
    probe_script_sha256: str

    @model_validator(mode="after")
    def hashes_are_well_formed(self) -> "PipelineArtifacts":
        for value in (self.wrapper_sha256, self.extractor_sha256, self.probe_script_sha256):
            _check_sha256(value)
        if self.extractor_pacing == "source" and self.extractor_schema_version != "1.1":
            raise ValueError("extractor_pacing: source requires the v2 extractor (extractor_schema_version 1.1)")
        return self


SELECTION_ACCEPTANCE = {
    "evaluated_baseline": "accepted",
    "provisional_smoke": "provisional_smoke",
    "candidate_evaluation": "candidate_unaccepted",
}


class ModelsConfig(StrictModel):
    schema_version: Literal[1]
    selected_model: str | None
    # The selection mode must agree with the selected model's acceptance, so an unaccepted
    # candidate can be run for evaluation but can never be presented as the evaluated baseline.
    selection_mode: Literal["evaluated_baseline", "provisional_smoke", "candidate_evaluation"] = "evaluated_baseline"
    pipeline: PipelineArtifacts | None = None
    models: list[ModelArtifact]
    tracker: TrackerConfig

    @property
    def selected(self) -> ModelArtifact | None:
        return next((model for model in self.models if model.id == self.selected_model), None)


class TelemetrySettings(StrictModel):
    interval_seconds: float = Field(default=2, gt=0, le=60)
    command_timeout_seconds: float = Field(default=2.5, gt=0, le=15)
    success_cache_seconds: float = Field(default=1.5, gt=0, le=60)
    failure_cache_seconds: float = Field(default=10, gt=0, le=300)
    wsl_distro: str = Field(default="Ubuntu-22.04", min_length=1)


class ReplaySettings(StrictModel):
    # paced: release frames on the source PTS timeline (demo); unpaced: max throughput (evaluation/benchmark).
    mode: Literal["paced", "unpaced"] = "paced"
    # Source-time spacing of persisted visible-count history samples.
    history_sample_seconds: float = Field(default=0.5, gt=0, le=60)
    # Per-epoch frame-record file (backend/var/perception/<camera>/<epoch>.jsonl): rotated to .prev at
    # this size so long or looping runs stay bounded (about 2x on disk). 0 = unlimited.
    frame_log_max_mib: float = Field(default=256, ge=0)
    # Deadline for a camera's first frame metadata after its container starts. Concurrent starts share
    # engine loading and GPU initialization (A6000, RT-DETR FP16: 8.6 s for 1 camera, 11.7 s for 2 at once).
    # A pipeline that fails exits and is reported immediately; this only bounds a slow or hung start.
    startup_timeout_seconds: float = Field(default=60, gt=0, le=600)


class CapabilitySettings(StrictModel):
    # Expensive container capability probe: reused while its fingerprint matches and a cheap
    # liveness check passes, for at most max_age_seconds.
    max_age_seconds: float = Field(default=43200, gt=0)
    failure_retry_seconds: float = Field(default=10, gt=0)
    liveness_cache_seconds: float = Field(default=5, ge=0)
    probe_timeout_seconds: float = Field(default=60, gt=0)
    cache_path: Path = Path("./backend/var/capability-cache.json")


class EvidenceSettings(StrictModel):
    # Source clips are stream-copied from the original file around the requested source time.
    clip_pre_seconds: float = Field(default=3, ge=0, le=60)
    clip_post_seconds: float = Field(default=3, ge=0, le=60)
    max_records: int = Field(default=200, gt=0)
    tool_timeout_seconds: float = Field(default=60, gt=0)


class RuntimeConfig(StrictModel):
    schema_version: Literal[1]
    environment: Literal["development", "production"] = "production"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    offline: bool = True
    database_path: Path
    evidence_root: Path
    frontend_dist: Path
    serve_frontend: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    max_page_size: int = Field(default=500, ge=1, le=5000)
    telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)
    replay: ReplaySettings = Field(default_factory=ReplaySettings)
    capability: CapabilitySettings = Field(default_factory=CapabilitySettings)
    evidence: EvidenceSettings = Field(default_factory=EvidenceSettings)


class AppConfig(StrictModel):
    cameras: list[CameraConfig]
    floors: list[FloorConfig]
    zones: list[ZoneConfig]
    portals: list[PortalConfig]
    analytics: AnalyticsConfig
    models: ModelsConfig
    runtime: RuntimeConfig
    project_root: Path = Field(exclude=True)
    config_dir: Path = Field(exclude=True)
    configuration_revision: str
    file_revisions: dict[str, str]
    # camera_id -> analytics.calibration.Calibration for cameras with a VALID, loaded calibration.
    calibrations: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @property
    def camera_map(self) -> dict[str, CameraConfig]:
        return {camera.id: camera for camera in self.cameras}

    @property
    def floor_map(self) -> dict[str, FloorConfig]:
        return {floor.id: floor for floor in self.floors}

    @property
    def zone_map(self) -> dict[str, ZoneConfig]:
        return {zone.id: zone for zone in self.zones}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigurationError(f"Required configuration file is missing: {path.name}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Cannot read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"{path.name} must contain a YAML mapping")
    return value


def _ensure_unique(kind: str, identifiers: list[str]) -> None:
    duplicates = sorted({item for item in identifiers if identifiers.count(item) > 1})
    if duplicates:
        raise ConfigurationError(f"Duplicate {kind} IDs: {', '.join(duplicates)}")


def _resolve(project_root: Path, path: Path | str) -> Path:
    candidate = Path(path).expanduser()
    return (candidate if candidate.is_absolute() else project_root / candidate).resolve()


def load_configuration(config_dir: Path | str, project_root: Path | str | None = None) -> AppConfig:
    config_path = Path(config_dir).resolve()
    root = Path(project_root).resolve() if project_root else config_path.parent
    file_names = ("cameras", "floors", "zones", "portals", "analytics", "models", "runtime")
    raw = {name: _read_yaml(config_path / f"{name}.yaml") for name in file_names}
    try:
        cameras_doc = raw["cameras"]
        floors_doc = raw["floors"]
        zones_doc = raw["zones"]
        portals_doc = raw["portals"]
        if any(doc.get("schema_version") != 1 for doc in (cameras_doc, floors_doc, zones_doc, portals_doc)):
            raise ConfigurationError("All facility configuration schemas must be version 1")
        cameras = [CameraConfig.model_validate(item) for item in cameras_doc.get("cameras", [])]
        floors = [FloorConfig.model_validate(item) for item in floors_doc.get("floors", [])]
        zones = [ZoneConfig.model_validate(item) for item in zones_doc.get("zones", [])]
        portals = [PortalConfig.model_validate(item) for item in portals_doc.get("portals", [])]
        analytics = AnalyticsConfig.model_validate(raw["analytics"])
        models = ModelsConfig.model_validate(raw["models"])
        runtime = RuntimeConfig.model_validate(raw["runtime"])
    except ConfigurationError:
        raise
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"Configuration validation failed: {exc}") from exc

    _ensure_unique("camera", [camera.id for camera in cameras])
    _ensure_unique("floor", [floor.id for floor in floors])
    _ensure_unique("zone", [zone.id for zone in zones])
    _ensure_unique("portal", [portal.id for portal in portals])
    _ensure_unique("boundary", [boundary.id for boundary in analytics.boundaries])
    _ensure_unique("model", [model.id for model in models.models])
    floor_map = {floor.id: floor for floor in floors}
    camera_map = {camera.id: camera for camera in cameras}
    zone_map = {zone.id: zone for zone in zones}

    for camera in cameras:
        if camera.floor_id not in floor_map:
            raise ConfigurationError(f"Camera {camera.id} references unknown floor {camera.floor_id}")
        camera.source_path = _resolve(root, camera.source) if camera.source_type == "file" else None
        for x, y in camera.coverage_polygon + [camera.position]:
            if not (0 <= x <= 1000 and 0 <= y <= 600):
                raise ConfigurationError(f"Camera {camera.id} geometry lies outside the 1000 x 600 view box")
    for zone in zones:
        floor = floor_map.get(zone.floor_id)
        if floor is None:
            raise ConfigurationError(f"Zone {zone.id} references unknown floor {zone.floor_id}")
        for x, y in zone.polygon:
            if not (0 <= x <= floor.width and 0 <= y <= floor.height):
                raise ConfigurationError(f"Zone {zone.id} polygon lies outside its floor")
        for camera_id in zone.authoritative_camera_ids:
            camera = camera_map.get(camera_id)
            if camera is None:
                raise ConfigurationError(f"Zone {zone.id} references unknown camera {camera_id}")
            if camera.floor_id != zone.floor_id:
                raise ConfigurationError(f"Zone {zone.id} and camera {camera_id} are on different floors")
    for portal in portals:
        for endpoint in (portal.source, portal.destination):
            zone = zone_map.get(endpoint.zone_id)
            if endpoint.floor_id not in floor_map or zone is None:
                raise ConfigurationError(f"Portal {portal.id} has an unresolved endpoint")
            if zone.floor_id != endpoint.floor_id:
                raise ConfigurationError(f"Portal {portal.id} mixes floor and zone coordinate systems")
            if endpoint.camera_id is not None:
                camera = camera_map.get(endpoint.camera_id)
                if camera is None or camera.floor_id != endpoint.floor_id:
                    raise ConfigurationError(f"Portal {portal.id} has an invalid endpoint camera")
            x, y = endpoint.position
            if not (0 <= x <= 1000 and 0 <= y <= 600):
                raise ConfigurationError(f"Portal {portal.id} position lies outside the view box")
    for boundary in analytics.boundaries:
        if boundary.camera_id not in camera_map:
            raise ConfigurationError(f"Boundary {boundary.id} references unknown camera {boundary.camera_id}")
    if models.selected_model is not None:
        selected = next((model for model in models.models if model.id == models.selected_model), None)
        if selected is None or not selected.enabled:
            raise ConfigurationError("selected_model must reference an enabled model")
        if selected.acceptance != SELECTION_ACCEPTANCE[models.selection_mode]:
            raise ConfigurationError(
                f"selection_mode {models.selection_mode} requires a model with acceptance "
                f"{SELECTION_ACCEPTANCE[models.selection_mode]}; {selected.id} is {selected.acceptance}")
    elif any(model.enabled for model in models.models):
        raise ConfigurationError("An enabled model must be explicitly selected")

    calibrations = _load_calibrations(config_path, cameras)
    runtime.database_path = _resolve(root, runtime.database_path)
    runtime.evidence_root = _resolve(root, runtime.evidence_root)
    runtime.frontend_dist = _resolve(root, runtime.frontend_dist)
    runtime.capability.cache_path = _resolve(root, runtime.capability.cache_path)
    for model in models.models:
        model.checkpoint_path = _resolve(root, model.checkpoint_path)
        if model.engine_path is not None:
            model.engine_path = _resolve(root, model.engine_path)
        profile = model.runtime_profile
        if profile is not None:
            profile.infer_config_path = _resolve(root, profile.infer_config_path)
            if profile.parser.library_path is not None:
                profile.parser.library_path = _resolve(root, profile.parser.library_path)
    if models.tracker.config_path is not None:
        models.tracker.config_path = _resolve(root, models.tracker.config_path)
    if models.pipeline is not None:
        for name in ("image_reference_path", "wrapper_path", "extractor_path", "probe_script_path"):
            setattr(models.pipeline, name, _resolve(root, getattr(models.pipeline, name)))

    file_revisions = {
        name: hashlib.sha256((config_path / f"{name}.yaml").read_bytes()).hexdigest() for name in file_names
    }
    combined = hashlib.sha256()
    for name in file_names:
        combined.update(name.encode("ascii") + b"\0" + bytes.fromhex(file_revisions[name]))
    return AppConfig(
        cameras=cameras,
        floors=floors,
        zones=zones,
        portals=portals,
        analytics=analytics,
        models=models,
        runtime=runtime,
        project_root=root,
        config_dir=config_path,
        configuration_revision=combined.hexdigest(),
        file_revisions=file_revisions,
        calibrations=calibrations,
    )


def _load_calibrations(config_path: Path, cameras: list[CameraConfig]) -> dict[str, Any]:
    """A VALID calibration status requires a matching, validating calibration file; nothing is assumed."""
    from .analytics.calibration import CalibrationError, load_calibration

    loaded: dict[str, Any] = {}
    for camera in cameras:
        if camera.calibration_ref is None:
            if camera.calibration_status == "VALID":
                raise ConfigurationError(f"Camera {camera.id} is VALID but has no calibration_ref")
            continue
        path = config_path / "calibration" / f"{camera.calibration_ref}.json"
        try:
            calibration = load_calibration(path)
        except CalibrationError as exc:
            if camera.calibration_status == "VALID":
                raise ConfigurationError(f"Camera {camera.id}: {exc}") from exc
            continue
        mismatches = [name for name, ok in (
            ("camera_id", calibration.camera_id == camera.id), ("floor_id", calibration.floor_id == camera.floor_id),
            ("revision", calibration.revision == camera.calibration_revision)) if not ok]
        valid, reason = calibration.validity()
        if camera.calibration_status == "VALID":
            if mismatches:
                raise ConfigurationError(f"Camera {camera.id} calibration file does not match: {', '.join(mismatches)}")
            if not valid:
                raise ConfigurationError(f"Camera {camera.id} calibration is not valid: {reason}")
            loaded[camera.id] = calibration
    return loaded
