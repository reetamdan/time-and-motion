#!/usr/bin/env python3
"""Real-hardware check of start latency (cold vs warm capability cache), artifact identity, per-run
provenance, the STOP-contract, and live operator evidence on demo/cam01.mp4.

Backends run as real processes through the documented scripts/run-backend.ps1 with a fresh
database, evidence root, and capability-cache file inside the run directory, so "cold" really means
no cached capability. Phases:

  A1 cold start   - backend process A, empty capability cache (probe runs)
  A2 warm start   - same process, cache in memory
  A3 forced probe - same process after POST /system/capability/invalidate
  B1 restart warm - backend process B, cache from disk (no probe)

Run: .venv/Scripts/python.exe scripts/validate_startup_and_evidence.py
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
import urllib.error
import urllib.request
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
from evidence_dirs import EvidenceExistsError, resolve_run_dir  # noqa: E402

PORT = 8769
BASE = f"http://127.0.0.1:{PORT}"
CAMERA = "cam-01"


def http(path: str, method: str = "GET", body: dict | None = None, timeout: float = 120) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
    request = urllib.request.Request(BASE + path, method=method, data=data,
                                     headers={"Content-Type": "application/json"} if body is not None else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


class Backend:
    def __init__(self, run_dir: Path, name: str, env: dict[str, str]) -> None:
        self.log = (run_dir / f"backend-{name}.log").open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "run-backend.ps1"),
             "-Port", str(PORT)], cwd=ROOT, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                if http("/health/live", timeout=3)[0] == 200:
                    return
            except Exception:
                time.sleep(0.5)
        raise RuntimeError(f"backend {name} did not become live")

    def kill(self) -> None:
        subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True, check=False)
        self.process.wait(30)
        self.log.close()


def timed_start(label: str) -> dict[str, Any]:
    started = time.perf_counter()
    status, snapshot = http("/api/v1/demo/start", "POST")
    seconds = time.perf_counter() - started
    run_id = snapshot.get("run_id") if isinstance(snapshot, dict) else None
    provenance = http(f"/api/v1/runs/{run_id}/provenance")[1] if run_id else None
    capability = (provenance or {}).get("capability") or {}
    camera = next((c for c in snapshot.get("cameras", []) if c["id"] == CAMERA), {}) if isinstance(snapshot, dict) else {}
    return {"label": label, "http_status": status, "run_status": snapshot.get("run_status") if isinstance(snapshot, dict) else None,
            "run_id": run_id, "epoch": camera.get("epoch"), "start_seconds": round(seconds, 3),
            "capability_source": capability.get("source"), "capability_probe_seconds": capability.get("probe_seconds")}


def timed_stop() -> dict[str, Any]:
    started = time.perf_counter()
    status, snapshot = http("/api/v1/demo/stop", "POST")
    return {"http_status": status, "run_status": snapshot.get("run_status"), "stop_seconds": round(time.perf_counter() - started, 3)}


def wait_source_position(minimum: float, timeout: float = 60) -> float | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _, snapshot = http("/api/v1/facility/snapshot")
        camera = next(c for c in snapshot["cameras"] if c["id"] == CAMERA)
        if camera.get("source_position_s") is not None and camera["source_position_s"] >= minimum:
            return camera["source_position_s"]
        time.sleep(0.25)
    return None


def psnr(a: Path, b: Path) -> float:
    result = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(a), "-i", str(b), "-lavfi", "psnr", "-f", "null", "-"],
                            capture_output=True, text=True, timeout=60, check=False)
    value = result.stderr.rsplit("average:", 1)[-1].split()[0] if "average:" in result.stderr else "nan"
    return float("inf") if value == "inf" else float(value)


def snapshot_identity(run_dir: Path, frame_number: int | None) -> dict[str, Any]:
    """Decode frame index N (== DeepStream frame_number) and N+2 independently and compare with the snapshot."""
    if frame_number is None:
        return {"frame_number": None}
    snapshot = run_dir / "downloaded-operator_marker_snapshot.jpg"
    result = {"frame_number": frame_number}
    for label, index in (("same_index", frame_number), ("index_plus_2", frame_number + 2)):
        reference = run_dir / f"reference-frame-{index}.jpg"
        subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(ROOT / "demo" / "cam01.mp4"),
                        "-vf", f"select=eq(n\\,{index})", "-fps_mode", "passthrough", "-frames:v", "1", "-q:v", "3",
                        str(reference)], check=True, timeout=120)
        result[f"psnr_{label}"] = psnr(snapshot, reference)
    return result


def leftover_containers(database: Path) -> dict[str, Any]:
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT resource_name, state FROM owned_resources").fetchall()
    present = []
    for name, _ in rows:
        result = subprocess.run(["wsl.exe", "-d", "Ubuntu-22.04", "-e", "docker", "ps", "-a", "--filter", f"name={name}",
                                 "--format", "{{.Names}}"], capture_output=True, text=True, timeout=30, check=False)
        if name in result.stdout.split():
            present.append(name)
    return {"owned": [{"name": n, "state": s} for n, s in rows], "present": present}


def main() -> int:
    try:
        evidence = resolve_run_dir(ROOT, "startup-evidence-cam01", "AEGIS_STARTUP_RUN_DIR")
    except EvidenceExistsError as exc:
        raise SystemExit(str(exc)) from exc
    run_dir = evidence.directory
    database = run_dir / "run.db"
    env = dict(os.environ, CCTV_DATABASE_PATH=str(database), CCTV_EVIDENCE_ROOT=str(run_dir / "evidence"),
               CCTV_CAPABILITY_CACHE=str(run_dir / "capability-cache.json"))
    checks: dict[str, bool] = {}
    observed: dict[str, Any] = {"starts": [], "stops": []}

    def check(name: str, passed: bool, detail: Any = None) -> None:
        checks[name] = bool(passed)
        if detail is not None:
            observed[name] = detail
        print(f"{'PASS' if passed else 'FAIL'} {name} {json.dumps(detail, default=str)[:240] if detail is not None else ''}", flush=True)

    backend = Backend(run_dir, "A", env)
    try:
        _, artifacts = http("/api/v1/system/artifacts")
        statuses = {item["name"]: item["status"] for item in artifacts["artifacts"]}
        check("artifact_identities_all_match", bool(statuses) and set(statuses.values()) == {"MATCH"}, statuses)
        check("tracker_identity_compared", statuses.get("tracker.config") == "MATCH")

        cold = timed_start("A1_cold")
        observed["starts"].append(cold)
        check("cold_start_running", cold["run_status"] == "RUNNING", cold)
        check("cold_start_probed", cold["capability_source"] == "probe")
        _, provenance = http(f"/api/v1/runs/{cold['run_id']}/provenance")
        observed["provenance_A1"] = provenance
        check("provenance_records_selected_unaccepted_detector",
              provenance["detector"]["id"] == "trafficcamnet-smoke" and provenance["detector"]["acceptance"] == "provisional_smoke"
              and provenance["detector"]["profile"]["person_class_id"] == 2)
        check("provenance_artifact_checks_match",
              {c["status"] for c in provenance["artifact_checks"]} == {"MATCH"} and len(provenance["artifact_checks"]) >= 8)

        # ---- live operator evidence (real frames from the source file at a real source time)
        reached = wait_source_position(5.0)
        check("source_position_reached", reached is not None, reached)
        status, marker = http(f"/api/v1/cameras/{CAMERA}/evidence-markers", "POST", {"note": "validator marker"})
        check("evidence_marker_created", status == 201, {"status": status, "body": marker if status != 201 else None})
        if status == 201:
            snapshot, clip = marker["snapshot"], marker["clip"]
            observed["evidence_marker"] = marker
            check("evidence_linked_to_run_epoch", snapshot["run_id"] == cold["run_id"] and snapshot["camera_epoch"] == cold["epoch"]
                  and clip["camera_epoch"] == cold["epoch"])
            check("snapshot_frame_within_one_frame_of_marker", abs(snapshot["source_start_s"] - snapshot["source_marked_s"]) < 0.05,
                  {"marked": snapshot["source_marked_s"], "frame": snapshot["source_start_s"]})
            check("clip_starts_on_keyframe_at_or_before_request",
                  clip["source_start_s"] <= clip["metadata"]["requested_source_start_s"] + 1e-6,
                  {"requested": clip["metadata"]["requested_source_start_s"], "actual": clip["source_start_s"],
                   "end": clip["source_end_s"]})
            for record in (snapshot, clip):
                media = urllib.request.urlopen(BASE + record["url"], timeout=30).read()
                (run_dir / f"downloaded-{record['kind']}{'.jpg' if record['media_type'] == 'image/jpeg' else '.mp4'}").write_bytes(media)
                check(f"{record['kind']}_served", len(media) == record["size_bytes"] and len(media) > 1000,
                      {"bytes": len(media)})
            frame_number = snapshot["metadata"].get("pipeline_frame_number")
            observed["snapshot_identity"] = identity = snapshot_identity(run_dir, frame_number)
            check("snapshot_is_the_pipeline_frame_not_a_neighbour",
                  frame_number is not None and identity["psnr_same_index"] >= 45 and identity["psnr_index_plus_2"] < 40, identity)
            alerts = http("/api/v1/alerts")[1]
            linked = [a for a in alerts if a["id"] == marker["alert_id"]]
            check("operator_alert_info_links_evidence", bool(linked) and linked[0]["severity"] == "INFO"
                  and linked[0]["evidence_id"] == snapshot["id"])
        stop = timed_stop()
        observed["stops"].append(stop)
        check("stop_A1", stop["run_status"] == "STOPPED", stop)

        warm = timed_start("A2_warm_memory")
        observed["starts"].append(warm)
        check("warm_start_running", warm["run_status"] == "RUNNING", warm)
        check("warm_start_reused_memory_cache", warm["capability_source"] == "memory")
        observed["stops"].append(timed_stop())

        status, _ = http("/api/v1/system/capability/invalidate", "POST")
        forced = timed_start("A3_after_invalidate")
        observed["starts"].append(forced)
        check("invalidate_forces_probe", status == 200 and forced["capability_source"] == "probe", forced)
        observed["stops"].append(timed_stop())
    finally:
        backend.kill()

    backend = Backend(run_dir, "B", env)
    try:
        restart = timed_start("B1_restart_disk_cache")
        observed["starts"].append(restart)
        check("restart_start_running", restart["run_status"] == "RUNNING", restart)
        check("restart_reused_disk_cache", restart["capability_source"] == "disk")
        observed["stops"].append(timed_stop())
        _, listing = http("/api/v1/evidence")
        check("evidence_survives_backend_restart", len(listing["items"]) == 2, listing["usage"])
    finally:
        backend.kill()

    cleanup = leftover_containers(database)
    observed["cleanup"] = cleanup
    check("all_owned_containers_released_and_absent",
          not cleanup["present"] and all(row["state"] == "RELEASED" for row in cleanup["owned"]) and len(cleanup["owned"]) == 4)
    starts = {s["label"]: s["start_seconds"] for s in observed["starts"]}
    observed["start_seconds"] = starts
    report = {
        "schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "command": evidence.command,
        "run_dir": str(run_dir.relative_to(ROOT)), "result": "PASS" if all(checks.values()) else "FAIL",
        "checks": {k: "PASS" if v else "FAIL" for k, v in checks.items()}, "observed": observed,
        "scope": "One camera (cam-01, the supplied clip), real GPU/container via the documented Windows route. "
                 "Start time = availability (artifact hashes + liveness + capability) + container launch + engine "
                 "load + first metadata frame. The evidence marker is a manual operator action, not an analytics event.",
    }
    (run_dir / "result.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"], "start_seconds": starts, "run_dir": report["run_dir"]}, indent=1))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
