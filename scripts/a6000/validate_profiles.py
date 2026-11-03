"""Real clip -> RTX A6000 DeepStream -> API/history/evidence/cleanup, for every detector profile.

Each profile runs the full demo clip unpaced with looping off (it checks the natural end: CLEAN_EOF and
all 710 frames). Evidence: reports/validation/a6000-profiles-<UTC>/result.json.
Run with the A6000 environment: powershell -ExecutionPolicy Bypass -File scripts\a6000\validate-profiles.ps1
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

import yaml
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.app.main import create_app

PROFILE = ROOT / 'configs' / 'a6000'
STAMP = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
OUTPUT = ROOT / 'reports' / 'validation' / f'a6000-profiles-{STAMP}'
OUTPUT.mkdir(parents=True)
results = {'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'gpu': 'NVIDIA RTX A6000',
           'scope': 'Real single-camera integration and cleanup; not detector accuracy or multi-camera capacity.',
           'models': []}


def camera(snapshot):
    return next(item for item in snapshot['cameras'] if item['id'] == 'cam-01')


def validate(model_id):
    work = OUTPUT / model_id
    config_dir = work / 'configs'
    shutil.copytree(PROFILE, config_dir)
    model_path = config_dir / 'models.yaml'
    models = yaml.safe_load(model_path.read_text(encoding='utf-8'))
    models['selected_model'] = model_id
    models['selection_mode'] = 'provisional_smoke' if model_id == 'trafficcamnet-smoke' else 'candidate_evaluation'
    model_path.write_bytes(yaml.safe_dump(models, sort_keys=False).encode('utf-8'))
    # This check needs the clip's natural end (CLEAN_EOF, 710 frames); the PC profile loops cam-01.
    cameras_path = config_dir / 'cameras.yaml'
    cameras = yaml.safe_load(cameras_path.read_text(encoding='utf-8'))
    for item in cameras['cameras']:
        item['loop'] = False
    cameras_path.write_bytes(yaml.safe_dump(cameras, sort_keys=False).encode('utf-8'))
    app = create_app(config_dir=config_dir, database_path=work / 'run.db', evidence_root=work / 'evidence')
    checks = {}
    record = {'id': model_id, 'checks': checks}
    epoch = None
    with TestClient(app) as client:
        artifacts = client.get('/api/v1/system/artifacts').json()
        checks['all_selected_artifacts_match'] = all(item['status'] == 'MATCH' for item in artifacts['artifacts'])
        record['artifacts'] = artifacts['artifacts']
        ready = client.get('/health/ready')
        record['readiness'] = ready.json()
        checks['runtime_ready'] = ready.status_code == 200
        if not checks['runtime_ready']:
            raise RuntimeError(f'{model_id} is not ready: {ready.json()}')
        try:
            response = client.post('/api/v1/demo/start?replay_mode=unpaced')
            response.raise_for_status()
            snapshot = response.json()
            epoch = camera(snapshot)['epoch']
            checks['camera_started'] = snapshot['run_status'] == 'RUNNING'
            record['run_id'] = snapshot['run_id']
            record['epoch'] = epoch
            deadline = time.monotonic() + 180
            marker = None
            while time.monotonic() < deadline:
                snapshot = client.get('/api/v1/facility/snapshot').json()
                state = camera(snapshot)
                if model_id == 'trafficcamnet-smoke' and marker is None and (state.get('source_position_s') or 0) >= 3:
                    marker_response = client.post('/api/v1/cameras/cam-01/evidence-markers', json={'note': 'A6000 profile validation'})
                    marker = {'status_code': marker_response.status_code, 'body': marker_response.json()}
                if state.get('stream_outcome'):
                    break
                time.sleep(0.25)
            record['final_camera'] = state
            checks['clean_end_of_clip'] = state.get('stream_outcome') == 'CLEAN_EOF'
            history = client.get('/api/v1/cameras/cam-01/count-history').json()
            (work / 'history.json').write_text(json.dumps(history, indent=2) + '\n', encoding='utf-8')
            rows = history['samples']
            record['history_samples'] = len(rows)
            record['processed_frames'] = sum(row['frames'] for row in rows)
            record['peak_people'] = max((row['visible_max'] for row in rows), default=0)
            checks['all_710_frames_processed'] = record['processed_frames'] == 710
            checks['positive_real_person_counts'] = record['peak_people'] > 0
            checks['history_covers_clip'] = len(rows) == 48 and rows[-1]['source_time_s'] >= 23
            if model_id == 'trafficcamnet-smoke':
                record['evidence_marker'] = marker
                checks['real_evidence_saved'] = marker is not None and marker['status_code'] == 201
                if checks['real_evidence_saved']:
                    for kind in ('snapshot', 'clip'):
                        item = marker['body'][kind]
                        download = client.get(f"/api/v1/evidence/{item['id']}")
                        checks[f'{kind}_downloadable'] = download.status_code == 200 and len(download.content) > 1000
        finally:
            stopped = client.post('/api/v1/demo/stop')
            checks['stopped_cleanly'] = stopped.status_code == 200 and stopped.json()['run_status'] == 'STOPPED'
    if epoch:
        name = f'aegis-cam-01-{epoch}'
        container = subprocess.run(['wsl.exe', '-d', 'Ubuntu-22.04', '-e', 'docker', 'ps', '-a',
                                    '--filter', f'name={name}', '--format', '{{.Names}}'],
                                   capture_output=True, text=True, timeout=30)
        checks['owned_container_removed'] = container.returncode == 0 and name not in container.stdout.splitlines()
    record['result'] = 'PASS' if all(checks.values()) else 'FAIL'
    return record


for model_id in ('trafficcamnet-smoke', 'rt-detr-person-candidate', 'rf-detr-person-candidate'):
    print(f'Validating {model_id} with the real GPU...', flush=True)
    try:
        record = validate(model_id)
    except Exception:
        record = {'id': model_id, 'result': 'FAIL', 'error': traceback.format_exc()}
    results['models'].append(record)
    print(json.dumps(record, indent=2), flush=True)
    (OUTPUT / 'result.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
results['result'] = 'PASS' if all(model['result'] == 'PASS' for model in results['models']) else 'FAIL'
(OUTPUT / 'result.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
print(f"{results['result']}: {OUTPUT / 'result.json'}", flush=True)
raise SystemExit(0 if results['result'] == 'PASS' else 1)
