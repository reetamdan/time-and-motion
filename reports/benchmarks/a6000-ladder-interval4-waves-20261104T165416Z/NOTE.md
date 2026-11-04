# 16 feeds after the startup fix: 12 cameras ran, 4 could not start

Ladder 16,20,24,32 at analysis interval 4, after f59e25b (cameras load in waves of `replay.startup_concurrency`
= 4; a refused start fails at once). Cameras 1-12 started in waves about 11 s apart and ran at 7.49 fps with
receive-to-result p95 736 ms. Cameras 13-16 failed immediately with
`Unable to set device in gst_nvstreammux_change_state` / `Unable to set the pipeline to the playing state`,
so the step FAILED and the ladder stopped (20/24/32 not attempted). VRAM 6.6 GiB of 48, WSL guest 24 GB
free, CPU p95 40 %: not a resource the benchmark samples. See `a6000-diag-16feeds-stageA-20261104T171720Z`
and `docs/rtx-a6000.md` (Capacity). Summaries only; the raw logs stayed on the workstation.
