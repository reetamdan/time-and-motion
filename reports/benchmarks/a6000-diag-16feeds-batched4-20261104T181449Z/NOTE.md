# Diagnostic only: first end-to-end run of shared pipelines (16 cameras, 4 per pipeline)

`--plan custom --stages D --feeds 16 --repeats 1 --warmup 30 --measure 90 --analysis-frame-interval 4
--cameras-per-pipeline 4` (run directory `.install/benchmarks/diag-16-batched`). PASS: 4 shared containers
recorded for 4 cameras each and all confirmed released, 7.5 fps per camera, p95 1086 ms, GPU 65 %, CPU 54 %,
VRAM 3.4 GiB. Shortened timings: not a capacity result; the ladder runs are.
