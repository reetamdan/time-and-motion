import { spawn } from 'node:child_process';
import { createWriteStream, existsSync } from 'node:fs';
import { mkdir, rm, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const stamp = new Date().toISOString().replaceAll(':', '-').replaceAll('.', '-');
const outputDir = resolve(root, 'reports', 'validation', stamp);
const browserProfile = resolve(outputDir, 'browser-profile');
const database = resolve(root, 'backend', 'var', `e2e-${process.pid}.db`);
const base = 'http://127.0.0.1:5173';
const api = `${base}/api/v1`;
const chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const checks = [];
let server;
let log;

function record(name, passed, detail) {
  checks.push({ name, passed, detail });
  console.log(`${passed ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${typeof detail === 'string' ? detail : JSON.stringify(detail)}` : ''}`);
}

async function request(path, options = {}) {
  const response = await fetch(`${api}${path}`, { ...options, signal: AbortSignal.timeout(15_000) });
  const body = await response.json();
  return { response, body };
}

async function waitForServer() {
  const deadline = Date.now() + 45_000;
  let lastError;
  let attempts = 0;
  while (Date.now() < deadline) {
    if (server.exitCode !== null) throw new Error(`Development launcher exited with ${server.exitCode}`);
    try {
      const ui = await fetch(base, { signal: AbortSignal.timeout(10_000) });
      const live = await fetch(`${base}/health/live`, { signal: AbortSignal.timeout(10_000) });
      if (ui.ok && live.ok) return;
      const uiBody = (await ui.text()).slice(0, 300);
      const liveBody = (await live.text()).slice(0, 300);
      lastError = new Error(`UI ${ui.status} ${JSON.stringify(uiBody)}; API ${live.status} ${JSON.stringify(liveBody)}; server=${ui.headers.get('server')}`);
      if (attempts % 5 === 0) console.log(`Waiting for valid routes: ${lastError.message}`);
    } catch (error) {
      lastError = error;
      if (attempts % 5 === 0) console.log(`Waiting for local servers: ${error.message}; cause: ${error.cause?.message || error.cause || 'none'}`);
    }
    attempts += 1;
    await new Promise((resolveWait) => setTimeout(resolveWait, 500));
  }
  throw new Error(`Server did not become ready: ${lastError?.message || 'timeout'}; cause: ${lastError?.cause?.message || lastError?.cause || 'none'}`);
}

async function captureWebSocket() {
  return new Promise((resolveSocket, rejectSocket) => {
    const socket = new WebSocket('ws://127.0.0.1:5173/api/v1/live');
    const timeout = setTimeout(() => {
      socket.close();
      rejectSocket(new Error('Timed out waiting for initial WebSocket snapshot'));
    }, 15_000);
    socket.addEventListener('message', (event) => {
      try {
        const message = JSON.parse(String(event.data));
        if (message.type === 'snapshot') {
          clearTimeout(timeout);
          socket.close();
          resolveSocket(message);
        }
      } catch (error) {
        clearTimeout(timeout);
        socket.close();
        rejectSocket(error);
      }
    });
    socket.addEventListener('error', () => {
      clearTimeout(timeout);
      rejectSocket(new Error('WebSocket connection failed'));
    });
  });
}

async function terminateTree(child) {
  if (!child || child.exitCode !== null) return;
  if (process.platform === 'win32') {
    await new Promise((resolveKill) => {
      const killer = spawn('taskkill.exe', ['/pid', String(child.pid), '/t', '/f'], { stdio: 'ignore', windowsHide: true });
      killer.on('close', resolveKill);
      killer.on('error', resolveKill);
    });
  } else {
    child.kill('SIGTERM');
    await Promise.race([
      new Promise((resolveExit) => child.once('exit', resolveExit)),
      new Promise((resolveWait) => setTimeout(resolveWait, 5_000)),
    ]);
    if (child.exitCode === null) child.kill('SIGKILL');
  }
}

async function screenshot(name, hash) {
  if (!existsSync(chrome)) {
    record(`screenshot:${name}`, false, 'Chrome executable unavailable');
    return;
  }
  const destination = resolve(outputDir, `${name}.png`);
  const args = [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    `--user-data-dir=${browserProfile}`, '--window-size=1600,1100', '--hide-scrollbars',
    '--run-all-compositor-stages-before-draw', '--virtual-time-budget=7000',
    `--screenshot=${destination}`, `${base}/#${hash}`,
  ];
  const result = await new Promise((resolveCapture) => {
    const browser = spawn(chrome, args, { stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true });
    let errors = '';
    browser.stderr.on('data', (chunk) => { errors += chunk.toString(); });
    const timeout = setTimeout(async () => {
      await terminateTree(browser);
      resolveCapture({ code: 124, errors: `${errors}\nBrowser capture timed out` });
    }, 35_000);
    browser.on('error', (error) => {
      clearTimeout(timeout);
      resolveCapture({ code: 1, errors: error.message });
    });
    browser.on('close', (code) => {
      clearTimeout(timeout);
      resolveCapture({ code, errors });
    });
  });
  record(`screenshot:${name}`, result.code === 0 && existsSync(destination), result.code === 0 ? destination : result.errors.slice(-1000));
}

try {
  await mkdir(outputDir, { recursive: true });
  await mkdir(dirname(database), { recursive: true });
  log = createWriteStream(resolve(outputDir, 'development-server.log'), { flags: 'w' });
  server = spawn(process.execPath, ['scripts/dev.mjs', '--host', '127.0.0.1', '--port', '5173'], {
    cwd: root,
    env: { ...process.env, CCTV_DATABASE_PATH: database },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  server.stdout.pipe(log, { end: false });
  server.stderr.pipe(log, { end: false });

  await waitForServer();
  record('development launcher and API startup', true, base);

  const { response: snapshotResponse, body: snapshot } = await request('/facility/snapshot');
  record('facility snapshot contract', snapshotResponse.ok && snapshot.cameras?.length === 4 && snapshot.floors?.length === 2, {
    status: snapshotResponse.status, cameras: snapshot.cameras?.length, floors: snapshot.floors?.length,
  });
  record('real GPU telemetry probe', snapshot.system_metrics?.gpu_name?.includes('RTX 5070 Ti') === true, snapshot.system_metrics);

  const readyResponse = await fetch(`${base}/health/ready`, { signal: AbortSignal.timeout(15_000) });
  const ready = await readyResponse.json();
  record('readiness exposes real blockers', readyResponse.status === 503 && ready.checks?.some((check) => !check.ready), {
    status: readyResponse.status, failed: ready.checks?.filter((check) => !check.ready).map((check) => check.name),
  });

  const initialLive = await captureWebSocket();
  record('WebSocket initial snapshot', initialLive.data?.cameras?.length === 4, { sequence: initialLive.data?.sequence });

  const { response: startResponse, body: started } = await request('/demo/start', { method: 'POST' });
  record('demo lifecycle starts with honest degradation', startResponse.ok && started.run_id && started.run_status === 'DEGRADED', {
    status: startResponse.status, run_status: started.run_status, alerts: started.alerts?.length,
  });

  const openAlert = started.alerts?.find((alert) => alert.status === 'OPEN');
  if (openAlert) {
    const { response: ackResponse, body: acknowledged } = await request(`/alerts/${openAlert.id}/acknowledge`, { method: 'POST' });
    record('alert acknowledgment', ackResponse.ok && acknowledged.status === 'ACKNOWLEDGED', { status: ackResponse.status });
  } else {
    record('alert acknowledgment', false, 'No real source alert was generated');
  }

  const { response: historyResponse, body: history } = await request('/analytics/history?metric=occupancy&limit=20');
  record('bounded history query', historyResponse.ok && Array.isArray(history.points) && history.points.length <= 20, historyResponse.status);

  await screenshot('overview', 'overview');
  await screenshot('floor-plans', 'floors');
  await screenshot('system', 'system');

  const { response: stopResponse } = await request('/demo/stop', { method: 'POST' });
  record('demo stop', stopResponse.ok, stopResponse.status);
  const { response: resetResponse, body: reset } = await request('/demo/reset', { method: 'POST' });
  record('run-scoped reset', resetResponse.ok && reset.run_id === null, { status: resetResponse.status, run_id: reset.run_id });
} catch (error) {
  record('unhandled validation error', false, error.stack || error.message);
} finally {
  await terminateTree(server);
  if (log) log.end();
  await writeFile(resolve(outputDir, 'report.json'), JSON.stringify({
    timestamp: new Date().toISOString(),
    passed: checks.length > 0 && checks.every((check) => check.passed),
    checks,
  }, null, 2));
  for (const suffix of ['', '-journal', '-wal', '-shm']) await rm(`${database}${suffix}`, { force: true });
  await rm(browserProfile, { recursive: true, force: true });
  console.log(`Validation report: ${outputDir}`);
  if (checks.some((check) => !check.passed)) process.exitCode = 1;
}
