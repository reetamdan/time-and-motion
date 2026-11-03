"""Real paced GPU demo in the production dashboard: continuous loop, live boxes overlay, video sync.

Runs past the clip's natural end (23.7 s) and checks that the camera stays live (no stale/unavailable
state), the overlay canvas paints tracked boxes from the backend, and the preview follows the pipeline.
Needs the production server running (Start-Aegis.cmd) and Playwright in .venv-windows.
Writes reports/validation/a6000-browser-loop-overlay-<UTC>/ (result.json and screenshots); with
--readme-screenshots also copies the screenshots to reports/ui/screenshots-a6000-<UTC>/.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sys
import time

import httpx
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:8000'
ROOT = Path(__file__).resolve().parents[2]
STAMP = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
OUTPUT = ROOT / 'reports' / 'validation' / f'a6000-browser-loop-overlay-{STAMP}'
OUTPUT.mkdir(parents=True)
README_SHOTS = ROOT / 'reports' / 'ui' / f'screenshots-a6000-{STAMP}'
RUN_SECONDS = 40  # well past the 23.7 s clip

result = {'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'checks': {}, 'samples': [], 'page_errors': []}
checks = result['checks']

PAINTED = """(canvas) => {
  if (!canvas || canvas.width === 0) return 0;
  const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
  let painted = 0;
  for (let i = 3; i < data.length; i += 4) if (data[i] > 0) painted++;
  return painted;
}"""


def camera(client):
    return next(c for c in client.get('/api/v1/facility/snapshot').json()['cameras'] if c['id'] == 'cam-01')


with httpx.Client(base_url=BASE, timeout=120, trust_env=False) as client, sync_playwright() as playwright:
    if client.get('/api/v1/facility/snapshot').json()['run_status'] not in ('IDLE', 'STOPPED'):
        raise SystemExit('An existing demo is active; refusing to interrupt it.')
    checks['runtime_ready'] = client.get('/health/ready').status_code == 200
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    page.on('pageerror', lambda error: result['page_errors'].append(str(error)))
    try:
        page.goto(BASE, wait_until='domcontentloaded')
        page.get_by_text('Live connection', exact=False).first.wait_for(timeout=60000)
        page.get_by_role('button', name='Paced', exact=True).click()
        page.get_by_role('button', name='Start demo', exact=True).click(timeout=60000)
        tile_canvas = page.locator('[data-camera-id="cam-01"] canvas.detection-overlay')
        tile_video = page.locator('[data-camera-id="cam-01"] video')
        page.wait_for_selector('[data-camera-id="cam-01"] canvas.detection-overlay[data-active="true"]', timeout=90000)
        started = time.monotonic()
        while time.monotonic() - started < RUN_SECONDS:
            time.sleep(4)
            state = camera(client)
            video = tile_video.evaluate('(v) => ({t: v.currentTime, ready: v.readyState, w: v.videoWidth, paused: v.paused})')
            sample = {
                'elapsed_s': round(time.monotonic() - started, 1),
                'freshness': page.locator('[data-camera-id="cam-01"]').get_attribute('data-freshness'),
                'visible_count': state['visible_count'], 'loop_pass': state['source_loop_index'],
                'pipeline_file_position_s': state['source_file_position_s'], 'video_time_s': round(video['t'], 3),
                'video_ready': video['ready'], 'video_width': video['w'],
                'overlay_painted_pixels': tile_canvas.evaluate(PAINTED),
            }
            if sample['pipeline_file_position_s'] is not None:
                drift = abs(sample['video_time_s'] - sample['pipeline_file_position_s'])
                sample['sync_drift_s'] = round(min(drift, abs(drift - 23.7)), 3)  # wrap-aware
            result['samples'].append(sample)
            if 10 < sample['elapsed_s'] < 15:
                page.screenshot(path=str(OUTPUT / '1440-overview.png'))
        samples = result['samples']
        after_eof = [s for s in samples if s['elapsed_s'] > 26]
        checks['stayed_live_past_clip_end'] = bool(after_eof) and all(
            s['freshness'] == 'live' and (s['visible_count'] or 0) > 0 for s in after_eof)
        checks['looped_at_least_once'] = max(s['loop_pass'] or 0 for s in samples) >= 1
        checks['overlay_painted_boxes'] = all(s['overlay_painted_pixels'] > 500 for s in samples)
        checks['source_video_loaded'] = all(s['video_width'] > 0 for s in samples)
        drifts = [s['sync_drift_s'] for s in samples if 'sync_drift_s' in s]
        result['max_sync_drift_s'] = max(drifts) if drifts else None
        checks['preview_follows_pipeline'] = bool(drifts) and max(drifts) < 1.0
        checks['tile_shows_no_stale_chip'] = page.locator('[data-camera-id="cam-01"]').get_by_text(re.compile('^Stale')).count() == 0

        page.get_by_role('button', name=re.compile('Open details for Outdoor Pedestrian Area')).first.click()
        page.locator('[data-testid=detail-visible-count] strong').wait_for(timeout=30000)
        page.wait_for_function("""() => {
            const history = document.querySelector('[data-testid="count-history"]');
            return history && Number(history.dataset.samples) > 0;
        }""", timeout=30000)
        time.sleep(2)
        checks['inspector_reports_loop'] = 'Continuous' in page.locator('[data-testid=clip-loop]').inner_text()
        page.screenshot(path=str(OUTPUT / '1440-inspector.png'))
        page.get_by_role('button', name='Close Outdoor Pedestrian Area', exact=True).click()
        page.get_by_role('button', name='Stop', exact=True).click()
        page.wait_for_function("""() => Array.from(document.querySelectorAll('[data-run-status]'))
            .some(node => node.dataset.runStatus === 'STOPPED')""", timeout=30000)
        checks['browser_stop_released_runtime'] = client.get('/api/v1/facility/snapshot').json()['run_status'] == 'STOPPED'
    finally:
        if client.get('/api/v1/facility/snapshot').json()['run_status'] not in ('IDLE', 'STOPPED'):
            client.post('/api/v1/demo/stop').raise_for_status()
        browser.close()

checks['no_uncaught_browser_errors'] = not result['page_errors']
result['result'] = 'PASS' if all(checks.values()) else 'FAIL'
(OUTPUT / 'result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
if result['result'] == 'PASS' and '--readme-screenshots' in sys.argv:
    README_SHOTS.mkdir(parents=True)
    for name in ('1440-overview.png', '1440-inspector.png'):
        if (OUTPUT / name).is_file():
            shutil.copyfile(OUTPUT / name, README_SHOTS / name)
    result['readme_screenshots'] = str(README_SHOTS.relative_to(ROOT))
print(json.dumps({k: v for k, v in result.items() if k != 'samples'}, indent=2))
for sample in result['samples']:
    print(sample)
raise SystemExit(0 if result['result'] == 'PASS' else 1)
