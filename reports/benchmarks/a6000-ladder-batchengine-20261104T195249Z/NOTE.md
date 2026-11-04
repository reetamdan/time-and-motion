# 24 and 28 cameras PASS with the batched engine; 32 fails on WSL2 GPU resources

After bc717db: the dynamic-batch RT-DETR engine runs each 4-camera pipeline in one inference call, and
state copies for snapshots no longer freeze the backend. `--ladder 24,28,32,36,40 --analysis-frame-interval 4
--cameras-per-pipeline 4` (recorded deviations). 24 cameras (6 pipelines): p95 354 ms, GPU 50 %; the same
24 failed before at p95 2305 ms and GPU 90 %. 28 (7 pipelines): p95 385 ms, GPU 61 %, NVDEC 85 %, 86 C.
At 32 the four pipelines of the second start wave failed with `GPUassert: out of memory` in the NvDCF tracker
(and one source with `Cuda failure: status=2`) while Windows reported ~8.3 GiB of 48 GiB in use; a single
process can allocate 46 GiB there, so this is the WSL2 GPU resource limit on concurrent video pipelines,
not memory. 36 and 40 were not attempted. Summaries only; the raw logs stayed on the workstation.
