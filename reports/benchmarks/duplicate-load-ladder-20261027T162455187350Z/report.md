# Duplicate-input load ladder — duplicate-load-ladder-20261027T162455187350Z

**Run kind:** ENGINEERING duplicate-input load ladder (not a PRD steady-state capacity run). **Stage:** D (decode+detect+track+camera-local analytics+persistence+API; dashboard closed).

Every feed is the same 23.7 s clip (`demo/cam01.mp4`, sha256 `4201f7162eb0b9bc…`, 1920×1080 H.264 29.97 fps, 710 frames) under a distinct camera ID. This measures load on this laptop only; it shows nothing about physical multi-camera coverage, overlap identity, cross-floor continuity, or counting accuracy.

Hardware: NVIDIA GeForce RTX 5070 Ti Laptop GPU (12 GB) via WSL2 Ubuntu-22.04 + the pinned DeepStream 9.2 container; detector `trafficcamnet-smoke` (provisional, FP16), NvDCF tracker. One pass per step, no repeats.

## Results

| Pacing | Feeds | All CLEAN_EOF, 710/710 frames, 0 drops | Start s | Stop s | Consumer fps / feed (min) | Aggregate fps | Paced lag p95 s | Backend queue p95 ms | GPU util p95 % | NVDEC p95 % | CPU p95 % | VRAM peak MiB (idle) | GPU °C peak |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| unpaced | 1 | yes | 12.13 | 0.251 | 253.8 | 253.8 | – | 7 | 21.0 | 12.0 | 31.7 | 1171.0 (853.0) | 61.0 |
| unpaced | 2 | yes | 4.82 | 0.531 | 207.5 | 415.0 | – | 15 | 82.0 | 25.0 | 33.5 | 1489.0 (853.0) | 66.0 |
| unpaced | 4 | yes | 5.402 | 0.97 | 125.2 | 500.8 | – | 12 | 83.0 | 23.0 | 72.1 | 2125.0 (853.0) | 69.0 |
| unpaced | 6 | yes | 6.624 | 1.482 | 81.3 | 504.5 | – | 16 | 83.0 | 25.0 | 64.5 | 2747.0 (838.0) | 71.0 |
| unpaced | 8 | yes | 7.921 | 1.966 | 60.3 | 498.2 | – | 15 | 84.0 | 23.0 | 91.5 | 3367.0 (829.0) | 74.0 |
| paced | 1 | yes | 5.853 | 0.253 | 29.7 | 29.7 | -0.02 | 611 | 24.0 | 13.0 | 28.4 | 1146.0 (828.0) | 65.0 |
| paced | 2 | yes | 4.49 | 0.58 | 29.5 | 59.0 | -0.027 | 611 | 78.0 | 23.0 | 12.6 | 1463.0 (828.0) | 65.0 |
| paced | 4 | yes | 5.742 | 0.93 | 29.5 | 118.0 | -0.011 | 608 | 83.0 | 22.0 | 52.1 | 2099.0 (828.0) | 67.0 |
| paced | 6 | yes | 6.183 | 1.603 | 29.6 | 177.4 | -0.019 | 610 | 83.0 | 22.0 | 64.7 | 2734.0 (828.0) | 68.0 |
| paced | 8 | yes | 6.572 | 2.016 | 29.9 | 239.2 | 0.016 | 612 | 83.0 | 30.0 | 89.4 | 3370.0 (828.0) | 70.0 |

## Ladder outcome

- **unpaced:** last acceptable step 8 feeds; ladder completed. Steps beyond 8 were not attempted (PRD 19.2: only when useful).
- **paced:** last acceptable step 8 feeds; ladder completed. Steps beyond 8 were not attempted (PRD 19.2: only when useful).

## How to read these numbers

- *Consumer fps / feed* = 710 frames ÷ wall seconds from the start response (first frame already received) to that feed's clean EOF, as observed by 0.5 s API polling. It is backend throughput, not a camera frame rate.
- *Paced* runs release frames on the source timeline (real time). *Unpaced* runs go as fast as possible. They are different operating points and are not compared as the same measurement.
- *Paced lag* = wall time since start minus the feed's source position (negative = slightly ahead of polling). The ~610 ms backend queue p95 in paced mode is the deliberate 16-frame pacing buffer, not congestion.
- GPU/NVDEC/CPU percentiles come from 1 s `nvidia-smi` samples and API host metrics over the whole step, including container start-up and idle tail, so medians are diluted; p95 approximates the streaming load. Windows `nvidia-smi` reports the WSL2 container's GPU/NVDEC work.
- Start time includes artifact identity checks, cached capability reuse, concurrent container launch, engine deserialization, and the first metadata frame; the first step also paid the capability probe.
- One short pass per step (the 23.7 s clip does not loop): no warm-up/steady-state split, no variance estimate, and no capacity extrapolation. This is not the PRD's 600 s steady-state capacity run.

## Not attempted

- Stage A-C: decode-only / no-tracker pipeline variants are not exposed by the protected extractor
- Stage E-F: no calibrated overlap fusion or Re-ID inputs
- Stage G: dashboard/preview load not included

Limits used for each step: `{"all_feeds_clean_eof": true, "max_dropped_frames": 0, "paced_source_lag_p95_s": 1.0, "max_vram_fraction": 0.9, "max_gpu_temperature_c": 87, "max_backend_queue_p95_ms_paced": 1000}`. Raw time series: `<mode>-<feeds>/raw.json`.
