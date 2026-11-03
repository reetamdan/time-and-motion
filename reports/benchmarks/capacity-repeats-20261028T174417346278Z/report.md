# Capacity report capacity-repeats-20261028T174417346278Z

**Result: INTERRUPTED** - 1/1 executed steps passed, 11 of 12 planned steps not executed

> LABELLED DUPLICATE-INPUT LOAD TEST. Every feed is an independent decoder instance of the same repeated demo clip. This measures load only; it does not establish physically distinct camera coverage, counting accuracy, or calibrated facility acceptance. The detector profile is provisional (not accuracy-accepted).

| PRD 19.4 field | Value |
| --- | --- |
| Run ID | capacity-repeats-20261028T174417346278Z |
| Timestamp | 2026-10-28T17:44:17.776450+00:00 -> 2026-10-28T17:56:52.942126+00:00 |
| GPU | NVIDIA GeForce RTX 5070 Ti Laptop GPU, 610.47, 12227 MiB, [N/A] |
| GPU identity verified by | nvidia-smi --query-gpu (host) |
| Driver / CUDA / TensorRT / DeepStream | driver 610.47; DeepStream 9.2 image (TensorRT/CUDA inside the pinned image) |
| Host / WSL / container image digest | Windows-11-10.0.26200-SP0; route windows-wsl; ghcr.io/nvidia/deepstream@sha256:092818f8142d82c4611c9e96eb589a975cd928baa3e15a92f8f67b0ff6fcdb85 |
| Workload stage(s) | A |
| Number of feeds | 4 |
| Source file / checksum | demo/derived/cam01-repeat33.mp4 sha256 03950b88b05d486a350199824b6898597e4f4ff020a2f89bf4a93649b59deafc |
| Source resolution / codec / FPS | {'codec': 'h264', 'profile': 'High', 'width': 1920, 'height': 1080, 'fps': '30000/1001', 'frames': 23430, 'duration_s': 782.09} |
| Replay pacing mode | paced real-time demo profile (PRD 18.3): producer paced to the source timeline with a 200 ms lead; the backend releases each frame at its source time |
| Configured analytics FPS | 8 per camera (gate 7.2) |
| Measured analytics FPS, per camera | per step below (D: backend-consumed; A-C: producer) |
| Detector / revision | trafficcamnet-smoke (provisional_smoke) |
| Detector precision / type policy | FP16 TensorRT engine (configs/models.yaml) |
| Tracker / revision | NvDCF, configs/models.yaml tracker identity (stages C, D) |
| Calibration / config revisions | configs tree sha256 c646ccc8a3b458ce1fb4034ff75e7e0c2289e5b479aad3d28f4d7465d7df9f19; no calibration (replicas carry no facility claims) |
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
| stageA-4feeds-r1 | True | bench-01: 31.127, bench-02: 31.157, bench-03: 31.188, bench-04: 31.218 | -7570.429, -7563.32, -7568.036, -7571.385 | -25104.9, -25116.8, -25130.4, -25131.5 | n/a | 21.0 | 1490.0 | 58.0 | 5776.6 | True | - |

## Across repeats (one worst-feed figure per repeat)

- **A-4feeds**: 1/1 passed; min_feed_cadence_fps mean 31.127 sd None [31.127, 31.127]; max_feed_p95_ms mean -7563.32 sd None [-7563.32, -7563.32]; max_feed_backlog_growth_ms mean -25104.9 sd None [-25104.9, -25104.9]; gpu_util_p95 mean 21.0 sd None [21.0, 21.0]

## Not run

- Stage E: calibrated overlap fusion needs calibrated overlapping footage
- Stage F: selective Re-ID is not implemented/enabled
- Stage G: needs the accepted core profile (detector accuracy unaccepted); the soak plan exercises dashboard/preview/history/evidence under the provisional profile
- Stage H: optional advanced AI out of scope

## Disclosures

- Competing non-project host workloads are recorded in environment.competing_host_processes_top_rss and were not stopped.
