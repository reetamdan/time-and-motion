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

# start.ps1 keeps WSL running with a tagged wsl.exe and ends it itself; remove one whose launcher was
# killed abruptly (its parent is gone), so WSL can idle again.
for process in psutil.process_iter(['name', 'cmdline', 'ppid']):
    try:
        if (process.info['name'] or '').lower() == 'wsl.exe' and 'aegis-keepalive' in ' '.join(process.info['cmdline'] or [])                 and not psutil.pid_exists(process.info['ppid']):
            process.terminate()
            print(f'Ended a leftover WSL keep-alive (PID {process.pid}).')
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
