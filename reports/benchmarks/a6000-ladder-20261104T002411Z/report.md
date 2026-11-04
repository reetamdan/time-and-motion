# Capacity report a6000-ladder-20261104T002411Z

**Result: COMPLETE** - 5/5 executed steps passed, 0 of 5 planned steps not executed

> LABELLED DUPLICATE-INPUT LOAD TEST. Every feed is an independent decoder instance of the same repeated demo clip. This measures load only; it does not establish physically distinct camera coverage, counting accuracy, or calibrated facility acceptance. The detector profile is provisional (not accuracy-accepted).

| PRD 19.4 field | Value |
| --- | --- |
| Run ID | a6000-ladder-20261104T002411Z |
| Timestamp | 2026-11-04T00:24:13.836460+00:00 -> 2026-11-04T01:32:23.780281+00:00 |
| GPU | NVIDIA RTX A6000, 596.71, 49140 MiB, 300.00 W |
| GPU identity verified by | nvidia-smi --query-gpu (host) |
| Driver / CUDA / TensorRT / DeepStream | driver 596.71; DeepStream 9.2 image (TensorRT/CUDA inside the pinned image) |
| Host / WSL / container image digest | Windows-10-10.0.19045-SP0; route windows-wsl; ghcr.io/nvidia/deepstream@sha256:092818f8142d82c4611c9e96eb589a975cd928baa3e15a92f8f67b0ff6fcdb85 |
| Workload stage(s) | D |
| Number of feeds | 1, 2, 4, 6, 8 |
| Source file / checksum | demo/derived/cam01-repeat33.mp4 sha256 03950b88b05d486a350199824b6898597e4f4ff020a2f89bf4a93649b59deafc |
| Source resolution / codec / FPS | {'codec': 'h264', 'profile': 'High', 'width': 1920, 'height': 1080, 'fps': '30000/1001', 'frames': 23430, 'duration_s': 782.09} |
| Replay pacing mode | paced real-time demo profile (PRD 18.3): producer paced to the source timeline with a 200 ms lead; the backend releases each frame at its source time |
| Configured analytics FPS | 8 per camera (gate 7.2) |
| Measured analytics FPS, per camera | per step below (D: backend-consumed; A-C: producer) |
| Detector / revision | rt-detr-person-candidate (candidate_unaccepted) |
| Configuration profile | configs/a6000 |
| Detector precision / type policy | FP16 TensorRT engine (models/rt-detr-person-candidate/rt_detr_r18vd_raw_fp16_sm86.engine) |
| Tracker / revision | NvDCF, models.yaml tracker identity of the profile (stages C, D) |
| Calibration / config revisions | configs tree sha256 7edd067e5aaabd0cb64126277b3cf9e2991e591189b3bfb43395a0c34a7328fa; no calibration (replicas carry no facility claims) |
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
| Bottleneck / next action | all ladder steps passed |

## Steps

| Step | Pass | Feeds: cadence fps (gated basis) | p95 ms (gated basis) | Backlog growth ms | Missing deliveries | GPU util p95 | VRAM max MiB | GPU temp max C | Host avail min MiB | Cleanup | Failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| stageD-1feeds-r1 | True | cam-01: 29.958 | 230.361 | 0.0 | 0 | 7.0 | 918.0 | 69.0 | 41289.7 | True | - |
| stageD-2feeds-r1 | True | cam-01: 29.958, cam-02: 29.958 | 234.589, 230.453 | -2.7, 0.0 | 0, 0 | 17.0 | 1429.0 | 71.0 | 40743.9 | True | - |
| stageD-4feeds-r1 | True | cam-01: 29.958, cam-02: 29.958, cam-03: 29.958, cam-04: 29.958 | 299.57, 290.029, 283.566, 288.297 | -0.1, 0.0, 0.0, 0.0 | 0, 0, 0, 0 | 47.0 | 2450.0 | 80.0 | 39461.7 | True | - |
| stageD-6feeds-r1 | True | cam-01: 29.957, cam-02: 29.958, cam-03: 29.957, cam-04: 29.958, cam-05: 29.958, cam-06: 29.958 | 490.15, 505.719, 480.269, 496.993, 517.283, 457.343 | 1.0, 0.7, 1.1, 1.1, 1.2, 1.1 | 0, 0, 0, 0, 0, 0 | 83.0 | 3471.0 | 86.0 | 38193.8 | True | - |
| stageD-8feeds-r1 | True | cam-01: 29.557, cam-02: 29.897, cam-03: 29.63, cam-04: 29.872, cam-05: 29.808, cam-06: 29.672, cam-07: 29.802, cam-08: 29.797 | 678.098, 676.619, 653.078, 708.955, 682.266, 694.215, 728.451, 713.599 | 2.6, 1.6, 1.6, 3.0, -0.4, -153.0, 1.4, -33.4 | 0, 0, 0, 0, 0, 0, 0, 0 | 99.0 | 4492.0 | 86.0 | 36886.5 | True | - |

## Across repeats (one worst-feed figure per repeat)

- **D-1feeds**: 1/1 passed; min_feed_cadence_fps mean 29.958 sd None [29.958, 29.958]; max_feed_p95_ms mean 230.361 sd None [230.361, 230.361]; max_feed_backlog_growth_ms mean 0.0 sd None [0.0, 0.0]; gpu_util_p95 mean 7.0 sd None [7.0, 7.0]
- **D-2feeds**: 1/1 passed; min_feed_cadence_fps mean 29.958 sd None [29.958, 29.958]; max_feed_p95_ms mean 234.589 sd None [234.589, 234.589]; max_feed_backlog_growth_ms mean 0.0 sd None [0.0, 0.0]; gpu_util_p95 mean 17.0 sd None [17.0, 17.0]
- **D-4feeds**: 1/1 passed; min_feed_cadence_fps mean 29.958 sd None [29.958, 29.958]; max_feed_p95_ms mean 299.57 sd None [299.57, 299.57]; max_feed_backlog_growth_ms mean 0.0 sd None [0.0, 0.0]; gpu_util_p95 mean 47.0 sd None [47.0, 47.0]
- **D-6feeds**: 1/1 passed; min_feed_cadence_fps mean 29.957 sd None [29.957, 29.957]; max_feed_p95_ms mean 517.283 sd None [517.283, 517.283]; max_feed_backlog_growth_ms mean 1.2 sd None [1.2, 1.2]; gpu_util_p95 mean 83.0 sd None [83.0, 83.0]
- **D-8feeds**: 1/1 passed; min_feed_cadence_fps mean 29.557 sd None [29.557, 29.557]; max_feed_p95_ms mean 728.451 sd None [728.451, 728.451]; max_feed_backlog_growth_ms mean 3.0 sd None [3.0, 3.0]; gpu_util_p95 mean 99.0 sd None [99.0, 99.0]

## Ladder

```json
{
 "last_acceptable_feeds": 8,
 "stop": null,
 "unattempted": []
}
```

## Not run

- Stage E: calibrated overlap fusion needs calibrated overlapping footage
- Stage F: selective Re-ID is not implemented/enabled
- Stage G: needs the accepted core profile (detector accuracy unaccepted); the soak plan exercises dashboard/preview/history/evidence under the provisional profile
- Stage H: optional advanced AI out of scope

## Disclosures

- Competing non-project host workloads are recorded in environment.competing_host_processes_top_rss and were not stopped.
