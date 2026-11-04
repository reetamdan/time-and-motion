# INTERRUPTED after 9 of 12 steps; stages A-C complete, all 9 PASS

The driver process was killed when the host session that launched it ended, 7 minutes into stage D r1
(a partial step with no summary, discarded). `summary.json` and `report.md` were rebuilt offline from the nine
per-step `summary.json` files with `run_capacity.summarize` / `report_md`; nothing was re-measured or edited.

Stage D x3 was then run on its own with identical frozen identities (same code tree, configs tree, extractor,
engine, clip, protocol v2 timings): `a6000-repeats-stageD-20261104T160620Z`, PASS 3/3. Together the two runs
are the full A-D x3 stage comparison at 4 feeds. Summaries only; the raw logs stayed on the workstation.
