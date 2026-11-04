# Capacity report a6000-ladder-batched4-20261104T181821Z

**Result: INTERRUPTED** - 2/3 executed steps passed, 2 of 5 planned steps not executed (deviations: not acceptance-eligible)

> LABELLED DUPLICATE-INPUT LOAD TEST. Every feed is an independent decoder instance of the same repeated demo clip. This measures load only; it does not establish physically distinct camera coverage, counting accuracy, or calibrated facility acceptance. The detector profile is provisional (not accuracy-accepted).

| PRD 19.4 field | Value |
| --- | --- |
| Run ID | a6000-ladder-batched4-20261104T181821Z |
| Timestamp | 2026-11-04T18:18:23.738702+00:00 -> 2026-11-04T18:46:16.362892+00:00 |
| GPU | NVIDIA RTX A6000, 596.71, 49140 MiB, 300.00 W |
| GPU identity verified by | nvidia-smi --query-gpu (host) |
| Driver / CUDA / TensorRT / DeepStream | driver 596.71; DeepStream 9.2 image (TensorRT/CUDA inside the pinned image) |
| Host / WSL / container image digest | Windows-10-10.0.19045-SP0; route windows-wsl; ghcr.io/nvidia/deepstream@sha256:092818f8142d82c4611c9e96eb589a975cd928baa3e15a92f8f67b0ff6fcdb85 |
| Workload stage(s) | D |
| Number of feeds | 16, 20, 24 |
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
| Calibration / config revisions | configs tree sha256 ffde8051cfbda74e2f986a837e063151267256694838dc8c5914582a28e32641; no calibration (replicas carry no facility claims) |
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
| Acceptance result | INTERRUPTED |
| Bottleneck / next action | all ladder steps passed |

## Steps

| Step | Pass | Feeds: cadence fps (gated basis) | p95 ms (gated basis) | Backlog growth ms | Missing deliveries | GPU util p95 | VRAM max MiB | GPU temp max C | Host avail min MiB | Cleanup | Failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| stageD-16feeds-r1 | True | cam-01: 7.5, cam-02: 7.5, cam-03: 7.5, cam-04: 7.5, cam-05: 7.5, cam-06: 7.5, cam-07: 7.5, cam-08: 7.5, cam-09: 7.5, cam-10: 7.5, cam-11: 7.5, cam-12: 7.498, cam-13: 7.5, cam-14: 7.5, cam-15: 7.5, cam-16: 7.5 | 1069.932, 1088.878, 1121.215, 1109.933, 1134.156, 1155.922, 1159.803, 1153.361, 1130.874, 1131.4, 1150.504, 1149.175, 1037.035, 1047.592, 1052.596, 1048.065 | -0.3, -0.7, -0.2, 0.5, -0.3, -0.5, -1.0, -0.6, 0.3, 1.3, 0.9, 0.6, 1.1, -0.7, -1.5, 0.3 | 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 | 65.0 | 3518.0 | 82.0 | 41684.9 | True | - |
| stageD-20feeds-r1 | True | cam-01: 7.488, cam-02: 7.49, cam-03: 7.488, cam-04: 7.49, cam-05: 7.49, cam-06: 7.49, cam-07: 7.49, cam-08: 7.49, cam-09: 7.49, cam-10: 7.49, cam-11: 7.49, cam-12: 7.49, cam-13: 7.49, cam-14: 7.49, cam-15: 7.49, cam-16: 7.49, cam-17: 7.497, cam-18: 7.497, cam-19: 7.497, cam-20: 7.497 | 1492.09, 1670.281, 1581.449, 1703.103, 1523.691, 1521.849, 1577.256, 1568.489, 1562.411, 1549.421, 1583.908, 1583.092, 1529.304, 1528.357, 1519.316, 1503.126, 1513.44, 1491.99, 1550.45, 1534.872 | 1.4, 2.0, 1.6, 1.5, 1.9, 2.4, 2.0, 1.7, 2.2, 1.7, 0.2, 1.8, 1.2, 0.8, 1.2, 1.8, 1.4, 0.6, 1.0, -0.2 | 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 | 78.0 | 4237.0 | 85.0 | 40780.7 | True | - |
| stageD-24feeds-r1 | False |  |  |  |  | None | None | None | None | False | step interrupted: exception; RuntimeError: backend exited 1 before becoming live; demo start HTTP None; demo stop HTTP None, run_status None |

## Across repeats (one worst-feed figure per repeat)

- **D-16feeds**: 1/1 passed; min_feed_cadence_fps mean 7.498 sd None [7.498, 7.498]; max_feed_p95_ms mean 1159.803 sd None [1159.803, 1159.803]; max_feed_backlog_growth_ms mean 1.3 sd None [1.3, 1.3]; gpu_util_p95 mean 65.0 sd None [65.0, 65.0]
- **D-20feeds**: 1/1 passed; min_feed_cadence_fps mean 7.488 sd None [7.488, 7.488]; max_feed_p95_ms mean 1703.103 sd None [1703.103, 1703.103]; max_feed_backlog_growth_ms mean 2.4 sd None [2.4, 2.4]; gpu_util_p95 mean 78.0 sd None [78.0, 78.0]
- **D-24feeds**: 0/1 passed; 

## Ladder

```json
{
 "last_acceptable_feeds": 20,
 "stop": null,
 "unattempted": [
  28,
  32
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
