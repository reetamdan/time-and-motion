#!/usr/bin/env python3
"""Capture dashboard screenshots of every view at fixed widths against a REAL backend + GPU run.

Starts the documented `scripts/run-backend.ps1` on its own port with an isolated database and
evidence root, starts a paced demo run (real TrafficCamNet + NvDCF on demo/cam01.mp4), waits for
live counts, then screenshots each view at 390/768/1024/1280/1440/1920 px plus the camera
inspector. Every browser request is recorded; any request to a host other than 127.0.0.1 is
listed in requests.json (expected: none). The run is stopped and the backend process tree killed.

Run: .install/browser-tests/venv/Scripts/python.exe scripts/capture_ui_screenshots.py --label before|after
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
from evidence_dirs import utc_stamp  # noqa: E402

PORT = 8769
BASE = f"http://127.0.0.1:{PORT}"
WIDTHS = [390, 768, 1024, 1280, 1440, 1920]
HEIGHTS = {390: 844, 768: 1024, 1024: 768, 1280: 800, 1440: 900, 1920: 1080}
VIEWS = ["overview", "cameras", "floors", "analytics", "alerts", "evidence", "system"]


def http(path: str, method: str = "GET") -> tuple[int, object]:
    request = urllib.request.Request(BASE + path, method=method, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    out = ROOT / "reports" / "ui" / f"screenshots-{args.label}-{utc_stamp()}"
    out.mkdir(parents=True)
    env = dict(os.environ, CCTV_DATABASE_PATH=str(out / "run.db"), CCTV_EVIDENCE_ROOT=str(out / "evidence"),
               CCTV_CAPABILITY_CACHE=str(out / "capability-cache.json"))
    log = (out / "backend.log").open("w", encoding="utf-8")
    backend = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                str(ROOT / "scripts" / "run-backend.ps1"), "-Port", str(PORT)],
                               cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    external: list[str] = []
    shots: list[str] = []
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if http("/health/live")[0] == 200:
                    break
            except Exception:
                time.sleep(0.5)
        status, _ = http("/api/v1/demo/start?replay_mode=paced", "POST")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            cameras = http("/api/v1/facility/snapshot")[1]["cameras"]  # type: ignore[index]
            if any(c.get("visible_count") for c in cameras):
                break
            time.sleep(0.5)
        time.sleep(4)  # some history buckets
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            for width in WIDTHS:
                context = browser.new_context(viewport={"width": width, "height": HEIGHTS[width]})
                page = context.new_page()
                page.on("request", lambda request: external.append(request.url)
                        if urllib.parse.urlparse(request.url).hostname not in ("127.0.0.1", None) else None)
                for view in VIEWS:
                    page.goto(f"{BASE}/#{view}")
                    page.wait_for_timeout(1500)
                    path = out / f"{width:04d}-{view}.png"
                    page.screenshot(path=str(path), full_page=False)
                    shots.append(path.name)
                page.goto(f"{BASE}/#cameras")
                page.wait_for_timeout(1000)
                opener = page.get_by_role("button", name=re.compile("Open details for"))
                if opener.count():
                    opener.first.click()
                    page.wait_for_timeout(2000)
                    path = out / f"{width:04d}-camera-inspector.png"
                    page.screenshot(path=str(path), full_page=False)
                    shots.append(path.name)
                context.close()
            browser.close()
        http("/api/v1/demo/stop", "POST")
    finally:
        subprocess.run(["taskkill", "/PID", str(backend.pid), "/T", "/F"], capture_output=True, check=False)
        backend.wait(30)
        log.close()
    (out / "requests.json").write_text(json.dumps({
        "generated_at_utc": datetime.now(timezone.utc).isoformat(), "start_status": status,
        "external_requests": sorted(set(external)), "screenshots": shots,
        "scope": "Real backend + real GPU paced run of demo/cam01.mp4; screenshots after live counts appeared."},
        indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"dir": str(out.relative_to(ROOT)), "screenshots": len(shots), "external_requests": sorted(set(external))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
