#!/usr/bin/env python3
"""Browser checks specific to the refactored operations workspace (real backend + real GPU run).

Part A (real hardware, documented run-backend.ps1):
  * every view at 390/768/1024/1280/1440/1920 px: run status visible, no horizontal page overflow,
    no browser console errors, no request to any host other than 127.0.0.1;
  * keyboard: skip link is the first Tab stop and moves focus to main; nav items reachable;
  * camera inspector: opens from a tile, focus moves inside, Tab stays trapped, Escape closes and
    focus returns; history "Browse range" fetches window=range and Earlier/Later change the window;
  * reset dialog: Escape cancels without resetting;
  * mobile (390 px): navigation drawer opens, navigates, closes;
  * theme toggle switches data-theme and persists across reload;
  * facility metrics show "Unavailable" with a reason (uncalibrated clip), never 0;
  * evidence deep link (#evidence?item=<id>) highlights the linked card after a real marker.
Part B (TEST-ONLY fault adapter, no perception evidence):
  * STOP_FAILED: cleanup banner + Retry cleanup visible at every width; Start disabled;
    a direct POST /demo/start returns HTTP 409 CLEANUP_UNCONFIRMED; Retry cleanup reaches STOPPED.

Run: .install/browser-tests/venv/Scripts/python.exe scripts/validate_ui_refactor.py
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
import urllib.parse

import shutil

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))
from evidence_dirs import EvidenceExistsError, resolve_run_dir  # noqa: E402
from validate_browser_workflow import BACKEND_PYTHON, Run, http_json, kill_tree, status_text, wait_for, wait_http  # noqa: E402

PORT = 8771
FAULT_PORT = 8772
BASE = f"http://127.0.0.1:{PORT}"
WIDTHS = [(390, 844), (768, 1024), (1024, 768), (1280, 800), (1440, 900), (1920, 1080)]
VIEWS = ["overview", "cameras", "floors", "analytics", "alerts", "evidence", "system"]


def visible_status(page: Page) -> bool:
    badges = page.locator("[data-run-status]")
    return any(badges.nth(i).is_visible() for i in range(badges.count()))


LONG_CLIP = "./demo/derived/cam01-repeat33.mp4"  # labelled repeated footage (demo/derived/*.manifest.json)


def long_clip_config(directory: Path) -> Path:
    """Configuration copy whose cam-01 replays the labelled 13-minute repeated clip (history range browsing needs > 30 s)."""
    target = directory / "configs"
    shutil.copytree(ROOT / "configs", target)
    path = target / "cameras.yaml"
    text = path.read_text(encoding="utf-8")
    original = "    source: ./demo/cam01.mp4\n"
    if text.count(original) != 1:
        raise RuntimeError("configs/cameras.yaml no longer has exactly one cam01 source line; update the validator")
    path.write_text(text.replace(original, f"    source: {LONG_CLIP}\n"), encoding="utf-8")
    return target


def diagnose(run: Run, page: Page, name: str, selectors: dict[str, str], failures: list[str]) -> None:
    """Record what the page actually showed when a check failed."""
    info: dict = {"url": page.url, "network_failures": failures[-10:]}
    for label, selector in selectors.items():
        try:
            locator = page.locator(selector)
            info[label] = {"count": locator.count(), "visible": locator.first.is_visible() if locator.count() else False,
                           "texts": [locator.nth(i).inner_text()[:80] for i in range(min(locator.count(), 5))]}
        except Exception as exc:  # noqa: BLE001
            info[label] = f"{type(exc).__name__}: {exc}"
    run.observed[f"diagnostic:{name}"] = info
    try:
        run.shot(page, f"FAILED-{name}")
    except Exception:
        pass


def overflow(page: Page) -> int:
    return page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")


def part_a(run: Run, page: Page, external: list[str], console: list[str]) -> None:
    out = run.directory
    env = dict(os.environ, CCTV_DATABASE_PATH=str(out / "real.db"), CCTV_EVIDENCE_ROOT=str(out / "evidence"),
               CCTV_CAPABILITY_CACHE=str(out / "capability-cache.json"), CCTV_CONFIG_DIR=str(long_clip_config(out)))
    failures: list[str] = []
    page.on("requestfailed", lambda request: failures.append(f"{request.method} {request.url}: {request.failure}"))
    page.on("response", lambda response: failures.append(f"HTTP {response.status} {response.url}") if response.status >= 400 else None)
    log = (out / "backend-real.log").open("w", encoding="utf-8")
    backend = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "run-backend.ps1"),
                                "-Port", str(PORT)], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        run.check("backend_served", wait_http(f"{BASE}/health/live", 90))
        page.goto(f"{BASE}/#overview")
        run.check("live_connection", wait_for(lambda: "Live connection" in page.locator("body").inner_text(), 30))

        # Keyboard: first Tab stop is the skip link; activating it focuses main.
        page.keyboard.press("Tab")
        run.check("skip_link_first_tab_stop", page.evaluate("() => document.activeElement?.textContent") == "Skip to main content")
        page.keyboard.press("Enter")
        run.check("skip_link_focuses_main", wait_for(lambda: page.evaluate("() => document.activeElement?.id") == "main-content", 5))

        # Start a real paced run from the UI.
        page.get_by_role("button", name="Paced", exact=True).click()
        page.get_by_role("button", name="Start demo", exact=True).click()
        run.check("ui_start_reaches_running", wait_for(lambda: "RUNNING" in status_text(page), 120), {"status": status_text(page)})
        run.check("replay_mode_locked_while_running", page.get_by_role("button", name="Unpaced", exact=True).is_disabled())
        wait_for(lambda: any(c.get("visible_count") for c in http_json(f"{BASE}/api/v1/facility/snapshot")["cameras"]), 60)

        # Facility metrics are unavailable with reasons (uncalibrated moving-camera clip), never zero.
        page.goto(f"{BASE}/#overview")
        metrics = page.locator("[data-testid=facility-metrics]")
        metrics.wait_for(timeout=15000)
        text = metrics.inner_text()
        run.check("facility_metrics_unavailable_with_reason", "Unavailable" in text and "calibration" in text.lower()
                  and page.locator("[data-testid=facility-metrics] [data-available=true]").count() == 0, {"excerpt": text[:300]})
        tile = page.locator(".camera-tile[data-camera-id=cam-01] .overlay-count strong")
        run.check("camera_tile_shows_live_count", wait_for(lambda: tile.count() and tile.inner_text().isdigit(), 30),
                  {"text": tile.inner_text() if tile.count() else None})

        # Inspector: focus trap, Escape, focus return; history range browsing.
        opener = page.get_by_role("button", name=re.compile("Open details for Outdoor Pedestrian Area"))
        opener.first.focus()
        page.keyboard.press("Enter")
        inspector = page.locator("[data-testid=camera-inspector]")
        run.check("inspector_opens_with_keyboard", wait_for(lambda: inspector.count() and inspector.is_visible(), 15))
        run.check("focus_moves_into_inspector", wait_for(lambda: page.evaluate(
            "() => !!document.activeElement?.closest('[data-testid=camera-inspector]')"), 5))
        inside = True
        for _ in range(40):
            page.keyboard.press("Tab")
            inside = inside and page.evaluate("() => !!document.activeElement?.closest('[data-testid=camera-inspector]')")
        run.check("tab_trapped_in_inspector", inside)
        history = page.locator("[data-testid=count-history]")
        # Range browsing needs more recorded source time than one 30 s window: wait for >= 75 s of the long clip.
        run.check("long_clip_reached_75s", wait_for(lambda: (next(c for c in http_json(f"{BASE}/api/v1/facility/snapshot")["cameras"]
                                                                   if c["id"] == "cam-01")["source_position_s"] or 0) >= 75, 150))
        wait_for(lambda: int(history.get_attribute("data-total-samples") or 0) >= 140, 30)
        requests: list[str] = []
        page.on("request", lambda request: requests.append(request.url) if "count-history" in request.url else None)
        page.get_by_role("button", name="Browse range", exact=True).click()
        run.check("range_mode_requests_window_range", wait_for(lambda: any("window=range" in url for url in requests), 15)
                  and history.get_attribute("data-window") == "range", {"requests": requests[-2:]})
        page.get_by_role("button", name="30 s", exact=True).click()
        inspector_buttons = inspector.get_by_role("button")
        earlier = inspector.get_by_role("button", name="Earlier window", exact=True)
        later = inspector.get_by_role("button", name="Later window", exact=True)
        run.check("earlier_enabled_with_long_history", wait_for(lambda: earlier.count() == 1 and earlier.is_enabled(), 15),
                  {"buttons": [inspector_buttons.nth(i).inner_text() for i in range(inspector_buttons.count())]})

        def bounds(url: str) -> tuple[float, float]:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            return float(query["from_source_s"][0]), float(query["to_source_s"][0])

        wait_for(lambda: any("window=range" in u for u in requests), 10)
        newest = bounds([u for u in requests if "window=range" in u][-1])
        count_before = len([u for u in requests if "window=range" in u])
        earlier.click()
        moved = wait_for(lambda: len([u for u in requests if "window=range" in u]) > count_before, 15)
        older = bounds([u for u in requests if "window=range" in u][-1]) if moved else None
        run.check("earlier_window_requests_older_range", bool(moved) and older is not None and older[1] < newest[1]
                  and abs((older[1] - older[0]) - 30) < 0.01, {"newest": newest, "earlier": older})
        if not moved:
            diagnose(run, page, "earlier_window", {"inspector": "[data-testid=camera-inspector]"}, failures)
        count_before = len([u for u in requests if "window=range" in u])
        later.click()
        moved = wait_for(lambda: len([u for u in requests if "window=range" in u]) > count_before, 15)
        run.check("later_window_returns_forward", bool(moved) and bounds([u for u in requests if "window=range" in u][-1])[1] > older[1]
                  if moved and older else False, {"last": requests[-1:]})
        run.shot(page, "inspector-range")
        page.keyboard.press("Escape")
        run.check("escape_closes_inspector", wait_for(lambda: inspector.count() == 0, 5))
        run.check("focus_returns_to_tile", page.evaluate("() => document.activeElement?.getAttribute('aria-label') || ''").startswith("Open details for"))

        # Real evidence marker, then deep link highlight.
        opener.first.click()
        page.get_by_label("Evidence marker note").fill("ui refactor validator")
        page.locator("[data-testid=mark-evidence]").click()
        result = page.locator("[data-testid=evidence-marker-result]")
        run.check("marker_saved", wait_for(lambda: result.count() and result.is_visible(), 60))
        page.get_by_role("button", name="Open in Evidence").click()
        card = page.locator("[data-testid=evidence-card][data-highlighted=true]")
        run.check("open_in_evidence_highlights_card", wait_for(lambda: card.count() == 1, 20), {"url": page.url})
        items = http_json(f"{BASE}/api/v1/evidence")["items"]
        snapshot_item = next(i for i in items if i["media_type"] == "image/jpeg")
        group = snapshot_item["group_id"]
        page.goto(f"{BASE}/#evidence?item={snapshot_item['id']}")
        run.check("deep_link_highlights_card", wait_for(lambda: card.count() == 1 and card.get_attribute("data-group") == group, 20),
                  {"group": group})
        # The View button of THIS card, by exact accessible name (a page-wide "View" also matches "Overview").
        view = card.get_by_role("button", name="View", exact=True)
        run.check("view_button_unique_in_card", view.count() == 1, {"matches": view.count()})
        viewer = page.locator("[data-testid=evidence-viewer]")
        view.click()
        opened = wait_for(lambda: viewer.count() == 1 and viewer.is_visible(), 15)
        run.check("viewer_opens", bool(opened), {"url": page.url})
        large = viewer.locator("[data-testid=evidence-image-large]")
        # The drawer slides in: measure geometry only once no animation runs and the image rect is stable.
        settled = {"rect": None}

        def stable() -> bool:
            if large.count() != 1 or page.evaluate("document.getAnimations().filter(a => a.playState === 'running').length") != 0:
                return False
            rect = large.evaluate("i => i.getBoundingClientRect().toJSON()")
            same = rect == settled["rect"]
            settled["rect"] = rect
            return same
        if opened:
            wait_for(stable, 10, interval=0.2)
        state = wait_for(lambda: large.evaluate(
            "i => i.complete && i.naturalWidth > 0 ? {src: i.currentSrc, w: i.naturalWidth, h: i.naturalHeight, fit: getComputedStyle(i).objectFit,"
            " box: [i.clientWidth, i.clientHeight], view: [innerWidth, innerHeight], rect: i.getBoundingClientRect().toJSON()} : null")
            if large.count() == 1 else None, 20) if opened else None
        shows_record = bool(state) and state["src"].endswith(f"/api/v1/evidence/{snapshot_item['id']}")
        full = bool(state) and (state["w"], state["h"]) == (1920, 1080)
        # Full frame, not cropped: object-fit contain (whole image inside the box) and the box inside the viewport.
        uncropped = bool(state) and state["fit"] == "contain" and state["rect"]["right"] <= state["view"][0] + 1 \
            and state["rect"]["left"] >= -1 and state["box"][0] > 0 and state["box"][1] > 0
        run.check("viewer_shows_expected_record", shows_record, {"state": state, "expected": snapshot_item["id"]})
        run.check("viewer_shows_full_frame", full and uncropped, {"state": state})
        if not (opened and shows_record and full and uncropped):
            diagnose(run, page, "viewer", {"view_buttons_page": "role=button[name='View']", "viewer": "[data-testid=evidence-viewer]",
                                            "large_image": "[data-testid=evidence-image-large]", "cards": "[data-testid=evidence-card]"}, failures)
        if opened:
            run.shot(page, "evidence-viewer")
            page.keyboard.press("Escape")
            run.check("viewer_closes_on_escape", wait_for(lambda: viewer.count() == 0, 5))
            run.check("focus_returns_to_view_button", page.evaluate("() => document.activeElement?.textContent?.trim()") == "View"
                      and page.evaluate("() => !!document.activeElement?.closest('[data-highlighted=true]')"))
        else:
            run.check("viewer_closes_on_escape", False, {"reason": "viewer never opened; Escape not tested"})

        # Reset dialog: Escape cancels.
        page.get_by_role("button", name="Reset", exact=True).click()
        dialog = page.get_by_role("alertdialog")
        run.check("reset_dialog_opens", wait_for(lambda: dialog.count() == 1, 5))
        page.keyboard.press("Escape")
        run.check("reset_dialog_escape_cancels", wait_for(lambda: dialog.count() == 0, 5) and "RUNNING" in status_text(page))

        # Theme toggle persists.
        page.get_by_role("button", name=re.compile("Switch to light theme")).click()
        run.check("theme_switches_to_light", page.evaluate("() => document.documentElement.dataset.theme") == "light")
        page.reload()
        run.check("theme_persists_after_reload", wait_for(lambda: page.evaluate("() => document.documentElement.dataset.theme") == "light", 10))
        run.shot(page, "light-theme-overview")
        page.get_by_role("button", name=re.compile("Switch to dark theme")).click()

        # Responsive sweep.
        sweep = {}
        for width, height in WIDTHS:
            page.set_viewport_size({"width": width, "height": height})
            for view in VIEWS:
                page.goto(f"{BASE}/#{view}")
                page.wait_for_timeout(700)
                sweep[f"{width}:{view}"] = {"status_visible": visible_status(page), "overflow_px": overflow(page)}
        run.check("run_status_visible_every_width_and_view", all(v["status_visible"] for v in sweep.values()),
                  {k: v for k, v in sweep.items() if not v["status_visible"]})
        run.check("no_horizontal_overflow_every_width_and_view", all(v["overflow_px"] <= 1 for v in sweep.values()),
                  {k: v for k, v in sweep.items() if v["overflow_px"] > 1})

        # Mobile navigation drawer.
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{BASE}/#overview")
        page.get_by_role("button", name="Open navigation").click()
        nav = page.get_by_role("navigation", name="Primary navigation (mobile)")
        run.check("mobile_nav_opens", wait_for(lambda: nav.count() and nav.is_visible(), 5))
        nav.get_by_role("button", name=re.compile(r"^Alerts(, \d+ open)?$")).click()
        run.check("mobile_nav_navigates_and_closes", wait_for(lambda: page.url.endswith("#alerts") and nav.count() == 0, 5), {"url": page.url})
        run.shot(page, "mobile-alerts")
        page.set_viewport_size({"width": 1440, "height": 900})

        page.get_by_role("button", name="Stop", exact=True).click()
        run.check("ui_stop_reaches_stopped", wait_for(lambda: "STOPPED" in status_text(page), 60))
    finally:
        try:
            http_json(f"{BASE}/api/v1/demo/stop", "POST", timeout=60)
        except Exception:
            pass
        kill_tree(backend)
        log.close()
    run.check("no_external_requests", not external, {"external": sorted(set(external))[:10]})
    run.check("no_console_errors", not console, {"errors": console[:10]})


def part_b(run: Run, page: Page) -> None:
    log = (run.directory / "backend-TEST-ONLY-fault.log").open("w", encoding="utf-8")
    backend = subprocess.Popen([str(BACKEND_PYTHON), str(ROOT / "scripts" / "browser" / "fault_injection_backend.py"),
                                "--port", str(FAULT_PORT), "--database", str(run.directory / "TEST-ONLY-fault.db")],
                               cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{FAULT_PORT}"
    try:
        run.check("fault_backend_served", wait_http(f"{base}/health/live", 60))
        page.goto(f"{base}/")
        wait_for(lambda: "Live connection" in page.locator("body").inner_text(), 30)
        page.get_by_role("button", name="Start demo", exact=True).click()
        wait_for(lambda: "RUNNING" in status_text(page), 30)
        page.get_by_role("button", name="Stop", exact=True).click()
        banner = page.locator("[data-testid=cleanup-banner]")
        run.check("cleanup_banner_shown", wait_for(lambda: banner.count() and banner.is_visible(), 30))
        seen = {}
        for width, height in WIDTHS:
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(300)
            seen[f"{width}"] = banner.is_visible() and page.get_by_role("button", name="Retry cleanup").is_visible() and visible_status(page)
        run.check("cleanup_banner_and_retry_visible_every_width", all(seen.values()), seen)
        page.set_viewport_size({"width": 1280, "height": 800})
        run.check("start_disabled_during_stop_failed", page.get_by_role("button", name="Start demo", exact=True).is_disabled())
        response = page.evaluate("async (url) => { const r = await fetch(url, {method: 'POST'}); return {status: r.status, body: await r.json()}; }",
                                 f"{base}/api/v1/demo/start")
        run.check("start_api_returns_409_cleanup_unconfirmed", response["status"] == 409 and response["body"]["error"]["code"] == "CLEANUP_UNCONFIRMED",
                  {"status": response["status"], "code": response["body"].get("error", {}).get("code")})
        run.shot(page, "TEST-ONLY-cleanup-banner")
        page.get_by_role("button", name="Retry cleanup").click()
        run.check("retry_cleanup_reaches_stopped", wait_for(lambda: "STOPPED" in status_text(page), 30) and banner.count() == 0)
    finally:
        kill_tree(backend)
        log.close()


def main() -> int:
    try:
        evidence = resolve_run_dir(ROOT, "ui-refactor-cam01", "AEGIS_UI_RUN_DIR")
    except EvidenceExistsError as exc:
        raise SystemExit(str(exc)) from exc
    run = Run(evidence.directory)
    external: list[str] = []
    console: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        for label, part in (("A_real_hardware", part_a), ("B_TEST_ONLY_fault", part_b)):
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            if label.startswith("A"):
                page.on("request", lambda request: external.append(request.url)
                        if urllib.parse.urlparse(request.url).hostname not in ("127.0.0.1", None) else None)
                page.on("console", lambda message: console.append(message.text) if message.type == "error" else None)
            try:
                part(run, page, external, console) if label.startswith("A") else part(run, page)
            except Exception as exc:
                run.errors.append(f"{label}: {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
                try:
                    run.shot(page, f"{label}-error")
                except Exception:
                    pass
            finally:
                page.close()
        browser.close()
    report = {"schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "command": evidence.command,
              "run_dir": str(evidence.directory.relative_to(ROOT)),
              "result": "PASS" if run.checks and all(run.checks.values()) and not run.errors else "FAIL",
              "checks": {k: "PASS" if v else "FAIL" for k, v in run.checks.items()}, "observed": run.observed, "errors": run.errors,
              "scope": "Refactored dashboard on a real backend socket with a real GPU cam01 run (Part A) and a TEST-ONLY fault adapter "
                       "(Part B, no perception evidence). UI behaviour only; not detection accuracy."}
    (evidence.directory / "result.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"], "run_dir": report["run_dir"],
                      "failed": [k for k, v in run.checks.items() if not v], "errors": len(run.errors)}, indent=1))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
