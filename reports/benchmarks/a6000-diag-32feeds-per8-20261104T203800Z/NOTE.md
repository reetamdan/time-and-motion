# Diagnostic only: 32 cameras as 4 pipelines of 8 (batch 8)

`--plan custom --stages D --feeds 32 --repeats 1 --warmup 60 --measure 120 --analysis-frame-interval 4
--cameras-per-pipeline 8` (run directory `.install/benchmarks/diag-32-per8`). Three pipelines (24 cameras) ran
at 7.48+ fps, p95 410 ms, GPU 43 %, VRAM 4.6 GiB; in the fourth, cam-25 delivered 40 frames and cam-26 none
within the 60 s start deadline (no error logged), so the whole pipeline was released. With 28 decoders
running in the ladder and 32 failing here in two different ways, the limit is the number of concurrent
video decode sessions/pipelines WSL2 grants on this host. Shortened timings: not a capacity result.
