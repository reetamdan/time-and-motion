# Superseded: not a capacity result

1 and 2 feeds passed. At 4 feeds every camera failed at startup ("no valid stdout frame metadata within the
startup deadline"): the first-frame deadline was fixed at 15 s and four concurrent starts take longer. Fixed in
4dc3307 (`replay.startup_timeout_seconds`, 60 s). Result: `reports/benchmarks/a6000-ladder-20261104T002411Z`.
Summaries only; the raw per-frame logs stayed on the workstation (`.install/benchmarks/`).
