#!/usr/bin/env python3
"""Timed application runs of each configured detector profile on demo/cam01.mp4 (PRD 18.3 timing only).

For each profile (the provisional TrafficCamNet default and the two unaccepted candidates) a separate
configuration directory selects it (candidates with selection_mode: candidate_evaluation), and the
real backend runs the full clip unpaced N times. Recorded per run: start latency, wall time to clean
EOF, frames, consumer fps, extractor pipeline fps, peak VRAM, the person class the profile normalized,
visible-count statistics, top-k truncation frames, and the run provenance (acceptance label).

These are TIMING and INTEGRATION measurements. Detection counts are not accuracy: no human-reviewed
labels exist yet (docs/annotation-guide.md), and a detector that reports more people is not better.

Run: .venv/Scripts/python.exe scripts/benchmark/run_detector_profiles.py [--runs 3]
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts" / "benchmark"))
from evidence_dirs import EvidenceExistsError, resolve_run_dir, utc_stamp  # noqa: E402
from run_duplicate_load import Backend, GpuSampler, http, pct  # noqa: E402

PROFILES = {
    "trafficcamnet-smoke": "provisional_smoke",
    "rt-detr-person-candidate": "candidate_evaluation",
    "rf-detr-person-candidate": "candidate_evaluation",
}


def config_for(directory: Path, model: str, mode: str) -> None:
    shutil.copytree(ROOT / "configs", directory)
    path = directory / "models.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document.update(selected_model=model, selection_mode=mode)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def one_run(step_dir: Path) -> dict[str, Any]:
    sampler = GpuSampler()
    sampler.start()
    time.sleep(1.5)
    baseline = len(sampler.samples)
    started = time.perf_counter()
    status, snapshot = http("/api/v1/demo/start?replay_mode=unpaced", "POST")
    start_seconds = time.perf_counter() - started
    returned = time.monotonic()
    run_id = snapshot.get("run_id")
    end = None
    while time.monotonic() - returned < 120:
        camera = next(c for c in http("/api/v1/facility/snapshot")[1]["cameras"] if c["id"] == "cam-01")
        if camera.get("stream_outcome"):
            end = time.monotonic() - returned
            break
        time.sleep(0.25)
    final = camera
    provenance = http(f"/api/v1/runs/{run_id}/provenance")[1]
    http("/api/v1/demo/stop", "POST")
    sampler.stop_event.set()
    sampler.join(5)
    gpu = sampler.samples[baseline:]
    database = Path(os.environ["CCTV_DATABASE_PATH"])
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT visible_mean, visible_max, frames FROM camera_count_samples WHERE run_id = ?",
                                  (run_id,)).fetchall()
        epoch = connection.execute("SELECT camera_epoch FROM camera_sessions WHERE run_id = ?", (run_id,)).fetchone()[0]
        owned = connection.execute("SELECT resource_name, state FROM owned_resources WHERE run_id = ?", (run_id,)).fetchall()
    summary_path = ROOT / "backend" / "var" / "perception" / "cam-01" / f"{epoch}.summary.json"
    extractor = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    frames = sum(r[2] for r in rows)
    return {
        "run_id": run_id, "http_status": status, "run_status": snapshot.get("run_status"),
        "start_seconds": round(start_seconds, 3), "wall_to_end_s": round(end, 3) if end else None,
        "stream_outcome": final.get("stream_outcome"), "frames_persisted": frames,
        "consumer_fps": round(frames / end, 1) if end else None,
        "extractor_frames": extractor.get("frames_written"), "extractor_elapsed_s": extractor.get("elapsed_seconds"),
        "visible_mean_of_buckets": round(statistics.fmean(r[0] for r in rows), 2) if rows else None,
        "visible_max": max((r[1] for r in rows), default=None),
        "frames_at_detection_limit": final.get("frames_at_detection_limit"),
        "vram_peak_mib": max((s.get("vram_used_mib") or 0 for s in gpu), default=None),
        "gpu_util_p95": pct([s.get("gpu_util") for s in gpu], 0.95),
        "detector": provenance["detector"]["id"], "acceptance": provenance["detector"]["acceptance"],
        "person_class_id": provenance["detector"]["profile"]["person_class_id"],
        "capability_source": (provenance.get("capability") or {}).get("source"),
        "owned": [{"name": n, "state": s} for n, s in owned],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    default = ROOT / "reports" / "model-evaluation" / "timed-runs" / f"profiles-cam01-{utc_stamp()}"
    try:
        evidence = resolve_run_dir(ROOT, "profiles", "AEGIS_PROFILE_RUN_DIR", argv=[],
                                   environ={**os.environ, "AEGIS_PROFILE_RUN_DIR": os.environ.get("AEGIS_PROFILE_RUN_DIR") or str(default)})
    except EvidenceExistsError as exc:
        raise SystemExit(str(exc)) from exc
    run_dir = evidence.directory
    results: dict[str, Any] = {}
    for model, mode in PROFILES.items():
        step = run_dir / model
        step.mkdir()
        config_for(step / "configs", model, mode)
        env = dict(os.environ, CCTV_CONFIG_DIR=str(step / "configs"), CCTV_DATABASE_PATH=str(step / "run.db"),
                   CCTV_EVIDENCE_ROOT=str(step / "evidence"), CCTV_CAPABILITY_CACHE=str(run_dir / "capability-cache.json"))
        os.environ["CCTV_DATABASE_PATH"] = env["CCTV_DATABASE_PATH"]
        backend = Backend(step / "backend.log", env)
        runs = []
        try:
            for index in range(args.runs):
                runs.append(one_run(step))
                print(json.dumps({"model": model, "run": index + 1, **{k: runs[-1][k] for k in (
                    "run_status", "start_seconds", "wall_to_end_s", "frames_persisted", "consumer_fps",
                    "visible_mean_of_buckets", "vram_peak_mib", "person_class_id", "acceptance")}}), flush=True)
        finally:
            backend.kill()
        ok = [r for r in runs if r["stream_outcome"] == "CLEAN_EOF" and r["frames_persisted"] == 710]
        results[model] = {
            "selection_mode": mode, "runs": runs, "complete_runs": len(ok),
            "start_seconds_median": statistics.median(r["start_seconds"] for r in runs),
            "consumer_fps_median": statistics.median(r["consumer_fps"] for r in ok) if ok else None,
            "vram_peak_mib_max": max((r["vram_peak_mib"] or 0) for r in runs),
            "visible_mean_range": [min(r["visible_mean_of_buckets"] for r in ok), max(r["visible_mean_of_buckets"] for r in ok)] if ok else None,
            "all_released": all(o["state"] == "RELEASED" for r in runs for o in r["owned"]),
        }
    report = {
        "schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "command": evidence.command,
        "run_dir": str(run_dir.relative_to(ROOT)), "profiles": results,
        "scope": "Unpaced full-clip application runs per detector profile on one clip. Timing, resource use, and "
                 "profile integration only (person-class normalization, geometry policy, provenance labels). "
                 "Visible counts are detector outputs, NOT accuracy; no labelled comparison has been made.",
    }
    (run_dir / "summary.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({m: {k: v for k, v in r.items() if k != "runs"} for m, r in results.items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
