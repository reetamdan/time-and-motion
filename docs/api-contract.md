# Local API contract

This describes the current implementation, not full PRD acceptance. All services bind to loopback by configuration. API prefix: `/api/v1`. Times are ISO-8601 UTC strings. Measurements that are not genuinely available are `null` and include an `unavailable_reason` where useful; never substitute realistic demo values.

The current real perception scope is one enabled file camera, `cam-01`, using the provisional TrafficCamNet + NvDCF smoke profile in the pinned DeepStream container. Real visible-person counts are model/tracker outputs, not labeled accuracy or facility occupancy. The other three configured cameras remain disabled.

## Common shapes

```ts
type Health = 'HEALTHY' | 'DEGRADED' | 'OFFLINE' | 'CALIBRATION WARNING'
type Calibration = 'VALID' | 'MISSING' | 'INVALID' | 'STALE'
type Quality = 'OBSERVED' | 'ESTIMATED' | 'UNAVAILABLE'
type AlertSeverity = 'INFO' | 'WARNING' | 'CRITICAL'
type AlertStatus = 'OPEN' | 'ACKNOWLEDGED' | 'RESOLVED'
type RunStatus = 'IDLE' | 'STARTING' | 'RUNNING' | 'STOPPED' | 'DEGRADED' | 'STOP_FAILED'  // STOP_FAILED: cleanup unconfirmed; retry stop
```

`Camera` (example is the configured idle state before an operator start):
```json
{
  "id":"cam-01","name":"Outdoor Pedestrian Area","floor_id":"floor-1","enabled":true,
  "health":"OFFLINE","calibration_status":"MISSING","calibration_revision":null,
  "source_type":"file","source_label":"cam01.mp4","epoch":null,
  "visible_count":null,"source_fps":null,"analytics_fps":null,
  "frame_age_ms":null,"frame_age_basis":null,"dropped_frames":0,
  "last_good_age_ms":null,"unavailable_reason":"Demo run not started; source-only preview is available",
  "preview_url":"/api/v1/cameras/cam-01/preview"
}
```

Camera field semantics:

- `epoch` is a string UUID, or `null`; it is not numeric. Track identity is scoped by `(camera_id, camera_epoch, local_track_id)`.
- `visible_count` is the number of distinct fresh, confirmed, valid tracked `person` keys in the latest accepted metadata frame. Non-person records and invalid/untracked records are excluded. It is not ground truth, a cumulative unique-person count, or facility occupancy.
- With fresh metadata and missing calibration, `health` remains `CALIBRATION WARNING`. After the configured 1,500 ms without a fresh batch, the snapshot reports `DEGRADED`, sets `visible_count`, `source_fps`, and `analytics_fps` to `null`, and uses `unavailable_reason: "No fresh perception metadata; last values are not current"`. `last_good_age_ms` continues to increase. EOS does not automatically change `run_status`.
- The Service Maker bridge currently leaves `frame_age_ms` null and supplies a `frame_age_basis` explaining that source PTS is preserved in internal metadata while host arrival uses monotonic time. `last_good_age_ms` is the available host-arrival age diagnostic.
- Disabled cameras remain in camera/floor responses with `enabled:false`, `health:"OFFLINE"`, and null epoch/counts; they do not start adapter sessions. After an operator start, their reason is `Camera disabled in configuration`; before start, source availability can instead determine the idle reason.

`Zone`: `{ "id", "floor_id", "name", "kind":"dining|queue|entrance|stairs|corridor|blind", "polygon":[[x,y]], "capacity":null|number, "occupancy":null|number, "congestion":"NORMAL|BUSY|CONGESTED|SEVERELY CONGESTED|UNAVAILABLE", "queue_size":null|number }`. Polygon coordinates use a 1000 × 600 floor-plan view box.

`Floor`: `{ "id":"floor-1", "name":"Floor 1", "level":1, "width":1000, "height":600, "visible_occupancy":null, "estimated_occupancy":null, "quality":"UNAVAILABLE", "zones":[Zone], "cameras":[{ "id", "name", "position":[x,y], "coverage_polygon":[[x,y]], "health", "calibration_status" }], "walls":[[[x,y],[x,y]]], "portals":[{ "id","name","position":[x,y],"direction":"UP|DOWN|BOTH" }] }`. Configured camera/zone/portal positions are illustrative floor-plan coordinates, not calibrated observation world positions.

`Alert`: `{ "id", "stable_key", "severity", "category", "title", "message", "scope_type", "scope_id":null|string, "status":AlertStatus, "triggered_at", "acknowledged_at":null|string, "measurements":{}, "rule_revision", "evidence_id":null|string }`.

`SystemMetrics`: `{ "timestamp", "scope":"windows-host|wsl2|container|unavailable", "gpu_name":null|string, "gpu_utilization":null|number, "vram_used_mib":null|number, "vram_total_mib":null|number, "gpu_temperature_c":null|number, "nvdec_utilization":null|number, "cpu_utilization":null|number, "ram_used_mb":null|number, "ram_total_mb":null|number, "process_rss_mb":null|number, "unavailable_reason":null|string }`.

`AssociationRecord`: `{ "id", "run_id", "portal_id":null|string, "source_track_key", "candidate_track_key":null|string, "temporary_global_track_id":null|string, "status":"UNASSOCIATED|CANDIDATE|CONFIRMED|UNKNOWN|EXPIRED|REVOKED", "score":null|number, "margin":null|number, "decision_reason", "candidates":[], "created_at", "updated_at", "supersedes_id":null|string }`.

`Notice`: `{ "severity":AlertSeverity, "title", "message" }`.

`Snapshot`:
```json
{
  "schema_version":"1.0","run_id":null,"run_status":RunStatus,
  "generated_at":"...","sequence":0,"watermark":0,
  "cameras":[Camera],"floors":[Floor],"alerts":[Alert],
  "kpis":{
    "building_expected":null,"building_visible":null,"building_estimated":null,
    "unresolved_transitions":null,"discrepancy":null,"quality":"UNAVAILABLE",
    "entries":null,"exits":null,"queue_total":null,"max_wait_seconds":null,
    "stair_up_per_min":null,"stair_down_per_min":null,
    "congested_zones":0,"healthy_cameras":0,"enabled_cameras":1
  },
  "associations":[AssociationRecord],
  "system_metrics":SystemMetrics,
  "notices":[Notice]
}
```

The current real adapter writes camera-local observation/count state, not calibrated facility analytics. It supplies no evidence for facility crossings, calibrated occupancy, queue/stair metrics, overlap fusion, or cross-camera associations; empty, null, or zero fields must not be interpreted as validated zero activity. Real pixel boxes and local track IDs exist in internal `Observation`/JSONL metadata but are not fields in `Camera`, `Snapshot`, or `AssociationRecord`.

## Camera time fields

`Camera` also carries:

- `replay_mode` (`paced` | `unpaced` | null).
- `analytics_fps_basis`: the meaning of `analytics_fps` (consumer throughput, not camera FPS or capacity).
- `source_position_s` and `source_segment`: current source/event time. These are null when stale, like the counts.
- `source_time_basis`.
- `source_discontinuities`: PTS discontinuities, each of which raises a WARNING alert and restarts time-based analytics.
- `backend_queue_ms`: in-backend buffering of the latest frame.
- `source_loop`, `source_loop_index` and `source_file_position_s`: whether the file source replays continuously (`loop: true` in `cameras.yaml`), the number of completed passes, and the latest frame's position on the file's own timeline (seconds from its first frame; null when stale or when a looping source's frame count is unknown).
- `persisted_count_samples` and `camera_line_crossings`: counters for this epoch.

`frame_age_ms` stays null for file replay, because there is no capture clock.

`stream_outcome` (`CLEAN_EOF` | `ABNORMAL_TERMINATION` | `CONSUMER_FAILED` | null) and `stream_outcome_detail` report how the source ended without an operator stop:

- `CLEAN_EOF`: every accepted frame was drained and the extractor exited 0. The final source-time history bucket is persisted at that moment.
- `ABNORMAL_TERMINATION`: a reader error, a non-zero exit, or EOF while the process is still running.
- `CONSUMER_FAILED`: the backend consumer raised.

Both abnormal outcomes raise a WARNING alert and leave the open bucket to the stop flush. The value is null while streaming, after a stop, or for a stalled source (a paused source is not EOF).

Live (RTSP) cameras additionally carry `reconnect_attempt` (attempts since the last frame; 0 when connected) and `next_reconnect_at` (UTC). For every camera, `evidence_available` and `evidence_unavailable_reason` say whether operator evidence can be extracted (file sources only; live sources have no recording buffer). `preview_url` is null for live sources. A live source's stream ending is always `ABNORMAL_TERMINATION` (a live camera has no complete end), followed by a reconnect with a new epoch.

`kpis.entries` and `kpis.exits` are null unless an enabled `floor_map` boundary belongs to a running camera with VALID calibration. Unmeasured crossings are unknown, not zero.

## REST

- `GET /health/live` → `{ "status":"ok", "timestamp" }`.
- `GET /health/ready` → HTTP 200 with `status:"ready"`, or 503 with `status:"not_ready"`; body is `{ "status", "checks":[{"name","ready","detail"}] }`. Current check names are `configuration`, `persistence`, `sources`, `models`, and `perception`. Only enabled sources gate the source check.
- `GET /api/v1/system/environment` → `{ "host", "gpu", "runtime", "sources", "models", "configuration_revision", "blockers" }` from actual probes. Runtime capabilities are keyed by `deepstream`, `gstreamer`, `nvstreammux`, `nvinfer`, and `nvv4l2decoder`; each reports `available`, `scope`, `detail`, and `unavailable_reason`. Capability scope may be `pinned-deepstream-container`. The model report includes `selected_model`, `selection_mode`, `evaluated_baseline_selected`, artifact availability, and tracker availability.
- `GET /api/v1/system/metrics` → `SystemMetrics`.
- `GET /api/v1/cameras` → `Camera[]`.
- `GET /api/v1/cameras/{id}/detections?after_frame=&after_epoch=&limit=90` → `{ camera_id, camera_epoch, replay_mode, loop, loop_index, frame_width, frame_height, latest_age_ms, box_format, frames[] }`, where each frame is `{ frame_number, source_time_s, file_position_s, loop_index, boxes[] }` and each box is `[x, y, width, height, track_id]` in pipeline frame pixels (the tracker box when available). The last ~150 frames of the current epoch are kept; `after_frame` returns only newer frames. Send `after_epoch` with the cursor: a different current epoch ignores `after_frame` and returns recent frames immediately. **Display only:** the dashboard draws these over the source preview (aligned by `file_position_s`); counts and history never come from them. It expires boxes after 1500 ms without fresh detections, even if snapshot transport remains healthy. Unknown IDs return 404; with no epoch, `frames` is empty.
- `GET /api/v1/cameras/{id}/preview` → the configured local source file with single-range HTTP support; there is no current MJPEG-producing route. Unknown IDs return 404, disabled or non-file sources return 409, missing/empty files return 404, and invalid/unsatisfiable ranges return 416. Before perception starts, source-only responses include `X-Preview-Mode: source-only`, `X-Analytics-Available: false`, and source-preview label headers. No placeholder is presented as live.
- `GET /api/v1/floors` → `Floor[]`.
- `GET /api/v1/zones` → `Zone[]` flattened from the floors.
- `GET /api/v1/facility/snapshot` → `Snapshot`.
- `GET /api/v1/analytics/history?metric=occupancy&from=&to=&limit=200&cursor=&run_id=` → `{ "metric", "points":[{"timestamp","value":null|number,"quality","run_id"}], "gaps":[], "next_cursor":null|string }`. `limit` is 1–1000; `cursor` is a non-negative integer offset; time bounds require timezones and `from < to`. Unsupported metrics return `INVALID_METRIC`.
- `GET /api/v1/alerts?status=&severity=&limit=100&offset=0&run_id=` → `Alert[]`; `limit` is 1–500.
- `POST /api/v1/alerts/{id}/acknowledge` → updated `Alert`; structured 404 if unknown.
- `GET /api/v1/associations?limit=100&offset=0&run_id=&status=` → `AssociationRecord[]`; `limit` is 1–500. The current smoke produces no cross-camera association records.
- `POST /api/v1/cameras/{id}/evidence-markers` with body `{"note"?: string (max 500), "source_time_s"?: number}` → 201 `{group_id, snapshot, clip, alert_id}`. It is a manual operator marker: an exact decoded frame at the source time, plus a stream-copied clip around it, plus an INFO `operator_marker` alert linking them. `source_time_s` defaults to the camera's current source position and must not exceed what the epoch has processed (422 `SOURCE_TIME_NOT_REACHED`) or precede its first frame (422). Other errors:
  - 409 `NO_ACTIVE_EPOCH` / `NO_SOURCE_TIME` / `SOURCE_TIMELINE_DISCONTINUOUS`;
  - 503 `EVIDENCE_TOOL_UNAVAILABLE` when ffmpeg/ffprobe are missing;
  - 503 `EVIDENCE_UNAVAILABLE` when extraction fails, or when the camera has no local source file: a live (RTSP) camera is always refused this way, with the reason "live (RTSP) sources have no recording buffer in this version" (the PRD scope is file replay for the first demo; live recording is not implemented).
- `GET /api/v1/evidence?camera_id=&run_id=&group=&limit=100&offset=0` → `{items: EvidenceRecord[], usage: {records, bytes, max_records, max_bytes, max_age_days}, capability: {available, unavailable_reason, ...}}`. An `EvidenceRecord` has these fields:
  - identity and scope: `id`, `run_id`, `camera_id`, `camera_epoch`, `kind`, `media_type`, `label`, `trigger`, `group_id`, `note`;
  - source timing: `timestamp_basis`, `source_marked_s`, `source_start_s`, `source_end_s` (the actual extracted bounds on the epoch's source timeline);
  - file: `checksum`, `size_bytes`, `created_at`, `file_available`, `unavailable_reason`, `url`;
  - `metadata`: requested bounds, `timeline_mapping`, `pipeline_frame_number`, source sha256, tool version, and detector provenance.
- `GET /api/v1/evidence/{id}/meta` → one `EvidenceRecord`.
- `GET /api/v1/evidence/{id}` → the file, with single-range support, confined to the configured evidence root. Unknown IDs return 404; paths outside the root return 403; a missing or empty file returns 404 `EVIDENCE_UNAVAILABLE`.
- `GET /api/v1/system/artifacts` → `{artifacts: [{name, path, expected_sha256, current_sha256, status}], capability: {cache_path, max_age_seconds, probe_count, liveness_count, liveness, cached, last_invalidation}, basis}`. `status` is `MATCH` only when a recorded expected hash exists and equals the recomputed one. `NO_EXPECTED_IDENTITY`, `MISMATCH`, and `MISSING_FILE` block perception.
- `POST /api/v1/system/capability/invalidate` → discards the cached container capability, so the next start re-probes. Returns the capability status, or 409 `CAPABILITY_CACHE_UNSUPPORTED` for adapters without a cache.
- `GET /api/v1/system/capabilities` → the capability matrix:
  - `cameras.{id}`: `{enabled, running, fresh, freshness_reason, camera_local_counts, image_line_crossings, floor_positions, facility_crossings}`;
  - `zones.{id}`: `{kind, occupancy, congestion, queue?}`, each `{available, reason, current, current_reason}`: `available` is structural (configuration + runtime geometry); `current` additionally requires every contributing camera to be fresh now (receive time within `health.stale_after_ms`, stream not ended, a frame in this epoch);
  - `floors.{id}.visible_in_zones`: `{available, reason, aggregation, fixture_status}` — the floor figure is a de-duplicated union of zone memberships;
  - `building_estimate`, and `stair_association` (`identity_continuity`, `flow_basis`).

  Each entry is `{available, reason}` at least. The reason is exact, for example `calibration MISSING`, `not running: cam-02`, or `calibration rev-1 was measured on 1920x1080 but frames are 1280x720 (resolution/crop/zoom changed)`.
- `GET /api/v1/runs/{run_id}/provenance` → what that run used:
  - configuration revision and file hashes, and the replay mode;
  - the adapter and its blockers;
  - `selection_mode`;
  - the detector (id, acceptance, revision, precision, input shape, class map, and profile: person class, pipeline/tracker frame, geometry policy, preprocessing, parser);
  - tracker revision and expected sha256, and the image digest;
  - `artifact_checks` (expected/current/status) and the capability result (`source`: probe | memory | disk);
  - per-camera calibration status.

  Unknown run → 404 `PROVENANCE_NOT_FOUND`.
- `GET /api/v1/versions` → `{ "schema_version", "api_version", "database_schema_version", "configuration_revision", "configurations", "selected_model", "model_acceptance", "default_replay_mode", "model_revision", "model_selection_mode", "evaluated_baseline_selected", "model_checksum", "engine_checksum", "tracker_revision", "calibration_revisions" }`. The current smoke reports `model_selection_mode:"provisional_smoke"` and `evaluated_baseline_selected:false`.
- `POST /api/v1/demo/start?replay_mode=paced|unpaced` → starts a run and enabled source supervisors, isolating source/adapter failures by camera; returns `Snapshot`. `replay_mode` defaults to `configs/runtime.yaml` `replay.mode`; any other value returns 422. A start during a healthy active run (`STARTING`, `RUNNING`, or `DEGRADED`) is idempotent: it returns 200 with the current snapshot and starts no new epoch. A run in `STOP_FAILED`, or any unreleased owned resource, returns **409** `{"error": {"code": "CLEANUP_UNCONFIRMED", "details": {"run_id", "run_status", "cameras", "retry": "POST /api/v1/demo/stop"}}}`, and nothing is launched. Enabled cameras start concurrently; one camera's failure does not delay or stop the others. File sources are consumed once unless the camera has `loop: true`.
- `POST /api/v1/demo/stop` → stops started per-epoch sessions, ends the active run, clears live camera epoch/count state, and returns a `STOPPED` snapshot retaining that run ID. A camera whose exact owned container (`aegis-<camera>-<epoch>`) cannot be confirmed removed after a bounded stop → verify → kill/rm escalation leaves the run in `STOP_FAILED`: the camera is `OFFLINE` with a `Stop not confirmed` reason and a CRITICAL `camera-cleanup-failed:<camera>` alert. Calling stop again retries cleanup and, on success, resolves the alert and reaches `STOPPED`. `reports/validation/live-application-cam01/result.json` covers an early operator stop; `reports/validation/live-eos-replay-cam01/result.json` additionally covers two stops, natural-EOS container removal, final adapter-session disposal, and exact container absence for its two epochs. These are scoped proofs, not every cleanup/failure path.
- `POST /api/v1/demo/reset` → stop and clear run-scoped analytics/alerts/history, keeping configs, models, calibration, and evidence files (evidence is bounded by retention). Returns an `IDLE` snapshot with `run_id:null`. Returns 409 `CLEANUP_UNCONFIRMED` when cleanup is still unconfirmed.
- Owned containers from this or a previous backend process are recorded durably in the `owned_resources` table. On startup, the backend reconciles unreleased rows before serving, and restores a `STOP_FAILED` run if release cannot be confirmed.
- `GET /api/v1/cameras/{id}/count-history?run_id=&epoch=&limit=2000&window=recent|range&from_source_s=&to_source_s=&after=<segment>:<bucket>&max_points=` → `{ camera_id, run_id, camera_epoch, time_basis, receive_time_basis, sample_seconds, samples[], unavailable_reason, window, total_samples, returned_samples, segments, first_source_s, last_source_s, next_cursor, downsample_factor }`.
  - `window=recent` (the default) returns the newest `limit` samples in chronological order, so long runs keep updating.
  - `window=range` pages forward from the `after` cursor and/or source-time bounds; `next_cursor` is set while more samples exist.
  - `max_points` (10–10000) merges consecutive buckets *within one source segment* into bins carrying `bucket_span` and `buckets_present`. The bin's mean is frame-weighted, its max is the bin maximum, and its count is the last bucket's. Missing buckets are never filled; clients draw a gap wherever `bucket_index` jumps by more than one span, and at each segment boundary. An invalid cursor returns 422 `INVALID_CURSOR`. Each sample is one `sample_seconds` bucket on the epoch's **source timeline**: `{ camera_epoch, source_segment, bucket_index, source_time_s, frame_number, visible_count, visible_mean, visible_max, frames, replay_mode, received_at_utc, count_basis }`. `source_time_s` is relative media time, not UTC capture time; `received_at_utc` is when the backend persisted the bucket. It defaults to the latest run and that camera's latest epoch. Unknown camera → 404. Samples are idempotent per (run, camera, epoch, segment, bucket).
- `GET /api/v1/cameras/{id}/line-crossings?run_id=&epoch=&limit=` → camera-local `image_pixels` line crossings `{ event_id, source_segment, line_id, coordinate_space, direction, track_key, source_time_s, received_at_utc }`. These never feed building entries/exits or occupancy. `cam-01` has no image line configured because its view pans (`reports/validation/cam01-camera-motion/result.json`), so `unavailable_reason` explains the empty list.

Camera snapshot fields added for multi-camera visibility: `frames_at_detection_limit` and `detection_limit`. These count frames whose person detections reached the profile's per-class top-k (counts in those frames are lower bounds); the first such frame per epoch also raises a WARNING `detection-limit:<camera>:<epoch>` alert.

Calibration-gated facility analytics write the following, and only while the relevant cameras run with runtime-valid calibration and fresh contributions:

- `occupancy_snapshots` (zone, floor, building), `queue_snapshots`, and `congestion_snapshots`. When a contribution expires (stale, stream ended, camera stopped) a superseding row with `visible: null, quality: UNAVAILABLE` and the reason is written, so the snapshot never keeps a stale figure; floors (`Floor.visible_occupancy`) are de-duplicated across overlapping zones;
- `associations` for stair/portal transitions (status `UNKNOWN` without re-identification evidence) and `flow_snapshots` (`inferred_count` arrivals, quality `ESTIMATED`; `observed_count` 0);
- `line_crossings` for `floor_map` boundaries, which feed `kpis.entries` and `kpis.exits`;
- `congestion:<zone>` alerts (opened on a persisted CONGESTED or SEVERE level, resolved on recovery after hysteresis);
- `geometry-invalid:<camera>:<epoch>:…` alerts.

Uncalibrated cameras produce none of these. Their values stay null, and the reason is reported by `/system/capabilities`.

Handled API errors use HTTP JSON `{ "error": { "code", "message", "details": {} } }`; request validation uses code `VALIDATION_ERROR`.

## Service Maker streaming transport

- The selected extractor is `perception/deepstream/service_maker/extract_metadata_v2.py` (frame schema `1.1` = `1.0` + `emitted_unix_ns`, the producer's wall clock; the backend accepts `1.0` and `1.1`). Paced file replay passes `--pace source --pace-lead-ms 200`, so the producer never runs more than 200 ms ahead of the source timeline; unpaced runs and live sources never pace. The frozen v1 extractor is unchanged and not selected. Three clocks stay separate: source PTS (event time), backend receive monotonic time (freshness), and wall/emission time (transport diagnostics only).
- The extractor writes each real frame record to its per-epoch JSONL file and emits that same JSON payload on stdout with the `AEGIS_FRAME_V1:` prefix. Real frames with empty object lists are retained; no synthetic frame, detection, track ID, PTS, or replay record is generated. Stderr and non-prefixed stdout are retained in the epoch log; malformed prefixed frames are logged and skipped rather than replaced.
- The backend validates the frame schema, camera, epoch, box format, integer frame/PTS fields, and object list before enqueueing. Its queue is bounded to 128 frames. While the session is active, the reader retries when the queue is full, applying backpressure instead of allowing unbounded buffering.
- On natural stdout EOF, the reader marks itself done and the consumer continues until the remaining queue is drained before completing. The live EOS report observed 710 consumed frames, an empty queue, a finished reader/consumer, and no malformed-frame or reader/consumer error. This complete post-EOF drain guarantee is scoped to natural completion, not forced cancellation.
- Per-epoch `.jsonl`, `.summary.json`, and `.log` artifacts remain in use alongside stdout streaming. Early stop can leave partial JSONL/log evidence without a final summary; a missing summary is not synthesized. In the replay evidence, epoch one has all three artifacts, while the intentionally early-stopped second epoch has JSONL/log evidence and no final summary.

## File-source EOS and replay

**Continuous replay (`loop: true`).** A file camera with `loop: true` runs the extractor with `--loop` (DeepStream `file_loop`). Measured on DeepStream 9.2: buffer PTS and `frame_number` keep increasing across each wrap (one ~0.1 s PTS step at the join, below the 2 s discontinuity threshold), so the epoch stays one source segment, history and analytics continue, and the stream never reports `CLEAN_EOF`; an operator stop ends it. The backend counts the file's frames once (ffprobe's header `nb_frames`, else a packet count; one packet is one frame for the H.264/H.265 streams the pipeline decodes, and decoding the whole file with `-count_frames` would delay the camera's start) and maps pipeline frame *n* to file frame *n* mod *frames*. Evidence requires an observed first frame and a requested time within that pass's consumed interval. Missing boundaries, gaps between passes, unknown frame counts and expired mappings return `SOURCE_LOOP_UNMAPPED`, 409, rather than falling back to a preceding pass. The per-epoch frame file is rotated to `<epoch>.jsonl.prev` at `runtime.yaml` `replay.frame_log_max_mib` (default 256 MiB); this cap is per epoch, not a total retention policy across restarts.

Without `loop` (the default), the Service Maker pipeline sets `live_source=False` and `file_loop=False` (`live_source=True` for `rtsp://`). Natural EOS ends metadata production but does not loop, synthesize frames, automatically stop the run, or start another epoch. Freshness is evaluated when snapshots are assembled: after the stale threshold, current count/FPS fields become null and camera health becomes `DEGRADED`. To consume the file again, stop the active run and then start a new one.

The earlier `reports/validation/live-application-cam01/result.json` remains an early-stop smoke. The newer `reports/validation/live-eos-replay-cam01/result.json` passes all 28 declared checks and now provides the live natural-EOS/restart evidence: a positive count before EOS, 710 frames in both the contiguous JSONL and drained backend consumer, stale API values/health/age with the run still active, a distinct replay epoch with a fresh positive count, operator stops, exact container/session cleanup, and completed TestClient shutdown. The second epoch is intentionally not a full-clip run.

## WebSocket

`WS /api/v1/live` sends:

- an initial `{ "type":"snapshot", "data":Snapshot }`;
- a full snapshot message when the sequence changes;
- `{ "type":"metrics", "data":SystemMetrics, "sequence", "watermark" }` at the telemetry interval;
- `{ "type":"heartbeat", "timestamp", "sequence", "watermark" }` about every 15 seconds.

The backend does not currently emit `delta` messages. The browser attempts bounded reconnects and falls back to REST snapshot polling every 10 seconds when the socket is unavailable.

## Frontend serving

In production, FastAPI serves `frontend/dist` at `/` when `index.html` is installed, including client-side route fallback; otherwise `/` returns JSON with `frontend_available:false`. In development, Vite proxies `/api` (including the `/api/v1/live` WebSocket), `/health`, and `/ws` to `127.0.0.1:8000`.

## Current client alignment

The previous frontend nullable-type drift is superseded: `frontend/src/types.ts` now declares both `Camera.epoch` and `Alert.scope_id` as `string | null`, matching the backend schemas. The camera component renders epoch with `String`.

## Host routes

The same API is served on both routes (`backend/app/perception/service_maker.py` `host_route()`):

- `windows-wsl` (validated default): `scripts/run-backend.ps1` on Windows; perception through `wsl.exe -d Ubuntu-22.04 -e docker ...` (`-e`: argv passed verbatim; `--` would re-parse the joined arguments in the login shell, so `$`, `;` or spaces in a resolved RTSP credential would be interpreted).
- `linux-docker`: `bash scripts/run-backend.sh` inside WSL (or native Linux); docker is called directly. Telemetry scope reports `wsl2` (or `linux-host`).

## Known contract and evidence gaps

- There is no public per-object REST/WebSocket schema for the real pixel boxes, source PTS, or local track IDs; only aggregate camera telemetry is exposed.
- Source-preview response headers are keyed to whether perception started, not whether metadata is still fresh. Live sources have no preview route.
- Live (RTSP) evidence clips need a recording ring buffer that this version does not implement; markers on live cameras are refused with that reason.
- Accuracy, calibrated physical analytics, and physical multi-camera behaviour remain unproven: see `docs/acceptance-matrix.md` for the evidence each row has.
