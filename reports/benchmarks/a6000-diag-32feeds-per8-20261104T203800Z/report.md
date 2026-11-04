# Capacity report diag-32-per8

**Result: FAIL** - 0/1 executed steps passed, 0 of 1 planned steps not executed (deviations: not acceptance-eligible)

> LABELLED DUPLICATE-INPUT LOAD TEST. Every feed is an independent decoder instance of the same repeated demo clip. This measures load only; it does not establish physically distinct camera coverage, counting accuracy, or calibrated facility acceptance. The detector profile is provisional (not accuracy-accepted).

| PRD 19.4 field | Value |
| --- | --- |
| Run ID | diag-32-per8 |
| Timestamp | 2026-11-04T20:39:04.430019+00:00 -> 2026-11-04T20:45:45.499770+00:00 |
| GPU | NVIDIA RTX A6000, 596.71, 49140 MiB, 300.00 W |
| GPU identity verified by | nvidia-smi --query-gpu (host) |
| Driver / CUDA / TensorRT / DeepStream | driver 596.71; DeepStream 9.2 image (TensorRT/CUDA inside the pinned image) |
| Host / WSL / container image digest | Windows-10-10.0.19045-SP0; route windows-wsl; ghcr.io/nvidia/deepstream@sha256:092818f8142d82c4611c9e96eb589a975cd928baa3e15a92f8f67b0ff6fcdb85 |
| Workload stage(s) | D |
| Number of feeds | 32 |
| Source file / checksum | demo/derived/cam01-repeat33.mp4 sha256 03950b88b05d486a350199824b6898597e4f4ff020a2f89bf4a93649b59deafc |
| Source resolution / codec / FPS | {'codec': 'h264', 'profile': 'High', 'width': 1920, 'height': 1080, 'fps': '30000/1001', 'frames': 23430, 'duration_s': 782.09} |
| Replay pacing mode | paced real-time demo profile (PRD 18.3): producer paced to the source timeline with a 200 ms lead; the backend releases each frame at its source time |
| Configured analytics FPS | 8 per camera (gate 7.2) |
| Analysis frame interval | every 4th decoded frame (decoder drop-frame-interval) |
| Cameras per pipeline (stage D) | up to 8 file cameras batched per DeepStream pipeline (one container each) |
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
| Warm-up duration | 60.0 s |
| Test duration | 120.0 s per step |
| Accuracy/reference result links | none: performance-only load run (no labels used) |
| Errors / missing counters | per-frame inference latency (no pipeline timer); capture-to-result age (no capture clock) |
| Acceptance result | FAIL |
| Bottleneck / next action | see steps |

## Steps

| Step | Pass | Feeds: cadence fps (gated basis) | p95 ms (gated basis) | Backlog growth ms | Missing deliveries | GPU util p95 | VRAM max MiB | GPU temp max C | Host avail min MiB | Cleanup | Failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| stageD-32feeds-r1 | False | cam-01: 7.483, cam-02: 7.483, cam-03: 7.492, cam-04: 7.492, cam-05: 7.492, cam-06: 7.492, cam-07: 7.492, cam-08: 7.483, cam-09: 7.483, cam-10: 7.483, cam-11: 7.483, cam-12: 7.483, cam-13: 7.483, cam-14: 7.483, cam-15: 7.492, cam-16: 7.483, cam-17: 7.492, cam-18: 7.492, cam-19: 7.492, cam-20: 7.492, cam-21: 7.492, cam-22: 7.492, cam-23: 7.492, cam-24: 7.492, cam-25: None, cam-26: None, cam-27: None, cam-28: None, cam-29: None, cam-30: None, cam-31: None, cam-32: None | 166.6, 164.311, 344.44, 341.038, 340.442, 336.073, 329.639, 179.964, 254.005, 253.522, 252.519, 251.845, 250.582, 280.83, 410.044, 270.003, 339.756, 338.195, 336.526, 183.037, 172.696, 296.534, 357.048, 349.334, None, None, None, None, None, None, None, None | 0.0, -1.6, 0.7, 0.5, 0.7, -0.2, 0.3, -0.4, 0.4, -0.2, 0.1, 0.4, 0.1, 0.0, 0.7, 0.8, 0.6, 0.5, 0.6, -0.4, -1.3, 0.2, 0.5, 0.3, None, None, None, None, None, None, None, None | 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, None, None, None, None, None, None, None, None | 43.0 | 4731.0 | 82.0 | 40289.3 | True | cam-25: no producer frames in the measured window; cam-26: no producer frames recorded; cam-27: no producer frames in the measured window; cam-28: no producer frames recorded |

## Across repeats (one worst-feed figure per repeat)

- **D-32feeds**: 0/1 passed; min_feed_cadence_fps mean 7.483 sd None [7.483, 7.483]; max_feed_p95_ms mean 410.044 sd None [410.044, 410.044]; max_feed_backlog_growth_ms mean 0.8 sd None [0.8, 0.8]; gpu_util_p95 mean 43.0 sd None [43.0, 43.0]

## Not run

- Stage E: calibrated overlap fusion needs calibrated overlapping footage
- Stage F: selective Re-ID is not implemented/enabled
- Stage G: needs the accepted core profile (detector accuracy unaccepted); the soak plan exercises dashboard/preview/history/evidence under the provisional profile
- Stage H: optional advanced AI out of scope

## Disclosures

- Competing non-project host workloads are recorded in environment.competing_host_processes_top_rss and were not stopped.
