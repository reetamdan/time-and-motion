#!/usr/bin/env python3
"""Steady-state capacity benchmark and full-demo soak (PRD 18.3 / 19): stages A-D, repeats, feed ladder.

Stages (PRD 19.1), identical footage, one recorded configuration difference each:
  A  decode only                      extractor --stage decode   (containers only, no backend)
  B  decode + detection (no tracker)  extractor --stage detect   (containers only)
  C  B + authoritative NvDCF tracking extractor --stage track    (containers only)
  D  C + deterministic analytics      the real backend, stage track, all analytics on
  E-H need calibrated overlap footage / Re-ID / the accepted core profile: NOT RUN (recorded in the report).

Footage: LABELLED REPEATED/CONCATENATED DEMO FOOTAGE (manifest beside each clip). Every feed is a
DUPLICATE decoder instance of that clip with its own camera id: load measurement only, never
physically distinct camera coverage, counting accuracy, or calibrated facility acceptance.

Protocol v2 (PROTOCOL below) is frozen before measuring and written to every report first. Plans:
  repeats  A,B,C,D x3 at 4 feeds, 120 s warm-up + 600 s measured        (stage comparison)
  ladder   D at 1,2,4,6,8 feeds, stop at the first unacceptable step     (stream-count scaling)
  soak     D at 4 feeds, 120 s warm-up + 1800 s measured, with dashboard/preview/history/evidence probes
  custom   any --stages/--feeds/--repeats/--warmup/--measure (recorded as deviations; never acceptance)

Exit codes: 0 every executed condition passed and the plan completed; 1 a condition failed its gates;
2 interrupted (resource guard, frozen-input change, exception, signal) - evidence kept, INTERRUPTED.json;
3 refused before measuring (artifact identity mismatch or insufficient memory headroom).

Run (Windows route):  .venv/Scripts/python.exe scripts/benchmark/run_capacity.py --plan repeats
Run (native Linux / backend inside WSL):  .venv-wsl/bin/python scripts/benchmark/run_capacity.py --plan repeats
Profile: --config-dir, else CCTV_CONFIG_DIR, else configs/ (RTX A6000: --config-dir configs/a6000). The selected
profile is used for every stage and recorded in the report. In the benchmark's copy, frame-log rotation is off
(raw logs are measurement evidence) and source looping is off (each step's clip is long enough).
"""

from __future__ import annotations

import argparse
from array import array
from collections import deque
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Iterator
import urllib.error
import urllib.request
import uuid

import psutil
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
from backend.app.configuration import load_configuration  # noqa: E402
from backend.app.perception.artifacts import HashMemo, runtime_artifact_checks  # noqa: E402
from backend.app.perception.service_maker import host_route, runtime_prefix, workspace_path, wsl_path  # noqa: E402
from evidence_dirs import utc_stamp  # noqa: E402

CLIPS = {
    "capacity": ROOT / "demo" / "derived" / "cam01-repeat33.mp4",   # 782 s >= 60 + 120 + 600 s
    "soak": ROOT / "demo" / "derived" / "cam01-repeat90.mp4",       # 2133 s >= 60 + 120 + 1800 s
}
DISTRO = "Ubuntu-22.04"
PORT = 8773
BASE = f"http://127.0.0.1:{PORT}"
STAGE_ARG = {"A": "decode", "B": "detect", "C": "track", "D": "track"}
EXIT_PASS, EXIT_FAIL, EXIT_INTERRUPTED, EXIT_REFUSED = 0, 1, 2, 3
# The configuration profile under test (set in main from --config-dir / CCTV_CONFIG_DIR).
CONFIG_DIR = ROOT / "configs"

PROTOCOL: dict[str, Any] = {
    "protocol_version": 2,
    "frozen_at": "2026-10-28, before any v2 acceptance step; v1 (run capacity-repeats-20261028T080352534437Z) "
                 "gated stage D on producer records and treated a missing processing age as 0 - superseded",
    "profile": "paced real-time demo profile (PRD 18.3): producer paced to the source timeline with a 200 ms lead; "
               "the backend releases each frame at its source time",
    "warmup_s": 120, "measure_s": 600, "soak_measure_s": 1800, "repeats": 3, "feeds_for_stage_comparison": 4,
    "ladder": [1, 2, 4, 6, 8], "ladder_stage": "D",
    "ladder_stop_rule": "stop increasing at the first step that fails any gate, trips the resource guard, or lacks memory headroom",
    "gates": {
        "cadence_fps_min_per_feed": 7.2,
        "cadence_basis": "90% of the configured analytics target 8 fps/camera (configs/cameras.yaml analytics_fps). "
                         "Stage D: frames the backend analytics consumed (release log, analytics_done) per camera; "
                         "stages A-C: frames the producer emitted. The 29.97 fps source rate is reported separately.",
        "p95_receive_to_result_ms_max": 2000,
        "receive_to_result_basis": "stage D only: backend analytics_done minus backend stdout read time, same host clock, "
                                   "over every produced frame in the window; a produced frame never consumed is a missing "
                                   "delivery (fail), never excluded",
        "p95_producer_schedule_lag_ms_max_stages_a_c": 2000,
        "producer_schedule_lag_basis": "stages A-C (no consumer): producer emit time minus the frame's due time on the source "
                                       "timeline anchored at the feed's first frame; negative = the deliberate 200 ms lead. "
                                       "A real-time keep-up criterion, NOT receive-to-result latency",
        "backlog_growth_ms_max": 500,
        "backlog_basis": "last-minute median minus first-minute median of the gated latency (D: receive-to-result; "
                         "A-C: producer schedule lag); D also reports backend queue depth",
        "frame_gaps_max": 0, "missing_deliveries_max": 0, "crash_or_oom": "none allowed",
        "missing_measurement": "any required metric that is unavailable fails the step explicitly",
        "expected_feed_set": "every configured feed must be measured; an absent feed fails the step",
        "gpu_temp_max_c": 87, "backend_rss_growth_mib_max": 256, "container_rss_growth_mib_max": 128,
        "memory_growth_basis": "last-minute median minus first-minute median of the measured window",
        "cleanup": "every owned container confirmed absent after the step",
    },
    "resource_policy": {
        "host_available_hard_stop_mib": 512, "host_available_sustained_stop_mib": 768, "sustained_s": 30,
        "host_commit_available_stop_mib": 2048, "wsl_guest_available_stop_mib": 512,
        "gpu_temp_stop_c": 92, "gpu_temp_stop_sustained_s": 20,
        "preflight_mib_per_feed": 650, "preflight_backend_mib": 400, "preflight_floor_mib": 768, "preflight_wait_s": 180,
        "preflight_basis": "measured 2026-10-28 (reports/benchmarks/memory-diagnosis-20261028T114500000000Z): 4 stage-C "
                           "containers took ~2.6 GiB of host memory",
        "between_steps": "WSL route: guest clean page cache dropped after cleanup and before preflight/warm-up (never "
                         "inside a measured window); result recorded per step",
        "on_trip": "stop owned workloads, write INTERRUPTED.json, exit 2 and start no further step (ladder: the step is "
                   "the first unacceptable one)",
    },
    "time_bases": {
        "producer_clock": "container wall clock (emitted_unix_ns)",
        "backend_clock": "host wall clock (read/released/analytics_done_unix_ns)",
        "clock_offset": "WSL/container minus host, best of 9 round trips, measured before and after every step; "
                        "uncertainty = half the best round trip. Used only for the reported producer->backend transit.",
        "capture_to_result": "not measured: file replay has no capture clock",
    },
    "reported_not_gated": ["producer cadence at stage D", "fraction of the 29.97 fps source rate", "producer->backend transit",
                           "GPU/NVDEC/VRAM/power/SM clock", "host CPU/RAM and WSL guest memory", "aggregate throughput"],
}


# ---------------------------------------------------------------------------------------------
# Bounded JSONL access (the memory fix: never materialise whole files)
# ---------------------------------------------------------------------------------------------

def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """One decoded record at a time; a line cut by the final stop (or any undecodable line) is skipped."""
    if not path.is_file():
        return
    with path.open("rb") as handle:
        for line in handle:
            if line.strip():
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def first_record(path: Path) -> dict[str, Any] | None:
    """The first complete record, reading only as far as needed."""
    try:
        with path.open("rb") as handle:
            for line in handle:
                if line.strip():
                    try:
                        return json.loads(line)
                    except ValueError:
                        return None
    except OSError:
        return None
    return None


def last_record(path: Path, chunk: int = 4096) -> dict[str, Any] | None:
    """The last complete record, reading backwards in growing blocks so a record of any size is found."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            block = chunk
            while True:
                start = max(0, size - block)
                handle.seek(start)
                data = handle.read(size - start)
                lines = data.split(b"\n")
                # Without having reached the file start, the first piece may be a partial line.
                candidates = lines if start == 0 else lines[1:]
                for line in reversed(candidates):
                    if line.strip():
                        try:
                            return json.loads(line)
                        except ValueError:
                            continue  # a truncated final line: try the previous one
                if start == 0:
                    return None
                block *= 4
    except OSError:
        return None


def pct(values, q: float) -> float | None:
    ordered = sorted(v for v in values if v is not None)
    if not ordered:
        return None
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return round(ordered[index], 3)


def _median(values) -> float | None:
    values = list(values)
    return round(statistics.median(values), 1) if values else None


# ---------------------------------------------------------------------------------------------
# Per-feed metrics
# ---------------------------------------------------------------------------------------------

def producer_metrics(path: Path, warmup: float, measure: float) -> dict[str, Any]:
    """Producer cadence, frame gaps, schedule lag and its growth, from one pass over the producer JSONL."""
    start, end = warmup, warmup + measure
    t0 = pts0 = previous = None
    frames_total = gaps = objects = 0
    times, lags = array("d"), array("d")
    window_frames: set[int] = set()
    last_t = None
    for record in iter_jsonl(path):
        try:
            emitted, pts, number = int(record["emitted_unix_ns"]), int(record["pts_ns"]), int(record["frame_number"])
        except (KeyError, TypeError, ValueError):
            continue
        if t0 is None:
            t0, pts0 = emitted, pts
        frames_total += 1
        if previous is not None and number > previous + 1:
            gaps += number - previous - 1
        previous = number
        t = (emitted - t0) / 1e9
        last_t = t
        if start <= t < end:
            times.append(t)
            lags.append(((emitted - t0) - (pts - pts0)) / 1e6)
            objects += len(record.get("objects") or ())
            window_frames.add(number)
    if not frames_total:
        return {"frames_total": 0, "frames_in_window": 0, "error": "no producer frames recorded", "_window_frames": set()}
    first = [lag for t, lag in zip(times, lags) if t < start + 60]
    last = [lag for t, lag in zip(times, lags) if t >= end - 60]
    first_m, last_m = _median(first), _median(last)
    return {
        "frames_total": frames_total, "frames_in_window": len(times),
        "cadence_fps": round(len(times) / measure, 3) if times else 0.0,
        "frame_gaps": gaps,
        "covered_window_s": round(min(last_t, end) - start, 1) if last_t is not None and last_t >= start else 0.0,
        "schedule_lag_ms": {"p50": pct(lags, 0.5), "p95": pct(lags, 0.95), "max": round(max(lags), 1) if lags else None,
                            "basis": PROTOCOL["gates"]["producer_schedule_lag_basis"]},
        "schedule_backlog_growth_ms": round(last_m - first_m, 1) if first_m is not None and last_m is not None else None,
        "objects_per_frame_mean": round(objects / len(times), 2) if times else None,
        "_window_frames": window_frames,
    }


def release_metrics(path: Path, warmup: float, measure: float, produced_in_window: set[int],
                    offset_ns: int | None) -> dict[str, Any]:
    """Backend-consumed cadence, receive-to-result age, gaps, missing deliveries and backlog (stage D)."""
    start, end = warmup, warmup + measure
    base = previous = None
    consumed: set[int] = set()
    gaps = records = 0
    times, ages, holds, depths, transit = array("d"), array("d"), array("d"), array("d"), array("d")
    for record in iter_jsonl(path):
        try:
            number = int(record["frame_number"])
            read, done = int(record["read_unix_ns"]), int(record["analytics_done_unix_ns"])
            released = int(record["released_unix_ns"])
        except (KeyError, TypeError, ValueError):
            continue
        records += 1
        consumed.add(number)
        if previous is not None and number > previous + 1:
            gaps += number - previous - 1
        previous = number
        if base is None:
            base = read
        t = (done - base) / 1e9
        if start <= t < end:
            times.append(t)
            ages.append((done - read) / 1e6)
            holds.append((released - read) / 1e6)
            depths.append(float(record.get("queue_depth") or 0))
            emitted = record.get("emitted_unix_ns")
            if offset_ns is not None and type(emitted) is int:
                transit.append((read - (emitted - offset_ns)) / 1e6)
    if not records:
        return {"consumed_frames_total": 0, "consumed_in_window": 0, "error": "no backend release records"}
    missing = sorted(n for n in produced_in_window if n not in consumed)
    first = [a for t, a in zip(times, ages) if t < start + 60]
    last = [a for t, a in zip(times, ages) if t >= end - 60]
    first_m, last_m = _median(first), _median(last)
    first_d = [d for t, d in zip(times, depths) if t < start + 60]
    last_d = [d for t, d in zip(times, depths) if t >= end - 60]
    return {
        "consumed_frames_total": records, "consumed_in_window": len(times),
        "consumed_cadence_fps": round(len(times) / measure, 3) if times else 0.0,
        "release_frame_gaps": gaps,
        "produced_in_window": len(produced_in_window),
        "missing_deliveries": len(missing), "missing_delivery_examples": missing[:20],
        "delivery_coverage": round(1 - len(missing) / len(produced_in_window), 6) if produced_in_window else None,
        # Every produced frame of the window is either consumed (an age) or missing (a failure); none is dropped silently.
        "receive_to_result_ms": {"p50": pct(ages, 0.5), "p95": pct(ages, 0.95), "max": round(max(ages), 1) if ages else None,
                                 "basis": PROTOCOL["gates"]["receive_to_result_basis"]},
        "pacing_hold_ms": {"p50": pct(holds, 0.5), "p95": pct(holds, 0.95),
                           "basis": "released minus read: deliberate hold until the frame's source time (paced profile)"},
        "receive_to_result_backlog_growth_ms": round(last_m - first_m, 1) if first_m is not None and last_m is not None else None,
        "queue_depth": {"p50": pct(depths, 0.5), "p95": pct(depths, 0.95), "max": max(depths) if depths else None,
                        "first_minute_median": _median(first_d), "last_minute_median": _median(last_d)},
        "producer_to_backend_transit_ms": {"p50": pct(transit, 0.5), "p95": pct(transit, 0.95),
                                           "basis": "backend read (host clock) minus producer emit corrected by the measured "
                                                    "clock offset; reported, not gated"} if transit else None,
    }


def growth(samples: list[dict[str, Any]], key: str, window: tuple[float, float]) -> float | None:
    start, end = window
    first = [s[key] for s in samples if start <= s["t"] < start + 60 and isinstance(s.get(key), (int, float))]
    last = [s[key] for s in samples if end - 60 <= s["t"] < end and isinstance(s.get(key), (int, float))]
    return round(statistics.median(last) - statistics.median(first), 1) if first and last else None


# ---------------------------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------------------------

def gate(stage: str, expected: list[str], feeds: dict[str, dict[str, Any]], measure: float, crashed: list[str],
         resources: dict[str, Any] | None, extra_failures: list[str] | None = None) -> dict[str, Any]:
    g = PROTOCOL["gates"]
    failures: list[str] = list(extra_failures or [])

    def need(camera: str, label: str, value: Any) -> bool:
        if not isinstance(value, (int, float)):
            failures.append(f"{camera}: required measurement unavailable: {label}")
            return False
        return True

    missing_feeds = [c for c in expected if c not in feeds]
    failures += [f"{c}: expected feed was not measured" for c in missing_feeds]
    failures += [f"{c}: unexpected feed (not in the expected set)" for c in feeds if c not in expected]
    for camera in expected:
        metrics = feeds.get(camera)
        if metrics is None:
            continue
        producer = metrics.get("producer", {})
        if producer.get("error") or not producer.get("frames_in_window"):
            failures.append(f"{camera}: {producer.get('error') or 'no producer frames in the measured window'}")
            continue
        if need(camera, "covered window", producer.get("covered_window_s")) and producer["covered_window_s"] < measure - 5:
            failures.append(f"{camera}: producer frames cover only {producer['covered_window_s']} s of the {measure:g} s window")
        if need(camera, "producer frame gaps", producer.get("frame_gaps")) and producer["frame_gaps"] > g["frame_gaps_max"]:
            failures.append(f"{camera}: {producer['frame_gaps']} missing producer frame numbers")
        if stage == "D":
            backend = metrics.get("backend") or {}
            if backend.get("error"):
                failures.append(f"{camera}: {backend['error']}")
                continue
            cadence = backend.get("consumed_cadence_fps")
            if need(camera, "backend consumed cadence", cadence) and cadence < g["cadence_fps_min_per_feed"]:
                failures.append(f"{camera}: backend consumed cadence {cadence} fps < {g['cadence_fps_min_per_feed']}")
            if need(camera, "missing deliveries", backend.get("missing_deliveries")) and backend["missing_deliveries"] > g["missing_deliveries_max"]:
                failures.append(f"{camera}: {backend['missing_deliveries']} produced frames never consumed by the backend")
            if need(camera, "release gaps", backend.get("release_frame_gaps")) and backend["release_frame_gaps"] > g["frame_gaps_max"]:
                failures.append(f"{camera}: {backend['release_frame_gaps']} frame numbers skipped by the backend consumer")
            p95 = (backend.get("receive_to_result_ms") or {}).get("p95")
            if need(camera, "p95 receive-to-result", p95) and p95 > g["p95_receive_to_result_ms_max"]:
                failures.append(f"{camera}: p95 receive-to-result {p95} ms > {g['p95_receive_to_result_ms_max']}")
            backlog = backend.get("receive_to_result_backlog_growth_ms")
            if need(camera, "receive-to-result backlog growth", backlog) and backlog > g["backlog_growth_ms_max"]:
                failures.append(f"{camera}: receive-to-result grew {backlog} ms over the window")
        else:
            cadence = producer.get("cadence_fps")
            if need(camera, "producer cadence", cadence) and cadence < g["cadence_fps_min_per_feed"]:
                failures.append(f"{camera}: producer cadence {cadence} fps < {g['cadence_fps_min_per_feed']}")
            p95 = (producer.get("schedule_lag_ms") or {}).get("p95")
            if need(camera, "p95 producer schedule lag", p95) and p95 > g["p95_producer_schedule_lag_ms_max_stages_a_c"]:
                failures.append(f"{camera}: p95 producer schedule lag {p95} ms > {g['p95_producer_schedule_lag_ms_max_stages_a_c']}")
            backlog = producer.get("schedule_backlog_growth_ms")
            if need(camera, "schedule backlog growth", backlog) and backlog > g["backlog_growth_ms_max"]:
                failures.append(f"{camera}: producer schedule lag grew {backlog} ms over the window")
    failures += [f"crash: {c}" for c in crashed]
    if resources is None:
        failures.append("resource measurements unavailable")
    else:
        temp = (resources.get("gpu_temp_c") or {}).get("max")
        if not isinstance(temp, (int, float)):
            failures.append("required measurement unavailable: GPU temperature")
        elif temp > g["gpu_temp_max_c"]:
            failures.append(f"GPU temperature reached {temp} C > {g['gpu_temp_max_c']} C policy")
        backend_growth = resources.get("backend_rss_growth_mib")
        if stage == "D":
            if not isinstance(backend_growth, (int, float)):
                failures.append("required measurement unavailable: backend RSS growth")
            elif backend_growth > g["backend_rss_growth_mib_max"]:
                failures.append(f"backend RSS grew {backend_growth} MiB > {g['backend_rss_growth_mib_max']}")
        for name, value in (resources.get("container_rss_growth_mib") or {}).items():
            if isinstance(value, (int, float)) and value > g["container_rss_growth_mib_max"]:
                failures.append(f"container {name} RSS grew {value} MiB > {g['container_rss_growth_mib_max']}")
    return {"pass": not failures, "failures": failures}


# ---------------------------------------------------------------------------------------------
# Host commands, clocks, identities
# ---------------------------------------------------------------------------------------------

def docker_host(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    """Run a command on the docker host: through wsl.exe from Windows, directly on Linux."""
    try:
        return subprocess.run([*runtime_prefix(DISTRO), *args], capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(exc.cmd, 124, "", f"timeout after {timeout} s")


def clock_offset_ns() -> dict[str, Any]:
    """Container/WSL wall clock minus host wall clock (0 by construction on native Linux)."""
    if not runtime_prefix(DISTRO):
        return {"wsl_minus_host_ns": 0, "uncertainty_ns": 0, "samples": 0, "basis": "same kernel clock (native Linux)"}
    samples = []
    for _ in range(9):
        before = time.time_ns()
        out = docker_host("date", "+%s%N", timeout=30).stdout.strip()
        after = time.time_ns()
        if out.isdigit():
            samples.append((after - before, int(out) - (before + after) // 2))
    if not samples:
        return {"wsl_minus_host_ns": None, "uncertainty_ns": None, "samples": 0, "error": "no clock sample"}
    rtt, offset = min(samples)
    return {"wsl_minus_host_ns": offset, "uncertainty_ns": rtt // 2, "best_round_trip_ns": rtt, "samples": len(samples)}


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_sha256(directory: Path, pattern: str) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in directory.rglob(pattern) if "__pycache__" not in p.parts):
        digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + (sha256(path) or "").encode() + b"\n")
    return digest.hexdigest()


def _display(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def frozen_identities(config, clip: Path) -> dict[str, Any]:
    """Every input whose change would invalidate comparability; recomputed around every condition."""
    pipeline = config.models.pipeline
    return {
        "artifacts": {c.name: {"recorded": c.expected_sha256, "actual": c.current_sha256, "status": c.status}
                      for c in runtime_artifact_checks(config, HashMemo())},
        "clip": {"path": clip.relative_to(ROOT).as_posix(), "sha256": sha256(clip)},
        "wrapper_actual": sha256(ROOT / "scripts" / "deepstream.sh"),
        "extractor_actual": sha256(config.models.pipeline.extractor_path),
        "extractor_schema": pipeline.extractor_schema_version, "pacing": pipeline.extractor_pacing,
        "pace_lead_ms": pipeline.pace_lead_ms,
        "config_dir": _display(CONFIG_DIR),
        "configs_tree": tree_sha256(CONFIG_DIR, "*.yaml"),
        "backend_code_tree": tree_sha256(ROOT / "backend" / "app", "*.py"),
        "benchmark_script": sha256(Path(__file__)),
    }


# ---------------------------------------------------------------------------------------------
# Resource sampling and the guard
# ---------------------------------------------------------------------------------------------

class _MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def host_memory() -> dict[str, float | None]:
    memory = psutil.virtual_memory()
    result: dict[str, float | None] = {"host_available_mib": round(memory.available / 1048576, 1),
                                       "host_used_mib": round((memory.total - memory.available) / 1048576, 1),
                                       "host_commit_available_mib": None}
    if os.name == "nt":
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            result["host_commit_available_mib"] = round(status.ullAvailPageFile / 1048576, 1)
    return result


def wsl_guest_memory() -> dict[str, float | None]:
    if not runtime_prefix(DISTRO):
        return {}
    out = docker_host("cat", "/proc/meminfo", timeout=20).stdout
    values = {}
    for line in out.splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable", "Cached", "SwapTotal", "SwapFree"):
            try:
                values[key] = int(rest.split()[0]) / 1024
            except (IndexError, ValueError):
                pass
    if "MemAvailable" not in values:
        return {"wsl_error": "meminfo unavailable"}
    return {"wsl_available_mib": round(values["MemAvailable"], 1), "wsl_cached_mib": round(values.get("Cached", 0), 1),
            "wsl_swap_used_mib": round(values.get("SwapTotal", 0) - values.get("SwapFree", 0), 1)}


def _mib(text: str) -> float | None:
    text = text.strip()
    for unit, scale in (("GiB", 1024), ("MiB", 1), ("KiB", 1 / 1024), ("GB", 1000 ** 3 / 1048576), ("MB", 1000 ** 2 / 1048576),
                        ("kB", 1000 / 1048576), ("B", 1 / 1048576)):
        if text.endswith(unit):
            try:
                return round(float(text[: -len(unit)]) * scale, 1)
            except ValueError:
                return None
    return None


def container_memory(names: list[str]) -> dict[str, float | None]:
    if not names:
        return {}
    out = docker_host("docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}", *names, timeout=40).stdout
    result = {}
    for line in out.splitlines():
        name, _, usage = line.partition("\t")
        if name in names:
            result[name] = _mib(usage.split("/")[0])
    return result


class Sampler(threading.Thread):
    """1 Hz GPU + host samples, 10 s WSL guest + container samples; streamed to disk, bounded in memory.

    It is also the resource guard: a breach of PROTOCOL['resource_policy'] sets ``tripped`` with a reason.
    """

    GPU_QUERY = "utilization.gpu,utilization.decoder,memory.used,memory.total,temperature.gpu,power.draw,clocks.sm,pstate"
    GPU_KEYS = ["gpu_util", "nvdec_util", "vram_used_mib", "vram_total_mib", "temp_c", "power_w", "sm_clock_mhz", "pstate"]

    def __init__(self, raw_path: Path, backend_pid: Callable[[], int | None] = lambda: None,
                 containers: Callable[[], list[str]] = list, maxlen: int = 8000) -> None:
        super().__init__(daemon=True)
        self.raw = raw_path.open("a", encoding="utf-8")
        self.samples: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self.backend_pid, self.containers = backend_pid, containers
        self.stop_event = threading.Event()
        self.tripped: str | None = None
        self.errors: deque[str] = deque(maxlen=50)
        self._low_since: float | None = None
        self._hot_since: float | None = None

    def run(self) -> None:
        psutil.cpu_percent(None)
        tick = 0
        while not self.stop_event.is_set():
            started = time.monotonic()
            sample: dict[str, Any] = {"t": round(started, 3), "cpu_percent": psutil.cpu_percent(None), **host_memory()}
            try:
                out = subprocess.run(["nvidia-smi", f"--query-gpu={self.GPU_QUERY}", "--format=csv,noheader,nounits"],
                                     capture_output=True, text=True, timeout=10, check=False).stdout.strip()
                for key, value in zip(self.GPU_KEYS, (p.strip() for p in out.splitlines()[0].split(","))):
                    try:
                        sample[key] = float(value)
                    except ValueError:
                        sample[key] = value
            except (subprocess.SubprocessError, IndexError, OSError) as exc:
                self.errors.append(f"nvidia-smi: {exc}")
            pid = self.backend_pid()
            if pid:
                try:
                    process = psutil.Process(pid)
                    sample["backend_rss_mib"] = round(sum(p.memory_info().rss for p in [process, *process.children(recursive=True)])
                                                      / 1048576, 1)
                except psutil.Error as exc:
                    self.errors.append(f"backend rss: {exc}")
            if tick % 10 == 0:
                sample.update(wsl_guest_memory())
                sample["containers_mib"] = container_memory(self.containers())
            tick += 1
            self.samples.append(sample)
            self.raw.write(json.dumps(sample) + "\n")
            self.raw.flush()
            self._check(sample)
            self.stop_event.wait(max(0.0, 1.0 - (time.monotonic() - started)))
        self.raw.close()

    def _check(self, s: dict[str, Any]) -> None:
        if self.tripped:
            return
        p = PROTOCOL["resource_policy"]
        now = s["t"]
        available = s.get("host_available_mib")
        if isinstance(available, float):
            if available < p["host_available_hard_stop_mib"]:
                self.tripped = f"host available memory {available} MiB < hard stop {p['host_available_hard_stop_mib']} MiB"
            elif available < p["host_available_sustained_stop_mib"]:
                self._low_since = self._low_since or now
                if now - self._low_since >= p["sustained_s"]:
                    self.tripped = (f"host available memory below {p['host_available_sustained_stop_mib']} MiB for "
                                    f"{p['sustained_s']} s (now {available} MiB)")
            else:
                self._low_since = None
        commit = s.get("host_commit_available_mib")
        if isinstance(commit, float) and commit < p["host_commit_available_stop_mib"]:
            self.tripped = f"host commit available {commit} MiB < {p['host_commit_available_stop_mib']} MiB"
        guest = s.get("wsl_available_mib")
        if isinstance(guest, float) and guest < p["wsl_guest_available_stop_mib"]:
            self.tripped = f"WSL guest MemAvailable {guest} MiB < {p['wsl_guest_available_stop_mib']} MiB"
        temp = s.get("temp_c")
        if isinstance(temp, float) and temp >= p["gpu_temp_stop_c"]:
            self._hot_since = self._hot_since or now
            if now - self._hot_since >= p["gpu_temp_stop_sustained_s"]:
                self.tripped = f"GPU temperature >= {p['gpu_temp_stop_c']} C for {p['gpu_temp_stop_sustained_s']} s"
        else:
            self._hot_since = None

    def finish(self) -> None:
        self.stop_event.set()
        self.join(15)


def resources(samples: list[dict[str, Any]], window: tuple[float, float], errors: list[str]) -> dict[str, Any]:
    start, end = window
    rows = [s for s in samples if start <= s["t"] < end]

    def stat(key: str):
        values = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        return {"p50": pct(values, 0.5), "p95": pct(values, 0.95), "min": min(values), "max": max(values)} if values else None

    containers: dict[str, list[dict[str, Any]]] = {}
    for r in samples:
        for name, value in (r.get("containers_mib") or {}).items():
            containers.setdefault(name, []).append({"t": r["t"], "rss": value})
    return {
        "window_samples": len(rows), "gpu_util_percent": stat("gpu_util"), "nvdec_util_percent": stat("nvdec_util"),
        "vram_used_mib": stat("vram_used_mib"), "gpu_temp_c": stat("temp_c"), "gpu_power_w": stat("power_w"),
        "sm_clock_mhz": stat("sm_clock_mhz"), "pstates": sorted({str(r.get("pstate")) for r in rows if r.get("pstate")}),
        "host_cpu_percent": stat("cpu_percent"), "host_available_mib": stat("host_available_mib"),
        "host_used_mib": stat("host_used_mib"), "host_commit_available_mib": stat("host_commit_available_mib"),
        "wsl_available_mib": stat("wsl_available_mib"), "wsl_cached_mib": stat("wsl_cached_mib"),
        "backend_rss_mib": stat("backend_rss_mib"),
        "host_used_growth_mib": growth(rows, "host_used_mib", window),
        "wsl_cached_growth_mib": growth(rows, "wsl_cached_mib", window),
        "backend_rss_growth_mib": growth(rows, "backend_rss_mib", window),
        "container_rss_mib": {n: {"min": min(v["rss"] for v in vs if v["rss"] is not None),
                                  "max": max(v["rss"] for v in vs if v["rss"] is not None)}
                              for n, vs in containers.items() if any(v["rss"] is not None for v in vs)},
        "container_rss_growth_mib": {n: growth([{"t": v["t"], "rss": v["rss"]} for v in vs], "rss", window)
                                     for n, vs in containers.items()},
        "collection_errors": list(errors),
    }


def release_guest_cache() -> dict[str, Any]:
    """Between steps only (WSL route): drop the guest's clean page cache so the VM returns memory to the host.

    WSL keeps cached pages (image libraries, engines) after containers exit, and the host sees them as used.
    Measured 2026-10-28: +715 MiB host available within 20 s. Not a configuration change; never during a window.
    """
    if not runtime_prefix(DISTRO):
        return {"performed": False, "reason": "native Linux: host and container share the kernel page cache"}
    before = host_memory()["host_available_mib"]
    try:
        result = subprocess.run(["wsl.exe", "-d", DISTRO, "-u", "root", "-e", "sh", "-c", "sync; echo 1 > /proc/sys/vm/drop_caches"],
                                capture_output=True, text=True, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        return {"performed": False, "reason": "timeout"}
    time.sleep(20)
    return {"performed": result.returncode == 0, "host_available_before_mib": before,
            "host_available_after_20s_mib": host_memory()["host_available_mib"], "stderr": result.stderr.strip()[:200]}


def preflight(feeds: int, stage: str) -> dict[str, Any]:
    """Wait (bounded) for enough host memory headroom before launching a step; never launch into a predicted trip."""
    p = PROTOCOL["resource_policy"]
    need = feeds * p["preflight_mib_per_feed"] + (p["preflight_backend_mib"] if stage == "D" else 0) + p["preflight_floor_mib"]
    deadline = time.monotonic() + p["preflight_wait_s"]
    while True:
        available = host_memory()["host_available_mib"] or 0.0
        if available >= need:
            return {"ok": True, "needed_mib": need, "available_mib": available}
        if time.monotonic() >= deadline:
            return {"ok": False, "needed_mib": need, "available_mib": available,
                    "reason": f"insufficient host memory headroom: {available} MiB available, {need} MiB needed "
                              f"({feeds} feeds x {p['preflight_mib_per_feed']} + floor {p['preflight_floor_mib']}"
                              + (f" + backend {p['preflight_backend_mib']}" if stage == "D" else "") + ")"}
        time.sleep(5)


# ---------------------------------------------------------------------------------------------
# Stages A-C: containers only
# ---------------------------------------------------------------------------------------------

def container_command(config, clip: Path, camera_id: str, epoch: str, name: str, stage: str, out: Path) -> list[str]:
    root = config.project_root
    profile = config.models.selected.runtime_profile
    pipeline, tracker = config.models.pipeline, config.models.tracker
    return [*runtime_prefix(DISTRO), "env", f"DEEPSTREAM_CONTAINER_NAME={name}", "bash", wsl_path(root / "scripts" / "deepstream.sh"),
            "python3", workspace_path(pipeline.extractor_path, root), "--source", workspace_path(clip, root),
            "--infer-config", workspace_path(profile.infer_config_path, root), "--tracker-config", workspace_path(tracker.config_path, root),
            "--tracker-lib", tracker.library, "--width", str(profile.pipeline_width), "--height", str(profile.pipeline_height),
            "--tracker-width", str(profile.tracker_width), "--tracker-height", str(profile.tracker_height),
            "--output", workspace_path(out / f"{camera_id}.jsonl", root), "--run-summary", workspace_path(out / f"{camera_id}.summary.json", root),
            "--camera-id", camera_id, "--camera-epoch", epoch, "--pace", "source", "--pace-lead-ms", f"{pipeline.pace_lead_ms:g}",
            "--stage", STAGE_ARG[stage]]


def container_absent(name: str) -> bool | None:
    result = docker_host("docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}", timeout=30)
    if result.returncode != 0:
        return None
    return name not in result.stdout.split()


def remove_owned(names: list[str]) -> dict[str, Any]:
    """Stop exactly the named owned containers; return per-name absence afterwards."""
    for name in names:
        docker_host("docker", "stop", "--time", "5", name, timeout=60)
    absent = {name: container_absent(name) for name in names}
    for name, gone in absent.items():
        if gone is False:  # --rm normally removes it; force-remove only this exact owned name
            docker_host("docker", "rm", "-f", name, timeout=60)
            absent[name] = container_absent(name)
    return absent


def wait_covered(paths: list[Path], seconds: float, deadline: float, alive: Callable[[], bool], guard: Sampler) -> str:
    while time.monotonic() < deadline:
        if guard.tripped:
            return "resource_stop"
        covered = 0
        for path in paths:
            a, b = first_record(path), last_record(path)
            if a and b and (b.get("emitted_unix_ns", 0) - a.get("emitted_unix_ns", 0)) / 1e9 >= seconds:
                covered += 1
        if covered == len(paths):
            return "covered"
        if not alive():
            return "process_exit"
        time.sleep(5)
    return "deadline"


def run_containers(config, clip: Path, stage: str, feeds: int, out: Path, warmup: float, measure: float,
                   stop_flag: threading.Event) -> dict[str, Any]:
    token = uuid.uuid4().hex[:8]
    names = {f"bench-{i:02d}": f"aegis-bench-{stage.lower()}-{i:02d}-{token}" for i in range(1, feeds + 1)}
    guard = Sampler(out / "resources.samples.jsonl", containers=lambda: list(names.values()))
    processes: dict[str, tuple[subprocess.Popen, Any]] = {}
    ended = "not_started"
    absent: dict[str, Any] = {}
    try:
        guard.start()
        for camera_id, name in names.items():
            log = (out / f"{camera_id}.container.log").open("w", encoding="utf-8")
            processes[camera_id] = (subprocess.Popen(container_command(config, clip, camera_id, str(uuid.uuid4()), name, stage, out),
                                                     stdout=subprocess.DEVNULL, stderr=log), log)
        paths = [out / f"{c}.jsonl" for c in names]
        ended = wait_covered(paths, warmup + measure + 2, time.monotonic() + 120 + warmup + measure + 30,
                             lambda: all(p.poll() is None for p, _ in processes.values()) and not stop_flag.is_set(), guard)
    finally:
        absent = remove_owned(list(names.values()))
        for process, log in processes.values():
            try:
                process.wait(30)
            except subprocess.TimeoutExpired:
                process.kill()
            log.close()
        guard.finish()
    exits = {c: p.returncode for c, (p, _) in processes.items()}
    feeds_metrics = {}
    crashed = []
    for camera_id in names:
        producer = producer_metrics(out / f"{camera_id}.jsonl", warmup, measure)
        producer.pop("_window_frames", None)
        feeds_metrics[camera_id] = {"producer": producer}
        if (producer.get("covered_window_s") or 0) < measure - 5 and exits.get(camera_id) not in (None, 0, 137, 143):
            crashed.append(f"{camera_id}: container exited {exits[camera_id]} before covering the window")
    starts = [r["emitted_unix_ns"] for r in (first_record(out / f"{c}.jsonl") for c in names) if r]
    window = _host_window(min(starts), warmup, measure) if starts else (0.0, 0.0)
    return {"expected": list(names), "feeds": feeds_metrics, "crashed": crashed, "ended": ended,
            "resource_stop": guard.tripped, "containers": list(names.values()), "containers_absent_after": absent,
            "exit_codes": exits, "resources": resources(list(guard.samples), window, list(guard.errors))}


def _host_window(first_emit_unix_ns: int, warmup: float, measure: float, offset_ns: int = 0) -> tuple[float, float]:
    """Host monotonic interval for a window anchored at a wall-clock first frame."""
    anchor = time.monotonic() - (time.time_ns() - (first_emit_unix_ns - offset_ns)) / 1e9
    return anchor + warmup, anchor + warmup + measure


# ---------------------------------------------------------------------------------------------
# Stage D: the real backend
# ---------------------------------------------------------------------------------------------

def http(path: str, method: str = "GET", timeout: float = 180, headers: dict[str, str] | None = None,
         body: bytes | None = None) -> tuple[int, Any]:
    request = urllib.request.Request(BASE + path, method=method, headers=headers or {},
                                     data=body if body is not None else (b"" if method == "POST" else None))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
            kind = response.headers.get("content-type", "")
            return response.status, json.loads(data or b"null") if "json" in kind else {"bytes": len(data)}
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"null")
        except ValueError:
            return exc.code, None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


class Backend:
    def __init__(self, log: Path, env: dict[str, str]) -> None:
        self.log = log.open("w", encoding="utf-8")
        if os.name == "nt":
            command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "run-backend.ps1"),
                       "-Port", str(PORT)]
        else:
            command = ["bash", str(ROOT / "scripts" / "run-backend.sh")]
            env = dict(env, BACKEND_PORT=str(PORT))
        self.process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"backend exited {self.process.returncode} before becoming live")
            if http("/health/live", timeout=3)[0] == 200:
                return
            time.sleep(0.5)
        raise RuntimeError("backend did not become live within 120 s")

    def python_pid(self) -> int | None:
        try:
            process = psutil.Process(self.process.pid)
            for candidate in [process, *process.children(recursive=True)]:
                if "python" in candidate.name().lower():
                    return candidate.pid
        except psutil.Error:
            return None
        return None

    def kill(self) -> None:
        if self.process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True, check=False)
            else:
                self.process.terminate()
            try:
                self.process.wait(30)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.log.close()


def write_backend_config(directory: Path, feeds: int, clip: Path) -> list[str]:
    shutil.copytree(CONFIG_DIR, directory, ignore=shutil.ignore_patterns("a6000"))
    runtime = directory / "runtime.yaml"
    document = yaml.safe_load(runtime.read_text(encoding="utf-8"))
    # Raw frame logs are the measurement: never rotate them away mid-step (run_backend refuses a rotated log).
    document.setdefault("replay", {})["frame_log_max_mib"] = 0
    runtime.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    path = directory / "cameras.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    template = document["cameras"][0]
    ids = [f"cam-{i:02d}" for i in range(1, feeds + 1)]
    document["cameras"] = [dict(template, id=camera_id, enabled=True, source=str(clip), overlap_group=None, loop=False,
                                name=f"DUPLICATE INPUT {i} of {clip.stem} (load test only)", floor_id="floor-1",
                                position=[90 + 40 * i, 105]) for i, camera_id in enumerate(ids, 1)]
    document["load_test_note"] = "DUPLICATE INPUT of labelled repeated footage; load measurement only"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    zones = directory / "zones.yaml"
    document = yaml.safe_load(zones.read_text(encoding="utf-8"))
    for zone in document["zones"]:  # replicas must not feed facility/identity claims
        zone["authoritative_camera_ids"] = []
    zones.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    portals = directory / "portals.yaml"
    document = yaml.safe_load(portals.read_text(encoding="utf-8"))
    document["portals"] = []
    portals.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return ids


class SoakProbes:
    """Dashboard-facing API exercise during the soak: history, preview, metrics every 60 s; evidence every 10 min."""

    def __init__(self, path: Path, cameras: list[str]) -> None:
        self.out = path.open("a", encoding="utf-8")
        self.cameras = cameras
        self.counts: dict[str, list[int]] = {}
        self.next_light = self.next_evidence = 0.0
        self.evidence_ids: list[str] = []

    def _record(self, kind: str, ok: bool, detail: Any) -> None:
        self.counts.setdefault(kind, [0, 0])[0 if ok else 1] += 1
        self.out.write(json.dumps({"t": round(time.monotonic(), 2), "kind": kind, "ok": ok, "detail": detail}, default=str) + "\n")
        self.out.flush()

    def tick(self, elapsed: float) -> None:
        if elapsed >= self.next_light:
            self.next_light = elapsed + 60
            for camera in self.cameras:
                status, body = http(f"/api/v1/cameras/{camera}/count-history?max_points=500", timeout=30)
                self._record("history", status == 200 and bool((body or {}).get("total_samples")),
                             {"camera": camera, "status": status, "total": (body or {}).get("total_samples")})
                status, body = http(f"/api/v1/cameras/{camera}/preview", timeout=30, headers={"Range": "bytes=0-65535"})
                self._record("preview", status in (200, 206), {"camera": camera, "status": status})
            status, body = http("/api/v1/system/metrics", timeout=30)
            self._record("metrics", status == 200, {"status": status})
            status, body = http("/api/v1/facility/snapshot", timeout=30)
            self._record("dashboard_snapshot", status == 200, {"status": status})
        if elapsed >= self.next_evidence and elapsed > 150:
            self.next_evidence = elapsed + 600
            camera = self.cameras[0]
            status, body = http(f"/api/v1/cameras/{camera}/evidence-markers", "POST", timeout=180,
                                headers={"Content-Type": "application/json"}, body=b'{"note": "soak probe"}')
            ok = status == 201
            detail: dict[str, Any] = {"camera": camera, "status": status}
            if ok:
                ids = [body["snapshot"]["id"], body["clip"]["id"]]
                self.evidence_ids += ids
                for evidence_id in ids:
                    s_meta, meta = http(f"/api/v1/evidence/{evidence_id}/meta", timeout=30)
                    s_file, _ = http(f"/api/v1/evidence/{evidence_id}", timeout=60, headers={"Range": "bytes=0-65535"})
                    ok = ok and s_meta == 200 and s_file in (200, 206)
                    detail[evidence_id] = {"meta": s_meta, "file": s_file, "state": (meta or {}).get("state")}
            else:
                detail["error"] = body
            self._record("evidence", ok, detail)

    def summary(self) -> dict[str, Any]:
        self.out.close()
        return {"probes": {k: {"ok": v[0], "failed": v[1]} for k, v in self.counts.items()}, "evidence_ids": self.evidence_ids}


def run_backend(stage_dir: Path, clip: Path, feeds: int, warmup: float, measure: float, offset_ns: int | None, cache: Path,
                stop_flag: threading.Event, soak: bool) -> dict[str, Any]:
    expected = write_backend_config(stage_dir / "configs", feeds, clip)
    env = dict(os.environ, CCTV_CONFIG_DIR=str(stage_dir / "configs"), CCTV_DATABASE_PATH=str(stage_dir / "run.db"),
               CCTV_EVIDENCE_ROOT=str(stage_dir / "evidence"), CCTV_CAPABILITY_CACHE=str(cache), CCTV_RELEASE_LOG="1")
    backend: Backend | None = None
    owned: list[tuple[str, str, str]] = []
    run_id = None
    status = stop_status = None
    stopped: dict[str, Any] = {}
    outcomes: dict[str, Any] = {}
    epochs: dict[str, str] = {}
    snapshot_log = (stage_dir / "snapshot-samples.jsonl").open("w", encoding="utf-8")
    probes = SoakProbes(stage_dir / "soak-probes.jsonl", expected) if soak else None
    ended = "not_started"
    names: list[str] = []
    guard = Sampler(stage_dir / "resources.samples.jsonl", backend_pid=lambda: backend.python_pid() if backend else None,
                    containers=lambda: names)
    absent: dict[str, Any] = {}
    errors: list[str] = []
    try:
        guard.start()
        backend = Backend(stage_dir / "backend.log", env)
        status, snapshot = http("/api/v1/demo/start?replay_mode=paced", "POST")
        run_id = (snapshot or {}).get("run_id")
        epochs = {c["id"]: c["epoch"] for c in (snapshot or {}).get("cameras", []) if c.get("epoch")}
        if status != 200:
            errors.append(f"demo start returned HTTP {status}: {snapshot}")
        with sqlite3.connect(f"file:{stage_dir / 'run.db'}?mode=ro", uri=True) as connection:
            names = [n for (n,) in connection.execute("SELECT resource_name FROM owned_resources WHERE run_id = ?", (run_id,))]
        began = time.monotonic()
        ended = "deadline"
        while time.monotonic() - began < 90 + warmup + measure:
            if guard.tripped:
                ended = "resource_stop"
                break
            if stop_flag.is_set():
                ended = "signal"
                break
            s, body = http("/api/v1/facility/snapshot", timeout=30)
            cameras = (body or {}).get("cameras", []) if s == 200 else []
            snapshot_log.write(json.dumps({"t": round(time.monotonic(), 2), "status": s, **{c["id"]: {k: c.get(k) for k in (
                "source_position_s", "analytics_fps", "backend_queue_ms", "stream_outcome", "health", "visible_count")}
                for c in cameras}}) + "\n")
            if probes:
                probes.tick(time.monotonic() - began)
            tracked = [c for c in cameras if c["id"] in epochs]
            if tracked and len(tracked) == len(epochs) and all(
                    (c.get("source_position_s") or 0) >= warmup + measure + 5 or c.get("stream_outcome") for c in tracked):
                ended = "covered"
                break
            time.sleep(2)
        s, body = http("/api/v1/facility/snapshot", timeout=30)
        outcomes = {c["id"]: c.get("stream_outcome") for c in (body or {}).get("cameras", [])}
        stop_status, stopped = http("/api/v1/demo/stop", "POST")
    except Exception as exc:  # noqa: BLE001 - recorded, then cleanup still runs
        errors.append(f"{type(exc).__name__}: {exc}")
        ended = "exception" if ended in ("not_started", "deadline") else ended
    finally:
        guard.finish()
        snapshot_log.close()
        soak_summary = probes.summary() if probes else None
        if backend is not None:
            backend.kill()
        if run_id:
            try:
                with sqlite3.connect(stage_dir / "run.db") as connection:
                    owned = connection.execute("SELECT camera_id, resource_name, state FROM owned_resources WHERE run_id = ?",
                                               (run_id,)).fetchall()
            except sqlite3.Error as exc:
                errors.append(f"ownership read: {exc}")
        names = sorted({n for _, n, _ in owned} | set(names))
        absent = remove_owned(names)
    persistence = _persistence(stage_dir / "run.db", run_id)
    directory = ROOT / "backend" / "var" / "perception"
    feeds_metrics: dict[str, Any] = {}
    first_emit = None
    for camera, epoch in epochs.items():
        source_dir = directory / camera
        for suffix in (".jsonl", ".jsonl.prev", ".release.jsonl", ".summary.json"):
            source = source_dir / f"{epoch}{suffix}"
            if source.is_file():  # move the run's own raw evidence into the report (no RAM copy, no duplicate on disk)
                shutil.move(str(source), str(stage_dir / f"{camera}{suffix}"))
        if (stage_dir / f"{camera}.jsonl.prev").is_file():
            # Metrics anchor on the first record; a rotated log has lost the start of the run.
            errors.append(f"{camera}: frame log rotated during the step; the measurement is incomplete")
        producer = producer_metrics(stage_dir / f"{camera}.jsonl", warmup, measure)
        produced = producer.pop("_window_frames", set())
        backend_metrics = release_metrics(stage_dir / f"{camera}.release.jsonl", warmup, measure, produced, offset_ns)
        feeds_metrics[camera] = {"producer": producer, "backend": backend_metrics}
        record = first_record(stage_dir / f"{camera}.jsonl")
        if record:
            first_emit = record["emitted_unix_ns"] if first_emit is None else min(first_emit, record["emitted_unix_ns"])
    window = _host_window(first_emit, warmup, measure, offset_ns or 0) if first_emit else (0.0, 0.0)
    crashed = [f"{c}: stream outcome {o}" for c, o in outcomes.items() if c in epochs and o in ("ABNORMAL_TERMINATION", "CONSUMER_FAILED")]
    extra = list(errors)
    if status != 200:
        extra.append(f"demo start HTTP {status}")
    if stop_status != 200 or (stopped or {}).get("run_status") not in ("STOPPED", "COMPLETED"):
        extra.append(f"demo stop HTTP {stop_status}, run_status {(stopped or {}).get('run_status')}")
    if soak_summary:
        failed = {k: v["failed"] for k, v in soak_summary["probes"].items() if v["failed"]}
        if failed:
            extra.append(f"soak probes failed: {failed}")
        if not soak_summary["probes"].get("evidence", {}).get("ok"):
            extra.append("soak: no evidence marker succeeded")
    return {"expected": expected, "run_id": run_id, "start_http_status": status,
            "stop": {"status": stop_status, "run_status": (stopped or {}).get("run_status")},
            "feeds": feeds_metrics, "crashed": crashed, "outcomes": outcomes, "ended": ended, "errors": errors,
            "extra_failures": extra, "resource_stop": guard.tripped,
            "ownership": [{"camera": c, "name": n, "state": s} for c, n, s in owned], "containers": names,
            "containers_absent_after": absent, "persistence": persistence, "soak": soak_summary,
            "resources": resources(list(guard.samples), window, list(guard.errors))}


def _persistence(database: Path, run_id: str | None) -> dict[str, Any]:
    if not run_id or not database.is_file():
        return {"available": False}
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            rows = connection.execute("SELECT camera_id, COUNT(*) FROM camera_count_samples WHERE run_id = ? GROUP BY camera_id",
                                      (run_id,)).fetchall()
            evidence = connection.execute("SELECT COUNT(*) FROM evidence WHERE run_id = ?", (run_id,)).fetchone()
        return {"available": True, "count_samples_per_camera": dict(rows), "evidence_records": evidence[0] if evidence else 0}
    except sqlite3.Error as exc:
        return {"available": False, "error": str(exc)}


# ---------------------------------------------------------------------------------------------
# Summaries and the human-readable report
# ---------------------------------------------------------------------------------------------

def per_repeat_figures(item: dict[str, Any]) -> dict[str, Any]:
    """One figure per repeat (worst feed), so run-to-run variance is not hidden by pooling feeds."""
    feeds = item["result"].get("feeds", {})
    d = item["stage"] == "D"

    def values(fn):
        out = [fn(m) for m in feeds.values()]
        return [v for v in out if isinstance(v, (int, float))]
    cadence = values(lambda m: (m.get("backend") or {}).get("consumed_cadence_fps") if d else m["producer"].get("cadence_fps"))
    age = values(lambda m: ((m.get("backend") or {}).get("receive_to_result_ms") or {}).get("p95") if d
                 else (m["producer"].get("schedule_lag_ms") or {}).get("p95"))
    backlog = values(lambda m: (m.get("backend") or {}).get("receive_to_result_backlog_growth_ms") if d
                     else m["producer"].get("schedule_backlog_growth_ms"))
    return {"repeat": item["repeat"], "pass": item["gate"]["pass"], "min_feed_cadence_fps": min(cadence) if cadence else None,
            "max_feed_p95_ms": max(age) if age else None, "max_feed_backlog_growth_ms": max(backlog) if backlog else None,
            "gpu_util_p95": ((item["result"].get("resources") or {}).get("gpu_util_percent") or {}).get("p95")}


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        groups.setdefault(f"{item['stage']}-{item['feeds']}feeds", []).append(item)
    table = {}
    for key, items in groups.items():
        rows = [per_repeat_figures(item) for item in items]

        def spread(metric):
            vals = [r[metric] for r in rows if isinstance(r[metric], (int, float))]
            return ({"mean": round(statistics.fmean(vals), 3), "stdev": round(statistics.stdev(vals), 3) if len(vals) > 1 else None,
                     "min": min(vals), "max": max(vals), "n": len(vals)} if vals else None)
        table[key] = {"repeats": len(items), "passed": sum(1 for r in rows if r["pass"]), "per_repeat": rows,
                      "across_repeats": {m: spread(m) for m in ("min_feed_cadence_fps", "max_feed_p95_ms",
                                                               "max_feed_backlog_growth_ms", "gpu_util_p95")},
                      "metric_basis": "D: backend-consumed cadence / receive-to-result; A-C: producer cadence / schedule lag"}
    return table


def report_md(summary: dict[str, Any]) -> str:
    env = summary["environment"]
    lines = [f"# Capacity report {summary['run_id']}", "",
             f"**Result: {summary['result']}** - {summary['result_detail']}", "",
             "> LABELLED DUPLICATE-INPUT LOAD TEST. Every feed is an independent decoder instance of the same repeated "
             "demo clip. This measures load only; it does not establish physically distinct camera coverage, counting "
             "accuracy, or calibrated facility acceptance. The detector profile is provisional (not accuracy-accepted).", "",
             "| PRD 19.4 field | Value |", "| --- | --- |"]
    fields = [
        ("Run ID", summary["run_id"]), ("Timestamp", f"{summary['started_at_utc']} -> {summary.get('finished_at_utc')}"),
        ("GPU", env["gpu"]), ("GPU identity verified by", "nvidia-smi --query-gpu (host)"),
        ("Driver / CUDA / TensorRT / DeepStream", f"driver {env.get('driver')}; DeepStream 9.2 image (TensorRT/CUDA inside the pinned image)"),
        ("Host / WSL / container image digest", f"{env['host']}; route {env['host_route']}; {env['image']}"),
        ("Workload stage(s)", ", ".join(sorted({s['stage'] for s in summary['steps']})) or "none"),
        ("Number of feeds", ", ".join(str(n) for n in sorted({s['feeds'] for s in summary['steps']})) or "none"),
        ("Source file / checksum", f"{summary['footage']['clip']} sha256 {summary['identities_start']['clip']['sha256']}"),
        ("Source resolution / codec / FPS", summary["footage"].get("video", "see clip manifest")),
        ("Replay pacing mode", PROTOCOL["profile"]),
        ("Configured analytics FPS", "8 per camera (gate 7.2)"),
        ("Measured analytics FPS, per camera", "per step below (D: backend-consumed; A-C: producer)"),
        ("Detector / revision", f"{env['selected_model']} ({env['acceptance']})"),
        ("Configuration profile", summary["identities_start"].get("config_dir", "configs")),
        ("Detector precision / type policy", f"{env.get('precision')} TensorRT engine ({env.get('engine')})"),
        ("Tracker / revision", "NvDCF, models.yaml tracker identity of the profile (stages C, D)"),
        ("Calibration / config revisions", f"configs tree sha256 {summary['identities_start']['configs_tree']}; no calibration (replicas carry no facility claims)"),
        ("Re-ID enabled / update cadence", "disabled (stage F not run)"),
        ("Preview / recording settings", "no recording; soak probes exercise preview/history/evidence" if summary["plan"] == "soak" else "not exercised (stage G not run)"),
        ("Inference latency p50/p95", "unavailable: the pipeline exposes no per-frame inference timer; receive-to-result and schedule lag reported instead"),
        ("Processing/frame age basis", PROTOCOL["gates"]["receive_to_result_basis"] + " | " + PROTOCOL["gates"]["producer_schedule_lag_basis"]),
        ("Capture-to-result age", PROTOCOL["time_bases"]["capture_to_result"]),
        ("Intentional sampling", "none: every decoded frame is processed (29.97 fps source)"),
        ("Warm-up duration", f"{summary['protocol_used']['warmup_s']} s"), ("Test duration", f"{summary['protocol_used']['measure_s']} s per step"),
        ("Accuracy/reference result links", "none: performance-only load run (no labels used)"),
        ("Errors / missing counters", "; ".join(summary.get("missing_counters", [])) or "none recorded"),
        ("Acceptance result", summary["result"]), ("Bottleneck / next action", summary.get("bottleneck", "see steps")),
    ]
    lines += [f"| {k} | {str(v).replace('|', '/')} |" for k, v in fields]
    lines += ["", "## Steps", "", "| Step | Pass | Feeds: cadence fps (gated basis) | p95 ms (gated basis) | Backlog growth ms | "
              "Missing deliveries | GPU util p95 | VRAM max MiB | GPU temp max C | Host avail min MiB | Cleanup | Failures |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for step in summary["steps"]:
        feeds, res = step["feeds_metrics"], step["resources"] or {}
        d = step["stage"] == "D"
        cad = ", ".join(f"{c}: {(m.get('backend') or {}).get('consumed_cadence_fps') if d else m['producer'].get('cadence_fps')}" for c, m in feeds.items())
        age = ", ".join(str(((m.get("backend") or {}).get("receive_to_result_ms") or {}).get("p95") if d else (m["producer"].get("schedule_lag_ms") or {}).get("p95")) for m in feeds.values())
        back = ", ".join(str((m.get("backend") or {}).get("receive_to_result_backlog_growth_ms") if d else m["producer"].get("schedule_backlog_growth_ms")) for m in feeds.values())
        miss = ", ".join(str((m.get("backend") or {}).get("missing_deliveries")) for m in feeds.values()) if d else "n/a"

        def g(key, sub):
            return (res.get(key) or {}).get(sub)
        lines.append(f"| {step['name']} | {step['gate']['pass']} | {cad} | {age} | {back} | {miss} | {g('gpu_util_percent', 'p95')} | "
                     f"{g('vram_used_mib', 'max')} | {g('gpu_temp_c', 'max')} | {g('host_available_mib', 'min')} | "
                     f"{step['gate'].get('cleanup_confirmed')} | {'; '.join(step['gate']['failures'][:4]) or '-'} |")
    lines += ["", "## Across repeats (one worst-feed figure per repeat)", ""]
    for key, row in summary["conditions"].items():
        lines.append(f"- **{key}**: {row['passed']}/{row['repeats']} passed; " + "; ".join(
            f"{m} mean {v['mean']} sd {v['stdev']} [{v['min']}, {v['max']}]" for m, v in row["across_repeats"].items() if v))
    if summary.get("ladder_result"):
        lines += ["", "## Ladder", "", "```json", json.dumps(summary["ladder_result"], indent=1), "```"]
    lines += ["", "## Not run", ""] + [f"- Stage {k}: {v}" for k, v in summary["not_run"].items()]
    lines += ["", "## Disclosures", ""] + [f"- {d}" for d in summary.get("disclosures", [])]
    return "\n".join(lines) + "\n"


def environment(config, clip: Path) -> dict[str, Any]:
    smi = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,power.limit", "--format=csv,noheader"],
                         capture_output=True, text=True, check=False).stdout.strip()
    battery = psutil.sensors_battery()
    return {"gpu": smi, "driver": smi.split(",")[1].strip() if smi.count(",") >= 1 else None, "host": platform.platform(),
            "host_route": host_route(), "cpu_count": psutil.cpu_count(), "python": sys.version.split()[0],
            "host_ram_total_mib": round(psutil.virtual_memory().total / 1048576), "host_memory_at_start": host_memory(),
            "wsl_guest_memory_at_start": wsl_guest_memory(),
            "power": {"plugged_in": battery.power_plugged if battery else None, "battery_percent": battery.percent if battery else None},
            "wsl_distro": DISTRO if runtime_prefix(DISTRO) else None, "image": config.models.pipeline.image,
            "selected_model": config.models.selected_model, "acceptance": config.models.selected.acceptance,
            "config_dir": _display(CONFIG_DIR), "precision": config.models.selected.precision,
            "engine": _display(config.models.selected.engine_path) if config.models.selected.engine_path else None,
            "competing_gpu_processes": subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader"],
                                                      capture_output=True, text=True, check=False).stdout.strip() or "none reported (WDDM may hide them)",
            "competing_host_processes_top_rss": _top_processes()}


def _top_processes(n: int = 12) -> list[dict[str, Any]]:
    rows = []
    for process in psutil.process_iter(["pid", "name", "memory_info"]):
        info = process.info
        if info.get("memory_info"):
            rows.append({"pid": info["pid"], "name": info["name"], "rss_mib": round(info["memory_info"].rss / 1048576)})
    return sorted(rows, key=lambda r: -r["rss_mib"])[:n]


# ---------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------

def step_status(result: dict[str, Any], verdict: dict[str, Any]) -> str:
    """PASS / FAIL / INTERRUPTED; an interrupted step never passes, whatever its partial numbers show."""
    if result.get("resource_stop") or result.get("ended") in ("resource_stop", "signal", "exception"):
        verdict["pass"] = False
        verdict["failures"].insert(0, f"step interrupted: {result.get('resource_stop') or result.get('ended')}")
        return "INTERRUPTED"
    return "PASS" if verdict["pass"] else "FAIL"


def build_plan(args, ladder: list[int]) -> list[tuple[str, int, int]]:
    if args.plan == "ladder":
        return [("D", n, 1) for n in ladder]
    if args.plan == "soak":
        return [("D", args.feeds, 1)]
    return [(stage, args.feeds, r + 1) for stage in args.stages.split(",") for r in range(args.repeats)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", choices=("repeats", "ladder", "soak", "custom"), required=True)
    parser.add_argument("--stages", default="A,B,C,D")
    parser.add_argument("--feeds", type=int, default=PROTOCOL["feeds_for_stage_comparison"])
    parser.add_argument("--repeats", type=int, default=PROTOCOL["repeats"])
    parser.add_argument("--warmup", type=float, default=PROTOCOL["warmup_s"])
    parser.add_argument("--measure", type=float, default=None)
    parser.add_argument("--ladder", default=",".join(str(n) for n in PROTOCOL["ladder"]),
                        help="feed counts for --plan ladder (A6000: 4,8,12,16,20, recorded as a deviation)")
    parser.add_argument("--run-dir", default=None, help="fresh directory (must not exist)")
    parser.add_argument("--config-dir", default=None,
                        help="configuration profile (default: CCTV_CONFIG_DIR, else configs/; RTX A6000: configs/a6000)")
    args = parser.parse_args()
    global CONFIG_DIR
    CONFIG_DIR = Path(args.config_dir or os.environ.get("CCTV_CONFIG_DIR") or ROOT / "configs").resolve()
    if not (CONFIG_DIR / "models.yaml").is_file():
        print(json.dumps({"refused": f"no models.yaml in configuration profile {CONFIG_DIR}"}))
        return EXIT_REFUSED
    soak = args.plan == "soak"
    measure = args.measure if args.measure is not None else (PROTOCOL["soak_measure_s"] if soak else PROTOCOL["measure_s"])
    clip = CLIPS["soak" if soak else "capacity"]
    ladder = [int(n) for n in args.ladder.split(",")]
    deviations = {}
    if args.warmup != PROTOCOL["warmup_s"]:
        deviations["warmup_s"] = args.warmup
    if measure != (PROTOCOL["soak_measure_s"] if soak else PROTOCOL["measure_s"]):
        deviations["measure_s"] = measure
    if args.plan in ("repeats", "custom") and args.repeats != PROTOCOL["repeats"]:
        deviations["repeats"] = args.repeats
    if args.plan in ("repeats", "custom", "soak") and args.feeds != PROTOCOL["feeds_for_stage_comparison"]:
        deviations["feeds"] = args.feeds
    if args.plan == "ladder" and ladder != PROTOCOL["ladder"]:
        deviations["ladder"] = ladder
    if args.plan == "repeats" and args.stages != "A,B,C,D":
        deviations["stages"] = args.stages
    if args.plan == "custom":
        deviations["plan"] = "custom (diagnostic; never an acceptance result)"
    run_dir = Path(args.run_dir).resolve() if args.run_dir else ROOT / "reports" / "benchmarks" / f"capacity-{args.plan}-{utc_stamp()}"
    run_dir.mkdir(parents=True, exist_ok=False)
    stop_flag = threading.Event()

    def on_signal(signum, _frame):
        stop_flag.set()
    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)):
        if sig is not None:
            signal.signal(sig, on_signal)

    config = load_configuration(CONFIG_DIR, ROOT)
    if not clip.is_file():
        (run_dir / "REFUSED.json").write_text(json.dumps({"reason": f"clip missing: {clip}"}) + "\n", encoding="utf-8")
        print(json.dumps({"refused": f"clip missing: {clip}"}))
        return EXIT_REFUSED
    identities = frozen_identities(config, clip)
    mismatched = {n: a for n, a in identities["artifacts"].items() if a["status"] != "MATCH"}
    manifest_path = clip.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    if manifest.get("sha256") and manifest["sha256"] != identities["clip"]["sha256"]:
        mismatched["clip"] = {"manifest": manifest["sha256"], "actual": identities["clip"]["sha256"]}
    plan = build_plan(args, ladder)
    header = {"schema_version": 2, "run_id": run_dir.name, "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "command": " ".join(sys.argv), "plan": args.plan, "steps_planned": plan, "protocol": PROTOCOL,
              "protocol_used": {"warmup_s": args.warmup, "measure_s": measure}, "deviations_from_protocol": deviations,
              "acceptance_eligible": not deviations,
              "footage": {"clip": clip.relative_to(ROOT).as_posix(), "manifest": manifest,
                          "video": manifest.get("stream", "see manifest"),
                          "label": "LABELLED REPEATED/CONCATENATED DEMO FOOTAGE; duplicate decoder instances; load only"},
              "identities_start": identities, "clock_offset_start": clock_offset_ns(), "environment": environment(config, clip),
              "not_run": {"E": "calibrated overlap fusion needs calibrated overlapping footage",
                          "F": "selective Re-ID is not implemented/enabled",
                          "G": "needs the accepted core profile (detector accuracy unaccepted)" + ("" if soak else "; the soak plan exercises dashboard/preview/history/evidence under the provisional profile"),
                          "H": "optional advanced AI out of scope"},
              "disclosures": ["Competing non-project host workloads are recorded in environment.competing_host_processes_top_rss and were not stopped."]}
    (run_dir / "protocol.json").write_text(json.dumps(header, indent=2, default=str) + "\n", encoding="utf-8")
    if mismatched:
        (run_dir / "REFUSED.json").write_text(json.dumps({"reason": "artifact identity mismatch; never auto-blessed",
                                                          "mismatched": mismatched}, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"refused": "artifact identity mismatch", "mismatched": list(mismatched)}))
        return EXIT_REFUSED

    results: list[dict[str, Any]] = []
    interrupted: dict[str, Any] | None = None
    ladder_stop: dict[str, Any] | None = None
    cache = run_dir / "capability-cache.json"
    for stage, feeds, repeat in plan:
        name = f"stage{stage}-{feeds}feeds-r{repeat}"
        if stop_flag.is_set():
            interrupted = {"reason": "signal received", "before_step": name}
            break
        current = frozen_identities(config, clip)
        changed = {k: (identities[k], current[k]) for k in identities if identities[k] != current[k]}
        if changed:
            interrupted = {"reason": "frozen input changed during the run", "before_step": name, "changed": changed}
            break
        reclaim = release_guest_cache()
        check = preflight(feeds, stage)
        check["guest_cache_release"] = reclaim
        if not check["ok"]:
            if args.plan == "ladder":
                ladder_stop = {"feeds": feeds, "reason": check["reason"], "kind": "unattempted"}
            interrupted = {"reason": check["reason"], "before_step": name, "kind": "insufficient_headroom"}
            break
        step = run_dir / name
        step.mkdir()
        offset_before = clock_offset_ns()
        print(json.dumps({"start": name, "utc": datetime.now(timezone.utc).isoformat(), "preflight": check}), flush=True)
        try:
            result = (run_backend(step, clip, feeds, args.warmup, measure, offset_before.get("wsl_minus_host_ns"), cache, stop_flag, soak)
                      if stage == "D" else run_containers(config, clip, stage, feeds, step, args.warmup, measure, stop_flag))
        except Exception as exc:  # noqa: BLE001 - recorded as an interruption of this step
            result = {"expected": [], "feeds": {}, "crashed": [], "resources": None, "ended": "exception",
                      "errors": [f"{type(exc).__name__}: {exc}"], "containers_absent_after": {}}
        offset_after = clock_offset_ns()
        identities_after = frozen_identities(config, clip)
        verdict = gate(stage, result.get("expected", []), result.get("feeds", {}), measure, result.get("crashed", []),
                       result.get("resources"), result.get("extra_failures"))
        absent = result.get("containers_absent_after") or {}
        verdict["cleanup_confirmed"] = bool(absent) and all(v is True for v in absent.values())
        if not verdict["cleanup_confirmed"]:
            verdict["pass"] = False
            verdict["failures"].append(f"owned containers not all confirmed absent: {absent}")
        if identities_after != identities:
            verdict["pass"] = False
            verdict["failures"].append("frozen inputs changed during the step")
        item = {"name": name, "stage": stage, "feeds": feeds, "repeat": repeat, "preflight": check,
                "clock_offset": {"before": offset_before, "after": offset_after}, "identities_after": identities_after,
                "result": result, "gate": verdict}
        item["status"] = status_word = step_status(result, verdict)
        (step / "summary.json").write_text(json.dumps(item, indent=2, default=str) + "\n", encoding="utf-8")
        results.append(item)
        print(json.dumps({"finished": name, "status": status_word, "failures": verdict["failures"][:4]}), flush=True)
        if status_word == "INTERRUPTED":
            if args.plan == "ladder" and result.get("resource_stop"):
                ladder_stop = {"feeds": feeds, "reason": f"resource guard: {result['resource_stop']}", "kind": "first_unacceptable"}
            interrupted = {"reason": result.get("resource_stop") or result.get("ended"), "during_step": name,
                           "errors": result.get("errors")}
            break
        if args.plan == "ladder" and not verdict["pass"]:
            ladder_stop = {"feeds": feeds, "reason": verdict["failures"], "kind": "first_unacceptable"}
            break

    executed = len(results)
    all_pass = bool(results) and all(r["gate"]["pass"] for r in results)
    if args.plan == "ladder":
        # A ladder ends at its first unacceptable step by design; the result is the last acceptable count.
        complete = interrupted is None or (ladder_stop is not None)
        result_word = ("COMPLETE" if complete else "INTERRUPTED")
    elif interrupted:
        result_word = "INTERRUPTED"
    else:
        result_word = "PASS" if all_pass and executed == len(plan) else "FAIL"
    summary = {**header, "finished_at_utc": datetime.now(timezone.utc).isoformat(), "result": result_word,
               "result_detail": f"{sum(r['gate']['pass'] for r in results)}/{executed} executed steps passed, "
                                f"{len(plan) - executed} of {len(plan)} planned steps not executed"
                                + (" (deviations: not acceptance-eligible)" if deviations else ""),
               "interrupted": interrupted,
               "steps": [{"name": r["name"], "stage": r["stage"], "feeds": r["feeds"], "repeat": r["repeat"], "status": r["status"],
                          "gate": r["gate"], "feeds_metrics": r["result"].get("feeds", {}), "resources": r["result"].get("resources"),
                          "persistence": r["result"].get("persistence"), "soak": r["result"].get("soak"),
                          "clock_offset": r["clock_offset"]} for r in results],
               "conditions": summarize(results),
               "missing_counters": ["per-frame inference latency (no pipeline timer)", "capture-to-result age (no capture clock)"]}
    if args.plan == "ladder":
        passed = [r["feeds"] for r in results if r["gate"]["pass"]]
        summary["ladder_result"] = {"last_acceptable_feeds": max(passed) if passed else None, "stop": ladder_stop,
                                    "unattempted": [n for n in ladder if n not in [r["feeds"] for r in results]]}
        summary["bottleneck"] = (f"stopped at {ladder_stop['feeds']} feeds: {ladder_stop['reason']}" if ladder_stop
                                 else "all ladder steps passed")
    if interrupted:
        (run_dir / "INTERRUPTED.json").write_text(json.dumps({"status": "INTERRUPTED - incomplete, not an acceptance result",
                                                              **interrupted, "completed_steps": [r["name"] for r in results]},
                                                             indent=2, default=str) + "\n", encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    (run_dir / "report.md").write_text(report_md(summary), encoding="utf-8", newline="\n")
    print(json.dumps({"run_dir": run_dir.relative_to(ROOT).as_posix(), "result": result_word,
                      "detail": summary["result_detail"], "ladder": summary.get("ladder_result")}, default=str), flush=True)
    if result_word in ("PASS", "COMPLETE"):
        return EXIT_PASS
    return EXIT_INTERRUPTED if result_word == "INTERRUPTED" else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
