#!/usr/bin/env python3
"""Browser check (headless Chrome, real backend socket, real GPU run) of evidence, capabilities, and
artifact identity in the dashboard.

1. System view: capability matrix shows why geometry analytics are unavailable for cam01 (MISSING
   calibration) and the artifact table shows recorded-vs-recomputed identities.
2. A paced cam01 run; the operator clicks "Mark evidence" in the camera drawer.
3. Evidence view: the record's camera, epoch, and source time match the API; the snapshot image
   renders at 1920x1080; the clip loads and plays.
4. The clip file is removed from this run's own evidence root: the view shows it as unavailable
   instead of hiding it; the snapshot still renders.
5. Alerts view: the INFO operator-marker alert links the evidence.

Run: .install/browser-tests/venv/Scripts/python.exe scripts/validate_browser_evidence.py
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
from evidence_dirs import EvidenceExistsError, resolve_run_dir  # noqa: E402
from validate_browser_workflow import (  # noqa: E402
    CAMERA_NAME, Run, close_camera, container_absent, control, http_json, kill_tree, open_camera, status_text, wait_for,
    wait_http,
)

PORT = 8771
BASE = f"http://127.0.0.1:{PORT}"


def main() -> int:
    try:
        evidence = resolve_run_dir(ROOT, "browser-evidence-cam01", "AEGIS_BROWSER_EVIDENCE_RUN_DIR")
    except EvidenceExistsError as exc:
        raise SystemExit(str(exc)) from exc
    run = Run(evidence.directory)
    evidence_root = run.directory / "evidence"
    env = dict(os.environ, CCTV_DATABASE_PATH=str(run.directory / "run.db"), CCTV_EVIDENCE_ROOT=str(evidence_root),
               CCTV_CAPABILITY_CACHE=str(run.directory / "capability-cache.json"))
    log = (run.directory / "backend.log").open("w", encoding="utf-8")
    backend = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                str(ROOT / "scripts" / "run-backend.ps1"), "-Port", str(PORT)],
                               cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    epoch = None
    try:
        run.check("backend_served_on_socket", wait_http(f"{BASE}/health/live", 90))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(f"{BASE}/#system")
            matrix = page.locator("[data-testid=capability-matrix]")
            run.check("capability_matrix_rendered", wait_for(lambda: matrix.count() and matrix.is_visible(), 30))
            text = matrix.inner_text() if matrix.count() else ""
            run.check("capability_reason_names_missing_calibration", "calibration MISSING" in text,
                      {"excerpt": text[:300]})
            artifacts = page.locator("[data-testid=artifact-identity]")
            run.check("artifact_identity_table_all_match",
                      wait_for(lambda: artifacts.count() and artifacts.inner_text().count("MATCH") >= 8
                               and "MISMATCH" not in artifacts.inner_text(), 30),
                      {"excerpt": artifacts.inner_text()[:400] if artifacts.count() else None})
            run.shot(page, "system-capabilities")

            page.goto(f"{BASE}/#overview")
            control(page, "Start demo")
            run.check("run_started", wait_for(lambda: "RUNNING" in status_text(page), 120), {"status": status_text(page)})
            open_camera(page)

            def position() -> float | None:
                camera = next(c for c in http_json(f"{BASE}/api/v1/facility/snapshot")["cameras"] if c["id"] == "cam-01")
                return camera["source_position_s"] if (camera["source_position_s"] or 0) >= 4 else None

            run.check("source_time_reached", wait_for(position, 60) is not None)
            page.get_by_label("Evidence marker note").fill("browser validator marker")
            page.locator("[data-testid=mark-evidence]").click()
            result = page.locator("[data-testid=evidence-marker-result]")
            run.check("marker_saved_in_drawer", wait_for(lambda: result.count() and result.is_visible(), 60),
                      {"text": result.inner_text() if result.count() else None})
            run.shot(page, "drawer-marker")
            listing = http_json(f"{BASE}/api/v1/evidence")
            items = listing["items"]
            snapshot = next(i for i in items if i["media_type"] == "image/jpeg")
            clip = next(i for i in items if i["media_type"] == "video/mp4")
            epoch = snapshot["camera_epoch"]
            api_camera = next(c for c in http_json(f"{BASE}/api/v1/facility/snapshot")["cameras"] if c["id"] == "cam-01")
            run.check("api_evidence_bound_to_live_epoch", epoch == api_camera["epoch"], {"evidence": epoch, "camera": api_camera["epoch"]})
            close_camera(page)

            page.goto(f"{BASE}/#evidence")
            card = page.locator("[data-testid=evidence-card]").first
            run.check("evidence_card_rendered", wait_for(lambda: card.count() and card.is_visible(), 30))
            run.check("card_camera_epoch_source_time_match_api",
                      card.get_attribute("data-camera") == "cam-01" and card.get_attribute("data-epoch") == epoch
                      and abs(float(card.get_attribute("data-source-marked")) - snapshot["source_marked_s"]) < 1e-6,
                      {"camera": card.get_attribute("data-camera"), "epoch": card.get_attribute("data-epoch"),
                       "marked": card.get_attribute("data-source-marked"), "api_marked": snapshot["source_marked_s"]})
            image = page.locator("[data-testid=evidence-image]").first
            natural = wait_for(lambda: image.evaluate("img => img.complete && img.naturalWidth") or None, 30)
            run.check("snapshot_image_renders_full_resolution", natural == 1920,
                      {"naturalWidth": natural, "naturalHeight": image.evaluate("img => img.naturalHeight")})
            video = page.locator("[data-testid=evidence-video]").first
            metadata = wait_for(lambda: video.evaluate("v => v.readyState >= 1 ? v.duration : null"), 30)
            run.check("clip_metadata_loaded", metadata is not None and abs(metadata - (clip["source_end_s"] - clip["source_start_s"])) < 0.5,
                      {"duration": metadata, "api_span": clip["source_end_s"] - clip["source_start_s"]})
            video.evaluate("v => { v.muted = true; return v.play(); }")
            played = wait_for(lambda: video.evaluate("v => v.currentTime") > 0.5, 15)
            run.check("clip_plays", bool(played), {"currentTime": video.evaluate("v => v.currentTime")})
            run.shot(page, "evidence-view")

            # Remove the clip file from this run's own evidence root: it must show as unavailable.
            files = list(evidence_root.rglob("clip.mp4"))
            run.check("only_this_runs_clip_present", len(files) == 1 and evidence_root in files[0].parents)
            # Unmount the <video> first: Windows keeps a file open while it is being streamed.
            page.goto(f"{BASE}/#overview")
            removed = wait_for(lambda: (files[0].unlink(), True)[1] if files[0].exists() else True, 20, interval=0.5)
            run.check("clip_file_removed", bool(removed) and not files[0].exists())
            page.goto(f"{BASE}/#evidence")
            unavailable = page.locator("[data-testid=evidence-unavailable]")
            run.check("missing_clip_shown_unavailable", wait_for(lambda: unavailable.count() == 1, 20),
                      {"text": unavailable.first.inner_text() if unavailable.count() else None})
            run.check("snapshot_still_renders", wait_for(lambda: page.locator("[data-testid=evidence-image]").count() == 1, 10))
            run.shot(page, "evidence-clip-unavailable")
            meta = http_json(f"{BASE}/api/v1/evidence/{clip['id']}/meta")
            run.check("api_reports_clip_unavailable", meta["file_available"] is False, {"reason": meta["unavailable_reason"]})

            page.goto(f"{BASE}/#alerts")
            link = page.locator("a.evidence-link").first
            run.check("operator_alert_links_evidence", wait_for(lambda: link.count() and snapshot["id"] in (link.get_attribute("href") or ""), 30),
                      {"href": link.get_attribute("href") if link.count() else None})
            run.shot(page, "alerts-evidence-link")
            control(page, "Stop")
            run.check("run_stopped", wait_for(lambda: "STOPPED" in status_text(page), 60), {"status": status_text(page)})
            browser.close()
    except Exception:
        run.errors.append(traceback.format_exc())
        print(traceback.format_exc(), flush=True)
    finally:
        try:  # never leave a container to a hard kill: stop through the API first
            http_json(f"{BASE}/api/v1/demo/stop", "POST", timeout=60)
        except Exception as exc:  # noqa: BLE001
            run.errors.append(f"final stop: {exc}")
        kill_tree(backend)
        log.close()
    if epoch:
        run.check("owned_container_absent", container_absent(f"aegis-cam-01-{epoch}"))
    report = {"schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "command": evidence.command,
              "run_dir": str(run.directory.relative_to(ROOT)),
              "result": "PASS" if run.checks and all(run.checks.values()) and not run.errors else "FAIL",
              "checks": {k: "PASS" if v else "FAIL" for k, v in run.checks.items()}, "observed": run.observed,
              "errors": run.errors,
              "scope": "Real backend socket + headless Chrome + real paced cam01 GPU run. The evidence marker is a manual "
                       "operator action. File removal targets only this run's own evidence root."}
    (run.directory / "result.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"], "checks": len(run.checks), "run_dir": report["run_dir"]}, indent=1))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
