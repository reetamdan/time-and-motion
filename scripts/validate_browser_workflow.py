#!/usr/bin/env python3
"""Browser-level dashboard validation against real backend processes (not TestClient).

Part A — REAL hardware: the documented `scripts/run-backend.ps1` serves the API + built dashboard on a
real socket; headless Chrome (Playwright) operates the UI through a full paced cam01 run:
start, live counts, source-time history, final bucket after clean EOF without a stop, stale values
shown as unavailable, reconnect across a hard backend restart, previous-epoch labelling, a new epoch,
stop with exact container cleanup, and reset.

Part B — TEST-ONLY fault injection: scripts/browser/fault_injection_backend.py serves the same
dashboard with an adapter whose first resource release fails, to verify STOP_FAILED presentation and
the cleanup retry. Part B produces no perception evidence.

Run with the isolated browser environment:
  .install/browser-tests/venv/Scripts/python.exe scripts/validate_browser_workflow.py
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
import urllib.request
from typing import Any, Callable

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
from evidence_dirs import EvidenceExistsError, resolve_run_dir  # noqa: E402

REAL_PORT = 8766
FAULT_PORT = 8767
BACKEND_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
CAMERA_NAME = "Outdoor Pedestrian Area"


class Run:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.checks: dict[str, bool] = {}
        self.observed: dict[str, Any] = {}
        self.errors: list[str] = []
        self.shots = 0

    def check(self, name: str, passed: bool, detail: Any = None) -> bool:
        self.checks[name] = bool(passed)
        if detail is not None:
            self.observed[name] = detail
        print(f"{'PASS' if passed else 'FAIL'} {name} {'' if detail is None else json.dumps(detail, default=str)[:200]}", flush=True)
        return bool(passed)

    def shot(self, page: Page, label: str) -> None:
        self.shots += 1
        page.screenshot(path=str(self.directory / f"{self.shots:02d}-{label}.png"), full_page=False)


def http_json(url: str, method: str = "GET", timeout: float = 10) -> Any:
    request = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def wait_http(url: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            http_json(url, timeout=3)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def wait_for(predicate: Callable[[], Any], timeout: float, interval: float = 0.25) -> Any:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except Exception:
            last = None
        time.sleep(interval)
    return last


def start_real_backend(run: Run, database: Path, label: str) -> subprocess.Popen:
    env = dict(os.environ, CCTV_DATABASE_PATH=str(database), BACKEND_PORT=str(REAL_PORT))
    log = (run.directory / f"backend-{label}.log").open("w", encoding="utf-8")
    # The documented Windows entry point (docs/operations-guide.md §3), unchanged.
    return subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                             str(ROOT / "scripts" / "run-backend.ps1"), "-Port", str(REAL_PORT)],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)


def kill_tree(process: subprocess.Popen) -> None:
    # Owned process only: the PowerShell launcher we started and its uvicorn child.
    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
    try:
        process.wait(15)
    except subprocess.TimeoutExpired:
        pass


def container_absent(name: str) -> bool:
    result = subprocess.run(["wsl.exe", "-d", "Ubuntu-22.04", "-e", "docker", "ps", "-a", "--filter", f"name={name}",
                             "--format", "{{.Names}}"], capture_output=True, text=True, timeout=30, check=False)
    return result.returncode == 0 and name not in {line.strip() for line in result.stdout.splitlines()}


def status_text(page: Page) -> str:
    """Run status from the dashboard's run-status badges (the top-bar copy is hidden at some widths)."""
    values = {page.locator("[data-run-status]").nth(i).get_attribute("data-run-status")
              for i in range(page.locator("[data-run-status]").count())}
    visible = [page.locator("[data-run-status]").nth(i).inner_text().strip()
               for i in range(page.locator("[data-run-status]").count()) if page.locator("[data-run-status]").nth(i).is_visible()]
    return " | ".join(sorted(v or "" for v in values)) + " :: " + " | ".join(visible)


def connection_text(page: Page) -> str:
    return page.locator("body").inner_text()


def drawer_count(page: Page) -> str:
    return page.locator("[data-testid=detail-visible-count] strong").inner_text().strip()


def history_attrs(page: Page) -> dict[str, Any]:
    section = page.locator("[data-testid=count-history]")
    return {"samples": int(section.get_attribute("data-samples") or 0), "epoch": section.get_attribute("data-epoch"),
            "scope": section.get_attribute("data-scope"), "line_paths": page.locator("[data-testid=count-history] .recharts-line path").count(),
            "label": section.inner_text().split("\n")[1] if section.count() else None}


def close_camera(page: Page) -> None:
    close = page.get_by_role("button", name=f"Close {CAMERA_NAME}")
    if close.count():
        close.first.click()
        page.locator("[data-testid=count-history]").wait_for(state="detached", timeout=10000)


def control(page: Page, name: str, exact: bool = True) -> None:
    """Header controls sit behind the camera drawer, as for a real user: close it first."""
    close_camera(page)
    page.get_by_role("button", name=name, exact=exact).click()


def open_camera(page: Page) -> None:
    if page.locator("[data-testid=count-history]").count():
        return
    page.get_by_role("button", name=re.compile(f"Open details for {CAMERA_NAME}")).first.click()
    page.locator("[data-testid=count-history]").wait_for(timeout=15000)


def part_a(run: Run, page: Page) -> None:
    database = run.directory / "real-backend.db"
    backend = start_real_backend(run, database, "1")
    try:
        run.check("real_backend_served_on_socket", wait_http(f"http://127.0.0.1:{REAL_PORT}/health/live", 90))
        page.goto(f"http://127.0.0.1:{REAL_PORT}/")
        run.check("dashboard_live_connection", wait_for(lambda: "Live connection" in connection_text(page), 30))
        run.shot(page, "idle")
        control(page, "Start demo")
        run.check("start_control_reaches_running", wait_for(lambda: "RUNNING" in status_text(page), 120),
                  {"status": status_text(page)})
        open_camera(page)
        counts: list[str] = []

        def positive() -> bool:
            value = drawer_count(page)
            counts.append(value)
            return value.isdigit() and int(value) > 0

        run.check("real_live_count_rendered", wait_for(positive, 90), {"sampled_texts": counts[-5:]})
        run.shot(page, "live-count")
        kpis = http_json(f"http://127.0.0.1:{REAL_PORT}/api/v1/facility/snapshot")["kpis"]
        run.check("uncalibrated_entries_exits_unknown_not_zero", kpis.get("entries") is None and kpis.get("exits") is None,
                  {"entries": kpis.get("entries"), "exits": kpis.get("exits")})
        run.check("history_chart_renders_during_run",
                  wait_for(lambda: (lambda a: a["samples"] > 0 and a["line_paths"] > 0 and a["scope"] == "live")(history_attrs(page)), 60),
                  history_attrs(page))
        # Clean EOF: no Stop click. The final bucket must appear on its own.
        run.check("clean_eof_shown_without_stop",
                  wait_for(lambda: "clean EOF" in page.locator("[data-testid=stream-outcome]").inner_text(), 90),
                  {"text": page.locator("[data-testid=stream-outcome]").inner_text()})
        eof_history = wait_for(lambda: (lambda a: a if a["samples"] == 48 else None)(history_attrs(page)), 20)
        run.check("final_bucket_displayed_after_eof_without_stop", bool(eof_history), history_attrs(page))
        stale = wait_for(lambda: drawer_count(page) == "Unavailable", 15)
        run.check("stale_count_shown_unavailable_not_zero", bool(stale) and drawer_count(page) != "0",
                  {"text": drawer_count(page), "run_status": status_text(page)})
        run.shot(page, "eof-stale")
        first_epoch = history_attrs(page)["epoch"]
        api_history = http_json(f"http://127.0.0.1:{REAL_PORT}/api/v1/cameras/cam-01/count-history")
        run.check("ui_history_matches_api", len(api_history["samples"]) == history_attrs(page)["samples"]
                  and api_history["camera_epoch"] == first_epoch, {"api_samples": len(api_history["samples"])})

        # Reconnect across a hard backend kill (no graceful shutdown) and a restart on the same database.
        kill_tree(backend)
        run.check("ui_detects_backend_loss",
                  wait_for(lambda: any(t in connection_text(page) for t in ("Reconnecting", "REST polling")), 40),
                  {"connection": [t for t in ("Reconnecting", "REST polling", "Live connection") if t in connection_text(page)]})
        run.shot(page, "disconnected")
        backend = start_real_backend(run, database, "2")
        run.check("backend_restart_served", wait_http(f"http://127.0.0.1:{REAL_PORT}/health/live", 120))
        run.check("ui_reconnects_live", wait_for(lambda: "Live connection" in connection_text(page), 60))
        after_restart = wait_for(lambda: (lambda a: a if a["scope"] == "previous" else None)(history_attrs(page)), 30)
        run.check("old_epoch_history_labelled_not_live_after_restart",
                  bool(after_restart) and after_restart["epoch"] == first_epoch and "not live" in (after_restart["label"] or ""),
                  history_attrs(page))
        snapshot = http_json(f"http://127.0.0.1:{REAL_PORT}/api/v1/facility/snapshot")
        run.check("restart_reconciled_run_not_running", snapshot["run_status"] in ("IDLE", "STOPPED"),
                  {"run_status": snapshot["run_status"]})
        run.shot(page, "after-restart")

        # A fresh epoch replaces the old history as current; stop mid-run with exact cleanup.
        control(page, "Start demo")
        open_camera(page)
        new_epoch = wait_for(lambda: (lambda a: a if a["scope"] == "live" and a["epoch"] and a["epoch"] != first_epoch
                                      and a["samples"] > 0 else None)(history_attrs(page)), 120)
        run.check("new_epoch_history_is_current", bool(new_epoch), history_attrs(page))
        epoch2 = (new_epoch or {}).get("epoch")
        control(page, "Stop")
        run.check("stop_control_reaches_stopped", wait_for(lambda: "STOPPED" in status_text(page), 60),
                  {"status": status_text(page)})
        open_camera(page)
        run.check("stopped_epoch_history_not_live",
                  bool(wait_for(lambda: history_attrs(page)["scope"] == "previous", 20)), history_attrs(page))
        run.check("exact_container_absent_after_ui_stop", bool(epoch2) and container_absent(f"aegis-cam-01-{epoch2}"),
                  {"container": f"aegis-cam-01-{epoch2}"})
        run.shot(page, "stopped")

        close_camera(page)
        page.get_by_role("button", name=re.compile("^Reset")).first.click()
        page.get_by_role("button", name="Reset run data").click()
        run.check("reset_returns_idle", wait_for(lambda: "IDLE" in status_text(page), 30), {"status": status_text(page)})
        open_camera(page)
        run.check("reset_clears_history_display",
                  bool(wait_for(lambda: history_attrs(page)["samples"] == 0 and history_attrs(page)["scope"] == "none", 30)),
                  history_attrs(page))
        run.shot(page, "reset")
    finally:
        kill_tree(backend)


def part_b(run: Run, page: Page) -> None:
    database = run.directory / "TEST-ONLY-fault-backend.db"
    log = (run.directory / "backend-TEST-ONLY-fault.log").open("w", encoding="utf-8")
    backend = subprocess.Popen([str(BACKEND_PYTHON), str(ROOT / "scripts" / "browser" / "fault_injection_backend.py"),
                                "--port", str(FAULT_PORT), "--database", str(database)],
                               cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    try:
        run.check("fault_backend_served", wait_http(f"http://127.0.0.1:{FAULT_PORT}/health/live", 60))
        page.goto(f"http://127.0.0.1:{FAULT_PORT}/")
        wait_for(lambda: "Live connection" in connection_text(page), 30)
        control(page, "Start demo")
        run.check("fault_run_running", wait_for(lambda: "RUNNING" in status_text(page), 30))
        control(page, "Stop")
        run.check("stop_failed_presented", wait_for(lambda: "STOP_FAILED" in status_text(page) and "Stop Failed" in status_text(page), 30),
                  {"status": status_text(page)})
        run.check("start_disabled_while_cleanup_unconfirmed", page.get_by_role("button", name="Start demo").is_disabled())
        visible_by_width = {}
        for width, height in ((390, 844), (1280, 800), (1440, 900), (1920, 1080)):
            page.set_viewport_size({"width": width, "height": height})
            time.sleep(0.3)
            visible_by_width[f"{width}x{height}"] = "Stop Failed" in status_text(page).split("::", 1)[1]
        page.set_viewport_size({"width": 1440, "height": 900})
        run.check("stop_failed_visible_at_all_tested_widths", all(visible_by_width.values()), visible_by_width)
        run.check("stop_not_confirmed_reason_visible", wait_for(lambda: "Stop not confirmed" in connection_text(page), 15))
        run.shot(page, "TEST-ONLY-stop-failed")
        control(page, "Stop")
        run.check("cleanup_retry_reaches_stopped", wait_for(lambda: "STOPPED" in status_text(page), 30),
                  {"status": status_text(page)})
        alerts = http_json(f"http://127.0.0.1:{FAULT_PORT}/api/v1/alerts?limit=50")
        cleanup_alerts = [a for a in alerts if a.get("title") == "Camera stop not confirmed"]
        run.check("cleanup_alert_resolved_after_retry", bool(cleanup_alerts) and all(a["status"] == "RESOLVED" for a in cleanup_alerts),
                  {"alerts": [(a["title"], a["status"]) for a in cleanup_alerts]})
        run.shot(page, "TEST-ONLY-retry-stopped")
    finally:
        kill_tree(backend)


def main() -> int:
    try:
        evidence = resolve_run_dir(ROOT, "browser-workflow-cam01", "AEGIS_BROWSER_RUN_DIR")
    except EvidenceExistsError as exc:
        raise SystemExit(str(exc)) from exc
    run = Run(evidence.directory)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        run.observed["browser"] = {"engine": "chromium (installed Google Chrome)", "version": browser.version,
                                   "playwright": "1.55.0", "headless": True}
        for label, part in (("A_real_hardware", part_a), ("B_TEST_ONLY_fault_injection", part_b)):
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            try:
                part(run, page)
            except Exception as exc:
                run.errors.append(f"{label}: {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
                try:
                    run.shot(page, f"{label}-error")
                except Exception:
                    pass
            finally:
                page.close()
        browser.close()
    leftovers = subprocess.run(["wsl.exe", "-d", "Ubuntu-22.04", "-e", "docker", "ps", "-a", "--filter", "name=aegis-cam-01-",
                                "--format", "{{.Names}}"], capture_output=True, text=True, timeout=30, check=False)
    run.check("no_leftover_cam01_containers", leftovers.returncode == 0 and not leftovers.stdout.strip(),
              {"names": leftovers.stdout.split()})
    report = {
        "schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": evidence.command, "run_dir": str(evidence.directory.relative_to(ROOT)),
        "result": "PASS" if run.checks and all(run.checks.values()) and not run.errors else "FAIL",
        "checks": {k: "PASS" if v else "FAIL" for k, v in run.checks.items()},
        "observed": run.observed, "errors": run.errors,
        "scope": {
            "A_real_hardware": "Documented run-backend.ps1 on a real loopback socket + headless Chrome; real GPU cam01 run. "
                               "Structural UI/lifecycle evidence; not detection/count accuracy.",
            "B_TEST_ONLY_fault_injection": "Same dashboard, test-only adapter with an injected first-release failure; no perception evidence.",
        },
    }
    (evidence.directory / "result.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"], "run_dir": report["run_dir"],
                      "failed": [k for k, v in run.checks.items() if not v], "errors": len(run.errors)}, indent=1))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
