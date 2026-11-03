#!/usr/bin/env python3
"""AC-25: execute the currently documented setup / run / reset / test commands and check troubleshooting references.

Commands are taken from docs/operations-guide.md (formerly README.md) as written. Deviations, recorded per step:
- setup steps run in a scratch copy (fresh venv, copy of frontend/) so the working project's
  .venv / node_modules are not replaced and run-backend.ps1's interpreter choice is not changed;
- the run/reset steps use a fresh database through CCTV_DATABASE_PATH and a non-default loopback port.

Run: .venv/Scripts/python.exe scripts/validate_documented_commands.py
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
from evidence_dirs import EvidenceExistsError, resolve_run_dir  # noqa: E402

sys.path.insert(0, str(ROOT))
from backend.app.perception.service_maker import wsl_path  # noqa: E402

PORT = 8768


def run_step(run_dir: Path, name: str, command: list[str], cwd: Path, timeout: int = 900, env: dict | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    log = run_dir / f"{name}.log"
    try:
        completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)
        output, code = completed.stdout + completed.stderr, completed.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        output, code = f"{type(exc).__name__}: {exc}", None
    log.write_text(f"COMMAND: {json.dumps(command)}\nCWD: {cwd}\n\n{output}", encoding="utf-8")
    return {"command": command, "cwd": str(cwd), "returncode": code, "seconds": round(time.perf_counter() - started, 1),
            "log": log.name, "tail": output.strip().splitlines()[-3:] if output.strip() else []}


def http(url: str, method: str = "GET") -> tuple[int, Any]:
    request = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def main() -> int:
    try:
        evidence = resolve_run_dir(ROOT, "documented-commands", "AEGIS_DOCS_RUN_DIR")
    except EvidenceExistsError as exc:
        raise SystemExit(str(exc)) from exc
    run_dir = evidence.directory
    scratch = run_dir / "scratch"
    scratch.mkdir()
    steps: dict[str, dict[str, Any]] = {}
    checks: dict[str, bool] = {}

    # --- operations guide §2 "Windows API/frontend development environment" (scratch copy) ---
    venv = scratch / "venv-windows"
    steps["setup_create_venv"] = run_step(run_dir, "setup_create_venv", ["py", "-3.12", "-m", "venv", str(venv)], ROOT)
    pip = venv / "Scripts" / "python.exe"
    steps["setup_pip_install"] = run_step(run_dir, "setup_pip_install",
                                          [str(pip), "-m", "pip", "install", "-r", str(ROOT / "backend" / "requirements.txt")], ROOT)
    steps["setup_pip_check"] = run_step(run_dir, "setup_pip_check", [str(pip), "-m", "pip", "check"], ROOT)
    frontend = scratch / "frontend"
    shutil.copytree(ROOT / "frontend", frontend, ignore=shutil.ignore_patterns("node_modules", "dist"))
    npm = shutil.which("npm") or "npm"
    steps["setup_npm_ci"] = run_step(run_dir, "setup_npm_ci", [npm, "ci"], frontend)
    steps["setup_npm_run_build"] = run_step(run_dir, "setup_npm_run_build", [npm, "run", "build"], frontend)
    for name in ("setup_create_venv", "setup_pip_install", "setup_pip_check", "setup_npm_ci", "setup_npm_run_build"):
        checks[name] = steps[name]["returncode"] == 0

    # --- operations guide §4 tests, exactly as documented ---
    steps["tests_documented_python_windows"] = run_step(
        run_dir, "tests_documented_python_windows",
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--basetemp", str(scratch / "pytest"), "tests", "perception/candidates"], ROOT)
    checks["tests_documented_python_windows"] = steps["tests_documented_python_windows"]["returncode"] == 0
    # .venv-wsl/bin/python is a Linux symlink (Windows cannot stat it): the documented WSL command runs inside WSL.
    if (ROOT / ".venv-wsl" / "pyvenv.cfg").is_file():
        steps["tests_documented_python_wsl"] = run_step(
            run_dir, "tests_documented_python_wsl",
            ["wsl.exe", "-d", "Ubuntu-22.04", "--cd", wsl_path(ROOT), "-e", ".venv-wsl/bin/python", "-m", "pytest", "-q",
             "-p", "no:cacheprovider", "--basetemp", f"/tmp/aegis-pytest-{run_dir.name}", "tests", "perception/candidates"], ROOT)
        checks["tests_documented_python_wsl"] = steps["tests_documented_python_wsl"]["returncode"] == 0
    else:
        steps["tests_documented_python_wsl"] = {
            "status": "NOT_RUN", "reason": "Documented precondition not met in this workspace: .venv-wsl has not been created (docs/operations-guide.md §2)."}
    steps["tests_documented_frontend"] = run_step(run_dir, "tests_documented_frontend", [npm, "test"], frontend)
    checks["tests_documented_frontend"] = steps["tests_documented_frontend"]["returncode"] == 0

    # --- operations guide §2 "Configuration and environment": configuration validator ---
    steps["config_validate"] = run_step(run_dir, "config_validate",
                                        [str(ROOT / ".venv" / "Scripts" / "python.exe"), str(ROOT / "scripts" / "config" / "validate_config.py"),
                                         str(ROOT / "configs")], ROOT)
    checks["config_validate"] = steps["config_validate"]["returncode"] == 0

    # --- operations guide §3 run + §6 reset with the documented scripts ---
    database = run_dir / "documented-run.db"
    env = dict(os.environ, CCTV_DATABASE_PATH=str(database))
    backend_log = (run_dir / "run_backend.log").open("w", encoding="utf-8")
    backend = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                str(ROOT / "scripts" / "run-backend.ps1"), "-Port", str(PORT)],
                               cwd=ROOT, env=env, stdout=backend_log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{PORT}"
    try:
        live = None
        for _ in range(180):
            try:
                live = http(f"{base}/health/live")
                break
            except Exception:
                time.sleep(0.5)
        checks["run_backend_ps1_serves_health_live"] = bool(live and live[0] == 200)
        ready_status, ready = http(f"{base}/health/ready")
        steps["health_ready"] = {"status": ready_status, "body": ready}
        checks["health_ready_structured"] = ready_status in (200, 503) and isinstance(ready, dict) and "checks" in ready
        index_status = urllib.request.urlopen(f"{base}/", timeout=30).status
        checks["dashboard_served_by_backend"] = index_status == 200
        start_status, started = http(f"{base}/api/v1/demo/start", "POST")
        again_status, again = http(f"{base}/api/v1/demo/start", "POST")
        checks["api_start_idempotent_while_running"] = again_status == 200 and again.get("run_id") == started.get("run_id")
        steps["api_start"] = {"status": start_status, "run_status": started.get("run_status"), "run_id": started.get("run_id")}
        checks["api_start_running"] = start_status == 200 and started.get("run_status") == "RUNNING"
        run_id = started.get("run_id")
        time.sleep(3)
        stop_status, stopped = http(f"{base}/api/v1/demo/stop", "POST")
        steps["api_stop"] = {"status": stop_status, "run_status": stopped.get("run_status")}
        checks["api_stop_stopped"] = stop_status == 200 and stopped.get("run_status") == "STOPPED"
        reset_env = dict(os.environ, CCTV_API_URL=base)
        steps["reset_demo_ps1"] = run_step(run_dir, "reset_demo_ps1", ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                                                        "-File", str(ROOT / "scripts" / "reset-demo.ps1"),
                                                                        "-RunId", str(run_id)], ROOT, timeout=120, env=reset_env)
        _, after_reset = http(f"{base}/api/v1/facility/snapshot")
        checks["reset_demo_ps1_resets"] = steps["reset_demo_ps1"]["returncode"] == 0 and after_reset.get("run_status") == "IDLE"
        steps["reset_demo_ps1_mismatch"] = run_step(run_dir, "reset_demo_ps1_mismatch", [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "reset-demo.ps1"),
            "-RunId", "not-the-active-run"], ROOT, timeout=120, env=reset_env)
        checks["reset_demo_ps1_refuses_mismatch"] = steps["reset_demo_ps1_mismatch"]["returncode"] not in (0, None)
    finally:
        subprocess.run(["taskkill", "/PID", str(backend.pid), "/T", "/F"], capture_output=True, check=False)
        backend_log.close()

    # --- operations guide §10 troubleshooting references must exist ---
    references = ["reports/environment/deepstream92/image-reference.txt", "scripts/setup/setup.sh", "scripts/run-demo.ps1",
                  "scripts/run-backend.ps1", "scripts/deepstream.sh", "scripts/config/fit_calibration.py",
                  "scripts/config/validate_config.py", "docs/annotation-guide.md", "scripts/lib/capability-probe.sh"]
    missing = [ref for ref in references if not (ROOT / ref).exists()]
    steps["troubleshooting_references"] = {"checked": references, "missing": missing}
    checks["troubleshooting_references_exist"] = not missing
    leftover = subprocess.run(["wsl.exe", "-d", "Ubuntu-22.04", "-e", "docker", "ps", "-a", "--filter", "name=aegis-cam-01-",
                               "--format", "{{.Names}}"], capture_output=True, text=True, timeout=30, check=False)
    checks["no_leftover_cam01_containers"] = leftover.returncode == 0 and not leftover.stdout.strip()
    shutil.rmtree(scratch / "frontend" / "node_modules", ignore_errors=True)  # large; the npm ci log is kept
    report = {
        "schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "command": evidence.command,
        "run_dir": str(run_dir.relative_to(ROOT)),
        "result": "PASS" if all(checks.values()) else "FAIL",
        "checks": {k: "PASS" if v else "FAIL" for k, v in checks.items()}, "steps": steps,
        "deviations": ["Setup ran in a scratch copy (venv + frontend copy), not in place, to avoid replacing the working "
                       ".venv/node_modules or changing run-backend.ps1's interpreter choice.",
                       "Run/reset used CCTV_DATABASE_PATH (fresh DB) and loopback port 8768."],
        "scope": "Documented Windows setup/run/reset/test commands and troubleshooting references on this laptop. "
                 "Not offline cold start (AC-23) and not the WSL production wrapper.",
    }
    (run_dir / "result.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"], "checks": report["checks"], "run_dir": report["run_dir"]}, indent=1))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
