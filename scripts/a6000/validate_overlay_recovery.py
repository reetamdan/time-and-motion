"""Real A6000/browser regression: loop evidence, overlay outage/recovery, epoch reset and mobile unpaced.

Starts an isolated backend on port 8779 using configs/a6000 and fresh local state. Requires the
provisioned A6000 environment, frontend build, httpx and Playwright. Refuses an occupied port.
Run: .venv-windows/Scripts/python.exe scripts/a6000/validate_overlay_recovery.py
"""
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import subprocess
import shutil
import socket
import sys
import time

import httpx
import psutil
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
STAMP = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
OUTPUT = ROOT / 'reports/validation' / ('overlay-recovery-' + STAMP)
OUTPUT.mkdir()
STATE = ROOT / '.install/overlay-validation' / STAMP
STATE.mkdir(parents=True)
BASE = 'http://127.0.0.1:8779'
env = {k: v for k, v in os.environ.items() if not k.startswith('CCTV_')}
ffmpeg = shutil.which('ffmpeg')
if not ffmpeg and os.name == 'nt':
    ffmpeg = next((Path(os.environ['LOCALAPPDATA']) / 'Programs/FFmpeg').rglob('ffmpeg.exe'), None)
if not ffmpeg:
    raise SystemExit('ffmpeg is missing; install it or add it to PATH.')
env.update(CCTV_CONFIG_DIR=str(ROOT / 'configs/a6000'), CCTV_DATABASE_PATH=str(STATE / 'run.db'),
           CCTV_EVIDENCE_ROOT=str(STATE / 'evidence'), CCTV_CAPABILITY_CACHE=str(STATE / 'capability.json'),
           PATH=str(Path(ffmpeg).parent) + os.pathsep + os.environ['PATH'])
result = {'checks': {}, 'page_errors': [], 'base_commit': subprocess.check_output(
    ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
    'working_tree_changes': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT)),
    'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
        'backend/app/services/runtime.py', 'backend/app/services/evidence.py', 'backend/app/api/facility.py',
        'frontend/src/components/DetectionOverlay.tsx', 'frontend/src/lib/api.ts',
        'scripts/a6000/validate_overlay_recovery.py')}}
checks = result['checks']
PAINT = '''c => { const d = c.getContext('2d').getImageData(0,0,c.width,c.height).data;
let n=0; for(let i=3;i<d.length;i+=4) if(d[i])n++; return n; }'''


def wait_for(predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except (httpx.HTTPError, KeyError):
            pass
        time.sleep(0.2)
    raise TimeoutError('Validation condition did not become true')


with socket.socket() as probe:
    probe.bind(('127.0.0.1', 8779))

with (STATE / 'server.log').open('w') as log, httpx.Client(base_url=BASE, timeout=120, trust_env=False) as client:
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.app.main:app', '--host', '127.0.0.1',
                               '--port', '8779'], cwd=ROOT, env=env, stdout=log, stderr=log)
    owns_listener = False
    try:
        def ready():
            if server.poll() is not None:
                raise RuntimeError('Isolated backend exited; see ' + str(STATE / 'server.log'))
            return client.get('/health/live').status_code == 200
        wait_for(ready)
        # Windows virtualenv's python.exe is a redirector: the actual interpreter is its child.
        process = psutil.Process(server.pid)
        owners = [process, *process.children(recursive=True)]
        owns_listener = any(connection.laddr.port == 8779 and connection.status == psutil.CONN_LISTEN
                            for owner in owners for connection in owner.net_connections(kind='tcp'))
        if not owns_listener:
            raise RuntimeError('Listener ownership could not be confirmed; refusing to control it')
        checks['ready'] = client.get('/health/ready').status_code == 200
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            page.on('pageerror', lambda error: result['page_errors'].append(str(error)))
            page.goto(BASE)
            page.get_by_text('Live connection', exact=False).first.wait_for(timeout=30000)
            started = client.post('/api/v1/demo/start?replay_mode=paced')
            started.raise_for_status()
            canvas = page.locator('[data-camera-id="cam-01"] canvas')
            def camera():
                return next(c for c in client.get('/api/v1/facility/snapshot').json()['cameras'] if c['id'] == 'cam-01')
            wait_for(lambda: (camera().get('source_loop_index') or 0) >= 1)
            page.wait_for_timeout(700)
            checks['paced_overlay_after_loop'] = canvas.evaluate(PAINT) > 500
            checks['looped'] = camera()['source_loop_index'] >= 1
            marker = client.post('/api/v1/cameras/cam-01/evidence-markers', json={'note': 'Independent review loop check'})
            checks['loop_evidence_created'] = marker.status_code == 201
            if marker.status_code == 201:
                body = marker.json()
                checks['jpeg_download'] = client.get(body['snapshot']['url']).content.startswith(b'\xff\xd8')
                checks['mp4_download'] = b'ftyp' in client.get(body['clip']['url']).content[:64]
            page.screenshot(path=str(OUTPUT / 'paced-desktop.png'))
            # Only boxes transport fails: camera snapshots remain fresh over WebSocket.
            page.route('**/detections?*', lambda route: route.fulfill(status=503, body='unavailable'))
            page.wait_for_timeout(2300)
            checks['failed_overlay_transport_clears_boxes'] = canvas.evaluate(PAINT) == 0
            checks['snapshot_still_live_during_overlay_failure'] = camera()['visible_count'] is not None
            page.unroute('**/detections?*')
            page.wait_for_function('''() => {const c=document.querySelector('[data-camera-id="cam-01"] canvas');
                return c && c.getContext('2d').getImageData(0,0,c.width,c.height).data.some((v,i)=>i%4===3&&v>0);}''', timeout=10000)
            checks['overlay_transport_recovers'] = True
            old_epoch = camera()['epoch']
            checks['paced_stop'] = client.post('/api/v1/demo/stop').json()['run_status'] == 'STOPPED'
            page.set_viewport_size({'width': 390, 'height': 844})
            client.post('/api/v1/demo/start?replay_mode=unpaced').raise_for_status()
            wait_for(lambda: camera()['visible_count'] is not None)
            page.wait_for_timeout(1800)
            new = camera()
            checks['fresh_epoch'] = new['epoch'] != old_epoch
            # Replaying a previous epoch's high cursor must immediately return the new run's boxes.
            payload = client.get('/api/v1/cameras/cam-01/detections', params={
                'after_epoch': old_epoch, 'after_frame': 1000000}).json()
            checks['old_cursor_recovers'] = bool(payload['frames']) and payload['camera_epoch'] == new['epoch']
            painted = []
            for _ in range(10):
                page.wait_for_timeout(200)
                painted.append(canvas.evaluate(PAINT))
            result['unpaced_painted_samples'] = painted
            checks['mobile_unpaced_boxes'] = sum(n > 500 for n in painted) >= 5
            checks['mobile_no_horizontal_overflow'] = page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(OUTPUT / 'unpaced-mobile.png'))
            checks['unpaced_stop'] = client.post('/api/v1/demo/stop').json()['run_status'] == 'STOPPED'
            browser.close()
        checks['no_browser_errors'] = not result['page_errors']
    except Exception as error:
        result['error'] = repr(error)
        checks['completed'] = False
    finally:
        try:
            checks['cleanup'] = owns_listener and client.post('/api/v1/demo/stop').json()['run_status'] == 'STOPPED'
        except Exception as error:
            checks['cleanup'] = False
            result['cleanup_error'] = repr(error)
        finally:
            server.terminate()
            server.wait(timeout=30)
        result['result'] = 'PASS' if checks and all(checks.values()) else 'FAIL'
        (OUTPUT / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(OUTPUT)
        print(json.dumps(result, indent=2))
raise SystemExit(0 if result['result'] == 'PASS' else 1)
