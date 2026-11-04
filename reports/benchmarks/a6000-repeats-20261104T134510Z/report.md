# Capacity report a6000-repeats-20261104T134510Z

**Result: INTERRUPTED** - 9/9 executed steps passed, 3 of 12 planned steps not executed (driver killed; remaining stage completed in a separate run)

> LABELLED DUPLICATE-INPUT LOAD TEST. Every feed is an independent decoder instance of the same repeated demo clip. This measures load only; it does not establish physically distinct camera coverage, counting accuracy, or calibrated facility acceptance. The detector profile is provisional (not accuracy-accepted).

| PRD 19.4 field | Value |
| --- | --- |
| Run ID | a6000-repeats-20261104T134510Z |
| Timestamp | 2026-11-04T13:45:12.870384+00:00 -> None |
| GPU | NVIDIA RTX A6000, 596.71, 49140 MiB, 300.00 W |
| GPU identity verified by | nvidia-smi --query-gpu (host) |
| Driver / CUDA / TensorRT / DeepStream | driver 596.71; DeepStream 9.2 image (TensorRT/CUDA inside the pinned image) |
| Host / WSL / container image digest | Windows-10-10.0.19045-SP0; route windows-wsl; ghcr.io/nvidia/deepstream@sha256:092818f8142d82c4611c9e96eb589a975cd928baa3e15a92f8f67b0ff6fcdb85 |
| Workload stage(s) | A, B, C |
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
| Acceptance result | INTERRUPTED |
| Bottleneck / next action | see steps |

## Steps

| Step | Pass | Feeds: cadence fps (gated basis) | p95 ms (gated basis) | Backlog growth ms | Missing deliveries | GPU util p95 | VRAM max MiB | GPU temp max C | Host avail min MiB | Cleanup | Failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| stageA-4feeds-r1 | True | bench-01: 29.958, bench-02: 29.958, bench-03: 29.958, bench-04: 29.958 | -199.692, -199.708, -199.692, -199.764 | 0.0, 0.1, 0.0, 0.0 | n/a | 2.0 | 1820.0 | 67.0 | 44508.3 | True | - |
| stageA-4feeds-r2 | True | bench-01: 29.958, bench-02: 29.958, bench-03: 29.958, bench-04: 29.958 | -199.696, -199.697, -199.851, -199.697 | 0.0, 0.0, 0.0, 0.0 | n/a | 3.0 | 2177.0 | 67.0 | 43947.0 | True | - |
| stageA-4feeds-r3 | True | bench-01: 29.958, bench-02: 29.958, bench-03: 29.958, bench-04: 29.958 | -199.697, -199.695, -199.7, -199.693 | 0.0, 0.0, 0.0, 0.0 | n/a | 1.0 | 2115.0 | 67.0 | 43963.5 | True | - |
| stageB-4feeds-r1 | True | bench-01: 29.958, bench-02: 29.958, bench-03: 29.958, bench-04: 29.958 | -199.684, -199.684, -199.833, -199.787 | 0.0, 0.0, 0.0, 0.0 | n/a | 40.0 | 2715.0 | 75.0 | 43401.6 | True | - |
| stageB-4feeds-r2 | True | bench-01: 29.958, bench-02: 29.958, bench-03: 29.958, bench-04: 29.958 | -199.684, -199.742, -199.7, -199.782 | 0.0, 0.0, 0.0, 0.0 | n/a | 45.0 | 2676.0 | 79.0 | 43309.2 | True | - |
| stageB-4feeds-r3 | True | bench-01: 29.958, bench-02: 29.958, bench-03: 29.958, bench-04: 29.958 | -199.862, -199.673, -199.846, -199.711 | 0.0, 0.0, 0.0, 0.0 | n/a | 45.0 | 2676.0 | 79.0 | 43256.0 | True | - |
| stageC-4feeds-r1 | True | bench-01: 29.958, bench-02: 29.958, bench-03: 29.958, bench-04: 29.958 | -199.769, -199.827, -199.767, -199.859 | 0.0, 0.0, 0.0, 0.0 | n/a | 53.0 | 2846.0 | 81.0 | 42204.5 | True | - |
| stageC-4feeds-r2 | True | bench-01: 29.958, bench-02: 29.958, bench-03: 29.958, bench-04: 29.958 | -199.83, -199.705, -199.838, -199.832 | 0.0, 0.0, 0.0, 0.0 | n/a | 47.0 | 2830.0 | 80.0 | 42223.3 | True | - |
| stageC-4feeds-r3 | True | bench-01: 29.958, bench-02: 29.958, bench-03: 29.958, bench-04: 29.958 | -199.838, -199.696, -199.835, -199.868 | 0.0, 0.0, 0.0, 0.0 | n/a | 48.0 | 2830.0 | 81.0 | 42214.5 | True | - |

## Across repeats (one worst-feed figure per repeat)

- **A-4feeds**: 3/3 passed; min_feed_cadence_fps mean 29.958 sd 0.0 [29.958, 29.958]; max_feed_p95_ms mean -199.694 sd 0.002 [-199.696, -199.692]; max_feed_backlog_growth_ms mean 0.033 sd 0.058 [0.0, 0.1]; gpu_util_p95 mean 2.0 sd 1.0 [1.0, 3.0]
- **B-4feeds**: 3/3 passed; min_feed_cadence_fps mean 29.958 sd 0.0 [29.958, 29.958]; max_feed_p95_ms mean -199.68 sd 0.006 [-199.684, -199.673]; max_feed_backlog_growth_ms mean 0.0 sd 0.0 [0.0, 0.0]; gpu_util_p95 mean 43.333 sd 2.887 [40.0, 45.0]
- **C-4feeds**: 3/3 passed; min_feed_cadence_fps mean 29.958 sd 0.0 [29.958, 29.958]; max_feed_p95_ms mean -199.723 sd 0.039 [-199.767, -199.696]; max_feed_backlog_growth_ms mean 0.0 sd 0.0 [0.0, 0.0]; gpu_util_p95 mean 49.333 sd 3.215 [47.0, 53.0]

## Not run

- Stage E: calibrated overlap fusion needs calibrated overlapping footage
- Stage F: selective Re-ID is not implemented/enabled
- Stage G: needs the accepted core profile (detector accuracy unaccepted); the soak plan exercises dashboard/preview/history/evidence under the provisional profile
- Stage H: optional advanced AI out of scope

## Disclosures

- Competing non-project host workloads are recorded in environment.competing_host_processes_top_rss and were not stopped.
- The driver process was killed after the last completed step; this summary was rebuilt from per-step files. Partial step discarded: stageD-4feeds-r1.
