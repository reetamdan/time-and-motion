#!/usr/bin/env python3
"""Real API -> supervisor -> Docker -> DeepStream -> snapshot smoke integration."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
from backend.app.main import create_app  # noqa: E402
from evidence_dirs import EvidenceExistsError, resolve_run_dir  # noqa: E402


def main() -> None:
    try:
        run = resolve_run_dir(ROOT, "live-application-cam01", "AEGIS_LIVE_APPLICATION_RUN_DIR")
    except EvidenceExistsError as exc:
        raise SystemExit(str(exc)) from exc
    run_dir = run.directory
    database = run_dir / "live-application.db"
    app = create_app(database_path=database)
    observed: dict[str, object] = {}
    checks: dict[str, bool] = {}
    with TestClient(app) as client:
        started = client.post("/api/v1/demo/start")
        start_body = started.json()
        checks["start_http_ok"] = started.status_code == 200
        checks["run_running"] = start_body.get("run_status") == "RUNNING"
        cameras = {camera["id"]: camera for camera in start_body.get("cameras", [])}
        epoch = cameras.get("cam-01", {}).get("epoch")
        checks["camera_epoch_present"] = isinstance(epoch, str) and bool(epoch)
        checks["disabled_cameras_isolated"] = all(
            cameras[camera_id]["health"] == "OFFLINE" for camera_id in ("cam-02", "cam-03", "cam-04")
        )
        first_count = None
        max_count = 0
        health = None
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            snapshot = client.get("/api/v1/facility/snapshot").json()
            camera = next(item for item in snapshot["cameras"] if item["id"] == "cam-01")
            count = camera.get("visible_count")
            if count is not None:
                first_count = count if first_count is None else first_count
                max_count = max(max_count, count)
                health = camera.get("health")
                break
            time.sleep(0.1)
        checks["real_visible_count_observed"] = isinstance(first_count, int) and first_count > 0
        checks["calibration_warning_preserved"] = health == "CALIBRATION WARNING"
        checks["provisional_profile_disclosed"] = any(
            notice.get("title") == "Provisional perception smoke profile" for notice in snapshot.get("notices", [])
        )
        environment = client.get("/api/v1/system/environment").json()
        checks["container_runtime_reported"] = environment["runtime"]["available"] and environment["runtime"]["runtime_detected"]
        checks["container_capability_scopes"] = all(
            capability["available"] and capability["scope"] == "pinned-deepstream-container"
            for capability in environment["runtime"]["capabilities"].values()
        )
        checks["smoke_not_baseline"] = (
            environment["models"]["selection_mode"] == "provisional_smoke"
            and environment["models"]["evaluated_baseline_selected"] is False
        )
        stopped = client.post("/api/v1/demo/stop")
        checks["stop_http_ok"] = stopped.status_code == 200 and stopped.json().get("run_status") == "STOPPED"
        container = f"aegis-cam-01-{epoch}"
        remaining = subprocess.run(
            ["wsl.exe", "-d", "Ubuntu-22.04", "-e", "docker", "ps", "-aq", "--filter", f"name=^{container}$"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        checks["container_cleaned_up"] = remaining.returncode == 0 and not remaining.stdout.strip()
        observed = {
            "run_id": start_body.get("run_id"),
            "camera_epoch": epoch,
            "first_visible_person_count": first_count,
            "maximum_observed_person_count_before_stop": max_count,
            "camera_health_at_observation": health,
            "stop_status": stopped.json().get("run_status"),
            "runtime_adapter": environment["runtime"]["adapter"],
            "capability_scopes": sorted({item["scope"] for item in environment["runtime"]["capabilities"].values()}),
            "remaining_container_id": remaining.stdout.strip() or None,
        }
    report = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": run.command,
        "run_dir": str(run_dir.relative_to(ROOT)),
        "result": "PASS" if all(checks.values()) else "FAIL",
        "checks": {name: "PASS" if passed else "FAIL" for name, passed in checks.items()},
        "observed": observed,
        "limitations": [
            "The run was stopped after the first fresh positive visible count to test operator stop and container cleanup; full-clip coverage is validated separately.",
            "Visible person counts are real tracker outputs, not ground-truth accuracy or calibrated facility occupancy.",
            "TrafficCamNet is a provisional smoke profile; final candidate comparison remains pending.",
        ],
    }
    (run_dir / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report["result"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
