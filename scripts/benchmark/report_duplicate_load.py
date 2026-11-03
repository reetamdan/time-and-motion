#!/usr/bin/env python3
"""Write report.md for a duplicate-input load ladder from its saved summary.json (no new measurements).

Run: .venv/Scripts/python.exe scripts/benchmark/report_duplicate_load.py reports/benchmarks/duplicate-load-ladder-<stamp>
"""

from __future__ import annotations

import json
from pathlib import Path
import sys


def derived(step: dict) -> dict:
    cameras = step["per_camera"].values()
    walls = [c["wall_to_end_s"] for c in cameras if c["wall_to_end_s"]]
    frames = [c["frames_persisted"] or 0 for c in cameras]
    per_feed = [f / w for f, w in zip(frames, walls)] if walls else []
    return {
        "consumer_fps_per_feed_min": round(min(per_feed), 1) if per_feed else None,
        "consumer_fps_aggregate": round(sum(per_feed), 1) if per_feed else None,
        "wall_to_end_max_s": max(walls) if walls else None,
    }


def main(directory: Path) -> int:
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    lines = [
        f"# Duplicate-input load ladder — {directory.name}", "",
        f"**Run kind:** {summary['run_kind']}. **Stage:** {summary['workload_stage']}.", "",
        "Every feed is the same 23.7 s clip (`demo/cam01.mp4`, sha256 "
        f"`{summary['source']['sha256'][:16]}…`, 1920×1080 H.264 29.97 fps, 710 frames) under a distinct camera ID. "
        "This measures load on this laptop only; it shows nothing about physical multi-camera coverage, overlap identity, "
        "cross-floor continuity, or counting accuracy.", "",
        "Hardware: NVIDIA GeForce RTX 5070 Ti Laptop GPU (12 GB) via WSL2 Ubuntu-22.04 + the pinned DeepStream 9.2 container; "
        "detector `trafficcamnet-smoke` (provisional, FP16), NvDCF tracker. One pass per step, no repeats.", "",
        "## Results", "",
        "| Pacing | Feeds | All CLEAN_EOF, 710/710 frames, 0 drops | Start s | Stop s | Consumer fps / feed (min) | Aggregate fps | Paced lag p95 s | Backend queue p95 ms | GPU util p95 % | NVDEC p95 % | CPU p95 % | VRAM peak MiB (idle) | GPU °C peak |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for mode, ladder in summary["ladders"].items():
        for step in ladder["steps"]:
            d = derived(step)
            lag = [c["source_lag_s_p95"] for c in step["per_camera"].values() if c["source_lag_s_p95"] is not None]
            queue = max(c["backend_queue_ms_p95"] or 0 for c in step["per_camera"].values())
            gpu, host = step["gpu"], step["host"]
            lines.append(
                f"| {mode} | {step['feeds']} | {'yes' if step['evaluation']['acceptable'] else 'NO: ' + '; '.join(step['evaluation']['reasons'])} "
                f"| {step['start_seconds']} | {step['stop_seconds']} | {d['consumer_fps_per_feed_min']} | {d['consumer_fps_aggregate']} "
                f"| {max(lag) if lag else '–'} | {round(queue)} | {gpu['gpu_util_p95']} | {gpu['nvdec_util_p95']} | {host['cpu_util_p95']} "
                f"| {gpu['vram_used_mib_peak']} ({gpu['baseline_vram_mib']}) | {gpu['temp_c_peak']} |")
    lines += [
        "", "## Ladder outcome", "",
        *[f"- **{mode}:** last acceptable step {ladder['last_acceptable_feeds']} feeds; {ladder['stop_reason']}. "
          "Steps beyond 8 were not attempted (PRD 19.2: only when useful)." for mode, ladder in summary["ladders"].items()],
        "", "## How to read these numbers", "",
        "- *Consumer fps / feed* = 710 frames ÷ wall seconds from the start response (first frame already received) to "
        "that feed's clean EOF, as observed by 0.5 s API polling. It is backend throughput, not a camera frame rate.",
        "- *Paced* runs release frames on the source timeline (real time). *Unpaced* runs go as fast as possible. "
        "They are different operating points and are not compared as the same measurement.",
        "- *Paced lag* = wall time since start minus the feed's source position (negative = slightly ahead of polling). "
        "The ~610 ms backend queue p95 in paced mode is the deliberate 16-frame pacing buffer, not congestion.",
        "- GPU/NVDEC/CPU percentiles come from 1 s `nvidia-smi` samples and API host metrics over the whole step, "
        "including container start-up and idle tail, so medians are diluted; p95 approximates the streaming load. "
        "Windows `nvidia-smi` reports the WSL2 container's GPU/NVDEC work.",
        "- Start time includes artifact identity checks, cached capability reuse, concurrent container launch, "
        "engine deserialization, and the first metadata frame; the first step also paid the capability probe.",
        "- One short pass per step (the 23.7 s clip does not loop): no warm-up/steady-state split, no variance "
        "estimate, and no capacity extrapolation. This is not the PRD's 600 s steady-state capacity run.",
        "", "## Not attempted", "",
        *[f"- Stage {stage}: {reason}" for stage, reason in summary["stages_not_attempted"].items()],
        "", f"Limits used for each step: `{json.dumps(summary['limits'])}`. Raw time series: `<mode>-<feeds>/raw.json`.",
    ]
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[8:20]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
