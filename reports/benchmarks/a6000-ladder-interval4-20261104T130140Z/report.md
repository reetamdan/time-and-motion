# Capacity report a6000-ladder-interval4-20261104T130140Z

**Result: COMPLETE** - 2/3 executed steps passed, 3 of 6 planned steps not executed (deviations: not acceptance-eligible)

> LABELLED DUPLICATE-INPUT LOAD TEST. Every feed is an independent decoder instance of the same repeated demo clip. This measures load only; it does not establish physically distinct camera coverage, counting accuracy, or calibrated facility acceptance. The detector profile is provisional (not accuracy-accepted).

| PRD 19.4 field | Value |
| --- | --- |
| Run ID | a6000-ladder-interval4-20261104T130140Z |
| Timestamp | 2026-11-04T13:01:42.682238+00:00 -> 2026-11-04T13:45:10.756766+00:00 |
| GPU | NVIDIA RTX A6000, 596.71, 49140 MiB, 300.00 W |
| GPU identity verified by | nvidia-smi --query-gpu (host) |
| Driver / CUDA / TensorRT / DeepStream | driver 596.71; DeepStream 9.2 image (TensorRT/CUDA inside the pinned image) |
| Host / WSL / container image digest | Windows-10-10.0.19045-SP0; route windows-wsl; ghcr.io/nvidia/deepstream@sha256:092818f8142d82c4611c9e96eb589a975cd928baa3e15a92f8f67b0ff6fcdb85 |
| Workload stage(s) | D |
| Number of feeds | 8, 12, 16 |
| Source file / checksum | demo/derived/cam01-repeat33.mp4 sha256 03950b88b05d486a350199824b6898597e4f4ff020a2f89bf4a93649b59deafc |
| Source resolution / codec / FPS | {'codec': 'h264', 'profile': 'High', 'width': 1920, 'height': 1080, 'fps': '30000/1001', 'frames': 23430, 'duration_s': 782.09} |
| Replay pacing mode | paced real-time demo profile (PRD 18.3): producer paced to the source timeline with a 200 ms lead; the backend releases each frame at its source time |
| Configured analytics FPS | 8 per camera (gate 7.2) |
| Analysis frame interval | every 4th decoded frame (decoder drop-frame-interval) |
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
| Intentional sampling | every 4th decoded frame is processed (29.97 / 4 = 7.49 fps); the decoder drops the rest before inference |
| Warm-up duration | 120 s |
| Test duration | 600 s per step |
| Accuracy/reference result links | none: performance-only load run (no labels used) |
| Errors / missing counters | per-frame inference latency (no pipeline timer); capture-to-result age (no capture clock) |
| Acceptance result | COMPLETE |
| Bottleneck / next action | stopped at 16 feeds: ['cam-01: no producer frames recorded', 'cam-02: no producer frames recorded', 'cam-03: no producer frames recorded', 'cam-04: no producer frames in the measured window', 'cam-05: no producer frames recorded', 'cam-06: no producer frames recorded', 'cam-07: no producer frames recorded', 'cam-08: no producer frames recorded', 'cam-09: no producer frames recorded', 'cam-10: no producer frames recorded', 'cam-11: no producer frames in the measured window', 'cam-12: no producer frames recorded', 'cam-13: no producer frames recorded', 'cam-14: no producer frames recorded', 'cam-15: no producer frames recorded', 'cam-16: no producer frames recorded'] |

## Steps

| Step | Pass | Feeds: cadence fps (gated basis) | p95 ms (gated basis) | Backlog growth ms | Missing deliveries | GPU util p95 | VRAM max MiB | GPU temp max C | Host avail min MiB | Cleanup | Failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| stageD-8feeds-r1 | True | cam-01: 7.49, cam-02: 7.49, cam-03: 7.49, cam-04: 7.49, cam-05: 7.49, cam-06: 7.49, cam-07: 7.49, cam-08: 7.49 | 447.819, 429.83, 444.667, 430.512, 422.416, 438.314, 423.332, 420.795 | 3.4, 0.1, 2.9, 0.4, 0.3, 0.4, 0.0, 0.4 | 0, 0, 0, 0, 0, 0, 0, 0 | 39.0 | 4254.0 | 73.0 | 41822.2 | True | - |
| stageD-12feeds-r1 | True | cam-01: 7.492, cam-02: 7.492, cam-03: 7.492, cam-04: 7.492, cam-05: 7.492, cam-06: 7.492, cam-07: 7.492, cam-08: 7.492, cam-09: 7.492, cam-10: 7.492, cam-11: 7.492, cam-12: 7.493 | 755.359, 743.156, 747.865, 732.807, 736.291, 732.383, 755.315, 747.082, 756.446, 763.804, 740.418, 800.736 | 2.0, -0.4, 1.5, 0.0, 0.5, -3.3, 0.3, -1.6, 1.0, -2.8, -1.3, -0.5 | 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 | 58.0 | 6882.0 | 82.0 | 36825.2 | True | - |
| stageD-16feeds-r1 | False | cam-01: None, cam-02: None, cam-03: None, cam-04: None, cam-05: None, cam-06: None, cam-07: None, cam-08: None, cam-09: None, cam-10: None, cam-11: None, cam-12: None, cam-13: None, cam-14: None, cam-15: None, cam-16: None | None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None | None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None | None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None | 0.0 | 530.0 | 67.0 | 42581.7 | True | cam-01: no producer frames recorded; cam-02: no producer frames recorded; cam-03: no producer frames recorded; cam-04: no producer frames in the measured window |

## Across repeats (one worst-feed figure per repeat)

- **D-8feeds**: 1/1 passed; min_feed_cadence_fps mean 7.49 sd None [7.49, 7.49]; max_feed_p95_ms mean 447.819 sd None [447.819, 447.819]; max_feed_backlog_growth_ms mean 3.4 sd None [3.4, 3.4]; gpu_util_p95 mean 39.0 sd None [39.0, 39.0]
- **D-12feeds**: 1/1 passed; min_feed_cadence_fps mean 7.492 sd None [7.492, 7.492]; max_feed_p95_ms mean 800.736 sd None [800.736, 800.736]; max_feed_backlog_growth_ms mean 2.0 sd None [2.0, 2.0]; gpu_util_p95 mean 58.0 sd None [58.0, 58.0]
- **D-16feeds**: 0/1 passed; gpu_util_p95 mean 0.0 sd None [0.0, 0.0]

## Ladder

```json
{
 "last_acceptable_feeds": 12,
 "stop": {
  "feeds": 16,
  "reason": [
   "cam-01: no producer frames recorded",
   "cam-02: no producer frames recorded",
   "cam-03: no producer frames recorded",
   "cam-04: no producer frames in the measured window",
   "cam-05: no producer frames recorded",
   "cam-06: no producer frames recorded",
   "cam-07: no producer frames recorded",
   "cam-08: no producer frames recorded",
   "cam-09: no producer frames recorded",
   "cam-10: no producer frames recorded",
   "cam-11: no producer frames in the measured window",
   "cam-12: no producer frames recorded",
   "cam-13: no producer frames recorded",
   "cam-14: no producer frames recorded",
   "cam-15: no producer frames recorded",
   "cam-16: no producer frames recorded"
  ],
  "kind": "first_unacceptable"
 },
 "unattempted": [
  20,
  24,
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
