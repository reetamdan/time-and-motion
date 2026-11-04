# Diagnostic only: 16 decode-only containers (stage A), 10 s warm-up + 30 s

`--plan custom --stages A --feeds 16 --repeats 1 --warmup 10 --measure 30 --analysis-frame-interval 4`
(the run directory was `.install/benchmarks/diag-16-stageA`). No detector or tracker. Five decoders failed
with `ERROR from nvv4l2decoder0: Failed to allocate required memory`, one pipeline could not reach PLAYING,
and the rest delivered frames erratically. A separate probe opened 16 plain CUDA contexts (each allocating
256 MiB), in one container and in 16 containers, all successfully. Conclusion: the limit is the number of
DeepStream video pipelines WSL2 grants on this GPU, independent of the detector. Not a capacity result.
