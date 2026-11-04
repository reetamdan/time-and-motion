# Capacity report diag-16-batched

**Result: PASS** - 1/1 executed steps passed, 0 of 1 planned steps not executed (deviations: not acceptance-eligible)

> LABELLED DUPLICATE-INPUT LOAD TEST. Every feed is an independent decoder instance of the same repeated demo clip. This measures load only; it does not establish physically distinct camera coverage, counting accuracy, or calibrated facility acceptance. The detector profile is provisional (not accuracy-accepted).

| PRD 19.4 field | Value |
| --- | --- |
| Run ID | diag-16-batched |
| Timestamp | 2026-11-04T18:14:24.177995+00:00 -> 2026-11-04T18:17:35.341706+00:00 |
| GPU | NVIDIA RTX A6000, 596.71, 49140 MiB, 300.00 W |
| GPU identity verified by | nvidia-smi --query-gpu (host) |
| Driver / CUDA / TensorRT / DeepStream | driver 596.71; DeepStream 9.2 image (TensorRT/CUDA inside the pinned image) |
| Host / WSL / container image digest | Windows-10-10.0.19045-SP0; route windows-wsl; ghcr.io/nvidia/deepstream@sha256:092818f8142d82c4611c9e96eb589a975cd928baa3e15a92f8f67b0ff6fcdb85 |
| Workload stage(s) | D |
| Number of feeds | 16 |
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
| Warm-up duration | 30.0 s |
| Test duration | 90.0 s per step |
| Accuracy/reference result links | none: performance-only load run (no labels used) |
| Errors / missing counters | per-frame inference latency (no pipeline timer); capture-to-result age (no capture clock) |
| Acceptance result | PASS |
| Bottleneck / next action | see steps |

## Steps

| Step | Pass | Feeds: cadence fps (gated basis) | p95 ms (gated basis) | Backlog growth ms | Missing deliveries | GPU util p95 | VRAM max MiB | GPU temp max C | Host avail min MiB | Cleanup | Failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| stageD-16feeds-r1 | True | cam-01: 7.5, cam-02: 7.5, cam-03: 7.5, cam-04: 7.5, cam-05: 7.5, cam-06: 7.5, cam-07: 7.5, cam-08: 7.5, cam-09: 7.511, cam-10: 7.5, cam-11: 7.5, cam-12: 7.5, cam-13: 7.5, cam-14: 7.5, cam-15: 7.5, cam-16: 7.5 | 984.495, 968.92, 1017.826, 1029.073, 944.242, 953.382, 980.765, 970.379, 833.315, 1086.111, 1077.447, 1058.124, 1008.941, 1005.526, 978.437, 993.084 | 0.0, 0.1, 0.7, -0.1, -0.6, 0.3, 0.1, 0.3, 0.9, -0.2, 0.5, 0.5, -0.7, -0.1, -0.2, 0.3 | 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 | 65.0 | 3518.0 | 80.0 | 41640.6 | True | - |

## Across repeats (one worst-feed figure per repeat)

- **D-16feeds**: 1/1 passed; min_feed_cadence_fps mean 7.5 sd None [7.5, 7.5]; max_feed_p95_ms mean 1086.111 sd None [1086.111, 1086.111]; max_feed_backlog_growth_ms mean 0.9 sd None [0.9, 0.9]; gpu_util_p95 mean 65.0 sd None [65.0, 65.0]

## Not run

- Stage E: calibrated overlap fusion needs calibrated overlapping footage
- Stage F: selective Re-ID is not implemented/enabled
- Stage G: needs the accepted core profile (detector accuracy unaccepted); the soak plan exercises dashboard/preview/history/evidence under the provisional profile
- Stage H: optional advanced AI out of scope

## Disclosures

- Competing non-project host workloads are recorded in environment.competing_host_processes_top_rss and were not stopped.
