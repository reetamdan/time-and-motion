import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const isWindows = process.platform === 'win32';
const python = process.env.CCTV_PYTHON || (isWindows
  ? resolve(root, '.venv', 'Scripts', 'python.exe')
  : resolve(root, '.venv-wsl', 'bin', 'python'));
const vite = resolve(root, 'frontend', 'node_modules', 'vite', 'bin', 'vite.js');

if (!existsSync(python)) {
  console.error(`Backend Python was not found at ${python}. Run the documented setup first or set CCTV_PYTHON.`);
  process.exit(1);
}
if (!existsSync(vite)) {
  console.error('Frontend dependencies are missing. Run `npm --prefix frontend ci` first.');
  process.exit(1);
}

const backendPort = process.env.CCTV_API_PORT || '8000';
const env = { ...process.env, PYTHONUNBUFFERED: '1' };
const children = [];
let stopping = false;

function launch(command, commandArgs, label) {
  const child = spawn(command, commandArgs, {
    cwd: root,
    env,
    stdio: 'inherit',
    windowsHide: true,
  });
  child.on('error', (error) => {
    console.error(`${label} failed to start: ${error.message}`);
    stop(1);
  });
  child.on('exit', (code, signal) => {
    if (!stopping) {
      console.error(`${label} stopped (${signal || `exit ${code ?? 1}`}).`);
      stop(code || 1);
    }
  });
  children.push(child);
  return child;
}

function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
  }
  const force = setTimeout(() => {
    for (const child of children) {
      if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    }
  }, 3_000);
  force.unref();
  const active = children.filter((child) => child.exitCode === null && child.signalCode === null);
  let remaining = active.length;
  if (!remaining) {
    clearTimeout(force);
    process.exit(code);
  }
  for (const child of active) {
    child.once('exit', () => {
      remaining -= 1;
      if (remaining === 0) {
        clearTimeout(force);
        process.exit(code);
      }
    });
  }
}

process.on('SIGINT', () => stop(0));
process.on('SIGTERM', () => stop(0));

launch(python, ['-m', 'uvicorn', 'backend.app.main:app', '--host', '127.0.0.1', '--port', backendPort], 'Backend');
launch(process.execPath, [vite, resolve(root, 'frontend'), ...args], 'Frontend');
