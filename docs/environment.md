# Environment evidence and recheck guide

Documentation verification: **2026-10-26 UTC**. This page separates current pinned-container validation, older host/native-userland observations, and capabilities that remain unmeasured. A report's `PASS` applies only to its recorded scope.

## Current status: single-camera container smoke and integration passed

DeepStream 9.2 works on this machine through a locally retained, digest-pinned container and the project wrapper `scripts/deepstream.sh`. Real evidence now covers NVDEC decode of `demo/cam01.mp4`, provisional TrafficCamNet + NvDCF plumbing, Service Maker prefixed-stdout streaming with retained JSONL/run-summary/log artifacts, bounded backend consumption, natural file EOS and stale-state handling, fresh-epoch replay, and live API start/stop with exact owned-container cleanup.

This is **not** an evaluated PRD detector baseline, an accuracy result, a four-camera demo, calibrated facility occupancy, a production soak, or full PRD acceptance. The native Ubuntu 22.04.5 userland is not thereby certified for DeepStream/CUDA/TensorRT. Windows NVML and older blocked-environment records remain below as history.

## Observed environment and runtime boundary

| Layer/component | Verified observation | Interpretation/limit |
| --- | --- | --- |
| Host | Windows 11 | Host identity only; exact build is captured by the host script |
| WSL | WSL 2.6.3.0; kernel 6.6.87.2 | Preserve these observed versions; recheck before a new frozen run |
| Distribution | `Ubuntu-22.04`; Ubuntu 22.04.5 LTS | Actual distribution and patch release; not Ubuntu 24.04 and not evidence of native DeepStream compatibility |
| GPU | NVIDIA GeForce RTX 5070 Ti Laptop GPU | GPU visibility plus container inference smoke; not a capacity or accuracy result |
| Windows driver | Raw `32.0.16.1047`; previously observed NVIDIA shorthand 610.47 | Preserve raw output rather than rewriting history |
| Windows NVML | `Failed to initialize NVML: Unknown Error` | Historical host failure remains recorded; it does not negate separately observed WSL/container execution |
| WSL NVIDIA driver fields | `NVIDIA-SMI 610.43.02`; `KMD Version: 610.47`; query field `driver_version` reports 610.47 | Raw header and query fields are preserved separately |
| WSL `nvidia-smi` CUDA field | CUDA UMD/driver capability 13.3 | Not proof of a native CUDA 13.3 toolkit or native TensorRT compatibility |
| WSL GPU memory | 12,227 MiB total reported | Capacity, not current usage or an available-memory benchmark |
| Docker / NVIDIA Container Toolkit | Installation record reports Docker Engine 29.9.2 and toolkit 1.20.1 in WSL | Later than the older `docker not found` probe; retain the installation record and reverify daemon/runtime state for new runs |
| DeepStream container | `ghcr.io/nvidia/deepstream@sha256:092818f8142d82c4611c9e96eb589a975cd928baa3e15a92f8f67b0ff6fcdb85`, linux/amd64 | Local inspected image used by the project wrapper; the wrapper refuses a missing local image instead of pulling it |
| Container SDK versions | DeepStream 9.2.0; CUDA Driver 13.3; CUDA Runtime 13.2; TensorRT 10.16; cuDNN 9.20 | Reported by `deepstream-app --version` inside the pinned image; container-scoped, not native Ubuntu package inventory |
| Backend/perception bridge | The backend launches one Service Maker container per camera epoch; `AEGIS_FRAME_V1:` stdout records feed a bounded queue (128 unpaced, 16 paced) while JSONL, run-summary and log outputs remain file artifacts | Two verified routes: Windows backend → `wsl.exe -e` → docker (default), and backend inside WSL → docker (`reports/validation/wsl-route-20261028T125420410621Z`). File sources plus a controlled local RTSP republish (`reports/validation/rtsp-controlled-20261028T131601797642Z`); native Linux not verified here. The queue bound is not a capacity benchmark |
| Native WSL Python/Node/SDK probes | Older probes found missing Torch/TensorRT/DeepStream/GStreamer and PATH-mixing failures | Historical native-userland state, not a description of the now-validated container path |

The PRD's broader dependency and platform targets remain requirements to assess, not a license to relabel this smoke environment as full acceptance.

## Current validation evidence

| Evidence | Observed result | Exact scope and warnings |
| --- | --- | --- |
| `reports/media/cam01/media_probe.json` and `provenance.json` | `demo/cam01.mp4`; H.264, 1920×1080, 710 frames, clean sequential host decode; SHA-256 `4201f7162eb0b9bcd3910f87b1a67a5bedac89c2b0a0bb4222e67d12f816b503` | One short, dense, uncalibrated outdoor clip. The authorization statement is user-supplied and not independently verified. No ground truth or entrance/queue/stair validation support was created |
| `reports/validation/nvdec-cam01/result.json` and `reports/validation/nvdec-cam01.log` | `passed: true`; pipeline exit 0; 710 decoded I420 frames; 981,504,000 bytes; zero remainder | Real `nvv4l2decoder` + `nvvideoconvert` decode/convert smoke in the pinned container. It is not detector or stream-capacity evidence |
| `reports/validation/deepstream-cam01/result.json` | `PASS`; 31 checks, zero failed checks, two warnings; detector and tracker outputs cover all 710 frames; local engine deserialized; clean EOS | Provisional TrafficCamNet + NvDCF output-structure/plumbing smoke. No accuracy or exact-count claim. The annotated images are mechanical tracker-row overlays, not human-verified boxes |
| `reports/validation/deepstream-cam01/run.log` | Pipeline running, source EOS, received EOS, and `App run successful` | Thirteen nonfatal plugin/audio warnings are preserved. Engine-build/`PERF` output is not an application benchmark |
| Earlier standalone `reports/validation/service-maker-cam01/result.json` and `run-summary.json` | `PASS`; 710 JSONL frame rows (0–709), valid real boxes/IDs/PTS, clean EOS; 12,468 object records | This report pins the earlier extractor hash `70f4e617281bc22a3531502394f40d79aeb6dd615b9333a825e529be2f3af42e`; it is not evidence that the current streaming extractor produced this standalone result. Two initial empty frames are retained; output need not match `deepstream-app` KITTI dumps record-for-record |
| Earlier standalone `reports/validation/service-maker-cam01/run.log` | Extraction completed with 710 frames | Preserves unavailable AAC decoder, unrelated plugin-load warnings, and a post-EOS GLib source-removal diagnostic. One tracker score fell outside `[0,1]`; scores are not assumed to be normalized probabilities |
| `reports/validation/backend-service-maker-cam01/result.json` | `PASS`; all 710 frames consumed; real person observations and persistent local track IDs delivered; source PTS and monotonic freshness checks passed; no contract errors | Current backend observation delivery only. Geometry positions are intentionally null; no calibrated crossing or facility occupancy is asserted |
| `reports/validation/live-application-cam01/result.json` | `PASS`; API start returned running, fresh positive tracker-derived visible count observed, calibration warning and provisional-profile notice preserved, stop returned stopped, owned container absent afterward | In-process FastAPI `TestClient` integration through the real supervisor/container path. It stopped after the first fresh positive count; full-clip natural EOS is covered separately. It is not a production-server or four-camera soak |
| `reports/validation/live-eos-replay-cam01/result.json` | `PASS`, 28/28 checks; positive count before natural EOS; 710 backend frames drained with an empty queue and no reader/consumer errors; contiguous JSONL 0–709; stale values null and health `DEGRADED` while the run stayed active; fresh second-epoch replay; exact containers absent after EOS/stops and sessions disposed | First epoch ran to natural full-clip EOS; second was intentionally stopped after fresh positive replay. The second-stop call took 22.719 seconds in this run, an observation rather than a performance pass. In-process `TestClient`, not an external production server/browser/soak |

For the current EOS report, the first epoch's JSONL, run-summary and log were copied into the evidence directory. The second early-stop epoch has copied JSONL and log artifacts, but the report records its run-summary as absent; do not claim a complete second-epoch clean-EOS summary.

### Result counts are not truth measurements

The reports contain detector/tracker record counts and track-ID counts to prove nonempty, structurally valid plumbing. They are not true person counts. No ground-truth annotations exist, so precision, recall, localization accuracy, IDF1/HOTA, ID-switch rate, detection correctness, and exact crowd counts are unmeasured. Dense occlusion may produce missed, merged, duplicate, or spurious boxes and track switches.

Live `visible_count` is likewise tracker output, not calibrated facility occupancy. Positive values in the live and EOS reports are run-specific observations from lifecycle tests, not site populations or accuracy claims.

## Current configuration and artifact identity

- `configs/cameras.yaml` enables only `cam-01` at `./demo/cam01.mp4`. `cam-02`, `cam-03`, and `cam-04` remain configured but disabled. Every camera has `calibration_status: MISSING`.
- `configs/models.yaml` selects `trafficcamnet-smoke` with `selection_mode: provisional_smoke`. The local ONNX and FP16 engine hashes match the validation evidence.
- `rt-detr-person-candidate` and `rf-detr-person-candidate` are disabled placeholders with null artifact provenance. The evaluated PRD candidate comparison remains pending.
- NvDCF is enabled for this smoke path with tracker revision `deepstream-9.2-nvdcf-perf-13662e73754e` and the pinned reference configuration.
- Exact image, model, engine, application/inference/tracker config, label, extractor, and source hashes are registered in `THIRD_PARTY.md`. The current extractor hash is a recomputed local-file identity; the older standalone report pins its predecessor, while the new live/backend/EOS reports do not embed the current extractor hash.

## Historical records — retain, do not present as current container results

These reports predate the successful container path or used older configuration/assets. They explain why earlier documentation said `BLOCKED`; they must not be deleted or silently rewritten.

| Historical evidence | Recorded result | Current interpretation |
| --- | --- | --- |
| `reports/environment/20261023T203438Z-host/` | Windows `nvidia-smi` failed with NVML exit 255; Windows GPU identity and WSL `nvidia-smi` passed | Preserve the Windows NVML failure and WSL visibility separately |
| `reports/environment/20261023T203721Z-runtime/` | Nine required native-runtime probes failed/were unavailable; Torch, TensorRT, DeepStream/GStreamer and required plugins did not pass | Native Ubuntu userland history only; not a contradiction of later container-scoped passes |
| `reports/offline/final-docs-validation-windows/` | Static preflight failed with five failures and nine warnings under the old four-source/no-selected-model state | Historical configuration and static-check result, not current runtime evidence or offline acceptance |
| `reports/offline/final-docs-validation-wsl/` | Static preflight failed with six failures and nine warnings, including missing native WSL `pydantic` | Historical native Python state; the validated live bridge uses Windows Python plus WSL/Docker |
| `reports/demo/final-demo-preflight-windows/` | Demo wrapper exited at static preflight before starting processes | Historical preflight failure only |
| `reports/benchmarks/final-invalid-benchmark-profile/` | Dry run rejected missing media and produced no benchmark summary/report | No benchmark result exists |

The repository's existing `.venv` is a Windows environment, not a Linux venv. Native Bash/WSL setup still uses a separate `.venv-wsl`; do not cross-use Windows and Linux environments.

## Reproduce the validated container path

Run Bash commands from the project root inside `Ubuntu-22.04`. Installation/pull steps are explicit and belong outside a frozen offline run.

### Install or verify the pinned image and Service Maker dependency

```bash
bash scripts/setup/pull-deepstream.sh
bash scripts/deepstream.sh bash /workspace/scripts/setup/install-service-maker-deps.sh
```

The pull script selects the inspected linux/amd64 digest and refuses a moved tag. The runtime wrapper reads `reports/environment/deepstream92/image-reference.txt`, verifies that exact image is installed locally, checks WSL codec libraries, and executes the supplied command with the project mounted at `/workspace`.

### NVDEC smoke

```bash
bash scripts/deepstream.sh bash /workspace/scripts/validate-nvdec.sh
```

The script checks required plugins and counts actual decoded/converted I420 bytes; EOS or process exit alone is insufficient.

### DeepStream detector/tracker smoke and output validator

```bash
bash scripts/deepstream.sh deepstream-app -c /workspace/perception/deepstream/cam01_trafficcamnet_smoke.txt
python scripts/validate_deepstream_smoke.py
```

The application config writes detector/tracker KITTI records. Preserve its stdout/stderr as `reports/validation/deepstream-cam01/run.log` for the validator. The validator requires OpenCV and NumPy, checks real output structure and complete frame coverage, and generates mechanical tracker-only evidence images. A `trtexec` pass or successful EOS alone is not detection validation.

### Service Maker streaming extractor and JSONL validator

```bash
bash scripts/deepstream.sh python3 /workspace/perception/deepstream/service_maker/extract_metadata.py \
  --camera-epoch smoke-manual
python scripts/validate_service_maker.py
```

The current extractor requires a nonempty camera epoch and writes one JSON object per real frame, including empty frames, to JSONL. It simultaneously emits the same payload as an `AEGIS_FRAME_V1:`-prefixed stdout line and flushes both outputs. The backend validates and consumes those stdout frames through a bounded 128-frame queue; JSONL, run-summary and process-log artifacts remain available separately. Source PTS is preserved separately from host receipt time, and JSONL EOF alone does not prove clean EOS or completeness.

The retained `service-maker-cam01` standalone report was generated with the earlier extractor hash recorded above. Do not describe it as output from the current streaming revision; see `THIRD_PARTY.md` for the current local-file hash and lineage boundary.

### Backend and live API integration

From Windows, use the backend Python environment with the project dependencies installed:

```powershell
.\.venv\Scripts\python.exe scripts\validate_backend_bridge.py
.\.venv\Scripts\python.exe scripts\validate_live_application.py
.\.venv\Scripts\python.exe scripts\validate_live_eos.py
.\.venv\Scripts\python.exe scripts\validate_live_workflow.py
```

The live validators (`validate_live_application.py`, `validate_live_eos.py`, `validate_live_workflow.py`) always write to a **new** directory: `reports/validation/<name>-<UTC timestamp>/` by default, or the path given with `--output-dir` or the validator's `AEGIS_LIVE_*_RUN_DIR` variable. An existing directory is refused, never cleared, so earlier evidence cannot be overwritten. Each `result.json` records the exact command and run directory.

These commands launch real WSL/container work. The three live validators write to new timestamped directories and refuse existing ones; `validate_backend_bridge.py` and the WSL-side smoke validators still use fixed paths, so preserve those reports before rerunning them. `validate_live_application.py` intentionally stops after the first fresh positive count. `validate_live_eos.py` covers natural EOS, backend drain, stale state and fresh-epoch replay as detailed above. Both live validators use FastAPI `TestClient`; they do not open a production socket or perform a browser/run soak.

## Read-only host/native inspection

The older inspection entry points remain useful for distinguishing host, native WSL, and container scope:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup/verify-host.ps1 -WslDistribution Ubuntu-22.04
wsl --distribution Ubuntu-22.04 --cd "$PWD" --exec bash scripts/setup/verify-environment.sh
```

For an already retained, digest-pinned image, the generic environment probe also accepts:

```bash
bash scripts/setup/verify-environment.sh --container-image 'IMAGE@sha256:DIGEST'
```

That generic container probe uses `--pull=never`, but its `nvidia-smi` success alone does not prove DeepStream decode/inference. Use the current validation entry points above for those capabilities. Required-probe failures return exit code `2` and are retained in timestamped reports.

## Remaining limitations and closure work

1. **Evaluated detector baseline:** compare the PRD candidates with real references and select deliberately. TrafficCamNet remains a provisional smoke model.
2. **Ground truth and accuracy:** create authorized, reviewed references before reporting precision/recall, localization, count error, IDF1/HOTA, ID switches, or exact people counts.
3. **Single-source scope:** supply and enable suitable authorized clips before claiming four-camera operation, overlap deduplication, queue behavior, stair transitions, or two-floor continuity. The current clip does not establish those scenes.
4. **Calibration and occupancy:** all camera calibration is missing and backend geometry is null. Illustrative polygons/view boxes are not surveyed calibration; visible tracker counts are not calibrated facility occupancy.
5. **Runtime warnings:** preserve plugin/audio diagnostics, the post-EOS GLib diagnostic, and out-of-unit-interval tracker scores. Do not hide them merely because video/metadata checks passed; no audio analytics are claimed.
6. **Capacity and performance:** output-count headroom checks and the 128-frame queue bound are not throughput/capacity measurements. `deepstream-app` perf lines and isolated `trtexec` timings are not the PRD benchmark ladder or a multi-stream soak. The EOS report's 22.719-second second-stop call is one observed lifecycle timing, not a latency or performance pass.
7. **Offline/cold start:** current passes are not network-isolation, reboot/cold-start, strict frozen-preflight, or offline-soak evidence. Static preflight alone remains insufficient.
8. **Native/host distinctions:** do not infer native Ubuntu 22.04.5 SDK compatibility from the container pass, and do not remove the Windows NVML failure from the record.
9. **Production lifecycle:** current live evidence covers early operator stop, one natural 710-frame EOS with backend drain and stale behavior, fresh second-epoch replay, and exact container/session cleanup. The second epoch is intentionally early-stopped and has no copied run-summary. External HTTP serving, browser behavior, long-run retention, four-feed load, and full PRD acceptance require separate evidence.

## 2026-10-27 additions

- **FFmpeg 9.0** (Windows `Gyan.FFmpeg` full build, on `PATH`) is used by the backend only for evidence extraction (`backend/app/services/evidence.py`) and by the annotation-package builder. When it is missing, evidence capture reports `EVIDENCE_TOOL_UNAVAILABLE`; nothing else depends on it.
- **Windows `nvidia-smi`** returned valid GPU, NVDEC, VRAM, temperature, and power readings, including the load from WSL containers, during the duplicate-input load ladder (`reports/benchmarks/duplicate-load-ladder-20261027T162455187350Z/`). The earlier `Failed to initialize NVML` record is kept as history.
- **Container capability probe:** `scripts/lib/capability-probe.sh` inside the pinned image reported DeepStream 9.2, `nvstreammux`/`nvinfer`/`nvtracker`/`nvv4l2decoder` present, the extractor runnable, the GPU `NVIDIA GeForce RTX 5070 Ti Laptop GPU, 610.47`, and TensorRT `10.16.0.72-1+cuda13.2`. A cold probe takes 3–6 s.
- **Pipeline vs file timestamps:** for `demo/cam01.mp4` (`has_b_frames=2`), DeepStream's first-frame PTS is 0.066733 s and ffmpeg's is 0.000000 s; both decode 710 frames, and frame index N is the same frame in both. Evidence extraction aligns the two timelines on the first frame.
- **pytest on this machine** needs a writable `--basetemp` (the default `%TEMP%\pytest-of-<user>` is not writable in this sandboxed shell); for example, `-p no:cacheprovider --basetemp .install\pytest-tmp`.

## 2026-10-28 additions

- **WSL distribution as found:** Ubuntu 22.04 (kernel `6.6.87.2-microsoft-standard-WSL2`), user `root`, system Python 3.10.12 without pip/venv, no Node, no ffmpeg; Docker Engine and `/usr/lib/wsl/lib/nvidia-smi` present; 7 GiB RAM visible to the VM; docker bridge gateway `172.17.0.1`. Nothing system-wide was installed, upgraded, or removed.
- **WSL application-route toolchain** (project-local, `scripts/setup/install-wsl-toolchain.sh`, every archive checked against a pinned SHA-256): uv 0.12.24 → CPython 3.12.15 (same interpreter version as the Windows `.venv`) under `~/.local/share/uv`; Node.js 24.14.1 (same as Windows); static ffmpeg n9.0.2. `.venv-wsl` is created by `scripts/setup/setup.sh --dev --isolated-frontend`, which builds the frontend in `~/.cache/aegis-ops/frontend-build` and copies only `dist/` back, so the Windows `node_modules` (platform-specific rollup/esbuild binaries) is never replaced. `backend/requirements-dev.txt` now lists `opencv-python-headless==5.0.0.93` (the Windows `.venv` already had it; the manifest was incomplete).
- **Host routes:** `host_route()` is `windows-wsl` on Windows (docker via `wsl.exe -d Ubuntu-22.04 -e`; `--` would re-parse the argv in the login shell) and `linux-docker` inside WSL/Linux (docker directly). Telemetry scope is `wsl2` when the backend runs in WSL (`/proc/version` contains `microsoft`), `linux-host` on other Linux.
- **Test-only RTSP server:** mediamtx v1.21.2 (`scripts/setup/install-rtsp-test-server.sh`), used only by `scripts/validate_rtsp_source.py`; the DeepStream container reaches it through the docker bridge gateway.
- **Load footage:** `demo/derived/cam01-repeat33.mp4` (33 × cam01, stream copy, 23,430 frames, 782.09 s, one source segment; manifest beside it) is labelled repeated footage for duration/load tests only.
- **Line endings:** project text files are LF. CRLF (introduced by Windows text-mode writes in earlier patch helpers) broke `bash scripts/setup/setup.sh` in WSL; 47 files were normalised.

## 2026-10-28 (session 7) additions

- **Host memory as measured:** 15.2 GiB physical RAM, commit limit 36.2 GiB (pagefile 21 GiB), no `%USERPROFILE%\.wslconfig` (WSL default: guest MemTotal 7538 MiB, 2 GiB swap, 32 vCPU). With WSL idle, competing non-project applications (an IDE agent, a browser, desktop compositor, antivirus) leave about 3.5-5.5 GiB available. Four DeepStream containers take about 2.6 GiB more (~650 MiB each). Nothing system-wide was changed; see `reports/benchmarks/memory-diagnosis-20261028T114500000000Z/diagnosis.json`.
- **Container logging:** `scripts/deepstream.sh` runs with `--log-driver none` (wrapper sha `b5d9a510…`); on hosts without `/usr/lib/wsl/lib` it mounts no WSL codec libraries (native Linux relies on the NVIDIA Container Toolkit; unverified here).
- **WSL loopback:** inside this WSL network, a server listening on `*:8554` refused connections to `127.0.0.1:8554` while accepting them on the docker bridge address `172.17.0.1`; the RTSP validator publishes and reads through `172.17.0.1`.
- **Soak footage:** `demo/derived/cam01-repeat90.mp4` (90 × cam01, 63,900 frames, 2133 s, stream copy, sha `a4c2c7d5…`, manifest beside it), labelled repeated footage.
- **Test baseline (2026-11-03, delivered code, extractor `4ef4f94a…`):** Windows `pytest tests perception/candidates` 276 passed; WSL (`.venv-wsl`) 274 passed + 2 skipped (Windows-only tests); frontend 33 passed; production build OK; fresh `npm ci` + build OK in a simulated clone of the repository contents. The full documented-commands run was last done in session 7 (`reports/validation/documented-commands-20261028T140143584826Z`, 19/19).
- **Long jobs** run one at a time under `scripts/run_supervised_batch.py` (memory floor, wrapper-death and supervisor-death handling, leftover-container check); batch records under `reports/batches/`.
- **WSL guest clocks (measured 2026-10-28).** CLOCK_MONOTONIC/BOOTTIME in Ubuntu-22.04 advanced 61.42 s per 60.07 s of Windows host time (+2.2 %; ~3.5 % under load); the guest wall clock tracked the host (0.996, time-synchronised, may step). Anything that must agree with the host in real time (paced replay) uses the wall clock; durations inside one process may still use monotonic time.
- **Host memory and the development host's reaper.** With 4 DeepStream feeds plus the other applications on this 15.2 GiB laptop, host available memory sat at ~3.8–4.3 GiB (flat, no growth). The development host twice stopped the session's background GPU work for "critically low" memory at about this level (≈ 3.0–4.0 GiB available), far above the project's own guard (512/768 MiB). Long runs need more free memory, or the development host's memory reaper disabled, which leaves only the project's predeclared guard active.
