# Benchmark methodology (capacity, ladder, soak)

Tool: `scripts/benchmark/run_capacity.py`, protocol version 2, frozen on 2026-10-28 before any v2 acceptance data was collected. The full protocol (gates, resource policy, time bases) is written to `protocol.json` at the start of every run and repeated in its `summary.json` and `report.md`. Nothing in this document is a measured result; results live under `reports/benchmarks/`.

## What is measured

| Stage (PRD 19.1) | Work | How it runs |
| --- | --- | --- |
| A | NVDEC decode + mux | N extractor containers, `--stage decode`, no backend |
| B | A + nvinfer (no tracker) | `--stage detect` |
| C | B + NvDCF tracking (the application pipeline) | `--stage track` |
| D | C + the real backend with all deterministic analytics, persistence and API | the backend (`run-backend.ps1` on Windows, `run-backend.sh` on Linux) with N cameras |
| E–H | calibrated overlap fusion, Re-ID, accepted core profile, optional AI | **not run**: need calibrated overlapping footage, a Re-ID implementation, an accepted detector |

Footage: every feed is an independent decoder instance of the same labelled repeated clip (`demo/derived/cam01-repeat33.mp4`, 782 s, for capacity; `cam01-repeat90.mp4`, 2133 s, for the soak). Each has its own camera id; facility zones and portals are emptied for these replicas. This measures **load only**: it is not physically distinct camera coverage, counting accuracy, or calibrated facility acceptance.

Profile: the paced real-time demo profile. The producer is held to the source timeline with a 200 ms lead (`--pace source`, timed on the host-synchronised wall clock since extractor `8a2340e2…`; the earlier extractor used the WSL guest's monotonic clock, which runs 2.2–3.5 % fast, see `docs/decisions.md`), and the backend releases each frame at its source time on the host clock. A paced run is never compared with an unpaced maximum-throughput run. The A–C schedule lag is computed from `emitted_unix_ns` (wall clock), so a pacer on a different clock shows up there as a growing negative lag.

## Plans

| Plan | Steps | Durations |
| --- | --- | --- |
| `repeats` | A, B, C, D × 3 repeats at 4 feeds | 120 s warm-up + 600 s measured per step |
| `ladder` | D at 1, 2, 4, 6, 8 feeds; stops at the first unacceptable or unattemptable step | same |
| `soak` | D at 4 feeds, with dashboard/preview/history/evidence probes | 120 s warm-up + 1800 s measured |
| `custom` | any stages/feeds/durations | recorded as deviations; never acceptance-eligible |

## Gates (per feed unless stated)

| Gate | Value | Basis |
| --- | --- | --- |
| Cadence | ≥ 7.2 fps | 90 % of the configured 8 fps analytics target. **D**: frames the backend analytics consumed (release log written after the analytics update). **A–C**: frames the producer emitted. The 29.97 fps source rate is reported separately. |
| p95 receive-to-result | ≤ 2000 ms | **D only**: `analytics_done − read` on the backend host clock, over every produced frame in the window. Includes the deliberate paced hold (reported separately). |
| p95 producer schedule lag | ≤ 2000 ms | **A–C only** (no consumer): producer emit time minus the frame's due time on the source timeline. Negative = the deliberate 200 ms lead. A real-time keep-up criterion, **not** receive-to-result latency. |
| Backlog growth | < 500 ms | last-minute median minus first-minute median of the gated latency; D also reports backend queue depth |
| Frame gaps / missing deliveries | 0 | producer frame-number gaps; D: produced frames never consumed, and frame numbers the consumer skipped |
| Expected feed set | all present | an absent feed fails the step; it is never dropped from an average |
| Missing measurement | fail | any unavailable required metric fails explicitly; it is never treated as 0 |
| Resource health | no crash/OOM; GPU ≤ 87 °C; backend RSS growth ≤ 256 MiB; container RSS growth ≤ 128 MiB | growth = last-minute median minus first-minute median |
| Cleanup | every owned container confirmed absent | exact owned names only |

Not gated, reported: producer cadence at D, the fraction of the source rate, the producer→backend transit (cross-clock, corrected by the WSL clock offset measured before and after every step, ± half the best round trip), GPU/NVDEC/VRAM/power/SM clock, host CPU/RAM, WSL guest memory, aggregate throughput. Capture-to-result age is not measured (file replay has no capture clock). Per-frame inference latency is unavailable (no pipeline timer).

Variance: each repeat contributes one worst-feed figure per metric; mean/stdev/min/max are taken across repeats, never by pooling feeds from different repeats.

## Integrity

- Frozen identities (artifact checks, clip sha, wrapper, extractor, configs tree, backend code tree, the benchmark script) are recorded at the start and re-hashed before and after every step; any change ends the run as INTERRUPTED.
- Configured artifacts must all be `MATCH` before measuring; a mismatch is refused (exit 3), never blessed.
- Resource guard (predeclared): host available memory < 512 MiB, or < 768 MiB for 30 s; host commit < 2 GiB; WSL MemAvailable < 512 MiB; GPU ≥ 92 °C for 20 s. A trip stops the owned workload, writes `INTERRUPTED.json` and exits 2; no further step starts (ladder: that step is the first unacceptable one).
- Headroom preflight: 650 MiB per feed + 400 MiB backend (D) + 768 MiB floor must be available before a step launches (up to 180 s wait), else the run stops as INTERRUPTED (ladder: the step is "unattempted").
- Between steps only (WSL route), the guest's clean page cache is dropped so the VM returns memory to the host; recorded per step.
- Exit codes: 0 = plan completed and every step passed (ladder: completed to its stop rule); 1 = a step failed its gates; 2 = interrupted; 3 = refused before measuring.
- Raw evidence per step: producer JSONL, backend release JSONL, extractor summaries, 1 Hz resource samples, snapshot samples, soak probes, backend log, run database.

## Running

```powershell
.venv/Scripts/python.exe scripts/benchmark/run_capacity.py --plan repeats
.venv/Scripts/python.exe scripts/benchmark/run_capacity.py --plan ladder
.venv/Scripts/python.exe scripts/benchmark/run_capacity.py --plan soak
```

Native Linux / backend inside WSL: the same commands with `.venv-wsl/bin/python` (the script calls docker directly when not on Windows). Close competing memory-heavy applications first; the preflight will refuse a step it predicts would exhaust host memory.
