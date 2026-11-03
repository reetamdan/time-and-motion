# Benchmark report — RUNNER MUST COMPLETE FROM REAL EVIDENCE

> This is a template, not a benchmark result. Replace no field with a guess.
> Keep unmeasured values as `UNMEASURED — <reason>` and unsupported values as
> `UNAVAILABLE — <probe/error>`. Link every number to raw metrics in `summary.json`.

- Run ID: REQUIRED
- Timestamp / monotonic timing basis: REQUIRED
- GPU / UUID: REQUIRED FROM ACTUAL PROBE
- GPU identity verified by: REQUIRED COMMAND/LOG
- Driver / CUDA runtime or toolkit / TensorRT / DeepStream: REQUIRED OR UNAVAILABLE
- Host / WSL / container image digest: REQUIRED OR UNAVAILABLE
- Workload stage (A–H): REQUIRED
- Number of feeds: REQUIRED
- Source files/profile references and SHA-256: REQUIRED
- Duplicated content used for capacity-only load: YES/NO + cameras
- Source resolution: MEASURED/PROBED OR UNAVAILABLE
- Source codec/profile: MEASURED/PROBED OR UNAVAILABLE
- Source FPS: MEASURED/PROBED OR UNAVAILABLE
- Replay pacing mode (real-time or unpaced): REQUIRED
- Configured analytics FPS per camera: REQUIRED FROM EFFECTIVE CONFIG
- Measured source/decode/detector/tracker/analytics FPS per camera: RAW METRICS LINK
- Detector / immutable revision / export and parser revision: REQUIRED OR BLOCKED
- Detector precision / strongly-typed policy: REQUIRED FROM EFFECTIVE CONFIG
- Tracker / revision: REQUIRED OR DISABLED WITH STAGE
- Calibration / config revisions: REQUIRED OR NOT APPLICABLE WITH REASON
- Re-ID enabled / update cadence: REQUIRED OR DISABLED
- Preview / recording settings: REQUIRED FROM EFFECTIVE CONFIG
- VRAM MiB baseline / median / peak: RAW METRICS LINK OR UNAVAILABLE
- GPU utilization: RAW METRICS LINK OR UNAVAILABLE
- NVDEC utilization: RAW METRICS LINK OR UNAVAILABLE REASON
- CPU utilization: RAW METRICS LINK OR UNAVAILABLE
- RAM / process memory: RAW METRICS LINK OR UNAVAILABLE
- p50 / p95 inference latency (batch/per-frame semantics): RAW METRICS LINK
- p50 / p95 processing age and time basis: RAW METRICS LINK
- Capture-to-result age, only if clock quality supports it: OTHERWISE UNAVAILABLE
- Dropped frames by camera/stage: RAW METRICS LINK
- Intentional sampling, separately from unexpected drops: CONFIG + RAW METRICS LINK
- Queue-depth behavior / backlog observation: RAW METRICS LINK
- GPU temperature / thermal observations: RAW METRICS LINK OR UNAVAILABLE
- AC/battery, power mode, competing GPU workloads: OBSERVED CONTEXT
- Warm-up duration: OBSERVED MONOTONIC DURATION
- Measured steady-state duration: OBSERVED MONOTONIC DURATION
- Accuracy/reference result links: REQUIRED FOR ACCURACY CLAIMS; OTHERWISE NOT EVALUATED
- Errors / missing counters / camera-specific failures: REQUIRED; DO NOT OMIT FAILURES
- Acceptance profile and thresholds frozen before run: REQUIRED
- Acceptance result: PASS / FAIL / BLOCKED / NOT_EVALUATED + EVIDENCE-BACKED REASON
- Bottleneck / next action: REQUIRED

## Required evidence index

List raw time-series metrics, per-camera counters, logs, effective config and hash,
engine/model/calibration revisions, source checksums, and summary.json here. An
aggregate average must not conceal a starved camera. A short engineering run must
not be represented as satisfying the 120 s warm-up + 600 s capacity profile or the
30-minute full-demo soak.
