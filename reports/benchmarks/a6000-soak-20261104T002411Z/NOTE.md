# FAIL under the protocol as run; every performance gate passed

The only failures were the four history probes at t=0, taken at each camera's first frame (demo start
returns then) and before the first history sample (every 0.5 s of source time); an empty history is correct then. All 319 later probes passed (history 124,
preview 128, metrics 32, dashboard 32, evidence 3). The probe fix took two attempts (2cb9140 was insufficient, see
`a6000-soak-20261104T020806Z/NOTE.md`; corrected in bd4be90). Summaries only; the raw logs stayed on the workstation (`.install/benchmarks/`).
