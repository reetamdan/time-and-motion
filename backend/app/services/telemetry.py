"""Measured psutil and nvidia-smi telemetry with local-then-WSL probing."""

from __future__ import annotations

import asyncio
import copy
import csv
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

import psutil

from ..configuration import TelemetrySettings
from ..timeutils import iso_now

GPU_QUERY = (
    "--query-gpu=name,driver_version,utilization.gpu,memory.used,memory.total,temperature.gpu,utilization.decoder",
    "--format=csv,noheader,nounits",
)


@dataclass(frozen=True, slots=True)
class GPUReading:
    scope: str
    name: str | None = None
    driver_version: str | None = None
    utilization: float | None = None
    memory_used: float | None = None
    memory_total: float | None = None
    temperature: float | None = None
    decoder_utilization: float | None = None
    unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        return self.name is not None


def _number(value: str | None) -> float | None:
    if value is None or value.strip().upper() in {"N/A", "NA", "UNKNOWN", "[NOT SUPPORTED]", ""}:
        return None
    try:
        return float(value.strip())
    except ValueError:
        return None


def local_scope() -> str:
    """Scope of a probe run directly by this process: Windows host, WSL 2 distro, or another Linux host."""
    if os.name == "nt":
        return "windows-host"
    try:
        if "microsoft" in open("/proc/version", encoding="utf-8").read().lower():
            return "wsl2"
    except OSError:
        pass
    return "linux-host"


def parse_gpu_csv(output: str, scope: str) -> GPUReading:
    """Parse one actual query row; unsupported counters stay null."""

    row = next(csv.reader([output.strip().splitlines()[0]]), [])
    if len(row) < 7 or not row[0].strip():
        raise ValueError("nvidia-smi returned no usable GPU row")
    return GPUReading(
        scope=scope,
        name=row[0].strip(),
        driver_version=row[1].strip() or None,
        utilization=_number(row[2]),
        memory_used=_number(row[3]),
        memory_total=_number(row[4]),
        temperature=_number(row[5]),
        decoder_utilization=_number(row[6]),
    )


class TelemetryCollector:
    def __init__(self, settings: TelemetrySettings) -> None:
        self.settings = settings
        self._process = psutil.Process(os.getpid())
        self._gpu_cache: GPUReading | None = None
        self._gpu_cached_at = 0.0
        self._lock = threading.Lock()

    def _run(self, command: list[str]) -> GPUReading:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.settings.command_timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "nvidia-smi failed").strip()
            raise RuntimeError(detail[:500])
        return parse_gpu_csv(completed.stdout, "wsl2" if command[0].lower().startswith("wsl") else (
            local_scope()
        ))

    def _probe_gpu(self) -> GPUReading:
        errors: list[str] = []
        try:
            return self._run(["nvidia-smi", *GPU_QUERY])
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
            errors.append(f"local: {exc}")
        if os.name == "nt":
            try:
                return self._run([
                    "wsl.exe", "-d", self.settings.wsl_distro, "-e", "nvidia-smi", *GPU_QUERY
                ])
            except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
                errors.append(f"WSL {self.settings.wsl_distro}: {exc}")
        return GPUReading(scope="unavailable", unavailable_reason="; ".join(errors)[:1000])

    def gpu(self, force: bool = False) -> GPUReading:
        with self._lock:
            now = time.monotonic()
            if self._gpu_cache is not None and not force:
                ttl = self.settings.success_cache_seconds if self._gpu_cache.available else self.settings.failure_cache_seconds
                if now - self._gpu_cached_at < ttl:
                    return self._gpu_cache
            reading = self._probe_gpu()
            self._gpu_cache = reading
            self._gpu_cached_at = now
            return reading

    def collect(self) -> dict[str, object]:
        gpu = self.gpu()
        memory = psutil.virtual_memory()
        # A short real sampling interval avoids psutil's meaningless first-call zero.
        cpu = psutil.cpu_percent(interval=0.1)
        return {
            "timestamp": iso_now(),
            "scope": gpu.scope,
            "gpu_name": gpu.name,
            "gpu_utilization": gpu.utilization,
            "vram_used_mib": gpu.memory_used,
            "vram_total_mib": gpu.memory_total,
            "gpu_temperature_c": gpu.temperature,
            "nvdec_utilization": gpu.decoder_utilization,
            "cpu_utilization": cpu,
            "ram_used_mb": memory.used / (1024 * 1024),
            "ram_total_mb": memory.total / (1024 * 1024),
            "process_rss_mb": self._process.memory_info().rss / (1024 * 1024),
            "unavailable_reason": gpu.unavailable_reason,
        }


class TelemetryService:
    """Owns the single periodic collector; WebSocket clients never fork competing nvidia-smi probes."""

    def __init__(self, collector: TelemetryCollector) -> None:
        self.collector = collector
        self._latest: dict[str, object] | None = None
        self._lock = threading.Lock()
        self._task: asyncio.Task[None] | None = None

    async def collect_now(self) -> dict[str, object]:
        metrics = await asyncio.to_thread(self.collector.collect)
        with self._lock:
            self._latest = metrics
        return copy.deepcopy(metrics)

    def current(self) -> dict[str, object]:
        with self._lock:
            if self._latest is not None:
                return copy.deepcopy(self._latest)
        return self.collector.collect()

    def start(self, interval_seconds: float, callback: Callable[[dict[str, object]], Awaitable[None]]) -> None:
        if self._task is not None:
            return

        async def run() -> None:
            while True:
                metrics = await self.collect_now()
                await callback(metrics)
                await asyncio.sleep(interval_seconds)

        self._task = asyncio.create_task(run(), name="cctv-system-telemetry")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
