# Capacity report a6000-ladder-20261103T230400Z

**Result: COMPLETE** - 2/3 executed steps passed, 2 of 5 planned steps not executed

> LABELLED DUPLICATE-INPUT LOAD TEST. Every feed is an independent decoder instance of the same repeated demo clip. This measures load only; it does not establish physically distinct camera coverage, counting accuracy, or calibrated facility acceptance. The detector profile is provisional (not accuracy-accepted).

| PRD 19.4 field | Value |
| --- | --- |
| Run ID | a6000-ladder-20261103T230400Z |
| Timestamp | 2026-11-03T23:04:02.637439+00:00 -> 2026-11-03T23:44:39.932754+00:00 |
| GPU | NVIDIA RTX A6000, 596.71, 49140 MiB, 300.00 W |
| GPU identity verified by | nvidia-smi --query-gpu (host) |
| Driver / CUDA / TensorRT / DeepStream | driver 596.71; DeepStream 9.2 image (TensorRT/CUDA inside the pinned image) |
| Host / WSL / container image digest | Windows-10-10.0.19045-SP0; route windows-wsl; ghcr.io/nvidia/deepstream@sha256:092818f8142d82c4611c9e96eb589a975cd928baa3e15a92f8f67b0ff6fcdb85 |
| Workload stage(s) | D |
| Number of feeds | 1, 2, 4 |
| Source file / checksum | demo/derived/cam01-repeat33.mp4 sha256 03950b88b05d486a350199824b6898597e4f4ff020a2f89bf4a93649b59deafc |
| Source resolution / codec / FPS | {'codec': 'h264', 'profile': 'High', 'width': 1920, 'height': 1080, 'fps': '30000/1001', 'frames': 23430, 'duration_s': 782.09} |
| Replay pacing mode | paced real-time demo profile (PRD 18.3): producer paced to the source timeline with a 200 ms lead; the backend releases each frame at its source time |
| Configured analytics FPS | 8 per camera (gate 7.2) |
| Measured analytics FPS, per camera | per step below (D: backend-consumed; A-C: producer) |
| Detector / revision | rt-detr-person-candidate (candidate_unaccepted) |
| Configuration profile | configs/a6000 |
| Detector precision / type policy | FP16 TensorRT engine (models/rt-detr-person-candidate/rt_detr_r18vd_raw_fp16_sm86.engine) |
| Tracker / revision | NvDCF, models.yaml tracker identity of the profile (stages C, D) |
| Calibration / config revisions | configs tree sha256 f87fe6208a4d592399e74dae4ec7bcd26de918e22824c2df3fc45503ff5a92d8; no calibration (replicas carry no facility claims) |
| Re-ID enabled / update cadence | disabled (stage F not run) |
| Preview / recording settings | not exercised (stage G not run) |
| Inference latency p50/p95 | unavailable: the pipeline exposes no per-frame inference timer; receive-to-result and schedule lag reported instead |
| Processing/frame age basis | stage D only: backend analytics_done minus backend stdout read time, same host clock, over every produced frame in the window; a produced frame never consumed is a missing delivery (fail), never excluded / stages A-C (no consumer): producer emit time minus the frame's due time on the source timeline anchored at the feed's first frame; negative = the deliberate 200 ms lead. A real-time keep-up criterion, NOT receive-to-result latency |
| Capture-to-result age | not measured: file replay has no capture clock |
| Intentional sampling | none: every decoded frame is processed (29.97 fps source) |
| Warm-up duration | 120 s |
| Test duration | 600 s per step |
| Accuracy/reference result links | none: performance-only load run (no labels used) |
| Errors / missing counters | per-frame inference latency (no pipeline timer); capture-to-result age (no capture clock) |
| Acceptance result | COMPLETE |
| Bottleneck / next action | stopped at 4 feeds: ['cam-01: no producer frames recorded', 'cam-02: no producer frames recorded', 'cam-03: no producer frames recorded', 'cam-04: no producer frames recorded', 'required measurement unavailable: GPU temperature', 'required measurement unavailable: backend RSS growth'] |

## Steps

| Step | Pass | Feeds: cadence fps (gated basis) | p95 ms (gated basis) | Backlog growth ms | Missing deliveries | GPU util p95 | VRAM max MiB | GPU temp max C | Host avail min MiB | Cleanup | Failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| stageD-1feeds-r1 | True | cam-01: 29.958 | 230.257 | 3.4 | 0 | 7.0 | 1026.0 | 70.0 | 41369.3 | True | - |
| stageD-2feeds-r1 | True | cam-01: 29.957, cam-02: 29.957 | 240.272, 240.205 | 0.1, 0.2 | 0, 0 | 28.0 | 1429.0 | 72.0 | 40516.3 | True | - |
| stageD-4feeds-r1 | False | cam-01: None, cam-02: None, cam-03: None, cam-04: None | None, None, None, None | None, None, None, None | None, None, None, None | None | None | None | None | True | cam-01: no producer frames recorded; cam-02: no producer frames recorded; cam-03: no producer frames recorded; cam-04: no producer frames recorded |

## Across repeats (one worst-feed figure per repeat)

- **D-1feeds**: 1/1 passed; min_feed_cadence_fps mean 29.958 sd None [29.958, 29.958]; max_feed_p95_ms mean 230.257 sd None [230.257, 230.257]; max_feed_backlog_growth_ms mean 3.4 sd None [3.4, 3.4]; gpu_util_p95 mean 7.0 sd None [7.0, 7.0]
- **D-2feeds**: 1/1 passed; min_feed_cadence_fps mean 29.957 sd None [29.957, 29.957]; max_feed_p95_ms mean 240.272 sd None [240.272, 240.272]; max_feed_backlog_growth_ms mean 0.2 sd None [0.2, 0.2]; gpu_util_p95 mean 28.0 sd None [28.0, 28.0]
- **D-4feeds**: 0/1 passed; 

## Ladder

```json
{
 "last_acceptable_feeds": 2,
 "stop": {
  "feeds": 4,
  "reason": [
   "cam-01: no producer frames recorded",
   "cam-02: no producer frames recorded",
   "cam-03: no producer frames recorded",
   "cam-04: no producer frames recorded",
   "required measurement unavailable: GPU temperature",
   "required measurement unavailable: backend RSS growth"
  ],
  "kind": "first_unacceptable"
 },
 "unattempted": [
  6,
  8
 ]
}
```

## Not run

- Stage E: calibrated overlap fusion needs calibrated overlapping footage
- Stage F: selective Re-ID is not implemented/enabled
- Stage G: needs the accepted core profile (detector accuracy unaccepted); the soak plan exercises dashboard/preview/history/evidence under the provisional profile
- Stage H: optional advanced AI out of scope

## Disclosures

- Competing non-project host workloads are recorded in environment.competing_host_processes_top_rss and were not stopped.
