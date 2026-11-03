#!/usr/bin/env python3
"""Measure end-to-end paced-replay lead and failure visibility: v1 extractor vs the v2 successor.

Two DUPLICATE-INPUT feeds of demo/cam01.mp4 (cam-01, cam-02) run paced on the real GPU stack.
Phase "v1" uses the frozen v1 extractor (producer unpaced; pacing only in the backend). Phase "v2"
uses extract_metadata_v2.py with --pace source (producer held to the source timeline). In each
phase cam-02's own container is killed (docker kill, exact owned name) once its source position
passes 6 s, and the script records:

* producer position: source PTS of the latest frame the extractor had emitted (v2 records carry
  emitted_unix_ns; v1 records do not, so v1 producer position comes from frames released after
  the kill, which the producer must have emitted before it),
* backend release position: per-frame release log (CCTV_RELEASE_LOG=1),
* end-to-end lead = producer position - released position at each release (v2, skew-corrected),
* time from the kill to the camera's visible ABNORMAL_TERMINATION outcome in the API,
* frames released for cam-02 after the kill,
* cam-01 (unaffected): keeps advancing, reaches CLEAN_EOF with all 710 frames.

Scope: file replay through the Windows backend + WSL docker pipe only. RTSP sources are NOT tested.

Run: .venv/Scripts/python.exe scripts/validate_paced_lead.py
"""

from __future__ import annotations

from datetime import datetime, timezone
import bisect
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts" / "benchmark"))
from evidence_dirs import EvidenceExistsError, resolve_run_dir  # noqa: E402
from run_duplicate_load import Backend, http, pct, write_config  # noqa: E402

V1 = {"extractor_path": "./perception/deepstream/service_maker/extract_metadata.py",
      "extractor_sha256": "6093b5d5d06089225b3bf60b0342fedccc50efde477f9663c5ef61ff91abad25",
      "extractor_schema_version": "1.0", "extractor_pacing": "none"}
KILL_AT_SOURCE_S = 6.0


def wsl(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["wsl.exe", "-d", "Ubuntu-22.04", "-e", *args], capture_output=True, text=True,
                          timeout=timeout, check=False)


def clock_skew_ns() -> dict[str, Any]:
    """WSL (container) wall clock minus host wall clock, from the lowest-round-trip sample."""
    samples = []
    for _ in range(7):
        before = time.time_ns()
        out = wsl("date", "+%s%N", timeout=30).stdout.strip()
        after = time.time_ns()
        if out.isdigit():
            samples.append((after - before, int(out) - (before + after) // 2))
    rtt, skew = min(samples)
    return {"skew_ns": skew, "rtt_ns": rtt, "samples": len(samples)}


def cameras() -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in http("/api/v1/facility/snapshot")[1]["cameras"]}


def lead_stats(release: list[dict], produced: list[dict], skew_ns: int) -> dict[str, Any]:
    """Producer position at each release minus the released frame's position (source seconds).

    Two clock alignments are reported. ``wsl_date_skew``: the WSL clock offset measured with
    ``date`` round trips (its process start-up makes it imprecise). ``min_transit_zero``: the offset
    at which the fastest frame's producer->read transit is 0 ms; since real transit is >= 0, the
    true offset is at least this large, so its lead is a LOWER bound. The skew-free measure of the
    same quantity is the number of frames still delivered after the producer was killed.
    """
    raw = [r for r in produced if r.get("emitted_unix_ns")]
    if not raw:
        return {"available": False, "reason": "producer records carry no emitted_unix_ns (v1 schema 1.0)"}
    transit_raw = [item["read_unix_ns"] - item["emitted_unix_ns"] for item in release if item.get("emitted_unix_ns")]
    calibrated = -min(transit_raw)  # offset making the fastest transit exactly 0
    return {"available": True,
            "wsl_date_skew": _lead(release, raw, skew_ns),
            "min_transit_zero": {**_lead(release, raw, calibrated), "offset_ns": calibrated,
                                 "note": "lower bound: assumes the fastest frame's transit was 0 ms"}}


def _lead(release: list[dict], produced: list[dict], skew_ns: int) -> dict[str, Any]:
    emitted = sorted((r["emitted_unix_ns"] - skew_ns, r["pts_ns"]) for r in produced)
    times = [e[0] for e in emitted]
    running_max, best = [], -1
    for _, pts in emitted:
        best = max(best, pts)
        running_max.append(best)
    leads, transits = [], []
    for item in release:
        index = bisect.bisect_right(times, item["released_unix_ns"]) - 1
        if index >= 0:
            leads.append((running_max[index] - item["pts_ns"]) / 1e9)
        if item.get("emitted_unix_ns"):
            transits.append((item["read_unix_ns"] - (item["emitted_unix_ns"] - skew_ns)) / 1e6)
    return {"frames": len(leads),
            "lead_s": {"p50": pct(leads, 0.5), "p95": pct(leads, 0.95), "max": max(leads) if leads else None},
            "producer_to_backend_read_ms": {"p50": pct(transits, 0.5), "p95": pct(transits, 0.95),
                                             "max": max(transits) if transits else None}}


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def phase(run_dir: Path, name: str, overrides: dict[str, Any] | None, skew: int, cache: Path) -> dict[str, Any]:
    step = run_dir / name
    step.mkdir()
    write_config(step / "configs", 2)
    if overrides:
        path = step / "configs" / "models.yaml"
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        document["pipeline"].update(overrides)
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    database = step / "run.db"
    env = dict(os.environ, CCTV_CONFIG_DIR=str(step / "configs"), CCTV_DATABASE_PATH=str(database),
               CCTV_EVIDENCE_ROOT=str(step / "evidence"), CCTV_CAPABILITY_CACHE=str(cache), CCTV_RELEASE_LOG="1")
    backend = Backend(step / "backend.log", env)
    result: dict[str, Any] = {"phase": name, "extractor_overrides": overrides, "timeline": []}
    try:
        status, snapshot = http("/api/v1/demo/start?replay_mode=paced", "POST")
        result["start_status"] = status
        run_id = snapshot["run_id"]
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and (cameras()["cam-02"]["source_position_s"] or 0) < KILL_AT_SOURCE_S:
            time.sleep(0.1)
        with sqlite3.connect(database) as connection:
            owned = {c: (n, e) for c, n, e in connection.execute(
                "SELECT camera_id, resource_name, camera_epoch FROM owned_resources WHERE run_id = ?", (run_id,))}
        victim, victim_epoch = owned["cam-02"]
        before = cameras()
        kill_unix_ns = time.time_ns()
        kill_started = time.monotonic()
        kill = wsl("docker", "kill", victim)
        result["kill"] = {"container": victim, "returncode": kill.returncode,
                          "cam02_position_at_kill_s": before["cam-02"]["source_position_s"],
                          "cam01_position_at_kill_s": before["cam-01"]["source_position_s"]}
        visible_after = None
        cam01_end = None
        while time.monotonic() - kill_started < 90:
            state = cameras()
            elapsed = time.monotonic() - kill_started
            result["timeline"].append({"t": round(elapsed, 2), **{c: {k: state[c].get(k) for k in (
                "source_position_s", "stream_outcome", "visible_count")} for c in ("cam-01", "cam-02")}})
            if visible_after is None and state["cam-02"].get("stream_outcome"):
                visible_after = elapsed
                result["cam02_outcome"] = state["cam-02"]["stream_outcome"]
            if state["cam-01"].get("stream_outcome"):
                cam01_end = state["cam-01"]["stream_outcome"]
                if visible_after is not None:
                    break
            time.sleep(0.1)
        result["kill_to_visible_outcome_s"] = round(visible_after, 2) if visible_after is not None else None
        result["cam01_outcome"] = cam01_end
        with sqlite3.connect(database) as connection:
            frames = dict(connection.execute("SELECT camera_id, SUM(frames) FROM camera_count_samples WHERE run_id = ? "
                                             "GROUP BY camera_id", (run_id,)).fetchall())
        result["history_frames"] = frames
        stop_status, stopped = http("/api/v1/demo/stop", "POST")
        result["stop"] = {"status": stop_status, "run_status": stopped["run_status"]}
        with sqlite3.connect(database) as connection:
            result["ownership"] = connection.execute(
                "SELECT camera_id, resource_name, state FROM owned_resources WHERE run_id = ?", (run_id,)).fetchall()
    finally:
        backend.kill()
    for camera, (_, epoch) in owned.items():
        directory = ROOT / "backend" / "var" / "perception" / camera
        release = read_jsonl(directory / f"{epoch}.release.jsonl")
        produced = read_jsonl(directory / f"{epoch}.jsonl")
        for suffix in (".release.jsonl", ".summary.json"):
            if (directory / f"{epoch}{suffix}").is_file():
                shutil.copy(directory / f"{epoch}{suffix}", step / f"{camera}{suffix}")
        after_kill = [r for r in release if r["released_unix_ns"] > kill_unix_ns] if camera == "cam-02" else []
        result.setdefault("cameras", {})[camera] = {
            "frames_produced": len(produced), "frames_released": len(release),
            "max_queue_depth": max((r["queue_depth"] for r in release), default=None),
            "lead": lead_stats(release, produced, skew),
            **({"frames_released_after_kill": len(after_kill),
                "source_seconds_released_after_kill": round((after_kill[-1]["pts_ns"] - after_kill[0]["pts_ns"]) / 1e9, 2)
                if len(after_kill) > 1 else 0.0} if camera == "cam-02" else {}),
        }
    leftover = [n for _, n, _ in result.get("ownership", [])
                if n in wsl("docker", "ps", "-a", "--filter", f"name={n}", "--format", "{{.Names}}").stdout.split()]
    result["leftover_containers"] = leftover
    return result


def main() -> int:
    try:
        evidence = resolve_run_dir(ROOT, "paced-lead-2feeds", "AEGIS_PACED_LEAD_RUN_DIR")
    except EvidenceExistsError as exc:
        raise SystemExit(str(exc)) from exc
    run_dir = evidence.directory
    skew = clock_skew_ns()
    cache = run_dir / "capability-cache.json"
    phases = {"v1": phase(run_dir, "v1", V1, skew["skew_ns"], cache),
              "v2": phase(run_dir, "v2", None, skew["skew_ns"], cache)}
    v1, v2 = phases["v1"], phases["v2"]
    checks = {
        # Skew-free: source seconds still delivered for cam-02 after its producer was killed.
        "v2_buffered_lead_at_kill_under_1s": v2["cameras"]["cam-02"]["source_seconds_released_after_kill"] < 1.0,
        "v1_buffered_lead_at_kill_measured": v1["cameras"]["cam-02"]["frames_released_after_kill"] > 0,
        "v2_failure_visible_within_3s": (v2["kill_to_visible_outcome_s"] or 99) < 3.0,
        "v2_failed_camera_abnormal": v2.get("cam02_outcome") == "ABNORMAL_TERMINATION",
        "v2_unaffected_camera_clean_eof_710": v2.get("cam01_outcome") == "CLEAN_EOF" and v2["history_frames"].get("cam-01") == 710,
        "v1_unaffected_camera_clean_eof_710": v1.get("cam01_outcome") == "CLEAN_EOF" and v1["history_frames"].get("cam-01") == 710,
        "v2_visibility_faster_than_v1": (v2["kill_to_visible_outcome_s"] or 99) < (v1["kill_to_visible_outcome_s"] or 0),
        "all_owned_released": all(s == "RELEASED" for p in phases.values() for _, _, s in p.get("ownership", [])),
        "no_owned_container_left": not any(p["leftover_containers"] for p in phases.values()),
    }
    report = {"schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "command": evidence.command,
              "run_dir": str(run_dir.relative_to(ROOT)), "clock_skew": skew,
              "result": "PASS" if all(checks.values()) else "FAIL",
              "checks": {k: "PASS" if v else "FAIL" for k, v in checks.items()}, "phases": phases,
              "scope": "Paced FILE replay of two duplicate-input feeds (cam01.mp4 as cam-01/cam-02) through the Windows "
                       "backend and the WSL docker stdout pipe on the real GPU. RTSP/live sources were not tested. "
                       "Fault = docker kill of cam-02's own container at source position >= 6 s."}
    (run_dir / "result.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"], "checks": report["checks"], "run_dir": report["run_dir"],
                      "v1_kill_to_visible_s": v1["kill_to_visible_outcome_s"], "v2_kill_to_visible_s": v2["kill_to_visible_outcome_s"],
                      "v1_after_kill": v1["cameras"]["cam-02"].get("frames_released_after_kill"),
                      "v2_after_kill": v2["cameras"]["cam-02"].get("frames_released_after_kill"),
                      "v2_lead": v2["cameras"]["cam-01"]["lead"]}, indent=1, default=str))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
