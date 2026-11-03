# Aegis Ops — local CCTV crowd intelligence

Local FastAPI/React operations workspace for an intended four-camera, two-floor CCTV crowd-intelligence demo, running entirely on this machine (GPU perception in a pinned DeepStream 9.2 container, SQLite persistence, a bundled dashboard; no cloud service, model key, or telemetry).

> **Status 2026-10-28, extractor `4ef4f94a…` (claims below are limited to completed runs).** Real local evidence under `reports/` covers the full single-camera workflow on `demo/cam01.mp4` (GPU decode, detection, tracking, source-time history, live dashboard, evidence, exact container cleanup, restart recovery), both host routes (Windows backend; backend inside WSL: `reports/validation/wsl-route-20261028T183618808723Z`), a controlled local RTSP republish with loss, backoff, stall and reconnect (`reports/validation/rtsp-controlled-20261028T183104407414Z`), fault isolation, paced replay held to real host time through WSL clock corrections, three timed runs of each detector profile, the UI at 390–1920 px, and controlled facility analytics. All of these except the detector timings were re-run on the current extractor after the pacing-clock fix (§8). **No completed steady-state capacity result or soak exists:** the final PRD A–D batch was paused by the operator after stage A r1 and r2 had passed (protocol gates and host-time pacing); earlier attempts were interrupted by the development host for low system memory. The benchmark launchers run on Linux (verified inside WSL); no native Linux / A6000 host was available.
>
> **Not established:** an evaluated detector baseline or any accuracy figure (no human-reviewed labels yet; the annotation package is ready), calibrated facility occupancy, real four-camera / overlap / stair / queue scenes (only one clip exists), and offline cold-start acceptance (not run, by instruction). Status per PRD criterion, with evidence scope: `docs/acceptance-matrix.md`.

## Quick start

**Windows route (validated default).** Prerequisites (one-time, §2): the pinned DeepStream image in WSL `Ubuntu-22.04`, a Windows `.venv` (or `.venv-windows`), a built `frontend/dist`, ffmpeg/ffprobe on the Windows `PATH` (evidence only).

```powershell
.\scripts\run-backend.ps1
```

**WSL route (backend inside WSL).** One-time: `bash scripts/setup/install-wsl-toolchain.sh`, then `source .install/wsl/env.sh && bash scripts/setup/setup.sh --dev --isolated-frontend`. Run from the project directory inside WSL:

```bash
bash scripts/run-backend.sh
```

Open **http://127.0.0.1:8000/** (WSL forwards loopback to Windows). Then:

1. Choose **Paced** (source-timeline replay) or **Unpaced** (as fast as the GPU allows) and click **Start demo**. The first start after a reboot probes the container runtime (about 15 s); later starts reuse the cached capability (about 5 s).
2. Select a camera tile for its inspector: live track count, source position, replay mode, epoch, rates, history (newest window or browse earlier/later source-time ranges), and **Mark evidence**.
3. **Stop** releases the camera containers. **Reset** clears run-scoped history (evidence files are kept and pruned by retention). If cleanup cannot be confirmed, a red **Stop not confirmed** banner with **Retry cleanup** stays visible and Start/Reset are refused (HTTP 409 `CLEANUP_UNCONFIRMED`).
4. Stop the server with **Ctrl+C**. If the backend is killed instead, the next start reconciles any container it left behind (§3).

### What the application does now

| Capability | State |
| --- | --- |
| Real perception | GPU decode → TrafficCamNet (provisional) → NvDCF, one tracker per camera epoch, through the pinned DeepStream 9.2 container. Candidates RT-DETR and RF-DETR are runnable profiles, unaccepted. |
| Sources | Local files (paced or unpaced replay) and live RTSP by configuration only (`source_type: rtsp`, credentials as `${VAR}` from the environment), with loss detection, capped-backoff reconnect and a stall watchdog. Validated with a controlled local RTSP republish of the demo clip. |
| Camera-local analytics | Visible track counts, source-time history (windowed, downsampled, honest gaps), and image-space line crossings when a fixed-camera line is configured. |
| Calibrated facility analytics | Floor positions, zone occupancy, de-duplicated floor/building figures, overlap fusion, queues, congestion with persistence/hysteresis, entries/exits, the occupancy ledger, stair transitions (identity `UNKNOWN` without re-ID evidence) and inferred stair flow. Enabled per camera only by a validated calibration file and only while its contribution is fresh. None exists yet, so the dashboard shows each as unavailable with its reason. Exercised with controlled inputs (`scripts/validate_controlled_facility.py`). |
| Health and lifecycle | Stale values are unavailable, never zero. Clean / abnormal / consumer-failure end states. `STOP_FAILED` is retried. Ownership survives restarts. Cameras start concurrently and fail independently. Paced replay keeps the producer within 200 ms of the source timeline, so a failure shows within about a second. |
| Evidence | Operator markers save the exact pipeline frame plus a stream-copied source clip (file sources); viewer, playback, provenance, missing-file and retention states in the dashboard. |
| Identity and provenance | Every runtime artifact: recorded vs recomputed hash. Per-run provenance records the detector, acceptance, pacing and the extractor identity. |
| Telemetry | Real GPU, VRAM, NVDEC, temperature, CPU and RAM readings in the dashboard and in benchmark reports. |
| Dashboard | Operations workspace on a design system adapted from OpenIntelligentUI (MIT, `frontend/src/design-system/NOTICE.md`): camera wall first, contextual inspector, facility metrics with reasons, alerts with filters and evidence links, evidence viewer, system/capability/artifact panels; dark and light themes; 390–1920 px; keyboard-operable. |

## Architecture

### Target architecture — not accepted end to end

```text
Authorized file / local RTSP
  -> source supervision, bounded queues, timestamps and health
  -> NVDEC -> GPU preprocessing -> TensorRT person detector
  -> one authoritative tracker per camera and camera-epoch local IDs
  -> crossing / occupancy / queue / congestion / health analytics
  -> calibrated projection and controlled overlap fusion
  -> topology/time/direction constrained temporary association
  -> local events, SQLite persistence and telemetry
  -> FastAPI (/api/v1, /health, WebSocket)
  -> React dashboard; production serves frontend/dist locally
```

### Currently validated path

```text
demo/cam01.mp4 (only enabled camera; duplicate-input feeds and a controlled RTSP republish in tests)
  -> artifact identity check (recorded vs recomputed sha256) + cached container capability
  -> pinned DeepStream 9.2 container per camera epoch (scripts/deepstream.sh, exact owned name)
     reached via wsl.exe (Windows route) or directly (WSL/Linux route)
  -> NVDEC + configured detector profile (default: TrafficCamNet FP16, provisional) + NvDCF
  -> extractor v2: AEGIS_FRAME_V1: stdout records (boxes, track IDs, source PTS, emit time),
     paced at the source for paced file replay
  -> bounded backend queue; person class normalized per profile
  -> camera-local analytics on source time; calibration- and freshness-gated facility analytics
  -> SQLite (history, alerts, provenance, evidence, ownership) -> REST/WebSocket -> dashboard
```

The browser renders state; it does not originate authoritative counts. Cross-camera hypotheses may remain `UNKNOWN`. Facial recognition, permanent biometric identity, demographic/emotion inference, hosted analytics, and hosted TypeSafe AI Jev calls are outside the design.

Current logical contracts are in `docs/api-contract.md`; requirements and acceptance gates are in `prd.md`. Decisions and their limits are in `docs/decisions.md`. The environment evidence and exact pass boundaries are in `docs/environment.md`; artifact identities and unresolved notice review are in `THIRD_PARTY.md`.

## 1. Prerequisites and inspection

The validated hardware route is Windows 11 -> WSL 2.6.3.0 -> the `Ubuntu-22.04` distribution running Ubuntu 22.04.5 LTS -> Docker -> a digest-pinned DeepStream 9.2 container. `scripts/deepstream.sh` mounts the project at `/workspace`, injects the WSL codec libraries, and refuses a missing local image rather than pulling it implicitly.

This does **not** establish DeepStream/CUDA/TensorRT compatibility in the native Ubuntu 22.04.5 userland. Older native-userland probes remain useful history. An earlier Windows `nvidia-smi` probe recorded `Failed to initialize NVML: Unknown Error`; that record is preserved. On 2026-10-27, Windows `nvidia-smi` returned valid readings, including the WSL container's GPU and NVDEC load, and the load ladder used it for telemetry.

Run read-only inspection first:

```powershell
# Windows PowerShell; saves raw host and WSL output.
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup/verify-host.ps1 -WslDistribution Ubuntu-22.04
```

```bash
# Run inside the exact WSL/native Linux runtime intended for perception.
bash scripts/setup/verify-environment.sh
```

Or invoke the runtime probe from Windows without opening a shell:

```powershell
wsl --distribution Ubuntu-22.04 --cd "$PWD" --exec bash scripts/setup/verify-environment.sh
```

Evidence is written to a new `reports/environment/<timestamp>-*/` directory. Required-probe failures return exit code `2` and are retained in `summary.json`, `summary.tsv` where available, and per-probe logs. These scripts do not install/change drivers, packages, SDKs, images, or models.

Do not blindly reinstall or downgrade the Windows driver to fix a host NVML error when WSL GPU access works. Record the failure, validate the supported WSL path, and resolve only a demonstrated compatibility problem.

## 2. Installation-time setup

Dependency downloads belong only in this phase. Runtime scripts set offline flags and never invoke `pip install`, `npm install`, `npm ci`, model download, engine build, or an image pull.

### Validated pinned DeepStream container

From the project root inside `Ubuntu-22.04`, perform the explicit installation-time image/dependency setup:

```bash
bash scripts/setup/pull-deepstream.sh
bash scripts/deepstream.sh bash /workspace/scripts/setup/install-service-maker-deps.sh
```

The pull script pins the linux/amd64 image to `ghcr.io/nvidia/deepstream@sha256:092818f8142d82c4611c9e96eb589a975cd928baa3e15a92f8f67b0ff6fcdb85`. The runtime wrapper uses `reports/environment/deepstream92/image-reference.txt`, requires that exact image to exist locally, mounts the project at `/workspace`, and does not pull a missing image. Service Maker's additional Python dependency is pinned in `perception/deepstream/service_maker/requirements.txt` and installed into the project-mounted `.deps` directory.

### WSL application environment (backend inside WSL)

The backend, its tests, and the frontend build can run inside the WSL distribution itself; perception then calls docker directly (host route `linux-docker`). The distribution's own Python is 3.10 (below the pinned packages' floor) and has no Node or ffmpeg, so the route uses a project-local, checksum-pinned toolchain and changes no system package. From the project root inside WSL:

```bash
bash scripts/setup/install-wsl-toolchain.sh          # uv 0.12.24 -> CPython 3.12.15, Node 24.14.1, static ffmpeg n9.0.2 in .install/wsl
source .install/wsl/env.sh
bash scripts/setup/setup.sh --dev --isolated-frontend # .venv-wsl (+ dev/test packages); frontend built in ~/.cache/aegis-ops/frontend-build
.venv-wsl/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/aegis-pytest tests perception/candidates
```

`--isolated-frontend` builds from a copy and copies only `dist/` back: a Linux `npm ci` in the shared tree would replace the Windows `node_modules` (platform-specific build binaries). `scripts/run-backend.sh` sources `.install/wsl/env.sh` itself, so ffmpeg for evidence is found. The full route is executed end to end by `scripts/validate_wsl_route.py` (setup, tests, config validation, real GPU run, evidence, stop, restart persistence, documented reset, cleanup).

Useful explicit variants:

```bash
bash scripts/setup/setup.sh --skip-frontend       # Python only
bash scripts/setup/setup.sh --skip-python         # Frontend only
bash scripts/setup/setup.sh --skip-build          # Dependencies without production build
bash scripts/setup/setup.sh --offline --wheelhouse .install/wheels
```

Offline installation requires a populated local wheelhouse, a frontend lockfile, and an already populated npm cache. It does not supply missing artifacts.

### Windows API/frontend development environment

Use a **separate** Windows venv; do not mix a Windows venv with WSL:

```powershell
py -3.12 -m venv .venv-windows
.\.venv-windows\Scripts\python.exe -m pip install -r .\backend\requirements.txt
Set-Location frontend
npm ci
npm run build
Set-Location ..
```

`run-backend.ps1` looks for `.venv-windows` first, then an existing Windows `.venv`, and drives perception through `wsl.exe` (host route `windows-wsl`, the validated default).

### Authorized footage installation

Only use footage you are authorized to record, process, and retain. `configs/cameras.yaml` currently has four slots but enables only one:

| Camera | Configured source | Current state |
| --- | --- | --- |
| `cam-01` | `./demo/cam01.mp4` | Enabled; the only source used by current validation |
| `cam-02` | `./data/demo/cam02.mp4` | Disabled; no current clip/result claimed |
| `cam-03` | `./data/demo/cam03.mp4` | Disabled; no current clip/overlap result claimed |
| `cam-04` | `./data/demo/cam04.mp4` | Disabled; no current clip/stair-transition result claimed |

The current clip's SHA-256 is `4201f7162eb0b9bcd3910f87b1a67a5bedac89c2b0a0bb4222e67d12f816b503`. Its technical and provenance records are under `reports/media/cam01/`. The recorded authorization statement is user-supplied, not independently verified, and grants no redistribution rights. No ground-truth labels exist.

For strict frozen preflight, create a `SHA256SUMS` sidecar beside each enabled source. For the current source, the standard entry in `demo/SHA256SUMS` would be:

```text
4201f7162eb0b9bcd3910f87b1a67a5bedac89c2b0a0bb4222e67d12f816b503  cam01.mp4
```

After reviewing the actual file and generated value, it can be created from `demo/` with:

```bash
sha256sum cam01.mp4 > SHA256SUMS
```

`demo/SHA256SUMS` now exists with this entry (added 2026-10-28); runtime passes recorded before it was added must not be described as strict frozen-preflight passes. Future enabled clips need their own provenance, authorization, synchronization notes, references where evaluation is claimed, and sidecars. Unrelated clips cannot prove overlap deduplication or stair continuity. This repository versions the current clip and its derived files in Git LFS by the project owner's decision; review provenance and authorization before adding any further media.

Reference fixtures belong under `tests/fixtures/`; calibration artifacts belong under `data/calibration/`; model checkpoints/engines belong under `models/`. The offline checker reads `SHA256SUMS` sidecars so unsupported fields need not be added to the strict camera YAML schema.

### Models and TensorRT engines

`configs/models.yaml` selects `trafficcamnet-smoke` with `selection_mode: provisional_smoke`. This is a real local model and engine used for plumbing validation, **not** an evaluated PRD baseline.

Every detector entry carries a `runtime_profile`. The profile defines:

- the infer config and its sha256;
- the parser (built-in, or a custom `.so` plus its sha256 and function);
- **the detector's own person class**: TrafficCamNet person is class `2`; the RT-DETR and RF-DETR candidate parsers emit person as class `0`. Other classes are counted and reported, never relabelled;
- the pipeline and tracker frame sizes;
- the source-geometry policy. `stretch_disclosed` rescales and records any aspect change. `require_exact` refuses any other source size; RF-DETR's full-frame graph needs exactly 1920×1080;
- the per-class top-k. Frames that reach it are counted and raise a "may be truncated" alert.

`acceptance` is `provisional_smoke`, `candidate_unaccepted`, or `accepted`. The loader refuses a `selection_mode` that does not match the selected model's acceptance, so an unaccepted candidate can never be presented as the evaluated baseline.

**Run a candidate for evaluation** (it stays labelled unaccepted everywhere):

```powershell
Copy-Item -Recurse configs .install\candidate-configs
# edit .install\candidate-configs\models.yaml: selected_model: rf-detr-person-candidate
#                                               selection_mode: candidate_evaluation
$env:CCTV_CONFIG_DIR = "$PWD\.install\candidate-configs"; .\scripts\run-backend.ps1
```

`GET /api/v1/system/artifacts` and the System view show, for each artifact, the recorded `expected_sha256`, the recomputed `current_sha256`, and `MATCH`, `MISMATCH`, `MISSING_FILE`, or `NO_EXPECTED_IDENTITY`. Any status other than `MATCH` blocks perception. Changing an artifact means recording its new hash and rerunning the real-output validation. Artifact values are registered in `THIRD_PARTY.md`.

For explicit installation-time downloads, prepare a JSON manifest matching the contract printed by:

```bash
bash scripts/setup/download-models.sh --help
bash scripts/setup/download-models.sh --manifest .install/model-artifacts.json
```

Every download requires an immutable revision, public HTTPS URL without embedded credentials/query strings, a destination below `models/`, expected SHA-256, license, and provenance. Existing mismatched files are never overwritten. A verified receipt is not an inference result. Record the final artifacts in `THIRD_PARTY.md`.

Build each engine on the actual GPU/runtime. The build wrapper requires a separate known-positive real-output validator, so a successful `trtexec` exit alone cannot be labeled working detection:

```bash
bash scripts/setup/build-engines.sh --help
```

Supply the actual ONNX, new engine path, executable validator, and release/model-matched `trtexec` precision/shape/type options. RT-DETR on Blackwell requires the applicable strongly-typed policy; verify real boxes and metadata on known-positive frames. An engine build does not prove NVDEC, tracking, analytics, or capacity.

### Configuration and environment

Environment overrides:

| Variable | Overrides | Typical use |
| --- | --- | --- |
| `CCTV_CONFIG_DIR` | the `configs/` directory | a candidate or load-test configuration |
| `CCTV_DATABASE_PATH` | `runtime.yaml` `database_path` | a validation run's own SQLite file |
| `CCTV_EVIDENCE_ROOT` | `evidence_root` | where evidence files are written |
| `CCTV_CAPABILITY_CACHE` | `capability.cache_path` | where the cached capability probe is stored |

The database records the exact ownership of every launched camera container (`owned_resources`). On startup the backend reconciles unreleased rows before allowing a new run (see §6).

Check a configuration directory before using it:

```powershell
.\.venv\Scripts\python.exe scripts\config\validate_config.py configs
```

It runs the same strict loader as the backend, covering:

- cross-references and geometry bounds;
- calibration files, revisions, and held-out validation;
- model and tracker identities, and the selection rules.

It then prints the capability matrix: which analytics each camera and zone could support, and the exact reason for each that could not.

### Calibration and facility geometry

Pixel coordinates and floor-map coordinates are never mixed. A camera's calibration is a file `configs/calibration/<camera>-<revision>.json` containing:

- an image-to-floor homography;
- the `image_size` it was measured on;
- held-out validation points and the maximum allowed error.

Create one from measured correspondences (at least 4 fit points and at least 2 held-out points, all measured on site):

```powershell
.\.venv\Scripts\python.exe scripts\config\fit_calibration.py measurements.json
```

Then set the printed `calibration_ref`, `calibration_revision`, and `calibration_status` in `cameras.yaml`. The loader refuses `VALID` without a matching file whose validation passes.

At runtime, the first frame's size is compared with the calibration's `image_size` and with each image line's `frame_size`. On a mismatch (resolution, crop, or zoom change), that geometry is disabled for the epoch and a `geometry-invalid` alert is raised; camera-local counts continue.

Metric fusion and density also need:

- `meters_per_unit` on a surveyed floor;
- cameras that share a `replay_group` (a common time base).

`GET /api/v1/system/capabilities` (also shown in the System view) reports what is available now and why anything is not.

For the current moving `cam01` clip, no physical boundary is configured. A fixed pixel line there would count camera motion, so crossings stay disabled.

The seven authoritative YAML files are under `configs/`:

- `cameras.yaml`, `floors.yaml`, `zones.yaml`, `portals.yaml`
- `analytics.yaml`, `models.yaml`, `runtime.yaml`

`.env.example` documents safe orchestration defaults and contains no secrets. The scripts do **not** automatically source `.env`. If you copy it to a trusted local `.env`, load it explicitly in Bash only after reviewing it:

```bash
set -a
source ./.env
set +a
```

The YAML files remain the application's facility/runtime authority unless the application explicitly supports an environment override. Never put real RTSP passwords or tokens in `.env.example`, committed YAML, logs, or screenshots.

## 3. Run and stop

### Current single-camera validation entry points

After the pinned container setup, the current evidence generators/validators are invoked as follows from the project root in WSL:

```bash
# Hardware decode.
bash scripts/deepstream.sh bash /workspace/scripts/validate-nvdec.sh

# Detector/tracker run; preserve its stdout/stderr as the run.log expected by the validator.
bash scripts/deepstream.sh deepstream-app -c /workspace/perception/deepstream/cam01_trafficcamnet_smoke.txt
python scripts/validate_deepstream_smoke.py

# Service Maker JSONL extraction and validation.
bash scripts/deepstream.sh python3 /workspace/perception/deepstream/service_maker/extract_metadata.py \
  --camera-epoch smoke-manual
python scripts/validate_service_maker.py
```

`validate_deepstream_smoke.py` requires OpenCV and NumPy. These entry points write to fixed report paths, so preserve existing evidence before an intentional rerun. The DeepStream validator expects `reports/validation/deepstream-cam01/run.log`; see `docs/environment.md` for output scopes and warnings.

The backend and live API integration entry points run from Windows with the current backend environment:

```powershell
.\.venv\Scripts\python.exe scripts\validate_backend_bridge.py
.\.venv\Scripts\python.exe scripts\validate_live_application.py
.\.venv\Scripts\python.exe scripts\validate_live_eos.py
.\.venv\Scripts\python.exe scripts\validate_live_workflow.py
```

The live validators (`validate_live_application.py`, `validate_live_eos.py`, `validate_live_workflow.py`) always write to a **new** directory: `reports/validation/<name>-<UTC timestamp>/` by default, or the path given with `--output-dir` or the validator's `AEGIS_LIVE_*_RUN_DIR` variable. An existing directory is refused, never cleared, so earlier evidence cannot be overwritten. Each `result.json` records the exact command and run directory.

`validate_live_application.py` exercises `POST /api/v1/demo/start`, snapshot/environment reads, and `POST /api/v1/demo/stop` through FastAPI `TestClient`, including real supervisor/container work. It stops after the first fresh positive visible count; it is not a production-server, browser, full-clip, or soak test.

`validate_live_eos.py` is `PASS` with 28/28 checks in `reports/validation/live-eos-replay-cam01/result.json`. Through the same in-process API and real supervisor/WSL/Docker path, it verifies a positive tracker-derived count before natural EOS, 710 backend frames drained, contiguous JSONL frames 0–709, stale values becoming null with `DEGRADED` health while the run remains active, a fresh second-epoch replay, and exact epoch-container cleanup. The first epoch reaches natural full-clip EOS; the second is intentionally stopped after a fresh positive replay count. That original run recorded a 22.719 s second stop. After the stop fixes (`--init`, stdout drain, verified ownership-aware cleanup) the rerun in `reports/validation/live-eos-replay-cam01-stopfix-timerate-20261026T165548Z/` passes 28/28 with a 0.813 s second stop (`reports/validation/stop-latency/result.json`).

`validate_live_workflow.py` is the end-to-end single-camera workflow check. It covers:

- a paced run and an unpaced run of the real clip;
- live counts over REST and WebSocket;
- source-time history persisted during the run, including the final bucket at a clean end of file;
- staleness after EOS, exact container cleanup, and restart recovery;
- recorded real detections delivered through the real consumer at both speeds.

It is structural evidence, not detector or count accuracy. The latest run is listed in `docs/acceptance-matrix.md`, along with the PRD status of every criterion.

Further real-hardware and browser checks (each writes a new evidence directory):

```powershell
.\.venv\Scripts\python.exe scripts\validate_startup_and_evidence.py
.\.install\browser-tests\venv\Scripts\python.exe scripts\validate_browser_workflow.py
.\.install\browser-tests\venv\Scripts\python.exe scripts\validate_browser_evidence.py
.\.venv\Scripts\python.exe scripts\validate_artifact_manifest.py
.\.venv\Scripts\python.exe scripts\validate_documented_commands.py
```

They check, in order:

1. Cold, warm, and restart start latency; artifact identity; per-run provenance; and live evidence whose snapshot is verified against an independent decode of the pipeline frame.
2. The dashboard workflow in headless Chrome, plus a TEST-ONLY `STOP_FAILED` fault backend.
3. Evidence, capabilities, and artifact identity in the browser, including the playback and missing-file states.
4. Locks and recorded-vs-recomputed artifact identities (AC-24).
5. The documented setup, run, reset, and test commands (AC-25).

### Replay modes and time domains

- `configs/runtime.yaml` `replay.mode`, or `POST /api/v1/demo/start?replay_mode=`, selects one of two modes:
  - `paced` (default, demo): frames are released on the clip's PTS timeline, about 1.0 source s per wall s.
  - `unpaced` (evaluation/benchmark): maximum throughput, about 12× on this laptop.
- Analytics run on **source time** (PTS seconds within a camera epoch), so results do not depend on replay speed. A PTS discontinuity starts a new segment and resets time-dependent analytics.
- **Receive time** (monotonic, plus UTC for display) drives only freshness and transport diagnostics. No UTC capture time is invented for file sources.
- `analytics_fps` is consumer throughput (frames per second of backend time). It is not a live-camera frame rate or a capacity measurement.
- A stop whose container removal cannot be confirmed leaves the run in `STOP_FAILED`, with an alert. `POST /api/v1/demo/start` then returns **409 `CLEANUP_UNCONFIRMED`**, and so does `reset`. Retry with `POST /api/v1/demo/stop`. A start during a healthy active run is idempotent: it returns 200 and the same run, with no new epoch.
- **Start latency and the capability cache.** Each start checks artifact identities (hashes are memoized by file size and mtime) and a cheap liveness check (`docker image inspect` of the pinned image, about 0.5 s). The expensive check is a single container that probes deepstream-app, the NVIDIA GStreamer elements, the extractor, and the GPU (3–6 s). It is reused across starts, concurrent readers, and backend restarts (`capability.cache_path`).

  The cached result is discarded when:
  - the image ID changes, or the current wrapper, probe, or extractor hash changes;
  - it is older than `max_age_seconds`;
  - liveness fails;
  - a container start fails;
  - `POST /api/v1/system/capability/invalidate` is called.

  Measured: about 16 s cold, about 5 s warm (container launch, engine load, and first frame remain).
- **Multiple cameras** start concurrently and fail independently. Each has its own epoch, bounded queue, owned container, and cleanup.
- **Clean end of a file source.** When the clip ends cleanly (every accepted frame drained, extractor exit 0), the final history bucket is persisted at once and the camera reports `stream_outcome: CLEAN_EOF`. The run stays active and the values go stale (null, not zero) until an operator stop. A later stop does not duplicate the bucket. A non-zero exit, a reader error or a consumer failure is reported as `ABNORMAL_TERMINATION` or `CONSUMER_FAILED`, with an alert.
- **Ownership and restart recovery.** Before any container is launched, the backend records its exact identity (`aegis-<camera>-<epoch>`, `docker@wsl:<distro>`) in the database. A start that fails after launch is cleaned up through the normal stop path. On startup the backend reconciles every unreleased container from a previous process. A run is marked `STOPPED` only after release is confirmed; otherwise it is restored as `STOP_FAILED` and new starts are refused (HTTP 409) until a stop retry succeeds. Only exact recorded names are touched, never an `aegis-*` prefix sweep.
- Browser-level check (real socket + headless Chrome, plus a TEST-ONLY fault backend): `.\.install\browser-tests\venv\Scripts\python.exe scripts\validate_browser_workflow.py` (needs `pip install playwright==1.55.0` in that venv and an installed Google Chrome).

### Windows application process with the pinned-container bridge

Two routes are validated: this **Windows backend** driving docker in WSL (`wsl.exe -d Ubuntu-22.04 -e`), and the WSL route below. The Windows API-process entry point is:

```powershell
.\scripts\run-backend.ps1
```

It uses `.venv-windows` first, then the existing Windows `.venv`. With the production runtime config, FastAPI can serve `frontend/dist` at `http://127.0.0.1:8000/`; starting the process still does not prove detection or acceptance. Use the API start/stop endpoints above, then **Ctrl+C** to stop the API process. The current `live-application-cam01` and `live-eos-replay-cam01` reports used `TestClient`, not this external Uvicorn socket path.

### WSL route: backend inside WSL

After the one-time WSL setup (§2), from the project directory inside WSL:

```bash
bash scripts/run-backend.sh                 # API + dashboard on http://127.0.0.1:8000/
bash scripts/run-demo.sh --strict           # full demo entry point: strict frozen preflight, then backend + readiness
```

The backend calls docker directly (host route `linux-docker`); everything else (configuration, database, evidence, API, dashboard) is identical to the Windows route. `run-demo.sh --strict` first runs `scripts/offline-test/run.sh --strict` (pinned source checksums in `demo/SHA256SUMS`, model/engine hashes, config schemas, local frontend build), writes logs and preflight evidence to `reports/demo/<run_id>/`, and stops both processes on Ctrl+C. Readiness and a strict static preflight are not detection, capacity, or offline-isolation evidence.

The PowerShell launcher `.\scripts\run-demo.ps1 -Distribution Ubuntu-22.04` delegates to the same WSL route.

### Development mode

For the current Windows workspace, the root launcher starts the Windows backend and Vite frontend and forwards Vite host/port arguments:

```powershell
npm run dev
# UI: http://127.0.0.1:7100/ ; API: http://127.0.0.1:8000/
# Custom address: npm run dev -- --host 127.0.0.1 --port 7100
```

It uses `.venv` on Windows and `.venv-wsl` on Linux, or an explicit `CCTV_PYTHON`. Press **Ctrl+C** to stop both processes. Both routes reach the same Service Maker bridge.

Generic separate-process entry points remain:

```bash
bash scripts/run-backend.sh
bash scripts/run-frontend.sh
# bash scripts/run-demo.sh --dev uses the configured development frontend port (default 5173)
```

```powershell
.\scripts\run-backend.ps1
.\scripts\run-frontend.ps1
```

For API/UI work before models/footage are ready, `bash scripts/run-demo.sh --skip-preflight` is an explicit unvalidated developer run. It must not be used as demo acceptance evidence.

Services default to loopback. A non-loopback bind requires deliberate authentication, TLS/firewall, credential redaction, and evidence-access review; changing the host is not sufficient security configuration.

## 4. Replay, tests, and benchmark

Run the application-owned checks after installation. Windows (use the venv `run-backend.ps1` selects):

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests perception/candidates
npm --prefix frontend test
npm run build
```

If pytest cannot write its default temporary directory (as in some sandboxed shells), add `-p no:cacheprovider --basetemp .install\pytest-tmp`.

WSL, only after creating `.venv-wsl` as in §2 "WSL API/frontend project environment":

```bash
.venv-wsl/bin/python -m pytest -q -p no:cacheprovider --basetemp /tmp/aegis-pytest tests perception/candidates
(cd ~/.cache/aegis-ops/frontend-build && npm test)   # the isolated copy from setup.sh --isolated-frontend; never npm ci in the shared frontend/ from WSL
```

`scripts/validate_documented_commands.py` executes the documented setup, run, reset and test commands and records the results in a new `reports/validation/documented-commands-<UTC>/` directory.

Use the actual test paths/options supplied by the application; their pass status is separate from GPU/video acceptance.

### Replay/regression contract

```bash
bash scripts/replay.sh --profile PATH --reference PATH --strict -- LOCAL_RUNNER [ARGS...]
```

The wrapper validates profile/reference files and local media, hashes inputs, and invokes only the explicit local runner. The runner receives `CCTV_REPLAY_*` paths/hashes/output variables and must write `result.json` with matching provenance, `status`, `metrics`, and `evidence`. Omitting the runner, references, media, or result is a blocker/failure, never a synthetic pass. Use `--dry-run` for asset validation only.

### Duplicate-input load ladder (engineering)

```powershell
.\.venv\Scripts\python.exe scripts\benchmark\run_duplicate_load.py            # unpaced + paced, 1,2,4,6,8 feeds
.\.venv\Scripts\python.exe scripts\benchmark\report_duplicate_load.py reports\benchmarks\duplicate-load-ladder-<stamp>
```

Every feed is `demo/cam01.mp4` under a distinct camera ID, labelled as a duplicate in the generated configuration. Each step is one full pass through the full application at stage D (decode, detection, tracking, camera-local analytics, persistence, API). It records:

- start and stop times;
- per-feed consumer fps, paced lag, and queue depth;
- frames persisted, drops, and top-k truncation;
- GPU, NVDEC, VRAM, temperature, power, CPU, and RAM;
- owned-container cleanup.

The ladder stops at the first step that breaks the predeclared limits. This is load data only, not multi-camera accuracy or a steady-state capacity result.

### Steady-state capacity and soak (PRD 19)

```powershell
.\.venv\Scripts\python.exe scripts\run_supervised_batch.py --batch scripts\batches\capacity.json --label capacity
# steps: run_capacity.py --plan repeats (A-D x3 @4 feeds), --plan soak (D @4, 30 min), --plan ladder (D 1/2/4/6/8)
```

Protocol, gates, resource guard and exit codes: `docs/benchmark-methodology.md`. Run one GPU workload at a time, with about 6 GiB of host memory free. The supervisor stops the batch, and lets the running step clean up, if memory runs low or its wrapper is killed. Each run writes `reports/benchmarks/capacity-<plan>-<UTC>/` with `summary.json` and `report.md`, or `INTERRUPTED.json` when stopped.

### Labelled evaluation

The annotation package `data/annotations/cam01-v1/` and its instructions are in `docs/annotation-guide.md`. `scripts/evaluation/evaluate_detections.py` scores the three detectors against human-REVIEWED labels. It refuses unreviewed files, and files identical to detector proposals.

### Benchmark contract

```bash
bash scripts/benchmark/run.sh \
  --stage G --streams 4 --profile PATH --warmup-seconds 120 --duration-seconds 600 \
  -- LOCAL_RUNNER [ARGS...]
```

Use the A–H workload ladder and 1/2/4/6/8 stream progression. Freeze sources, configs, model/engine, calibration, thresholds, pacing, and power context before measurement. The runner must produce real raw metrics plus `summary.json` and `report.md` in `CCTV_BENCHMARK_OUTPUT`.

`scripts/benchmark/summary.schema.json` defines the evidence-linked structure; `scripts/benchmark/report.template.md` contains the required report fields. The wrapper checks structure/provenance, not the truth of runner metrics, and never creates performance numbers itself. Shorter engineering runs and unpaced throughput runs must be labeled distinctly. No 20-stream inference is permitted from laptop results.

## 5. Offline and cold-start procedure

`HF_HUB_OFFLINE=1` and similar flags only disable supporting libraries' hub requests; they do not provide missing files or prove network isolation.

1. During installation, pin Python/frontend/native dependencies, download all models, build/validate engines on this GPU, build `frontend/dist`, and install authorized footage, references, calibration, and checksums.
2. Inspect and retain any container image by digest. Runtime container use must disable pulls; `verify-environment.sh --container-image IMAGE@DIGEST` uses `--pull=never` for its explicit probe.
3. Stop the app and reboot. Start the selected WSL runtime fresh; a warm process with cached remote connections is not a cold-start test.
4. As an operator, disable outbound Internet while preserving loopback and any explicitly required local camera/WSL networking. Record the exact firewall/network method, start time, and independent observation method.
5. Run strict static preflight:

   ```bash
   bash scripts/offline-test/run.sh --strict
   ```

   To record an operator attestation without pretending the script verified it:

   ```bash
   bash scripts/offline-test/run.sh --strict \
     --operator-isolation-confirmed \
     --isolation-method "DESCRIPTION OF FIREWALL/RULES AND INDEPENDENT OBSERVATION" \
     --require-isolation-attestation
   ```

6. Start with `bash scripts/run-demo.sh --strict`. Verify actual GPU execution, hardware decode, four sources, nonempty boxes/metadata, local tracker IDs, analytics, two-floor state, overlap/transition controls, local history/evidence/alerts, and real telemetry.
7. Observe logs/network using the operator's method. Record unsupported counters and every failed feature rather than removing it from the report.
8. Run the frozen soak profile (30 minutes for final full-demo soak) and retain logs, metrics, screenshots/recording, config/model/calibration revisions, boot/start times, and external-dependency findings under `reports/offline/<run_id>/`.

`scripts/offline-test/run.sh` performs **static preflight only**. Even a `PASS` line means only that its static checks passed. Its report always leaves offline acceptance undetermined until cold-start/runtime/operator evidence exists.

## 6. Reset and retention

Reset only an explicit active run:

```bash
bash scripts/reset-demo.sh --run-id ACTIVE_RUN_ID
```

```powershell
.\scripts\reset-demo.ps1 -RunId ACTIVE_RUN_ID
```

The script reads `/api/v1/facility/snapshot`, refuses an idle/mismatched target, then calls `POST /api/v1/demo/reset`. It never deletes files directly. The endpoint contract is to stop and clear run-scoped analytics/alerts/history while preserving configs, source footage, models, and calibration.

### Evidence storage and retention

Evidence files live under `runtime.evidence_root` (default `backend/var/evidence/<run>/<camera>/<marker>/`).

- **Create a marker.** Use **Mark evidence** in the camera drawer, or `POST /api/v1/cameras/{id}/evidence-markers` with an optional `{"note": "...", "source_time_s": 12.3}`. The source time must already have been processed by that epoch.
- **What is saved:**
  - the decoded frame at that source time, which is the pipeline's own frame. Pipeline PTS and file PTS can differ by the B-frame delay, so the two timelines are aligned on the epoch's first frame;
  - a stream-copied clip (`clip_pre_seconds` and `clip_post_seconds`), with no re-encode. The clip starts on the keyframe at or before the requested start; both the requested and the actual bounds are recorded;
  - an INFO operator-marker alert that links them.
- **Labelling.** Markers are labelled as manual operator records. No congestion or crossing alert is invented to produce evidence.
- **Access.** `GET /api/v1/evidence` lists records, flagging any whose file is missing. `GET /api/v1/evidence/{id}` streams a file and supports byte ranges. The **Evidence** view previews images and plays clips. Paths are confined to the evidence root.
- **Retention.** At startup and after each capture, the oldest records are removed when any limit is exceeded: `analytics.retention.evidence_days`, `runtime.evidence.max_records`, or `retention.max_evidence_mib`. Files are deleted only inside the evidence root.
- **Reset.** Reset keeps evidence (it is not run-scoped history); retention bounds it.

Retention for track samples, events, metrics, and logs must likewise stay bounded in configuration. A reset is not a substitute for retention policy or legal/privacy deletion requirements.

## 7. Calibration, RTSP, and privacy warnings

- The two floors have independent coordinate systems. Illustrative view-box geometry is not surveyed metric calibration.
- Version calibration and validate reprojection/world-position error. Camera movement, zoom, crop, or resolution changes invalidate/suspect calibration and must gate world/fusion metrics.
- Do not report people/m² without valid area. Do not place a camera-local person on a floor map when projection is unavailable.
- Overlap deduplication requires genuinely overlapping, time-aligned authorized footage and calibrated geometry. Ambiguous transitions must remain `UNKNOWN`.
- To replace a file with RTSP, change only the camera entry: `source_type: rtsp` and `source: rtsp://${CAM02_RTSP_USER}:${CAM02_RTSP_PASSWORD}@camera-host:554/stream`. `${VAR}` references are resolved from the backend's environment at launch (a missing variable is named in the error, its value never), never written to the database, and redacted from logs, alerts and API responses; the API shows only `RTSP camera-host`. No analytics code changes. A live stream ending or stalling (`analytics.health.live_stall_seconds`) marks the camera offline and reconnects it with a new epoch after a capped exponential backoff (`reconnect_backoff_seconds` doubling to `max_reconnect_backoff_seconds`); other cameras are unaffected. The DeepStream container runs on the docker bridge, so a camera served on the WSL host itself is reached at `172.17.0.1`.
- Live sources have no preview and no evidence clips (no local recording buffer in this version); markers on them are refused with that reason. Validated only with a controlled local republish of the demo clip (`scripts/validate_rtsp_source.py`): real camera RTSP, network timing and RTCP clock quality still need a real camera.
- Process only authorized footage. Minimize crops/embeddings/evidence and retain them only as needed. Do not add facial recognition, persistent gait, permanent identity, or demographic/emotion inference.

## 8. Current validated scope and open limits

At the 2026-10-28 verification point, re-checked 2026-11-03 (tests, build, configuration and artifact identity only; no GPU runs). Per-criterion status is in `docs/acceptance-matrix.md`. Unless a row says otherwise, the GPU runs below used the current extractor `4ef4f94a…` (host-time pacing clock; identity history in `configs/models.yaml`).

| Area | Currently supported statement | Evidence/boundary |
| --- | --- | --- |
| Host/WSL identity | RTX 5070 Ti Laptop GPU; WSL 2.6.3.0, Ubuntu 22.04.5 (unchanged) | `docs/environment.md`; Windows `Failed to initialize NVML: Unknown Error` preserved |
| Pinned container runtime | DeepStream 9.2.0 through the digest-pinned image and `scripts/deepstream.sh` (`--log-driver none`) | Container scope only; no native DeepStream/CUDA/TensorRT claim |
| Single-camera workflow | Paced and unpaced full-clip runs, `CLEAN_EOF`, 710 frames persisted, exact release, crash recovery | `reports/validation/live-workflow-cam01-20261028T181956896158Z` (44/44) |
| Host routes | Windows backend (`run-backend.ps1`) and backend inside WSL (`run-backend.sh`, `run-demo.sh --strict`) | `wsl-route-20261028T183618808723Z` (30/30); documented commands `documented-commands-20261028T140143584826Z` (19/19, extractor `e846ce3e…`) |
| Live RTSP (controlled) | Ingestion, loss, capped backoff, stall watchdog, reconnect with a new epoch, Stop cancels a pending reconnect, credentials redacted; no evidence clips for live sources (explicit refusal) | `rtsp-controlled-20261028T183104407414Z` (25/25, adds a test-server listening check); a local republish of the demo clip, not a real camera |
| Fault isolation | Killing one feed's container: that feed `ABNORMAL_TERMINATION` and stale; the other completes | `fault-isolation-2feeds-20261028T182129000616Z` (11/11), `paced-lead-2feeds-20261028T181842115333Z` (9/9, host-time pacing PASS, at most 0.57 s ahead); duplicate input |
| Dashboard | Real socket + headless Chrome: workflow, evidence viewer, mobile navigation and Alerts, Earlier/Later history, 390–1920 px | `browser-workflow-cam01-20261028T182204483248Z` (30/30), `browser-evidence-cam01-20261028T182256905383Z` (21/21), `ui-refactor-cam01-20261028T182320344809Z` (44/44); screenshots `reports/ui/screenshots-after-20261028T183345899702Z` |
| Detector profiles | TrafficCamNet (provisional) 309 fps, RT-DETR r2 130 fps, RF-DETR 119 fps consumer throughput; 3 runs each, 710/710 frames | `reports/model-evaluation/timed-runs/profiles-cam01-20261028T135347201228Z` (unpaced, extractor `e846ce3e…`; the pacing fix does not affect unpaced runs); timing only, candidates unaccepted |
| Detector numerics | Engine/parser parity PASS; strict cross-pipeline crosscheck FAIL for both candidates (not relaxed) | `reports/model-evaluation/{rt-detr,rf-detr}/`, `numerical-status-20261028.json` |
| Accuracy and occupancy | Not established: no reviewed labels, no calibration | Annotation package ready (`data/annotations/cam01-v1/`) |
| Artifact identity | Every configured artifact recorded vs recomputed = MATCH; calibration manifests BLOCKED | `artifact-manifest-check-20261103T174807Z` (Windows, current files), `...134034098702Z` (WSL) |
| Capacity (PRD 19) | Protocol v2 implemented, frozen and unit-tested; **no completed result**. Final batch on `4ef4f94a…`: stage A r1 and r2 PASS (gates and host-time pacing, `check_host_pacing.py`), then paused by the operator during r3 (its 5 s partial window is recorded as FAIL; not a defect). Earlier attempts (older extractors) were stopped by the host for low memory | `reports/benchmarks/capacity-repeats-20261028T184042736457Z/INTERRUPTED.json`; earlier `capacity-repeats-20261028T140450858882Z`; one-pass engineering ladder `duplicate-load-ladder-20261027T162455187350Z` |
| Soak / offline cold start | Not run (soak implemented, `demo/derived/cam01-repeat90.mp4`); no Internet-isolation testing by instruction | — |
| Tests (2026-11-03) | Windows 276 passed; WSL 274 passed, 2 skipped (Windows-only); frontend 33 passed; production build OK. In a clone without the local footage/model files, 5 tests skip and name the missing files (`AEGIS_REQUIRE_LOCAL_ARTIFACTS=1` makes them fail instead) | `docs/progress-notes.md` |

Older blocked native/host/preflight reports remain retained as history under `reports/environment/`, `reports/offline/`, `reports/demo/`, and `reports/benchmarks/`; they are not current container results and must not be silently discarded. Exact observations, commands, warnings, and limitations are in `docs/environment.md`.

## 9. Folder guide

```text
backend/                 FastAPI app, analytics, configuration and persistence
frontend/                React source, lockfile, local dependencies and dist build
configs/                 Typed camera/facility/model/analytics/runtime YAML
models/                  Operator-installed pinned checkpoints/engines/receipts
demo/                    Current authorized cam01 footage + optional SHA256SUMS sidecar
data/demo/               Future/disabled camera fixture locations, not current four-feed evidence
data/calibration/        Target location for surveyed, versioned calibration artifacts
perception/deepstream/   Smoke app/inference/tracker configs and Service Maker extractor
tests/                   Application tests, replay fixtures and reference labels
scripts/setup/           Inspect/install/model/engine/container entry points
scripts/deepstream.sh    Local pinned-image execution wrapper; no implicit missing-image pull
scripts/validate_*       Real-output/backend/live validation entry points
scripts/benchmark/       Real-runner wrapper, schema and report template
scripts/offline-test/    Static asset/config/source checker
scripts/replay.sh        Pinned replay runner wrapper
scripts/run-*.sh|ps1     Loopback runtime lifecycle commands; scopes differ by host route
scripts/reset-demo.*     Explicit run-scoped local API reset
docs/                    Environment, decisions and API contract
reports/environment/     Raw host/native probes and pinned container identity/version records
reports/media/cam01/     Source identity, technical probe and provenance assessment
reports/validation/      Current NVDEC/DeepStream/Service Maker/backend/live evidence
reports/demo/            Per-run logs and historical preflight evidence
reports/replay/          Replay inputs, logs and runner-produced results
reports/benchmarks/      Real raw metrics, summaries and reports; no current pass result
reports/offline/         Static checklists and operator cold-start evidence
```

### Repository contents, Git LFS, and what is not versioned

The repository holds the full project data: source, configs, docs, the footage (`demo/`), model checkpoints and TensorRT engines (`models/`), and all evidence under `reports/` and `backend/var/`, including raw per-frame logs, databases, clips, snapshots and frames. Large binary/data types (`*.mp4`, `*.onnx`, `*.engine`, `*.safetensors`, `*.jsonl`, `*.db`, tensors; see `.gitattributes`) are stored in **Git LFS**. Install it before cloning:

```bash
git lfs install
git clone https://github.com/reetamdan/time-and-motion.git
```

Without Git LFS a clone contains small pointer files instead of the footage and models. Five artifact-dependent tests then skip and name the missing files, and perception/readiness report `MISSING_FILE` rather than inventing availability. `git lfs pull` fetches the real files afterwards.

Not versioned (`.gitignore`), because they are machine-specific and rebuilt by the setup commands:

| Not versioned | Recreate with |
| --- | --- |
| `.venv*`, `node_modules/`, `.install/` (WSL toolchain, model-export environments) | §2 setup commands; lockfiles and pinned requirements are versioned |
| Copies of whole venvs/node_modules inside `reports/**/scratch/` | Made again by `scripts/validate_documented_commands.py` |
| `.tmp/`, `.pytest_tmp*/`, caches | Temporary |
| Key material (`*.key`, `*.pem`, `*.crt`, e.g. the throwaway MediaMTX `auto.key`) and `.env` | Generated locally when needed |

TensorRT engines are tied to the GPU, driver and TensorRT version they were built with (here RTX 5070 Ti, sm120, TensorRT 10.16); on other hardware rebuild them (§2 *Models and TensorRT engines*) and record the new hashes. The footage was supplied for this project without redistribution rights (§2 *Authorized footage installation*).
Evidence files record absolute paths from the original Windows machine.

## 10. Troubleshooting and migration

- **Windows `nvidia-smi` NVML error (seen historically):** preserve the error and compare the WSL/container evidence. Do not infer that WSL GPU access is broken or reinstall a driver blindly.
- **Ubuntu 22.04.5 native compatibility:** the DeepStream 9.2 pass belongs to the pinned container. Do not infer that the native WSL userland has a compatible SDK stack; recheck native and container scopes separately.
- **Older `docker`/`ffmpeg` not-found records:** these were PATH/runtime-scope observations, not proof of permanent uninstallation. Later WSL installation evidence records Docker and the container toolkit; query the intended runtime explicitly.
- **Missing native `deepstream-app`, GStreamer, TensorRT, or plugins:** use the validated pinned-container wrapper or deliberately install a separately supported native stack. For wrapper failures, check `reports/environment/deepstream92/image-reference.txt`, local image presence, and the required WSL codec libraries; the wrapper will not pull a missing image.
- **Backend in WSL cannot reach docker / perception unavailable with `No container runtime`:** check `docker info` inside the distribution; the WSL route calls docker directly. Do not mix routes: a Windows `.venv` cannot run in WSL and vice versa.
- **`bash: $'
': command not found` in WSL:** a script has CRLF line endings; project text files must be LF.
- **WSL `npm ci` broke the Windows build (`@rollup/rollup-win32-*` missing):** the shared `node_modules` was replaced by Linux binaries; run `npm ci` again on Windows and use `setup.sh --isolated-frontend` in WSL.
- **Live RTSP camera keeps reconnecting:** read the camera's `unavailable_reason` and the `camera-reconnecting:<id>` alert (attempt and delay); a missing `${VAR}` credential is named there. The container reaches a server on the WSL host at `172.17.0.1`, not `127.0.0.1`.
- **Nonfatal container warnings:** preserve plugin/audio diagnostics, the post-EOS GLib message, and tracker-score advisories. Video/metadata passes do not claim audio analytics or normalized tracker probabilities.
- **`/health/ready` returns 503/times out:** read its structured checks and `reports/demo/<run_id>/backend.log`; missing sources/models/perception must remain visible.
- **Missing `frontend/dist`:** run installation-time `npm ci`/`npm run build` through `scripts/setup/setup.sh`; runtime does not build it.
- **Venv interpreter missing:** use `.venv-wsl` in WSL and `.venv-windows` or an existing Windows `.venv` in Windows; do not cross-use them. WSL Node/npm `Permission denied` from a `/mnt/c/` shim is a PATH-mixing blocker, not an installation result.
- **Offline flags set but startup still fetches:** record the attempted dependency as a failure. Inspect application/library behavior and network evidence; flags alone are not acceptance.
- **Port already in use:** choose another explicit loopback port and update health/frontend proxy configuration consistently; do not kill unknown processes.
- **Start returns 409 `CLEANUP_UNCONFIRMED`:** a camera container from this or an earlier backend could not be confirmed removed. Run `POST /api/v1/demo/stop` (or the Stop button) to retry; only the exact recorded container name is touched.
- **Perception unavailable with an `identity MISMATCH` blocker:** a model, engine, infer config, parser, tracker, wrapper, extractor, or probe file differs from its recorded sha256 (`GET /api/v1/system/artifacts`). Restore the file or deliberately record the new identity after revalidating it.
- **Container runtime changed but start still uses an old capability:** liveness and fingerprints normally catch this; force a re-probe with `POST /api/v1/system/capability/invalidate`.
- **Evidence marker returns 503 `EVIDENCE_TOOL_UNAVAILABLE`:** install ffmpeg/ffprobe on the Windows PATH (e.g. `winget install Gyan.FFmpeg`). Evidence never falls back to placeholders.
- **Geometry-invalid alert:** the frames no longer match the calibration/line's measured frame size (resolution, crop, or zoom changed). Recalibrate and record a new revision.

### RTX A6000 migration procedure

The schemas, analytics, APIs, persistence, dashboard and validators carry over unchanged; only runtime artifacts are rebuilt. The preferred A6000 host is native Ubuntu, which uses the `linux-docker` host route (backend calling docker directly), exactly as the validated WSL route does.

1. Install the NVIDIA driver, Docker and the NVIDIA Container Toolkit; load the same pinned DeepStream image by digest (`scripts/setup/pull-deepstream.sh`). On native Linux `scripts/deepstream.sh` does not mount the WSL codec libraries (the toolkit injects NVDEC/NVENC libraries itself). Record `scripts/setup/verify-environment.sh` output.
2. Project environment: `bash scripts/setup/install-wsl-toolchain.sh` works on any x86-64 Linux (project-local uv/CPython 3.12, Node, ffmpeg); then `source .install/wsl/env.sh && bash scripts/setup/setup.sh --dev --isolated-frontend`.
3. Rebuild each detector engine on the A6000 (`scripts/setup/build-engines.sh`, same ONNX and precision policy). Engines are GPU-architecture specific; the `sm120` laptop engines will not load.
4. Record each new `engine_sha256` (and any changed infer config hash) in `configs/models.yaml`, then run `scripts/config/validate_config.py` and `scripts/validate_artifact_manifest.py`; every identity must be `MATCH`.
5. Rerun, with `.venv-wsl/bin/python` (or the host's project venv), the tools that run natively on Linux:
   - `-m pytest -q tests perception/candidates`;
   - `scripts/config/validate_config.py` and `scripts/validate_artifact_manifest.py` (its `pip check` uses the running interpreter);
   - `scripts/validate_controlled_facility.py`;
   - `bash scripts/run-demo.sh --strict` (preflight + backend + readiness), then a start/stop from the dashboard;
   - `scripts/benchmark/run_capacity.py --plan repeats|ladder|soak` (calls docker directly; the WSL-only cache drop is skipped);
   - `scripts/benchmark/run_detector_profiles.py --runs 3` and `scripts/benchmark/run_duplicate_load.py` (their backend launcher uses `run-backend.sh` with `BACKEND_PORT` on Linux);
   - `scripts/run_supervised_batch.py --batch FILE` to run any of these one at a time with memory protection;
   - `scripts/evaluation/evaluate_detections.py` once human-reviewed labels exist.

   Not native-Linux tools yet: `validate_live_workflow.py`, `validate_startup_and_evidence.py`, `validate_wsl_route.py`, `validate_rtsp_source.py`, `validate_fault_isolation.py`, `validate_paced_lead.py`, `validate_documented_commands.py`, `capture_ui_screenshots.py` and the browser validators orchestrate from Windows (`wsl.exe`, PowerShell launchers, Windows venv paths). On the A6000 they need their docker calls switched to `service_maker.runtime_prefix()` and their launchers to `run-backend.sh` before they can serve as evidence there. Detection results must be re-established, not assumed equal.
6. Capacity with real feeds: `scripts/benchmark/run_capacity.py --plan repeats --feeds 8` and `--plan ladder --ladder 4,8,12,16,20` (same predeclared protocol). More VRAM is not a throughput result; record the first unacceptable step and its reason.
