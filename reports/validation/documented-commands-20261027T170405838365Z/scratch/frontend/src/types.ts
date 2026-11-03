export type Health = 'HEALTHY' | 'DEGRADED' | 'OFFLINE' | 'CALIBRATION WARNING';
export type Calibration = 'VALID' | 'MISSING' | 'INVALID' | 'STALE';
export type Quality = 'OBSERVED' | 'ESTIMATED' | 'UNAVAILABLE';
export type AlertSeverity = 'INFO' | 'WARNING' | 'CRITICAL';
export type AlertStatus = 'OPEN' | 'ACKNOWLEDGED' | 'RESOLVED';
export type RunStatus = 'IDLE' | 'STARTING' | 'RUNNING' | 'STOPPED' | 'DEGRADED' | 'STOP_FAILED';
export type Congestion = 'NORMAL' | 'BUSY' | 'CONGESTED' | 'SEVERELY CONGESTED' | 'UNAVAILABLE';
export type ZoneKind = 'dining' | 'queue' | 'entrance' | 'stairs' | 'corridor' | 'blind';

export interface Camera {
  id: string;
  name: string;
  floor_id: string;
  enabled: boolean;
  health: Health;
  calibration_status: Calibration;
  calibration_revision: string | null;
  source_type: string;
  source_label: string;
  epoch: string | null;
  visible_count: number | null;
  source_fps: number | null;
  analytics_fps: number | null;
  frame_age_ms: number | null;
  frame_age_basis: string | null;
  dropped_frames: number;
  last_good_age_ms: number | null;
  unavailable_reason: string | null;
  preview_url: string | null;
  replay_mode?: 'paced' | 'unpaced' | null;
  analytics_fps_basis?: string | null;
  source_position_s?: number | null;
  source_segment?: number | null;
  source_time_basis?: string | null;
  source_discontinuities?: number;
  backend_queue_ms?: number | null;
  persisted_count_samples?: number;
  camera_line_crossings?: number;
  stream_outcome?: 'CLEAN_EOF' | 'ABNORMAL_TERMINATION' | 'CONSUMER_FAILED' | null;
  stream_outcome_detail?: string | null;
  frames_at_detection_limit?: number;
  detection_limit?: number | null;
}

export interface CountHistorySample {
  camera_epoch: string;
  source_segment: number;
  bucket_index: number;
  source_time_s: number;
  frame_number: number | null;
  visible_count: number;
  visible_mean: number;
  visible_max: number;
  frames: number;
  replay_mode: string | null;
  received_at_utc: string;
  count_basis: string;
  /** Downsampled bins: buckets covered / buckets that had frames (1/1 when not downsampled). */
  bucket_span?: number;
  buckets_present?: number;
}

export interface CameraCountHistory {
  camera_id: string;
  run_id: string | null;
  camera_epoch: string | null;
  time_basis: string;
  receive_time_basis: string;
  sample_seconds: number;
  samples: CountHistorySample[];
  unavailable_reason: string | null;
  window?: 'recent' | 'range';
  total_samples?: number;
  returned_samples?: number;
  segments?: number;
  first_source_s?: number | null;
  last_source_s?: number | null;
  next_cursor?: string | null;
  downsample_factor?: number;
}

export interface Zone {
  id: string;
  floor_id: string;
  name: string;
  kind: ZoneKind;
  polygon: [number, number][];
  capacity: number | null;
  occupancy: number | null;
  congestion: Congestion;
  queue_size: number | null;
}

export interface FloorCamera {
  id: string;
  name: string;
  position: [number, number];
  coverage_polygon: [number, number][];
  health: Health;
  calibration_status: Calibration;
}

export interface Portal {
  id: string;
  name: string;
  position: [number, number];
  direction: 'UP' | 'DOWN' | 'BOTH';
}

export interface Floor {
  id: string;
  name: string;
  level: number;
  width: number;
  height: number;
  visible_occupancy: number | null;
  estimated_occupancy: number | null;
  quality: Quality;
  zones: Zone[];
  cameras: FloorCamera[];
  walls: [[number, number], [number, number]][];
  portals: Portal[];
}

export interface Alert {
  id: string;
  stable_key: string;
  severity: AlertSeverity;
  category: string;
  title: string;
  message: string;
  scope_type: string;
  scope_id: string | null;
  status: AlertStatus;
  triggered_at: string;
  acknowledged_at: string | null;
  measurements: Record<string, unknown>;
  rule_revision: string | null;
  evidence_id: string | null;
}

export interface SystemMetrics {
  timestamp: string;
  scope: 'windows-host' | 'wsl2' | 'container' | 'unavailable';
  gpu_name: string | null;
  gpu_utilization: number | null;
  vram_used_mib: number | null;
  vram_total_mib: number | null;
  gpu_temperature_c: number | null;
  nvdec_utilization: number | null;
  cpu_utilization: number | null;
  ram_used_mb: number | null;
  ram_total_mb: number | null;
  process_rss_mb: number | null;
  unavailable_reason: string | null;
}

export interface Kpis {
  building_expected: number | null;
  building_visible: number | null;
  building_estimated: number | null;
  unresolved_transitions: number | null;
  discrepancy: number | null;
  quality: Quality;
  entries: number | null;
  exits: number | null;
  queue_total: number | null;
  max_wait_seconds: number | null;
  stair_up_per_min: number | null;
  stair_down_per_min: number | null;
  congested_zones: number;
  healthy_cameras: number;
  enabled_cameras: number;
}

export interface AssociationRecord {
  id?: string | null;
  association_id?: string | null;
  track_id?: string | null;
  person_id?: string | null;
  from?: string | null;
  to?: string | null;
  from_camera_id?: string | null;
  to_camera_id?: string | null;
  source_camera_id?: string | null;
  target_camera_id?: string | null;
  from_zone_id?: string | null;
  to_zone_id?: string | null;
  timestamp?: string | null;
  occurred_at?: string | null;
  created_at?: string | null;
  confidence?: number | null;
  score?: number | null;
  status?: string | null;
  quality?: string | null;
  [key: string]: unknown;
}

export interface Notice {
  severity: AlertSeverity;
  title: string;
  message: string;
}

export interface Snapshot {
  schema_version: string;
  run_id: string | null;
  run_status: RunStatus;
  generated_at: string;
  sequence: number;
  watermark: number;
  cameras: Camera[];
  floors: Floor[];
  alerts: Alert[];
  kpis: Kpis;
  associations: AssociationRecord[];
  system_metrics: SystemMetrics;
  notices: Notice[];
}

export interface HealthLive {
  status: string;
  timestamp: string;
}

export interface ReadinessCheck {
  name: string;
  ready: boolean;
  detail: string;
}

export interface ReadinessResponse {
  status: string;
  checks: ReadinessCheck[];
}

export type EnvironmentResponse = Record<string, unknown>;
export type VersionsResponse = Record<string, unknown>;

export interface HistoryPoint {
  timestamp: string;
  value: number | null;
  quality: Quality;
  run_id: string | null;
}

export interface HistoryResponse {
  metric: string;
  points: HistoryPoint[];
  gaps: unknown[];
  next_cursor: string | null;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
  };
}

export interface MetricsEvent {
  type: 'metrics';
  data: SystemMetrics;
  sequence?: number;
  watermark?: number;
}

export interface SnapshotEvent {
  type: 'snapshot';
  data: Snapshot;
}

export type SnapshotDelta = Omit<Partial<Snapshot>, 'kpis'> & {
  kpis?: Partial<Kpis>;
};

export interface DeltaEvent {
  type: 'delta';
  data: SnapshotDelta;
  sequence?: number;
  watermark?: number;
}

export type LiveEvent = SnapshotEvent | MetricsEvent | DeltaEvent;
export type ConnectionState = 'connecting' | 'live' | 'reconnecting' | 'polling' | 'offline';
export type ViewId = 'overview' | 'cameras' | 'floors' | 'analytics' | 'alerts' | 'evidence' | 'system';

export interface EvidenceRecord {
  id: string;
  run_id: string | null;
  camera_id: string | null;
  camera_epoch: string | null;
  kind: string;
  media_type: string;
  label: string | null;
  trigger: string | null;
  group_id: string | null;
  note: string | null;
  timestamp_basis: string | null;
  source_marked_s: number | null;
  source_start_s: number | null;
  source_end_s: number | null;
  checksum: string | null;
  size_bytes: number | null;
  created_at: string;
  file_available: boolean;
  unavailable_reason: string | null;
  url: string;
  metadata: Record<string, unknown>;
}

export interface CapabilityEntry {
  available: boolean;
  reason: string | null;
}

export interface CapabilityMatrix {
  cameras: Record<string, {
    enabled: boolean;
    running: boolean;
    camera_local_counts: CapabilityEntry;
    image_line_crossings: CapabilityEntry;
    floor_positions: CapabilityEntry;
    facility_crossings: CapabilityEntry;
  }>;
  zones: Record<string, { kind: string; occupancy: CapabilityEntry; congestion: CapabilityEntry; queue?: CapabilityEntry }>;
  building_estimate: CapabilityEntry;
  stair_association: CapabilityEntry;
}

export interface ArtifactCheck {
  name: string;
  path: string;
  expected_sha256: string | null;
  current_sha256: string | null;
  status: 'MATCH' | 'MISMATCH' | 'MISSING_FILE' | 'NO_EXPECTED_IDENTITY';
}

export interface ArtifactsResponse {
  artifacts: ArtifactCheck[];
  capability: {
    cached: { available: boolean; validated_at_utc: string | null; probe_seconds: number | null } | null;
    last_invalidation: { reason: string; at_utc: string } | null;
  } | null;
}

export interface EvidenceList {
  items: EvidenceRecord[];
  usage: { records: number; bytes: number; max_records: number; max_bytes: number; max_age_days: number };
  capability: { available: boolean; unavailable_reason: string | null };
}

export interface EvidenceMarkerResult {
  group_id: string;
  snapshot: EvidenceRecord;
  clip: EvidenceRecord;
  alert_id: string;
}
export type DemoAction = 'start' | 'stop' | 'reset';
