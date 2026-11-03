"""Stop only this repository's backend, after a confirmed demo stop; never touches other servers.

Port: --port, else BACKEND_PORT (as used by run-backend.ps1 / start.ps1), else 8000.
"""
import argparse
import hashlib
import os
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[2]
# Must match start.ps1: the keep-alive tag carries this checkout's identity.
KEEPALIVE_TAG = 'aegis-keepalive-' + hashlib.sha256(str(ROOT).lower().encode('utf-8')).hexdigest()[:12]

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--port', type=int, default=int(os.environ.get('BACKEND_PORT') or 8000))
port = parser.parse_args().port

owners = {item.pid for item in psutil.net_connections(kind='tcp')
          if item.pid and item.laddr and item.laddr.port == port and item.status == psutil.CONN_LISTEN}
for pid in owners:
    process = psutil.Process(pid)
    arguments = process.cmdline()
    if Path(process.cwd()).resolve() != ROOT or 'uvicorn' not in arguments or 'backend.app.main:app' not in arguments:
        raise SystemExit(f'Port {port} belongs to another process ({pid}); refusing to stop it.')
    with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=90, trust_env=False) as client:
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
    print(f'No server is listening on port {port}.')


def launcher_alive(process: psutil.Process) -> bool:
    """The keep-alive's launcher still runs: its parent PID exists and was not reused by a newer process."""
    try:
        parent = psutil.Process(process.ppid())
        return parent.create_time() <= process.create_time()
    except psutil.NoSuchProcess:
        return False


# start.ps1 keeps WSL running with a tagged wsl.exe and ends it itself; remove this checkout's keep-alive
# whose launcher was killed abruptly, so WSL can idle again.
for process in psutil.process_iter(['name', 'cmdline']):
    try:
        if ((process.info['name'] or '').lower() == 'wsl.exe'
                and KEEPALIVE_TAG in ' '.join(process.info['cmdline'] or [])
                and not launcher_alive(process)):
            process.terminate()
            print(f'Ended a leftover WSL keep-alive (PID {process.pid}).')
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
