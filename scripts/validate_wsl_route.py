#!/usr/bin/env python3
"""AC-25b: execute the documented WSL application route (backend + frontend build INSIDE WSL).

Orchestrated from Windows; every route step runs inside WSL Ubuntu-22.04 exactly as documented:
  1. bash scripts/setup/install-wsl-toolchain.sh                (pinned, checksum-verified, project-local)
  2. source .install/wsl/env.sh && bash scripts/setup/setup.sh --dev --isolated-frontend
  3. .venv-wsl/bin/python -m pytest tests perception/candidates   (in WSL)
  4. .venv-wsl/bin/python scripts/config/validate_config.py
  5. BACKEND_PORT=... bash scripts/run-backend.sh                 (real socket inside WSL; reached via WSL localhost forwarding)
  6. readiness; start paced -> real GPU counts via docker called directly from WSL (host route linux-docker);
     telemetry scope wsl2; history persisted; evidence marker (WSL ffmpeg); stop -> exact container absent;
     backend restart on the same database keeps history; start again; bash scripts/reset-demo.sh --run-id;
     stop; owned backend process terminated; no owned container left.
Deviations: fresh database/evidence root via CCTV_DATABASE_PATH/CCTV_EVIDENCE_ROOT and a non-default port.

Run: .venv/Scripts/python.exe scripts/validate_wsl_route.py
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
from evidence_dirs import EvidenceExistsError, resolve_run_dir  # noqa: E402

DISTRO = "Ubuntu-22.04"
PORT = 8774
BASE = f"http://127.0.0.1:{PORT}"
WSL_ROOT = "/mnt/" + str(ROOT)[0].lower() + str(ROOT)[2:].replace("\\", "/")


def wsl_bash(script: str, timeout: float = 1800) -> tuple[int | None, str]:
    try:
        completed = subprocess.run(["wsl.exe", "-d", DISTRO, "-e", "bash", "-c", f"cd {shlex.quote(WSL_ROOT)} && {script}"],
                                   capture_output=True, text=True, timeout=timeout, check=False)
        return completed.returncode, completed.stdout + completed.stderr
    except subprocess.TimeoutExpired as exc:
        return None, f"timeout: {exc}"


def http(path: str, method: str = "GET", body: dict | None = None, timeout: float = 180) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
    request = urllib.request.Request(BASE + path, method=method, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def wait(predicate, timeout: float, interval: float = 0.5):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except Exception:  # noqa: BLE001
            last = None
        time.sleep(interval)
    return last


def main() -> int:
    try:
        evidence = resolve_run_dir(ROOT, "wsl-route", "AEGIS_WSL_ROUTE_RUN_DIR")
    except EvidenceExistsError as exc:
        raise SystemExit(str(exc)) from exc
    out = evidence.directory
    wsl_out = "/mnt/" + str(out)[0].lower() + str(out)[2:].replace("\\", "/")
    checks: dict[str, bool] = {}
    observed: dict[str, Any] = {}

    def check(name: str, ok: bool, detail: Any = None) -> None:
        checks[name] = bool(ok)
        if detail is not None:
            observed[name] = detail
        print(f"{'PASS' if ok else 'FAIL'} {name} {json.dumps(detail, default=str)[:240] if detail is not None else ''}", flush=True)

    def step(name: str, script: str, timeout: float = 1800) -> tuple[int | None, str]:
        code, output = wsl_bash(script, timeout)
        (out / f"{name}.log").write_text(f"$ {script}\n\n{output}", encoding="utf-8")
        check(f"{name}_exit_0", code == 0, {"tail": output.strip().splitlines()[-3:]})
        return code, output

    step("install_wsl_toolchain", "bash scripts/setup/install-wsl-toolchain.sh")
    step("setup", "source .install/wsl/env.sh && bash scripts/setup/setup.sh --dev --isolated-frontend")
    _, tests = step("wsl_pytest", "rm -rf /tmp/aegis-wsl-route-pytest && .venv-wsl/bin/python -m pytest -q -p no:cacheprovider "
                                  "--basetemp /tmp/aegis-wsl-route-pytest tests perception/candidates", 1800)
    observed["wsl_pytest_summary"] = [line for line in tests.splitlines() if " passed" in line or " failed" in line][-1:]
    step("validate_config", ".venv-wsl/bin/python scripts/config/validate_config.py")

    env = f"BACKEND_PORT={PORT} CCTV_DATABASE_PATH={shlex.quote(wsl_out + '/run.db')} CCTV_EVIDENCE_ROOT={shlex.quote(wsl_out + '/evidence')} " \
          f"CCTV_CAPABILITY_CACHE={shlex.quote(wsl_out + '/capability-cache.json')}"

    def port_listeners() -> str:
        output = wsl_bash(f"ss -ltnpH 'sport = :{PORT}'", 30)[1]
        return " | ".join(line.strip() for line in output.splitlines() if f":{PORT}" in line)

    def owned_listener_pid() -> str:
        """The pid actually listening on PORT, only if its environment carries this run's database path."""
        match = re.search(r"pid=(\d+)", port_listeners())
        if not match:
            return ""
        pid = match.group(1)
        environ = wsl_bash(f"tr '\\0' '\\n' < /proc/{pid}/environ", 30)[1]
        return pid if f"CCTV_DATABASE_PATH={wsl_out}/run.db" in environ else ""

    def require_free_port(label: str) -> None:
        # A leftover listener would answer every HTTP check below instead of the process under test.
        listeners = port_listeners()
        check(f"port_{PORT}_free_before_{label}", listeners == "", {"listeners": listeners})
        if listeners:
            raise RuntimeError(f"port {PORT} is already in use before starting {label}: {listeners}")

    def start_backend(label: str) -> subprocess.Popen:
        require_free_port(label)
        log = (out / f"backend-{label}.log").open("w", encoding="utf-8")
        # setsid + pid file: the exact WSL backend process can be terminated later (owned process only); the log is
        # written inside WSL. `--wait` keeps wsl.exe attached until the process exits: plain setsid forks and returns at
        # once, and the detached child raced the session teardown (2026-10-28: backend 2 never wrote its pid file,
        # run wsl-route-20261028T182543776757Z; same defect as rtsp-controlled-20261028T165356944756Z).
        script = (f"cd {shlex.quote(WSL_ROOT)} && echo $$ > {shlex.quote(wsl_out + f'/backend-{label}.pid')} && "
                  f"exec env {env} bash scripts/run-backend.sh > {shlex.quote(wsl_out + f'/backend-{label}.log')} 2>&1")
        log.close()
        return subprocess.Popen(["wsl.exe", "-d", DISTRO, "-e", "setsid", "--wait", "bash", "-c", script],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop_backend(label: str, process: subprocess.Popen, children: list[str] | None = None) -> bool:
        """TERM the recorded pid and give its own cleanup (run-demo.sh stops its backend child) up to 60 s.

        Terminated means: the recorded pid and every recorded child pid are gone AND nothing listens on the port.
        An owned child that outlived its parent is stopped explicitly and reported as a failure.
        """
        pid = (out / f"backend-{label}.pid").read_text().strip() if (out / f"backend-{label}.pid").is_file() else ""
        # setsid may fork, so the recorded shell pid need not be the server: the owned listener is stopped too.
        listener = owned_listener_pid()
        children = [*(children or []), *([listener] if listener and listener != pid else [])]
        observed[f"listener_pid_{label}"] = listener
        if pid.isdigit():
            wsl_bash(f"kill -TERM {pid}; for i in $(seq 1 240); do kill -0 {pid} 2>/dev/null || exit 0; sleep 0.25; done; kill -KILL {pid}", 90)
        try:
            process.wait(30)
        except subprocess.TimeoutExpired:
            process.kill()
        orphans = [c for c in children if c.isdigit()
                   and wsl_bash(f"kill -0 {c} 2>/dev/null && echo alive || echo gone", 30)[1].strip().endswith("alive")]
        for child in orphans:
            wsl_bash(f"kill -TERM {child}; for i in $(seq 1 120); do kill -0 {child} 2>/dev/null || exit 0; sleep 0.25; done; kill -KILL {child}", 60)
        listeners = wait(lambda: port_listeners() == "" or None, 30) is True
        observed[f"stop_{label}"] = {"pid": pid, "orphaned_children": orphans, "port_free": listeners}
        # A listener stopped only by that explicit TERM is the expected setsid case, not an orphan of a parent's cleanup.
        orphans = [c for c in orphans if c != listener]
        return (pid.isdigit() and not orphans and listeners
                and wsl_bash(f"kill -0 {pid} 2>/dev/null && echo alive || echo gone", 30)[1].strip().endswith("gone"))

    # Any exception still writes result.json: later checks are then recorded as not executed, never as passed.
    try:
        backend = start_backend("1")
        run_ids: list[str] = []
        epochs: list[str] = []
        try:
            check("backend_served_from_wsl", bool(wait(lambda: http("/health/live")[0] == 200, 120)))
            log_text = (out / "backend-1.log").read_text(encoding="utf-8", errors="replace")
            check("launch_message_matches_route", f"http://127.0.0.1:{PORT}" in log_text and "/mnt/" in wsl_out, {"log_head": log_text[:200]})
            status, ready = http("/health/ready")
            check("readiness_ok", status == 200, {"status": status, "checks": ready})
            environment = http("/api/v1/system/environment")[1]
            observed["environment"] = environment
            status, snapshot = http("/api/v1/demo/start?replay_mode=paced", "POST")
            check("start_running", status == 200 and snapshot.get("run_status") == "RUNNING", {"status": status, "run_status": snapshot.get("run_status")})
            run_ids.append(snapshot.get("run_id"))
            epoch = next((c["epoch"] for c in snapshot.get("cameras", []) if c["id"] == "cam-01"), None)
            epochs.append(epoch)
            provenance = http(f"/api/v1/runs/{run_ids[0]}/provenance")[1]
            check("provenance_artifacts_match", all(c.get("status") == "MATCH" for c in provenance.get("artifact_checks", [])) and bool(provenance.get("artifact_checks")),
                  {"checks": [(c.get("name"), c.get("status")) for c in provenance.get("artifact_checks", [])]})

            def cam() -> dict:
                return next(c for c in http("/api/v1/facility/snapshot")[1]["cameras"] if c["id"] == "cam-01")
            positive = wait(lambda: (lambda c: c if (c.get("visible_count") or 0) > 0 and (c.get("source_position_s") or 0) > 5 else None)(cam()), 120)
            check("real_gpu_counts_via_docker_from_wsl", bool(positive), {"visible_count": positive and positive.get("visible_count"),
                                                                          "source_position_s": positive and positive.get("source_position_s")})
            metrics = http("/api/v1/system/metrics")[1]
            check("telemetry_scope_wsl2", metrics.get("scope") == "wsl2" and metrics.get("gpu_name"), {"scope": metrics.get("scope"), "gpu": metrics.get("gpu_name")})
            status, marker = http("/api/v1/cameras/cam-01/evidence-markers", "POST", {"note": "wsl route validator"}, timeout=180)
            check("evidence_marker_with_wsl_ffmpeg", status == 201 and marker.get("snapshot", {}).get("file_available"), {"status": status})
            if status == 201:
                image = urllib.request.urlopen(BASE + f"/api/v1/evidence/{marker['snapshot']['id']}", timeout=60).read()
                check("evidence_image_served", image[:2] == b"\xff\xd8" and len(image) > 10_000, {"bytes": len(image)})
            eof = wait(lambda: (lambda c: c if c.get("stream_outcome") else None)(cam()), 120)
            check("clean_eof", bool(eof) and eof.get("stream_outcome") == "CLEAN_EOF", {"outcome": eof and eof.get("stream_outcome")})
            history = http("/api/v1/cameras/cam-01/count-history")[1]
            frames = sum(s["frames"] for s in history.get("samples", []))
            check("history_persisted_all_frames", frames == 710, {"frames": frames, "samples": len(history.get("samples", []))})
            status, stopped = http("/api/v1/demo/stop", "POST")
            check("stop_confirmed", status == 200 and stopped.get("run_status") == "STOPPED")
            name = f"aegis-cam-01-{epoch}"
            check("exact_container_absent_after_stop", wsl_bash(f"docker ps -a --filter name={name} --format '{{{{.Names}}}}'", 60)[1].strip() == "", {"container": name})
        finally:
            check("backend_1_terminated", stop_backend("1", backend))

        backend = start_backend("2")
        try:
            check("backend_restarted_same_database", bool(wait(lambda: http("/health/live")[0] == 200, 120)))
            history = http(f"/api/v1/cameras/cam-01/count-history?run_id={run_ids[0]}&epoch={epochs[0]}")[1]
            check("history_survives_restart", sum(s["frames"] for s in history.get("samples", [])) == 710, {"samples": len(history.get("samples", []))})
            status, snapshot = http("/api/v1/demo/start?replay_mode=unpaced", "POST")
            check("second_start_running", status == 200 and snapshot.get("run_status") == "RUNNING",
                  {"status": status, "run_status": snapshot.get("run_status"), "error": snapshot.get("error") or snapshot.get("detail")})
            run_id = snapshot.get("run_id")
            epoch2 = next((c["epoch"] for c in snapshot.get("cameras", []) if c["id"] == "cam-01"), None)
            wait(lambda: (next(c for c in http("/api/v1/facility/snapshot")[1]["cameras"] if c["id"] == "cam-01").get("visible_count") or 0) > 0, 60)
            code, output = wsl_bash(f"bash scripts/reset-demo.sh --run-id {shlex.quote(run_id)} --api-url {BASE}", 120)
            (out / "reset.log").write_text(output, encoding="utf-8")
            snapshot = http("/api/v1/facility/snapshot")[1]
            check("documented_reset_returns_idle", code == 0 and snapshot.get("run_status") == "IDLE", {"code": code, "tail": output.strip().splitlines()[-2:]})
            name = f"aegis-cam-01-{epoch2}"
            check("reset_released_container", wsl_bash(f"docker ps -a --filter name={name} --format '{{{{.Names}}}}'", 60)[1].strip() == "", {"container": name})
        finally:
            check("backend_2_terminated", stop_backend("2", backend))
        # PRD 23.2 full-demo entry point: strict frozen preflight, then backend readiness and the served dashboard.
        demo = subprocess.Popen(["wsl.exe", "-d", DISTRO, "-e", "setsid", "--wait", "bash", "-c",
                                 f"cd {shlex.quote(WSL_ROOT)} && echo $$ > {shlex.quote(wsl_out + '/backend-run-demo.pid')} && "
                                 f"exec env {env} bash scripts/run-demo.sh --strict > {shlex.quote(wsl_out + '/run-demo.log')} 2>&1"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            ready = wait(lambda: http("/health/ready")[0] == 200, 180)
            html = urllib.request.urlopen(BASE + "/", timeout=30).read().decode("utf-8", errors="replace") if ready else ""
            # run-demo.sh prints its own readiness line after its next poll of /health/ready: wait for it (bounded).
            wait(lambda: "Demo processes are ready" in (out / "run-demo.log").read_text(encoding="utf-8", errors="replace"), 60)
            log_text = (out / "run-demo.log").read_text(encoding="utf-8", errors="replace")
            check("run_demo_strict_preflight_and_ready", bool(ready) and "Demo processes are ready" in log_text and "<div id=\"root\">" in html,
                  {"log_tail": log_text.strip().splitlines()[-3:]})
        finally:
            # run-demo.sh records its backend child in <Logs dir>/run-metadata.txt; that child must go too.
            log_text = (out / "run-demo.log").read_text(encoding="utf-8", errors="replace") if (out / "run-demo.log").is_file() else ""
            logs_dir = next((line.split("Logs:", 1)[1].strip() for line in log_text.splitlines() if line.startswith("Logs:")), "")
            meta = wsl_bash(f"cat {shlex.quote(logs_dir + '/run-metadata.txt')}", 30)[1] if logs_dir else ""
            children = [line.split("=", 1)[1].strip() for line in meta.splitlines() if line.startswith(("backend_pid=", "frontend_pid="))]
            check("run_demo_terminated", stop_backend("run-demo", demo, children) if (out / "backend-run-demo.pid").is_file() else False,
                  {"children": children})
        leftovers = wsl_bash("docker ps -a --filter name=aegis-cam-01- --format '{{.Names}}'", 60)[1].strip()
        check("no_owned_container_left", leftovers == "", {"names": leftovers.split()})
    except Exception as exc:  # noqa: BLE001
        check("validator_completed_without_exception", False, {"error": f"{type(exc).__name__}: {exc}"})
        leftover = owned_listener_pid()
        if leftover:  # stop this run's own backend before reporting
            wsl_bash(f"kill -TERM {leftover}; for i in $(seq 1 120); do kill -0 {leftover} 2>/dev/null || exit 0; sleep 0.25; done; kill -KILL {leftover}", 60)
        observed["exception_cleanup_listener_pid"] = leftover
    else:
        check("validator_completed_without_exception", True)

    report = {"schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "command": evidence.command,
              "run_dir": str(out.relative_to(ROOT)), "result": "PASS" if checks and all(checks.values()) else "FAIL",
              "checks": {k: "PASS" if v else "FAIL" for k, v in checks.items()}, "observed": observed,
              "route": "WSL application route: backend (uvicorn) and frontend build inside WSL Ubuntu-22.04; perception through docker "
                       "called directly from WSL (host_route linux-docker). Real GPU run of demo/cam01.mp4."}
    (out / "result.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"], "run_dir": report["run_dir"], "failed": [k for k, v in checks.items() if not v]}, indent=1))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
