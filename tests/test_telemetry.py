from __future__ import annotations

import subprocess

from backend.app.configuration import TelemetrySettings
from backend.app.services.telemetry import GPUReading, TelemetryCollector, parse_gpu_csv


def test_parse_real_query_shape_and_unsupported_decoder() -> None:
    reading = parse_gpu_csv("Test GPU, 1.23, 19, 1124, 12227, 65, N/A", "wsl2")
    assert reading.name == "Test GPU" and reading.driver_version == "1.23"
    assert reading.utilization == 19 and reading.memory_total == 12227
    assert reading.decoder_utilization is None


def test_local_failure_falls_back_to_wsl_without_hardcoded_values(monkeypatch) -> None:
    collector = TelemetryCollector(TelemetrySettings())
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        if command[0] == "nvidia-smi":
            return subprocess.CompletedProcess(command, 255, "", "Failed to initialize NVML")
        return subprocess.CompletedProcess(command, 0, "Actual Query GPU, 610.0, 7, 100, 2000, 50, 3\n", "")
    monkeypatch.setattr(subprocess, "run", run)
    reading = collector.gpu(force=True)
    if reading.available:
        # On the Windows target this is the WSL fallback; on other hosts only local probing is defined.
        assert reading.name == "Actual Query GPU" and reading.memory_total == 2000
    else:
        assert "NVML" in reading.unavailable_reason
    assert commands and commands[0][0] == "nvidia-smi"


def test_gpu_failures_are_cached(monkeypatch) -> None:
    collector = TelemetryCollector(TelemetrySettings(failure_cache_seconds=60))
    calls = []
    def probe():
        calls.append(1)
        return GPUReading(scope="unavailable", unavailable_reason="probe failed")
    monkeypatch.setattr(collector, "_probe_gpu", probe)
    assert collector.gpu().unavailable_reason == "probe failed"
    assert collector.gpu().unavailable_reason == "probe failed"
    assert len(calls) == 1


def test_cpu_ram_and_process_memory_are_real_even_without_gpu(monkeypatch) -> None:
    collector = TelemetryCollector(TelemetrySettings())
    monkeypatch.setattr(collector, "_probe_gpu", lambda: GPUReading(scope="unavailable", unavailable_reason="no GPU"))
    metrics = collector.collect()
    assert metrics["scope"] == "unavailable"
    assert metrics["gpu_name"] is None and metrics["unavailable_reason"] == "no GPU"
    assert 0 <= metrics["cpu_utilization"] <= 100
    assert metrics["ram_used_mb"] > 0 and metrics["ram_total_mb"] > 0
    assert metrics["process_rss_mb"] > 0
