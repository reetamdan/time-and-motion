#!/usr/bin/env python3
"""Live-source (RTSP) path with a CONTROLLED local RTSP stream, on the real GPU and the Windows backend route.

Setup (all owned by this run, all inside WSL): mediamtx (test-only RTSP server, per-run random
credentials) and a static-ffmpeg publisher that loops demo/cam01.mp4 in real time (-re, stream copy).
The RTSP stream is therefore the SAME authorized clip republished locally: it proves the live
ingestion/reconnect software path, not a physical camera, network camera timing, or new footage.

Configuration-only switch: cam-02 becomes source_type rtsp with
  rtsp://${AEGIS_RTSP_USER}:${AEGIS_RTSP_PASS}@172.17.0.1:8554/cam01   (docker0 gateway -> WSL host)
credentials are supplied to the backend process environment only. cam-01 stays the file source.

Checks: live counts on cam-02 at ~real-time rate; publisher killed -> loss visible (OFFLINE /
reconnecting, value unavailable, never 0) while cam-01 continues; failed reconnect attempts back off;
publisher restored -> new epoch, counts resume, reconnect alert resolved; credentials absent from API
responses, alerts, and backend/container logs; evidence refused for the live camera with a reason;
stop releases every owned container; server/publisher processes terminated.

Run: .venv/Scripts/python.exe scripts/validate_rtsp_source.py
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts" / "benchmark"))
from evidence_dirs import EvidenceExistsError, resolve_run_dir  # noqa: E402
from run_duplicate_load import Backend, http  # noqa: E402

DISTRO = "Ubuntu-22.04"
WSL_ROOT = "/mnt/" + str(ROOT)[0].lower() + str(ROOT)[2:].replace("\\", "/")


def wsl(script: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["wsl.exe", "-d", DISTRO, "-e", "bash", "-c", f"cd {shlex.quote(WSL_ROOT)} && {script}"],
                          capture_output=True, text=True, timeout=timeout, check=False)


def wsl_dir(path: Path) -> str:
    return "/mnt/" + str(path)[0].lower() + str(path)[2:].replace("\\", "/")


def spawn(script: str, pid_file: str, log: Path) -> subprocess.Popen:
    # The log is written inside WSL. `setsid --wait` keeps wsl.exe attached until the process exits: plain setsid
    # forks and returns at once, and the detached child then raced the session teardown (2026-10-28: mediamtx never
    # wrote its pid file, run rtsp-controlled-20261028T165356944756Z).
    command = f"cd {shlex.quote(WSL_ROOT)} && echo $$ > {shlex.quote(pid_file)} && exec {script} > {shlex.quote(wsl_dir(log))} 2>&1"
    return subprocess.Popen(["wsl.exe", "-d", DISTRO, "-e", "setsid", "--wait", "bash", "-c", command],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class ServerNotListening(RuntimeError):
    pass


def rtsp_server_listening() -> bool:
    return wsl("(exec 3<>/dev/tcp/172.17.0.1/8554) 2>/dev/null && echo listening || echo refused", 30).stdout.strip() == "listening"


def kill_owned(pid_file: Path, process: subprocess.Popen | None) -> bool:
    pid = pid_file.read_text().strip() if pid_file.is_file() else ""
    if pid.isdigit():
        wsl(f"kill -TERM {pid} 2>/dev/null; for i in $(seq 1 40); do kill -0 {pid} 2>/dev/null || exit 0; sleep 0.25; done; kill -KILL {pid}", 30)
    if process is not None:
        try:
            process.wait(20)
        except subprocess.TimeoutExpired:
            process.kill()
    return pid.isdigit() and wsl(f"kill -0 {pid} 2>/dev/null && echo alive || echo gone").stdout.strip() == "gone"


def wait(predicate, timeout: float, interval: float = 0.5):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except Exception:  # noqa: BLE001
            last = None
        time.sleep(interval)
    return last


def main() -> int:
    try:
        evidence = resolve_run_dir(ROOT, "rtsp-controlled", "AEGIS_RTSP_RUN_DIR")
    except EvidenceExistsError as exc:
        raise SystemExit(str(exc)) from exc
    out = evidence.directory
    checks: dict[str, bool] = {}
    observed: dict[str, Any] = {"timeline": []}

    def check(name: str, ok: bool, detail: Any = None) -> None:
        checks[name] = bool(ok)
        if detail is not None:
            observed[name] = detail
        print(f"{'PASS' if ok else 'FAIL'} {name} {json.dumps(detail, default=str)[:240] if detail is not None else ''}", flush=True)

    install = wsl("bash scripts/setup/install-rtsp-test-server.sh", 300)
    check("rtsp_test_server_installed", install.returncode == 0, {"tail": install.stdout.strip().splitlines()[-1:]})
    user, password = "aegis", secrets.token_urlsafe(18)
    (out / "mediamtx.yml").write_text(yaml.safe_dump({
        "logLevel": "info", "readTimeout": "60s", "rtspAddress": ":8554", "rtspTransports": ["tcp"], "rtmp": False, "hls": False, "webrtc": False,
        "srt": False, "api": False, "metrics": False, "pprof": False, "playback": False,
        "authInternalUsers": [{"user": user, "pass": password, "ips": [], "permissions": [{"action": "publish"}, {"action": "read"}]}],
        "paths": {"cam01": {}}}, sort_keys=False), encoding="utf-8")
    (out / "mediamtx.yml.note").write_text("Per-run random credentials; this directory is local evidence only.\n", encoding="utf-8")
    server_pid, publisher_pid = out / "mediamtx.pid", out / "publisher.pid"
    server = spawn(f".install/wsl/mediamtx/mediamtx {shlex.quote(wsl_dir(out / 'mediamtx.yml'))}", wsl_dir(server_pid), out / "mediamtx.log")
    # 127.0.0.1:8554 is refused inside this WSL network (measured 2026-10-28); docker0 is also what the containers use.
    publish_url = f"rtsp://{user}:{password}@172.17.0.1:8554/cam01"
    publisher_cmd = (".install/wsl/ffmpeg/bin/ffmpeg -hide_banner -loglevel warning -re -stream_loop -1 -i demo/cam01.mp4 "
                     f"-c copy -rtsp_transport tcp -f rtsp {shlex.quote(publish_url)}")
    publisher = None
    backend = None
    run_id = None
    try:
        listening = wait(lambda: server_pid.is_file() and rtsp_server_listening(), 60, 1.0)
        check("rtsp_test_server_listening", bool(listening), {"pid_file": server_pid.is_file(), "server_exited": server.poll()})
        if not listening:
            raise ServerNotListening("test RTSP server did not start listening on 172.17.0.1:8554")
        publisher = spawn(publisher_cmd, wsl_dir(publisher_pid), out / "publisher-1.log")
        time.sleep(3)
        config_dir = out / "configs"
        shutil.copytree(ROOT / "configs", config_dir)
        cameras = yaml.safe_load((config_dir / "cameras.yaml").read_text(encoding="utf-8"))
        cameras["cameras"][1].update(enabled=True, source_type="rtsp", name="CONTROLLED RTSP (cam01 republished locally)",
                                     source="rtsp://${AEGIS_RTSP_USER}:${AEGIS_RTSP_PASS}@172.17.0.1:8554/cam01")
        (config_dir / "cameras.yaml").write_text(yaml.safe_dump(cameras, sort_keys=False), encoding="utf-8")
        env = dict(os.environ, CCTV_CONFIG_DIR=str(config_dir), CCTV_DATABASE_PATH=str(out / "run.db"),
                   CCTV_EVIDENCE_ROOT=str(out / "evidence"), CCTV_CAPABILITY_CACHE=str(out / "capability-cache.json"),
                   AEGIS_RTSP_USER=user, AEGIS_RTSP_PASS=password)
        backend = Backend(out / "backend.log", env)

        def cams() -> dict[str, dict]:
            return {c["id"]: c for c in http("/api/v1/facility/snapshot")[1]["cameras"]}

        status, snapshot = http("/api/v1/demo/start?replay_mode=paced", "POST")
        run_id = snapshot.get("run_id")
        check("start_with_live_camera", status == 200 and snapshot.get("run_status") in ("RUNNING", "DEGRADED"), {"run_status": snapshot.get("run_status")})
        first_epoch = cams()["cam-02"].get("epoch")
        live = wait(lambda: (lambda c: c if (c.get("visible_count") or 0) > 0 and (c.get("source_position_s") or 0) > 3 else None)(cams()["cam-02"]), 120)
        check("live_counts_from_rtsp", bool(live), {"visible_count": live and live.get("visible_count"), "epoch": first_epoch})
        a = cams()["cam-02"]
        t0 = time.monotonic()
        time.sleep(10)
        b = cams()["cam-02"]
        rate = ((b.get("source_position_s") or 0) - (a.get("source_position_s") or 0)) / (time.monotonic() - t0)
        check("live_source_advances_in_real_time", 0.85 <= rate <= 1.15, {"source_seconds_per_second": round(rate, 3),
                                                                           "analytics_fps": b.get("analytics_fps")})
        check("live_camera_has_no_preview_and_no_evidence", b.get("preview_url") is None and b.get("evidence_available") is False,
              {"reason": b.get("evidence_unavailable_reason")})
        status, refused = http("/api/v1/cameras/cam-02/evidence-markers", "POST")
        check("evidence_refused_for_live_with_reason", status >= 400 and "recording buffer" in json.dumps(refused), {"status": status})

        killed = time.monotonic()
        check("publisher_killed", kill_owned(publisher_pid, publisher))
        publisher = None
        lost = wait(lambda: (lambda c: c if c.get("visible_count") is None and (c.get("reconnect_attempt") or 0) >= 1 else None)(cams()["cam-02"]), 60)
        check("loss_visible_unavailable_not_zero", bool(lost) and lost.get("visible_count") is None,
              {"seconds_after_kill": round(time.monotonic() - killed, 1), "health": lost and lost.get("health"),
               "reason": lost and lost.get("unavailable_reason")})
        file_before = cams()["cam-01"].get("source_position_s") or 0
        # While the publisher is down every attempt fails after the backend startup deadline (>= 15 s: Service Maker
        # surfaces no source error to Python), so watch until 3 attempts were scheduled (bounded) and record each delay.
        attempts: dict[int, float] = {}
        down_started = time.monotonic()
        while time.monotonic() - down_started < 180 and len(attempts) < 3:
            reason = cams()["cam-02"].get("unavailable_reason") or ""
            match = re.search(r"reconnect attempt (\d+) in ([\d.]+) s", reason)
            if match:
                attempts.setdefault(int(match.group(1)), float(match.group(2)))
            time.sleep(0.5)
        observed["reconnect_delays_by_attempt"] = attempts
        observed["reconnect_watch_seconds"] = round(time.monotonic() - down_started, 1)
        state = cams()
        observed["while_down"] = {c: {k: state[c].get(k) for k in ("health", "visible_count", "reconnect_attempt", "next_reconnect_at",
                                                                     "unavailable_reason", "source_position_s", "stream_outcome")} for c in ("cam-01", "cam-02")}
        check("file_camera_unaffected", (state["cam-01"].get("source_position_s") or 0) > file_before or state["cam-01"].get("stream_outcome") == "CLEAN_EOF",
              {"before": file_before, "after": state["cam-01"].get("source_position_s"), "outcome": state["cam-01"].get("stream_outcome")})
        health = yaml.safe_load((config_dir / "analytics.yaml").read_text(encoding="utf-8")).get("health", {})
        base, cap = float(health.get("reconnect_backoff_seconds", 2)), float(health.get("max_reconnect_backoff_seconds", 30))
        expected = {n: min(cap, base * 2 ** (n - 1)) for n in attempts}
        check("reconnect_attempts_back_off", len(attempts) >= 3 and attempts == expected,
              {"observed_delays": attempts, "expected_delays": expected, "cap_s": cap})

        publisher = spawn(publisher_cmd, wsl_dir(publisher_pid), out / "publisher-2.log")
        restored = time.monotonic()
        back = wait(lambda: (lambda c: c if (c.get("visible_count") or 0) > 0 and c.get("epoch") not in (None, first_epoch) else None)(cams()["cam-02"]), 120)
        check("reconnects_with_new_epoch_and_counts_resume", bool(back), {"seconds_after_restore": round(time.monotonic() - restored, 1),
                                                                         "epoch": back and back.get("epoch"), "visible_count": back and back.get("visible_count")})
        alerts = http("/api/v1/alerts?limit=200")[1]
        reconnecting = [a for a in alerts if a["stable_key"] == "camera-reconnecting:cam-02"]
        check("reconnect_alert_resolved", bool(reconnecting) and reconnecting[0]["status"] == "RESOLVED", {"alerts": [(a["status"], a["message"][:120]) for a in reconnecting]})

        # Stall: the publisher is frozen (SIGSTOP) while its RTSP session stays open (server readTimeout 60 s), so no
        # EOS and no error reach the extractor; only the backend's live watchdog can notice.
        stall_s = float(yaml.safe_load((config_dir / "analytics.yaml").read_text(encoding="utf-8")).get("health", {}).get("live_stall_seconds", 10))
        stalled_epoch = cams()["cam-02"].get("epoch")
        pid = publisher_pid.read_text().strip()
        frozen_utc = datetime.now(timezone.utc)
        wsl(f"kill -STOP {pid}")
        # The reason string is visible only during the short backoff window, so the authoritative record is the
        # persisted alert history: the first camera alert transition after the freeze names how the epoch was released
        # (backend watchdog "no frame for more than N s", or the source itself ending).

        def released_after_freeze():
            with sqlite3.connect(f"file:{out / 'run.db'}?mode=ro", uri=True) as connection:
                return connection.execute(
                    "SELECT changed_at, reason FROM alert_state_changes WHERE run_id = ? AND to_status = 'OPEN' AND changed_at > ? "
                    "AND (reason LIKE '%no frame for more than%' OR reason LIKE '%live source ended%') ORDER BY changed_at LIMIT 1",
                    (run_id, frozen_utc.isoformat(timespec="milliseconds").replace("+00:00", "Z"))).fetchone()
        row = wait(released_after_freeze, stall_s + 30)
        latency = (datetime.fromisoformat(row[0].replace("Z", "+00:00")) - frozen_utc).total_seconds() if row else None
        check("stall_detected_and_released", bool(row) and latency is not None and latency <= stall_s + 5,
              {"seconds_after_freeze": latency and round(latency, 2), "stall_seconds": stall_s, "reason": row and row[1][:200],
               "mechanism": None if not row else ("backend live watchdog" if "no frame for more than" in row[1] else "source ended")})
        wsl(f"kill -CONT {pid}")
        resumed = wait(lambda: (lambda c: c if (c.get("visible_count") or 0) > 0 and c.get("epoch") not in (None, stalled_epoch) else None)(cams()["cam-02"]), 120)
        check("stall_recovers_with_new_epoch", bool(resumed), {"epoch": resumed and resumed.get("epoch")})

        # Stop while a reconnect is pending must cancel it: no container may be launched after the stop.
        check("publisher_killed_again", kill_owned(publisher_pid, publisher))
        publisher = None
        pending = wait(lambda: (lambda c: c if c.get("next_reconnect_at") else None)(cams()["cam-02"]), 60)
        status, stopped = http("/api/v1/demo/stop", "POST")
        check("stop_confirmed", status == 200 and stopped.get("run_status") == "STOPPED",
              {"stopped_with_pending_reconnect": bool(pending), "next_reconnect_at": pending and pending.get("next_reconnect_at")})
        with sqlite3.connect(f"file:{out / 'run.db'}?mode=ro", uri=True) as connection:
            rows_at_stop = connection.execute("SELECT COUNT(*) FROM owned_resources WHERE run_id = ?", (run_id,)).fetchone()[0]
        time.sleep(float(health.get("max_reconnect_backoff_seconds", 30)) + 5)  # past any pending reconnect delay
        with sqlite3.connect(f"file:{out / 'run.db'}?mode=ro", uri=True) as connection:
            rows_later = connection.execute("SELECT COUNT(*) FROM owned_resources WHERE run_id = ?", (run_id,)).fetchone()[0]
        running = wsl("docker ps --filter name=aegis-cam-02- --format '{{.Names}}'").stdout.split()
        check("stop_cancels_pending_reconnect", bool(pending) and rows_later == rows_at_stop and not running
              and cams()["cam-02"].get("next_reconnect_at") is None,
              {"owned_rows_at_stop": rows_at_stop, "owned_rows_later": rows_later, "running": running})
        everything = json.dumps({"snapshot": http("/api/v1/facility/snapshot")[1], "alerts": alerts,
                                 "provenance": http(f"/api/v1/runs/{run_id}/provenance")[1]})
        check("password_absent_from_api", password not in everything)
    except ServerNotListening as exc:  # recorded as a FAIL check above; the report is still written
        observed["aborted"] = str(exc)
    finally:
        if backend is not None:
            try:
                http("/api/v1/demo/stop", "POST")
            except Exception:  # noqa: BLE001
                pass
            backend.kill()
        check("publisher_terminated", publisher is None or kill_owned(publisher_pid, publisher))
        check("rtsp_server_terminated", kill_owned(server_pid, server))
    logs = "".join(p.read_text(encoding="utf-8", errors="replace") for p in (ROOT / "backend" / "var" / "perception" / "cam-02").glob("*.log")
                   if p.stat().st_mtime > time.time() - 3600) + (out / "backend.log").read_text(encoding="utf-8", errors="replace")
    check("password_absent_from_backend_and_container_logs", password not in logs)
    summaries = [json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "backend" / "var" / "perception" / "cam-02").glob("*.summary.json")
                 if p.stat().st_mtime > time.time() - 3600]
    check("extractor_summaries_record_live_source_without_credentials", bool(summaries) and all(
        s.get("live_source") is True and password not in json.dumps(s) and s.get("source_sha256") is None for s in summaries),
        {"summaries": len(summaries)})
    if run_id:
        with sqlite3.connect(out / "run.db") as connection:
            owned = connection.execute("SELECT camera_id, resource_name, state FROM owned_resources WHERE run_id = ?", (run_id,)).fetchall()
        observed["ownership"] = owned
        check("all_owned_containers_released", bool(owned) and all(state == "RELEASED" for _, _, state in owned), {"rows": len(owned)})
        leftover = [n for _, n, _ in owned if n in wsl(f"docker ps -a --filter name={n} --format '{{{{.Names}}}}'").stdout.split()]
        check("no_owned_container_left", not leftover, leftover)
    (out / "mediamtx.yml").write_text("# removed after the run: contained per-run test credentials\n", encoding="utf-8")
    report = {"schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "command": evidence.command,
              "run_dir": str(out.relative_to(ROOT)), "result": "PASS" if checks and all(checks.values()) else "FAIL",
              "checks": {k: "PASS" if v else "FAIL" for k, v in checks.items()}, "observed": observed,
              "scope": "CONTROLLED local RTSP: demo/cam01.mp4 republished in real time by ffmpeg through a test-only mediamtx in WSL; "
                       "Windows backend route, real GPU. Proves the live ingestion, loss, backoff and reconnect software path; "
                       "not a physical camera, real network behaviour, or new footage."}
    (out / "result.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"], "run_dir": report["run_dir"], "failed": [k for k, v in checks.items() if not v]}, indent=1))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
