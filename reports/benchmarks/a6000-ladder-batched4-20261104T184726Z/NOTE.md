# 24 cameras FAIL on latency: the capacity with 4 cameras per pipeline is 20

Rerun of 24/28/32 after the camera-layout fix (76e5af6). At 24 cameras (6 shared pipelines) every camera still
ran at 7.48 fps with 0 missing deliveries, but receive-to-result p95 was 2.05-2.31 s on most cameras (gate
2.0 s) with GPU utilisation p95 90 % and backlog growth up to 42 ms: the GPU is the limit. The ladder stopped
there (28 and 32 not attempted), so the last acceptable count is 20 from
`a6000-ladder-batched4-20261104T181821Z`. Summaries only; the raw logs stayed on the workstation.
