# Capacity report a6000-repeats-stageD-20261104T160620Z

**Result: PASS** - 3/3 executed steps passed, 0 of 3 planned steps not executed (deviations: not acceptance-eligible)

> LABELLED DUPLICATE-INPUT LOAD TEST. Every feed is an independent decoder instance of the same repeated demo clip. This measures load only; it does not establish physically distinct camera coverage, counting accuracy, or calibrated facility acceptance. The detector profile is provisional (not accuracy-accepted).

| PRD 19.4 field | Value |
| --- | --- |
| Run ID | a6000-repeats-stageD-20261104T160620Z |
| Timestamp | 2026-11-04T16:06:22.134011+00:00 -> 2026-11-04T16:46:42.545913+00:00 |
| GPU | NVIDIA RTX A6000, 596.71, 49140 MiB, 300.00 W |
| GPU identity verified by | nvidia-smi --query-gpu (host) |
| Driver / CUDA / TensorRT / DeepStream | driver 596.71; DeepStream 9.2 image (TensorRT/CUDA inside the pinned image) |
| Host / WSL / container image digest | Windows-10-10.0.19045-SP0; route windows-wsl; ghcr.io/nvidia/deepstream@sha256:092818f8142d82c4611c9e96eb589a975cd928baa3e15a92f8f67b0ff6fcdb85 |
| Workload stage(s) | D |
| Number of feeds | 4 |
| Source file / checksum | demo/derived/cam01-repeat33.mp4 sha256 03950b88b05d486a350199824b6898597e4f4ff020a2f89bf4a93649b59deafc |
| Source resolution / codec / FPS | {'codec': 'h264', 'profile': 'High', 'width': 1920, 'height': 1080, 'fps': '30000/1001', 'frames': 23430, 'duration_s': 782.09} |
| Replay pacing mode | paced real-time demo profile (PRD 18.3): producer paced to the source timeline with a 200 ms lead; the backend releases each frame at its source time |
| Configured analytics FPS | 8 per camera (gate 7.2) |
| Analysis frame interval | every frame |
| Measured analytics FPS, per camera | per step below (D: backend-consumed; A-C: producer) |
| Detector / revision | rt-detr-person-candidate (candidate_unaccepted) |
| Configuration profile | configs/a6000 |
| Detector precision / type policy | FP16 TensorRT engine (models/rt-detr-person-candidate/rt_detr_r18vd_raw_fp16_sm86.engine) |
| Tracker / revision | NvDCF, models.yaml tracker identity of the profile (stages C, D) |
| Calibration / config revisions | configs tree sha256 35da176eaaf3d8f9375a1fc8b170448e101ab5d2d82f37787297b2f464e7bbe6; no calibration (replicas carry no facility claims) |
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
| Acceptance result | PASS |
| Bottleneck / next action | see steps |

## Steps

| Step | Pass | Feeds: cadence fps (gated basis) | p95 ms (gated basis) | Backlog growth ms | Missing deliveries | GPU util p95 | VRAM max MiB | GPU temp max C | Host avail min MiB | Cleanup | Failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| stageD-4feeds-r1 | True | cam-01: 29.957, cam-02: 29.957, cam-03: 29.957, cam-04: 29.957 | 300.026, 295.29, 296.648, 288.368 | 0.5, 0.3, 0.4, 0.6 | 0, 0, 0, 0 | 55.0 | 2681.0 | 83.0 | 37957.1 | True | - |
| stageD-4feeds-r2 | True | cam-01: 29.958, cam-02: 29.958, cam-03: 29.958, cam-04: 29.958 | 316.147, 311.552, 314.117, 301.149 | 0.1, -0.1, -0.2, 0.0 | 0, 0, 0, 0 | 54.0 | 2681.0 | 82.0 | 42078.1 | True | - |
| stageD-4feeds-r3 | True | cam-01: 29.958, cam-02: 29.958, cam-03: 29.958, cam-04: 29.958 | 307.087, 304.199, 295.099, 300.945 | -0.2, -0.2, 0.0, -0.2 | 0, 0, 0, 0 | 54.0 | 2681.0 | 82.0 | 42080.4 | True | - |

## Across repeats (one worst-feed figure per repeat)

- **D-4feeds**: 3/3 passed; min_feed_cadence_fps mean 29.958 sd 0.001 [29.957, 29.958]; max_feed_p95_ms mean 307.753 sd 8.081 [300.026, 316.147]; max_feed_backlog_growth_ms mean 0.233 sd 0.321 [0.0, 0.6]; gpu_util_p95 mean 54.333 sd 0.577 [54.0, 55.0]

## Not run

- Stage E: calibrated overlap fusion needs calibrated overlapping footage
- Stage F: selective Re-ID is not implemented/enabled
- Stage G: needs the accepted core profile (detector accuracy unaccepted); the soak plan exercises dashboard/preview/history/evidence under the provisional profile
- Stage H: optional advanced AI out of scope

## Disclosures

- Competing non-project host workloads are recorded in environment.competing_host_processes_top_rss and were not stopped.
