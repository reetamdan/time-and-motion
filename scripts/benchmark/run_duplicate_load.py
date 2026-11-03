#!/usr/bin/env python3
"""Engineering load ladder with DUPLICATE-INPUT feeds (PRD 19.2), through the full application.

Every feed is the same supplied clip (demo/cam01.mp4) under a distinct camera ID. This measures
load on this laptop only. It proves nothing about physical multi-camera coverage, overlap identity,
cross-floor continuity, or counting accuracy, and the generated configuration labels each replica.

Workload stage: D (decode + TrafficCamNet detection + NvDCF tracking + camera-local analytics +
persistence + API), with the dashboard closed. Stages A-C (decode-only / no-tracker variants) need
pipeline variants the protected extractor does not expose; they are recorded as not attempted.

The clip is 23.7 s and the pipeline does not loop it, so each step is one full-clip pass: these are
labelled ENGINEERING runs (no 120 s warm-up / 600 s steady state as in the PRD capacity profile).

For each pacing mode the ladder 1 -> 2 -> 4 -> 6 -> 8 stops at the first step that fails the
predeclared limits below, recording the last acceptable step and the reason.

Run: .venv/Scripts/python.exe scripts/benchmark/run_duplicate_load.py [--modes paced,unpaced] [--counts 1,2,4,6,8]
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
from evidence_dirs import EvidenceExistsError, resolve_run_dir, utc_stamp  # noqa: E402

PORT = 8770
BASE = f"http://127.0.0.1:{PORT}"
CLIP = "./demo/cam01.mp4"
# Predeclared step limits (an engineering envelope for this laptop, not PRD acceptance thresholds).
LIMITS = {
    "all_feeds_clean_eof": True,
    "max_dropped_frames": 0,
    "paced_source_lag_p95_s": 1.0,
    "max_vram_fraction": 0.90,
    "max_gpu_temperature_c": 87,
    "max_backend_queue_p95_ms_paced": 1000,
}


def http(path: str, method: str = "GET", timeout: float = 180) -> tuple[int, Any]:
    request = urllib.request.Request(BASE + path, method=method, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def pct(values: list[float], q: float) -> float | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    index = min(len(values) - 1, max(0, round(q * (len(values) - 1))))
    return round(values[index], 3)


class GpuSampler(threading.Thread):
    QUERY = "utilization.gpu,utilization.decoder,memory.used,memory.total,temperature.gpu,power.draw,clocks.sm,pstate"

    def __init__(self, interval: float = 1.0) -> None:
        super().__init__(daemon=True)
        self.interval = interval
        self.samples: list[dict[str, Any]] = []
        self.stop_event = threading.Event()

    def run(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            result = subprocess.run(["nvidia-smi", f"--query-gpu={self.QUERY}", "--format=csv,noheader,nounits"],
                                    capture_output=True, text=True, timeout=10, check=False)
            if result.returncode == 0 and result.stdout.strip():
                parts = [p.strip() for p in result.stdout.strip().splitlines()[0].split(",")]
                keys = ["gpu_util", "nvdec_util", "vram_used_mib", "vram_total_mib", "temp_c", "power_w", "sm_clock_mhz", "pstate"]
                sample = {"t": round(time.monotonic(), 3)}
                for key, value in zip(keys, parts):
                    try:
                        sample[key] = float(value)
                    except ValueError:
                        sample[key] = value
                self.samples.append(sample)
            self.stop_event.wait(max(0.0, self.interval - (time.monotonic() - started)))


def write_config(directory: Path, count: int) -> None:
    shutil.copytree(ROOT / "configs", directory)
    path = directory / "cameras.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    template = document["cameras"][0]
    cameras = []
    for index in range(1, 9):
        camera = dict(template)
        camera.update(
            id=f"cam-{index:02d}", enabled=index <= count, source=CLIP, overlap_group=None,
            name=f"DUPLICATE INPUT {index} of cam01.mp4 (load test only)",
            floor_id="floor-1" if index != 4 else "floor-2",
            position=[90 + 40 * index, 105], coverage_polygon=[[25, 35], [310, 35], [330, 285], [30, 285]],
        )
        cameras.append(camera)
    document["cameras"] = cameras
    document["load_test_note"] = "DUPLICATE INPUT: every camera is the same clip; load measurement only"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


class Backend:
    def __init__(self, log: Path, env: dict[str, str]) -> None:
        self.log = log.open("w", encoding="utf-8")
        if os.name == "nt":
            command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "run-backend.ps1"),
                       "-Port", str(PORT)]
        else:  # native Linux / inside WSL: run-backend.sh execs uvicorn, so the Popen pid is the server
            command = ["bash", str(ROOT / "scripts" / "run-backend.sh")]
            env = dict(env, BACKEND_PORT=str(PORT))
        self.process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"backend exited {self.process.returncode} before becoming live")
            try:
                if http("/health/live", timeout=3)[0] == 200:
                    return
            except Exception:
                time.sleep(0.5)
        raise RuntimeError("backend did not become live")

    def kill(self) -> None:
        if os.name == "nt":  # always: the tree (powershell -> python) is killed even if the wrapper already exited
            subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True, check=False)
        elif self.process.poll() is None:
            self.process.terminate()
        if self.process.poll() is None:
            try:
                self.process.wait(30)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(10)
        self.log.close()


def run_step(run_dir: Path, mode: str, count: int, cache: Path) -> dict[str, Any]:
    step_dir = run_dir / f"{mode}-{count}"
    step_dir.mkdir()
    write_config(step_dir / "configs", count)
    database = step_dir / "run.db"
    env = dict(os.environ, CCTV_CONFIG_DIR=str(step_dir / "configs"), CCTV_DATABASE_PATH=str(database),
               CCTV_EVIDENCE_ROOT=str(step_dir / "evidence"), CCTV_CAPABILITY_CACHE=str(cache))
    backend = Backend(step_dir / "backend.log", env)
    sampler = GpuSampler()
    polls: list[dict[str, Any]] = []
    result: dict[str, Any] = {"mode": mode, "feeds": count}
    try:
        _, idle_metrics = http("/api/v1/system/metrics")
        sampler.start()
        time.sleep(2)  # idle GPU baseline
        baseline = list(sampler.samples)
        started = time.perf_counter()
        status, snapshot = http(f"/api/v1/demo/start?replay_mode={mode}", "POST")
        start_seconds = time.perf_counter() - started
        start_return = time.monotonic()
        result.update(http_status=status, start_seconds=round(start_seconds, 3), run_status=snapshot.get("run_status"),
                      run_id=snapshot.get("run_id"))
        enabled = [f"cam-{i:02d}" for i in range(1, count + 1)]
        deadline = time.monotonic() + (90 if mode == "paced" else 60) + 10 * count
        finished_at: dict[str, float] = {}
        while time.monotonic() < deadline:
            _, snap = http("/api/v1/facility/snapshot")
            _, metrics = http("/api/v1/system/metrics")
            now = time.monotonic()
            poll = {"t": round(now - start_return, 3), "cpu": metrics.get("cpu_utilization"), "ram_mb": metrics.get("ram_used_mb"),
                    "rss_mb": metrics.get("process_rss_mb"), "cameras": {}}
            for camera in snap["cameras"]:
                if camera["id"] not in enabled:
                    continue
                poll["cameras"][camera["id"]] = {k: camera.get(k) for k in (
                    "source_position_s", "analytics_fps", "backend_queue_ms", "dropped_frames", "stream_outcome",
                    "persisted_count_samples", "visible_count", "frames_at_detection_limit", "health")}
                if camera.get("stream_outcome") and camera["id"] not in finished_at:
                    finished_at[camera["id"]] = now - start_return
            polls.append(poll)
            if len(finished_at) == count:
                break
            time.sleep(0.5)
        stop_started = time.perf_counter()
        _, stopped = http("/api/v1/demo/stop", "POST")
        result["stop_seconds"] = round(time.perf_counter() - stop_started, 3)
        result["stop_status"] = stopped.get("run_status")
        sampler.stop_event.set()
        sampler.join(5)
        result["idle_metrics"] = idle_metrics
        with sqlite3.connect(database) as connection:
            frames = dict(connection.execute(
                "SELECT camera_id, SUM(frames) FROM camera_count_samples GROUP BY camera_id").fetchall())
            owned = connection.execute("SELECT resource_name, state FROM owned_resources").fetchall()
            epochs = dict(connection.execute("SELECT camera_id, camera_epoch FROM camera_sessions").fetchall())
        per_camera = {}
        last = polls[-1]["cameras"] if polls else {}
        for camera_id in enabled:
            series = [p["cameras"].get(camera_id, {}) for p in polls]
            lags = [p["t"] - s["source_position_s"] for p, s in zip(polls, series)
                    if s.get("source_position_s") is not None and not s.get("stream_outcome")]
            summary_path = ROOT / "backend" / "var" / "perception" / camera_id / f"{epochs.get(camera_id)}.summary.json"
            extractor = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
            elapsed = extractor.get("elapsed_seconds")
            written = extractor.get("frames_written")
            per_camera[camera_id] = {
                "stream_outcome": last.get(camera_id, {}).get("stream_outcome"),
                "wall_to_end_s": round(finished_at[camera_id], 3) if camera_id in finished_at else None,
                "frames_persisted": frames.get(camera_id),
                "dropped_frames": last.get(camera_id, {}).get("dropped_frames"),
                "frames_at_detection_limit": last.get(camera_id, {}).get("frames_at_detection_limit"),
                "analytics_fps_p50": pct([s.get("analytics_fps") for s in series], 0.5),
                "backend_queue_ms_p50": pct([s.get("backend_queue_ms") for s in series], 0.5),
                "backend_queue_ms_p95": pct([s.get("backend_queue_ms") for s in series], 0.95),
                "source_lag_s_p50": pct(lags, 0.5) if mode == "paced" else None,
                "source_lag_s_p95": pct(lags, 0.95) if mode == "paced" else None,
                "extractor_frames_written": written, "extractor_elapsed_s": elapsed,
                "extractor_pipeline_fps": round(written / elapsed, 2) if written and elapsed else None,
                "extractor_clean_eos": extractor.get("clean_eos"),
            }
        gpu = sampler.samples[len(baseline):]
        result["per_camera"] = per_camera
        result["gpu"] = {
            "baseline_vram_mib": pct([s.get("vram_used_mib") for s in baseline], 0.5),
            "vram_used_mib_p50": pct([s.get("vram_used_mib") for s in gpu], 0.5),
            "vram_used_mib_peak": max((s.get("vram_used_mib") or 0 for s in gpu), default=None),
            "vram_total_mib": gpu[0].get("vram_total_mib") if gpu else None,
            "gpu_util_p50": pct([s.get("gpu_util") for s in gpu], 0.5), "gpu_util_p95": pct([s.get("gpu_util") for s in gpu], 0.95),
            "nvdec_util_p50": pct([s.get("nvdec_util") for s in gpu], 0.5), "nvdec_util_p95": pct([s.get("nvdec_util") for s in gpu], 0.95),
            "temp_c_peak": max((s.get("temp_c") or 0 for s in gpu), default=None),
            "power_w_p50": pct([s.get("power_w") for s in gpu], 0.5), "sm_clock_mhz_p50": pct([s.get("sm_clock_mhz") for s in gpu], 0.5),
            "samples": len(gpu),
        }
        result["host"] = {"cpu_util_p50": pct([p["cpu"] for p in polls], 0.5), "cpu_util_p95": pct([p["cpu"] for p in polls], 0.95),
                          "ram_used_mb_peak": max((p["ram_mb"] or 0 for p in polls), default=None),
                          "backend_rss_mb_peak": max((p["rss_mb"] or 0 for p in polls), default=None)}
        result["ownership"] = [{"name": n, "state": s} for n, s in owned]
        (step_dir / "raw.json").write_text(json.dumps({"polls": polls, "gpu_samples": sampler.samples}, indent=1), encoding="utf-8")
    finally:
        sampler.stop_event.set()
        backend.kill()
    present = []
    for row in result.get("ownership", []):
        out = subprocess.run(["wsl.exe", "-d", "Ubuntu-22.04", "-e", "docker", "ps", "-a", "--filter", f"name={row['name']}",
                              "--format", "{{.Names}}"], capture_output=True, text=True, timeout=30, check=False).stdout
        if row["name"] in out.split():
            present.append(row["name"])
    result["containers_left"] = present
    result["evaluation"] = evaluate(result)
    (step_dir / "summary.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


def evaluate(result: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    cameras = result.get("per_camera", {})
    if result.get("run_status") != "RUNNING":
        reasons.append(f"start status {result.get('run_status')}")
    if not cameras or any(c["stream_outcome"] != "CLEAN_EOF" for c in cameras.values()):
        reasons.append("not every feed reached CLEAN_EOF")
    if any((c["dropped_frames"] or 0) > LIMITS["max_dropped_frames"] for c in cameras.values()):
        reasons.append("dropped frames")
    if any(c["frames_persisted"] != 710 for c in cameras.values()):
        reasons.append("not all 710 frames persisted per feed")
    if result["mode"] == "paced":
        lag = [c["source_lag_s_p95"] for c in cameras.values() if c["source_lag_s_p95"] is not None]
        if lag and max(lag) > LIMITS["paced_source_lag_p95_s"]:
            reasons.append(f"paced source lag p95 {max(lag)} s > {LIMITS['paced_source_lag_p95_s']} s")
    gpu = result.get("gpu", {})
    if gpu.get("vram_used_mib_peak") and gpu.get("vram_total_mib") and gpu["vram_used_mib_peak"] / gpu["vram_total_mib"] > LIMITS["max_vram_fraction"]:
        reasons.append("VRAM above limit")
    if gpu.get("temp_c_peak") and gpu["temp_c_peak"] > LIMITS["max_gpu_temperature_c"]:
        reasons.append("GPU temperature above limit")
    if result.get("containers_left"):
        reasons.append("owned containers left running")
    return {"acceptable": not reasons, "reasons": reasons}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modes", default="unpaced,paced")
    parser.add_argument("--counts", default="1,2,4,6,8")
    args = parser.parse_args()
    default = ROOT / "reports" / "benchmarks" / f"duplicate-load-ladder-{utc_stamp()}"
    try:
        evidence = resolve_run_dir(ROOT, "duplicate-load-ladder", "AEGIS_LOAD_RUN_DIR", argv=[],
                                   environ={**os.environ, "AEGIS_LOAD_RUN_DIR": os.environ.get("AEGIS_LOAD_RUN_DIR") or str(default)})
    except EvidenceExistsError as exc:
        raise SystemExit(str(exc)) from exc
    run_dir = evidence.directory
    cache = run_dir / "capability-cache.json"
    clip_sha = hashlib.sha256((ROOT / "demo" / "cam01.mp4").read_bytes()).hexdigest()
    started = datetime.now(timezone.utc).isoformat()
    ladders: dict[str, Any] = {}
    for mode in args.modes.split(","):
        steps, last_ok, stop_reason = [], None, "ladder completed"
        for count in [int(c) for c in args.counts.split(",")]:
            print(f"=== {mode} x{count}", flush=True)
            step = run_step(run_dir, mode, count, cache)
            steps.append(step)
            print(json.dumps({"feeds": count, "start_s": step.get("start_seconds"), "eval": step["evaluation"],
                              "gpu": {k: step["gpu"].get(k) for k in ("vram_used_mib_peak", "gpu_util_p50", "nvdec_util_p50", "temp_c_peak")}},
                             default=str), flush=True)
            if not step["evaluation"]["acceptable"]:
                stop_reason = f"stopped at {count} feeds: " + "; ".join(step["evaluation"]["reasons"])
                break
            last_ok = count
        ladders[mode] = {"steps": steps, "last_acceptable_feeds": last_ok, "stop_reason": stop_reason}
    report = {
        "schema_version": 1, "run_kind": "ENGINEERING duplicate-input load ladder (not a PRD steady-state capacity run)",
        "started_utc": started, "finished_utc": datetime.now(timezone.utc).isoformat(), "command": evidence.command,
        "run_dir": str(run_dir.relative_to(ROOT)), "workload_stage": "D (decode+detect+track+camera-local analytics+persistence+API; dashboard closed)",
        "stages_not_attempted": {"A-C": "decode-only / no-tracker pipeline variants are not exposed by the protected extractor",
                                 "E-F": "no calibrated overlap fusion or Re-ID inputs", "G": "dashboard/preview load not included"},
        "source": {"path": CLIP, "sha256": clip_sha, "resolution": "1920x1080", "codec": "h264", "fps": "30000/1001",
                   "frames": 710, "duration_s": 23.69, "duplicate_input": True},
        "limits": LIMITS, "ladders": ladders,
        "scope": "Each feed is the same clip under a distinct camera ID: load measurement only. No physical multi-camera "
                 "coverage, overlap identity, cross-floor continuity, or counting accuracy is shown. One pass per step; "
                 "no repeat runs, so no variance estimate and no capacity extrapolation.",
    }
    (run_dir / "summary.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({m: {"last_acceptable_feeds": v["last_acceptable_feeds"], "stop_reason": v["stop_reason"]}
                      for m, v in ladders.items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
