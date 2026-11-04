# Capacity report a6000-soak-20261104T002411Z

**Result: FAIL** - 0/1 executed steps passed, 0 of 1 planned steps not executed

> LABELLED DUPLICATE-INPUT LOAD TEST. Every feed is an independent decoder instance of the same repeated demo clip. This measures load only; it does not establish physically distinct camera coverage, counting accuracy, or calibrated facility acceptance. The detector profile is provisional (not accuracy-accepted).

| PRD 19.4 field | Value |
| --- | --- |
| Run ID | a6000-soak-20261104T002411Z |
| Timestamp | 2026-11-04T01:32:28.020144+00:00 -> 2026-11-04T02:06:57.273925+00:00 |
| GPU | NVIDIA RTX A6000, 596.71, 49140 MiB, 300.00 W |
| GPU identity verified by | nvidia-smi --query-gpu (host) |
| Driver / CUDA / TensorRT / DeepStream | driver 596.71; DeepStream 9.2 image (TensorRT/CUDA inside the pinned image) |
| Host / WSL / container image digest | Windows-10-10.0.19045-SP0; route windows-wsl; ghcr.io/nvidia/deepstream@sha256:092818f8142d82c4611c9e96eb589a975cd928baa3e15a92f8f67b0ff6fcdb85 |
| Workload stage(s) | D |
| Number of feeds | 4 |
| Source file / checksum | demo/derived/cam01-repeat90.mp4 sha256 a4c2c7d51f831dcb0189d4ceec7de3bbc63dbe0aa2b6ff2c9f69eb16818d8d5f |
| Source resolution / codec / FPS | {'codec': 'h264', 'profile': 'High', 'width': 1920, 'height': 1080, 'fps': '30000/1001', 'frames': 63900, 'duration_s': 2133.0} |
| Replay pacing mode | paced real-time demo profile (PRD 18.3): producer paced to the source timeline with a 200 ms lead; the backend releases each frame at its source time |
| Configured analytics FPS | 8 per camera (gate 7.2) |
| Measured analytics FPS, per camera | per step below (D: backend-consumed; A-C: producer) |
| Detector / revision | rt-detr-person-candidate (candidate_unaccepted) |
| Configuration profile | configs/a6000 |
| Detector precision / type policy | FP16 TensorRT engine (models/rt-detr-person-candidate/rt_detr_r18vd_raw_fp16_sm86.engine) |
| Tracker / revision | NvDCF, models.yaml tracker identity of the profile (stages C, D) |
| Calibration / config revisions | configs tree sha256 7edd067e5aaabd0cb64126277b3cf9e2991e591189b3bfb43395a0c34a7328fa; no calibration (replicas carry no facility claims) |
| Re-ID enabled / update cadence | disabled (stage F not run) |
| Preview / recording settings | no recording; soak probes exercise preview/history/evidence |
| Inference latency p50/p95 | unavailable: the pipeline exposes no per-frame inference timer; receive-to-result and schedule lag reported instead |
| Processing/frame age basis | stage D only: backend analytics_done minus backend stdout read time, same host clock, over every produced frame in the window; a produced frame never consumed is a missing delivery (fail), never excluded / stages A-C (no consumer): producer emit time minus the frame's due time on the source timeline anchored at the feed's first frame; negative = the deliberate 200 ms lead. A real-time keep-up criterion, NOT receive-to-result latency |
| Capture-to-result age | not measured: file replay has no capture clock |
| Intentional sampling | none: every decoded frame is processed (29.97 fps source) |
| Warm-up duration | 120 s |
| Test duration | 1800 s per step |
| Accuracy/reference result links | none: performance-only load run (no labels used) |
| Errors / missing counters | per-frame inference latency (no pipeline timer); capture-to-result age (no capture clock) |
| Acceptance result | FAIL |
| Bottleneck / next action | see steps |

## Steps

| Step | Pass | Feeds: cadence fps (gated basis) | p95 ms (gated basis) | Backlog growth ms | Missing deliveries | GPU util p95 | VRAM max MiB | GPU temp max C | Host avail min MiB | Cleanup | Failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| stageD-4feeds-r1 | False | cam-01: 29.958, cam-02: 29.958, cam-03: 29.958, cam-04: 29.958 | 303.489, 300.156, 306.065, 294.689 | 0.1, 0.2, 0.2, 0.7 | 0, 0, 0, 0 | 47.0 | 2450.0 | 81.0 | 39324.7 | True | soak probes failed: {'history': 4} |

## Across repeats (one worst-feed figure per repeat)

- **D-4feeds**: 0/1 passed; min_feed_cadence_fps mean 29.958 sd None [29.958, 29.958]; max_feed_p95_ms mean 306.065 sd None [306.065, 306.065]; max_feed_backlog_growth_ms mean 0.7 sd None [0.7, 0.7]; gpu_util_p95 mean 47.0 sd None [47.0, 47.0]

## Not run

- Stage E: calibrated overlap fusion needs calibrated overlapping footage
- Stage F: selective Re-ID is not implemented/enabled
- Stage G: needs the accepted core profile (detector accuracy unaccepted)
- Stage H: optional advanced AI out of scope

## Disclosures

- Competing non-project host workloads are recorded in environment.competing_host_processes_top_rss and were not stopped.
