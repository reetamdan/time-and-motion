"""Stop only this repository's backend (port 8000), after a confirmed demo stop; never touches other servers."""
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[2]
owners = {item.pid for item in psutil.net_connections(kind='tcp')
          if item.pid and item.laddr and item.laddr.port == 8000 and item.status == psutil.CONN_LISTEN}
for pid in owners:
    process = psutil.Process(pid)
    arguments = process.cmdline()
    if Path(process.cwd()).resolve() != ROOT or 'uvicorn' not in arguments or 'backend.app.main:app' not in arguments:
        raise SystemExit(f'Port 8000 belongs to another process ({pid}); refusing to stop it.')
    with httpx.Client(base_url='http://127.0.0.1:8000', timeout=90, trust_env=False) as client:
        snapshot = client.get('/api/v1/facility/snapshot')
        snapshot.raise_for_status()
        if snapshot.json()['run_status'] not in ('IDLE', 'STOPPED'):
            stopped = client.post('/api/v1/demo/stop')
            stopped.raise_for_status()
            if stopped.json()['run_status'] != 'STOPPED':
                raise SystemExit('Camera cleanup was not confirmed; refusing to terminate the backend.')
    process.terminate()
    process.wait(timeout=30)
    print(f'Stopped this project backend (PID {pid}).')
if not owners:
    print('No server is listening on port 8000.')
