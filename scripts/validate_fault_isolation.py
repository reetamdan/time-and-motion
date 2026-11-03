#!/usr/bin/env python3
"""Real fault injection with two feeds: one camera's container is killed mid-run; the other must continue.

Both feeds are DUPLICATE INPUT (demo/cam01.mp4 under cam-01 and cam-02), which is sufficient for
health/failure isolation (it is not multi-camera coverage). The injected fault is `docker kill` of
cam-02's own container, whose exact name is read from this run's ownership record.

Checks: cam-02 reports ABNORMAL_TERMINATION with an alert and goes stale (null, last-good age grows);
cam-01 keeps advancing on its source timeline and reaches CLEAN_EOF with all 710 frames; stop releases
both owned resources; nothing of this run is left running.

Run: .venv/Scripts/python.exe scripts/validate_fault_isolation.py
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts" / "benchmark"))
from evidence_dirs import EvidenceExistsError, resolve_run_dir  # noqa: E402
from run_duplicate_load import Backend, http, write_config  # noqa: E402


def cameras() -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in http("/api/v1/facility/snapshot")[1]["cameras"]}


def main() -> int:
    try:
        evidence = resolve_run_dir(ROOT, "fault-isolation-2feeds", "AEGIS_FAULT_RUN_DIR")
    except EvidenceExistsError as exc:
        raise SystemExit(str(exc)) from exc
    run_dir = evidence.directory
    write_config(run_dir / "configs", 2)
    database = run_dir / "run.db"
    env = dict(os.environ, CCTV_CONFIG_DIR=str(run_dir / "configs"), CCTV_DATABASE_PATH=str(database),
               CCTV_EVIDENCE_ROOT=str(run_dir / "evidence"), CCTV_CAPABILITY_CACHE=str(run_dir / "capability-cache.json"))
    checks: dict[str, bool] = {}
    observed: dict[str, Any] = {"timeline": []}

    def check(name: str, ok: bool, detail: Any = None) -> None:
        checks[name] = bool(ok)
        if detail is not None:
            observed[name] = detail
        print(f"{'PASS' if ok else 'FAIL'} {name} {json.dumps(detail, default=str)[:220] if detail is not None else ''}", flush=True)

    backend = Backend(run_dir / "backend.log", env)
    try:
        status, snapshot = http("/api/v1/demo/start?replay_mode=paced", "POST")
        check("both_feeds_running", status == 200 and snapshot["run_status"] == "RUNNING")
        run_id = snapshot["run_id"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and min((cameras()[c]["source_position_s"] or 0) for c in ("cam-01", "cam-02")) < 6:
            time.sleep(0.25)
        with sqlite3.connect(database) as connection:
            owned = dict(connection.execute("SELECT camera_id, resource_name FROM owned_resources WHERE run_id = ?", (run_id,)).fetchall())
        victim = owned["cam-02"]
        before = cameras()
        kill = subprocess.run(["wsl.exe", "-d", "Ubuntu-22.04", "-e", "docker", "kill", victim], capture_output=True, text=True,
                              timeout=60, check=False)
        killed_at = time.monotonic()
        check("fault_injected_on_own_container", kill.returncode == 0,
              {"container": victim, "cam01_position_at_kill": before["cam-01"]["source_position_s"],
               "cam02_position_at_kill": before["cam-02"]["source_position_s"]})
        cam01_end = cam02_end = None
        while time.monotonic() - killed_at < 60:
            state = cameras()
            observed["timeline"].append({"t": round(time.monotonic() - killed_at, 2),
                                         **{c: {k: state[c].get(k) for k in ("source_position_s", "visible_count", "last_good_age_ms",
                                                                              "stream_outcome", "health")} for c in ("cam-01", "cam-02")}})
            if state["cam-02"].get("stream_outcome") and cam02_end is None:
                cam02_end = state["cam-02"]
            if state["cam-01"].get("stream_outcome") == "CLEAN_EOF":
                cam01_end = state["cam-01"]
                break
            time.sleep(0.5)
        check("failed_camera_reports_abnormal_termination", bool(cam02_end) and cam02_end["stream_outcome"] == "ABNORMAL_TERMINATION",
              {"outcome": cam02_end and cam02_end["stream_outcome"], "detail": cam02_end and cam02_end.get("stream_outcome_detail")})
        final = cameras()
        check("failed_camera_stale_not_zero", final["cam-02"]["visible_count"] is None and (final["cam-02"]["last_good_age_ms"] or 0) > 1500,
              {"visible_count": final["cam-02"]["visible_count"], "last_good_age_ms": final["cam-02"]["last_good_age_ms"],
               "health": final["cam-02"]["health"]})
        positions = [p["cam-01"]["source_position_s"] for p in observed["timeline"] if p["cam-01"]["source_position_s"] is not None]
        check("unaffected_camera_kept_advancing", len(positions) > 10 and positions[-1] > positions[0] + 10,
              {"first": positions[0] if positions else None, "last": positions[-1] if positions else None})
        check("unaffected_camera_clean_eof", bool(cam01_end), {"outcome": cam01_end and cam01_end["stream_outcome"]})
        alerts = http("/api/v1/alerts")[1]
        ended = [a for a in alerts if a["stable_key"].startswith("camera-stream-ended:cam-02:")]
        check("abnormal_end_alert_raised_for_failed_camera_only", len(ended) == 1
              and not any(a["stable_key"].startswith("camera-stream-ended:cam-01:") for a in alerts),
              {"alert": ended[0]["message"] if ended else None})
        with sqlite3.connect(database) as connection:
            frames = dict(connection.execute("SELECT camera_id, SUM(frames) FROM camera_count_samples WHERE run_id = ? GROUP BY camera_id",
                                             (run_id,)).fetchall())
        check("unaffected_camera_history_complete", frames.get("cam-01") == 710, frames)
        stop_status, stopped = http("/api/v1/demo/stop", "POST")
        check("stop_confirms_both_released", stop_status == 200 and stopped["run_status"] == "STOPPED")
        with sqlite3.connect(database) as connection:
            states = connection.execute("SELECT camera_id, resource_name, state FROM owned_resources WHERE run_id = ?", (run_id,)).fetchall()
        observed["ownership"] = states
        check("ownership_rows_released", all(s == "RELEASED" for _, _, s in states) and len(states) == 2)
    finally:
        backend.kill()
    leftover = []
    for _, name, _ in observed.get("ownership", []):
        out = subprocess.run(["wsl.exe", "-d", "Ubuntu-22.04", "-e", "docker", "ps", "-a", "--filter", f"name={name}", "--format",
                              "{{.Names}}"], capture_output=True, text=True, timeout=30, check=False).stdout
        if name in out.split():
            leftover.append(name)
    check("no_owned_container_left", not leftover, leftover)
    report = {"schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "command": evidence.command,
              "run_dir": str(run_dir.relative_to(ROOT)), "result": "PASS" if all(checks.values()) else "FAIL",
              "checks": {k: "PASS" if v else "FAIL" for k, v in checks.items()}, "observed": observed,
              "scope": "Two DUPLICATE-INPUT feeds of demo/cam01.mp4 (cam-01, cam-02), real GPU containers, real backend "
                       "socket. Fault = docker kill of cam-02's own container. Health/failure isolation only."}
    (run_dir / "result.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"], "run_dir": report["run_dir"]}, indent=1))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
