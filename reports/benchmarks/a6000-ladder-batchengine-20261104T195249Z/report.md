# Capacity report a6000-ladder-batchengine-20261104T195249Z

**Result: COMPLETE** - 2/3 executed steps passed, 2 of 5 planned steps not executed (deviations: not acceptance-eligible)

> LABELLED DUPLICATE-INPUT LOAD TEST. Every feed is an independent decoder instance of the same repeated demo clip. This measures load only; it does not establish physically distinct camera coverage, counting accuracy, or calibrated facility acceptance. The detector profile is provisional (not accuracy-accepted).

| PRD 19.4 field | Value |
| --- | --- |
| Run ID | a6000-ladder-batchengine-20261104T195249Z |
| Timestamp | 2026-11-04T19:52:52.153378+00:00 -> 2026-11-04T20:37:40.223888+00:00 |
| GPU | NVIDIA RTX A6000, 596.71, 49140 MiB, 300.00 W |
| GPU identity verified by | nvidia-smi --query-gpu (host) |
| Driver / CUDA / TensorRT / DeepStream | driver 596.71; DeepStream 9.2 image (TensorRT/CUDA inside the pinned image) |
| Host / WSL / container image digest | Windows-10-10.0.19045-SP0; route windows-wsl; ghcr.io/nvidia/deepstream@sha256:092818f8142d82c4611c9e96eb589a975cd928baa3e15a92f8f67b0ff6fcdb85 |
| Workload stage(s) | D |
| Number of feeds | 24, 28, 32 |
| Source file / checksum | demo/derived/cam01-repeat33.mp4 sha256 03950b88b05d486a350199824b6898597e4f4ff020a2f89bf4a93649b59deafc |
| Source resolution / codec / FPS | {'codec': 'h264', 'profile': 'High', 'width': 1920, 'height': 1080, 'fps': '30000/1001', 'frames': 23430, 'duration_s': 782.09} |
| Replay pacing mode | paced real-time demo profile (PRD 18.3): producer paced to the source timeline with a 200 ms lead; the backend releases each frame at its source time |
| Configured analytics FPS | 8 per camera (gate 7.2) |
| Analysis frame interval | every 4th decoded frame (decoder drop-frame-interval) |
| Cameras per pipeline (stage D) | up to 4 file cameras batched per DeepStream pipeline (one container each) |
| Measured analytics FPS, per camera | per step below (D: backend-consumed; A-C: producer) |
| Detector / revision | rt-detr-person-candidate (candidate_unaccepted) |
| Configuration profile | configs/a6000 |
| Detector precision / type policy | FP16 TensorRT engine (models/rt-detr-person-candidate/rt_detr_r18vd_raw_fp16_sm86.engine) |
| Tracker / revision | NvDCF, models.yaml tracker identity of the profile (stages C, D) |
| Calibration / config revisions | configs tree sha256 9948bafbb90da055f58d8172fee77f605f9890e86a81636529c3415b272a1eda; no calibration (replicas carry no facility claims) |
| Re-ID enabled / update cadence | disabled (stage F not run) |
| Preview / recording settings | not exercised (stage G not run) |
| Inference latency p50/p95 | unavailable: the pipeline exposes no per-frame inference timer; receive-to-result and schedule lag reported instead |
| Processing/frame age basis | stage D only: backend analytics_done minus backend stdout read time, same host clock, over every produced frame in the window; a produced frame never consumed is a missing delivery (fail), never excluded / stages A-C (no consumer): producer emit time minus the frame's due time on the source timeline anchored at the feed's first frame; negative = the deliberate 200 ms lead. A real-time keep-up criterion, NOT receive-to-result latency |
| Capture-to-result age | not measured: file replay has no capture clock |
| Intentional sampling | every 4th decoded frame is processed (29.97 / 4 = 7.49 fps); the decoder drops the rest before inference |
| Warm-up duration | 120 s |
| Test duration | 600 s per step |
| Accuracy/reference result links | none: performance-only load run (no labels used) |
| Errors / missing counters | per-frame inference latency (no pipeline timer); capture-to-result age (no capture clock) |
| Acceptance result | COMPLETE |
| Bottleneck / next action | stopped at 32 feeds: ['cam-17: no producer frames in the measured window', 'cam-18: no producer frames in the measured window', 'cam-19: no producer frames in the measured window', 'cam-20: no producer frames in the measured window', 'cam-21: no producer frames in the measured window', 'cam-22: no producer frames in the measured window', 'cam-23: no producer frames in the measured window', 'cam-24: no producer frames in the measured window', 'cam-25: no producer frames in the measured window', 'cam-26: no producer frames in the measured window', 'cam-27: no producer frames in the measured window', 'cam-28: no producer frames in the measured window', 'cam-29: no producer frames in the measured window', 'cam-30: no producer frames recorded', 'cam-31: no producer frames in the measured window', 'cam-32: no producer frames in the measured window'] |

## Steps

| Step | Pass | Feeds: cadence fps (gated basis) | p95 ms (gated basis) | Backlog growth ms | Missing deliveries | GPU util p95 | VRAM max MiB | GPU temp max C | Host avail min MiB | Cleanup | Failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| stageD-24feeds-r1 | True | cam-01: 7.488, cam-02: 7.488, cam-03: 7.487, cam-04: 7.487, cam-05: 7.488, cam-06: 7.487, cam-07: 7.487, cam-08: 7.487, cam-09: 7.488, cam-10: 7.487, cam-11: 7.487, cam-12: 7.488, cam-13: 7.488, cam-14: 7.487, cam-15: 7.487, cam-16: 7.488, cam-17: 7.49, cam-18: 7.49, cam-19: 7.49, cam-20: 7.49, cam-21: 7.49, cam-22: 7.49, cam-23: 7.49, cam-24: 7.49 | 106.899, 263.634, 33.738, 30.569, 265.417, 31.668, 28.879, 26.193, 354.019, 33.69, 30.159, 336.596, 248.275, 30.569, 27.586, 231.674, 233.348, 234.354, 248.549, 248.546, 244.345, 254.996, 253.295, 248.968 | -1.5, -0.5, 1.0, 0.9, -0.5, 1.5, 1.5, 1.9, 1.4, 1.0, 1.7, -0.2, 0.0, 0.7, 1.4, -1.2, 1.4, 1.7, 0.2, 0.8, -0.5, -0.2, 0.0, 0.4 | 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 | 50.0 | 7299.0 | 84.0 | 39121.2 | True | - |
| stageD-28feeds-r1 | True | cam-01: 7.49, cam-02: 7.49, cam-03: 7.49, cam-04: 7.49, cam-05: 7.49, cam-06: 7.488, cam-07: 7.49, cam-08: 7.49, cam-09: 7.49, cam-10: 7.49, cam-11: 7.49, cam-12: 7.49, cam-13: 7.49, cam-14: 7.49, cam-15: 7.49, cam-16: 7.49, cam-17: 7.49, cam-18: 7.49, cam-19: 7.49, cam-20: 7.49, cam-21: 7.49, cam-22: 7.49, cam-23: 7.49, cam-24: 7.49, cam-25: 7.49, cam-26: 7.49, cam-27: 7.49, cam-28: 7.49 | 369.926, 364.197, 357.222, 378.068, 385.093, 378.145, 370.053, 365.702, 377.984, 368.931, 361.272, 358.041, 369.56, 364.926, 356.426, 373.961, 353.328, 354.264, 371.098, 363.099, 348.534, 346.77, 342.859, 359.114, 340.413, 354.109, 355.26, 355.435 | -0.2, 0.7, 0.1, 0.8, 0.3, 1.0, 0.1, -0.6, 0.2, 0.4, 0.4, 0.1, 0.8, 1.2, -0.3, 0.8, -0.5, -0.3, -0.4, 0.5, -1.4, -0.5, -0.9, -1.1, 0.1, -0.3, 0.5, -0.8 | 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 | 61.0 | 8335.0 | 86.0 | 38363.9 | True | - |
| stageD-32feeds-r1 | False | cam-01: 7.492, cam-02: 7.492, cam-03: 7.492, cam-04: 7.49, cam-05: 7.492, cam-06: 7.493, cam-07: 7.492, cam-08: 7.492, cam-09: 7.492, cam-10: 7.49, cam-11: 7.49, cam-12: 7.49, cam-13: 7.49, cam-14: 7.49, cam-15: 7.492, cam-16: 7.49, cam-17: 0.0, cam-18: 0.0, cam-19: 0.0, cam-20: 0.0, cam-21: 0.0, cam-22: 0.0, cam-23: 0.0, cam-24: 0.0, cam-25: 0.0, cam-26: 0.0, cam-27: 0.0, cam-28: 0.0, cam-29: None, cam-30: None, cam-31: None, cam-32: None | 223.019, 219.503, 212.958, 232.376, 215.383, 208.826, 227.759, 221.384, 217.815, 236.608, 230.991, 227.433, 211.893, 210.894, 204.945, 222.491, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None | -0.9, 0.9, -1.2, 0.7, 2.4, 0.7, 1.2, -2.1, -3.3, 0.5, -1.1, 0.2, 0.3, 1.9, 0.8, -1.0, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None | 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, None, None, None, None | 38.0 | 8311.0 | 79.0 | 38167.0 | True | cam-17: no producer frames in the measured window; cam-18: no producer frames in the measured window; cam-19: no producer frames in the measured window; cam-20: no producer frames in the measured window |

## Across repeats (one worst-feed figure per repeat)

- **D-24feeds**: 1/1 passed; min_feed_cadence_fps mean 7.487 sd None [7.487, 7.487]; max_feed_p95_ms mean 354.019 sd None [354.019, 354.019]; max_feed_backlog_growth_ms mean 1.9 sd None [1.9, 1.9]; gpu_util_p95 mean 50.0 sd None [50.0, 50.0]
- **D-28feeds**: 1/1 passed; min_feed_cadence_fps mean 7.488 sd None [7.488, 7.488]; max_feed_p95_ms mean 385.093 sd None [385.093, 385.093]; max_feed_backlog_growth_ms mean 1.2 sd None [1.2, 1.2]; gpu_util_p95 mean 61.0 sd None [61.0, 61.0]
- **D-32feeds**: 0/1 passed; min_feed_cadence_fps mean 0.0 sd None [0.0, 0.0]; max_feed_p95_ms mean 236.608 sd None [236.608, 236.608]; max_feed_backlog_growth_ms mean 2.4 sd None [2.4, 2.4]; gpu_util_p95 mean 38.0 sd None [38.0, 38.0]

## Ladder

```json
{
 "last_acceptable_feeds": 28,
 "stop": {
  "feeds": 32,
  "reason": [
   "cam-17: no producer frames in the measured window",
   "cam-18: no producer frames in the measured window",
   "cam-19: no producer frames in the measured window",
   "cam-20: no producer frames in the measured window",
   "cam-21: no producer frames in the measured window",
   "cam-22: no producer frames in the measured window",
   "cam-23: no producer frames in the measured window",
   "cam-24: no producer frames in the measured window",
   "cam-25: no producer frames in the measured window",
   "cam-26: no producer frames in the measured window",
   "cam-27: no producer frames in the measured window",
   "cam-28: no producer frames in the measured window",
   "cam-29: no producer frames in the measured window",
   "cam-30: no producer frames recorded",
   "cam-31: no producer frames in the measured window",
   "cam-32: no producer frames in the measured window"
  ],
  "kind": "first_unacceptable"
 },
 "unattempted": [
  36,
  40
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
