# 16 and 20 cameras PASS with 4 cameras per pipeline; the 24-camera step never started

`--plan ladder --ladder 16,20,24,28,32 --analysis-frame-interval 4 --cameras-per-pipeline 4` (both recorded
deviations). 16 cameras ran in 4 shared containers and 20 in 5, every camera at 7.49-7.50 fps with 0 missing
deliveries. The 24-camera step was INTERRUPTED by a benchmark bug, not by load: `write_backend_config` placed
the 23rd duplicate camera at x = 1010, outside the 1000 x 600 floor view box, so the backend refused its
configuration and exited before becoming live. Fixed in 76e5af6 (rows of 20); 24+ was rerun in
`a6000-ladder-batched4-20261104T184726Z`. Summaries only; the raw logs stayed on the workstation.
