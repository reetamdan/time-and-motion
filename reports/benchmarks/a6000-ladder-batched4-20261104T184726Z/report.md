# Capacity report a6000-ladder-batched4-20261104T184726Z

**Result: COMPLETE** - 0/1 executed steps passed, 2 of 3 planned steps not executed (deviations: not acceptance-eligible)

> LABELLED DUPLICATE-INPUT LOAD TEST. Every feed is an independent decoder instance of the same repeated demo clip. This measures load only; it does not establish physically distinct camera coverage, counting accuracy, or calibrated facility acceptance. The detector profile is provisional (not accuracy-accepted).

| PRD 19.4 field | Value |
| --- | --- |
| Run ID | a6000-ladder-batched4-20261104T184726Z |
| Timestamp | 2026-11-04T18:47:29.033300+00:00 -> 2026-11-04T19:02:34.756792+00:00 |
| GPU | NVIDIA RTX A6000, 596.71, 49140 MiB, 300.00 W |
| GPU identity verified by | nvidia-smi --query-gpu (host) |
| Driver / CUDA / TensorRT / DeepStream | driver 596.71; DeepStream 9.2 image (TensorRT/CUDA inside the pinned image) |
| Host / WSL / container image digest | Windows-10-10.0.19045-SP0; route windows-wsl; ghcr.io/nvidia/deepstream@sha256:092818f8142d82c4611c9e96eb589a975cd928baa3e15a92f8f67b0ff6fcdb85 |
| Workload stage(s) | D |
| Number of feeds | 24 |
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
| Acceptance result | COMPLETE |
| Bottleneck / next action | stopped at 24 feeds: ['cam-01: p95 receive-to-result 2056.387 ms > 2000', 'cam-02: p95 receive-to-result 2199.215 ms > 2000', 'cam-03: p95 receive-to-result 2305.161 ms > 2000', 'cam-04: p95 receive-to-result 2302.656 ms > 2000', 'cam-05: p95 receive-to-result 2046.808 ms > 2000', 'cam-06: p95 receive-to-result 2055.846 ms > 2000', 'cam-07: p95 receive-to-result 2188.965 ms > 2000', 'cam-08: p95 receive-to-result 2148.615 ms > 2000', 'cam-09: p95 receive-to-result 2163.769 ms > 2000', 'cam-10: p95 receive-to-result 2146.482 ms > 2000', 'cam-11: p95 receive-to-result 2148.986 ms > 2000', 'cam-12: p95 receive-to-result 2228.309 ms > 2000', 'cam-14: p95 receive-to-result 2271.442 ms > 2000', 'cam-15: p95 receive-to-result 2265.198 ms > 2000', 'cam-16: p95 receive-to-result 2249.181 ms > 2000', 'cam-17: p95 receive-to-result 2209.142 ms > 2000', 'cam-18: p95 receive-to-result 2193.68 ms > 2000', 'cam-19: p95 receive-to-result 2297.445 ms > 2000', 'cam-20: p95 receive-to-result 2256.079 ms > 2000', 'cam-21: p95 receive-to-result 2175.701 ms > 2000', 'cam-22: p95 receive-to-result 2187.272 ms > 2000', 'cam-23: p95 receive-to-result 2271.785 ms > 2000', 'cam-24: p95 receive-to-result 2217.992 ms > 2000'] |

## Steps

| Step | Pass | Feeds: cadence fps (gated basis) | p95 ms (gated basis) | Backlog growth ms | Missing deliveries | GPU util p95 | VRAM max MiB | GPU temp max C | Host avail min MiB | Cleanup | Failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| stageD-24feeds-r1 | False | cam-01: 7.485, cam-02: 7.485, cam-03: 7.485, cam-04: 7.485, cam-05: 7.483, cam-06: 7.483, cam-07: 7.483, cam-08: 7.483, cam-09: 7.483, cam-10: 7.483, cam-11: 7.483, cam-12: 7.483, cam-13: 7.485, cam-14: 7.485, cam-15: 7.485, cam-16: 7.485, cam-17: 7.488, cam-18: 7.488, cam-19: 7.488, cam-20: 7.488, cam-21: 7.49, cam-22: 7.49, cam-23: 7.49, cam-24: 7.49 | 2056.387, 2199.215, 2305.161, 2302.656, 2046.808, 2055.846, 2188.965, 2148.615, 2163.769, 2146.482, 2148.986, 2228.309, 1989.965, 2271.442, 2265.198, 2249.181, 2209.142, 2193.68, 2297.445, 2256.079, 2175.701, 2187.272, 2271.785, 2217.992 | -8.9, 6.0, -28.9, 14.8, -25.5, 3.0, 22.6, -0.3, 19.4, 23.9, 41.6, -2.2, 36.7, -29.0, -29.2, -26.1, -18.3, -13.8, -6.0, 9.6, -32.0, -30.5, -10.5, -6.3 | 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 | 90.0 | 4957.0 | 85.0 | 39880.0 | True | cam-01: p95 receive-to-result 2056.387 ms > 2000; cam-02: p95 receive-to-result 2199.215 ms > 2000; cam-03: p95 receive-to-result 2305.161 ms > 2000; cam-04: p95 receive-to-result 2302.656 ms > 2000 |

## Across repeats (one worst-feed figure per repeat)

- **D-24feeds**: 0/1 passed; min_feed_cadence_fps mean 7.483 sd None [7.483, 7.483]; max_feed_p95_ms mean 2305.161 sd None [2305.161, 2305.161]; max_feed_backlog_growth_ms mean 41.6 sd None [41.6, 41.6]; gpu_util_p95 mean 90.0 sd None [90.0, 90.0]

## Ladder

```json
{
 "last_acceptable_feeds": null,
 "stop": {
  "feeds": 24,
  "reason": [
   "cam-01: p95 receive-to-result 2056.387 ms > 2000",
   "cam-02: p95 receive-to-result 2199.215 ms > 2000",
   "cam-03: p95 receive-to-result 2305.161 ms > 2000",
   "cam-04: p95 receive-to-result 2302.656 ms > 2000",
   "cam-05: p95 receive-to-result 2046.808 ms > 2000",
   "cam-06: p95 receive-to-result 2055.846 ms > 2000",
   "cam-07: p95 receive-to-result 2188.965 ms > 2000",
   "cam-08: p95 receive-to-result 2148.615 ms > 2000",
   "cam-09: p95 receive-to-result 2163.769 ms > 2000",
   "cam-10: p95 receive-to-result 2146.482 ms > 2000",
   "cam-11: p95 receive-to-result 2148.986 ms > 2000",
   "cam-12: p95 receive-to-result 2228.309 ms > 2000",
   "cam-14: p95 receive-to-result 2271.442 ms > 2000",
   "cam-15: p95 receive-to-result 2265.198 ms > 2000",
   "cam-16: p95 receive-to-result 2249.181 ms > 2000",
   "cam-17: p95 receive-to-result 2209.142 ms > 2000",
   "cam-18: p95 receive-to-result 2193.68 ms > 2000",
   "cam-19: p95 receive-to-result 2297.445 ms > 2000",
   "cam-20: p95 receive-to-result 2256.079 ms > 2000",
   "cam-21: p95 receive-to-result 2175.701 ms > 2000",
   "cam-22: p95 receive-to-result 2187.272 ms > 2000",
   "cam-23: p95 receive-to-result 2271.785 ms > 2000",
   "cam-24: p95 receive-to-result 2217.992 ms > 2000"
  ],
  "kind": "first_unacceptable"
 },
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
