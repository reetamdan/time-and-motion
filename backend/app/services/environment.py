"""Actual environment, artifact, and readiness probes."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ..configuration import AppConfig, ModelArtifact
from ..db.repository import Repository
from ..perception.base import PerceptionAdapter
from ..perception.service_maker import wsl_path
from .telemetry import local_scope


class EnvironmentService:
    def __init__(
        self, config: AppConfig, repository: Repository, adapter: PerceptionAdapter, telemetry: Any
    ) -> None:
        self.config = config
        self.repository = repository
        self.adapter = adapter
        self.telemetry = telemetry
        self._commands: dict[str, dict[str, Any]] = {}
        self._commands_at = 0.0

    def _command(self, name: str, arguments: list[str]) -> dict[str, Any]:
        errors: list[str] = []
        if shutil.which(name):
            try:
                result = subprocess.run(
                    [name, *arguments], capture_output=True, text=True,
                    timeout=self.config.runtime.telemetry.command_timeout_seconds, check=False,
                )
                detail = (result.stdout or result.stderr).strip()[:1000]
                if result.returncode == 0:
                    return {"available": True, "scope": local_scope(),
                            "detail": detail, "unavailable_reason": None}
                errors.append(detail or f"exit {result.returncode}")
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(str(exc))
        else:
            errors.append(f"{name} is not on the local PATH")
        if os.name == "nt" and shutil.which("wsl.exe"):
            try:
                result = subprocess.run(
                    ["wsl.exe", "-d", self.config.runtime.telemetry.wsl_distro, "-e", name, *arguments],
                    capture_output=True, text=True,
                    timeout=self.config.runtime.telemetry.command_timeout_seconds, check=False,
                )
                detail = (result.stdout or result.stderr).strip()[:1000]
                if result.returncode == 0:
                    return {"available": True, "scope": "wsl2", "detail": detail, "unavailable_reason": None}
                errors.append(f"WSL: {detail or f'exit {result.returncode}'}")
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(f"WSL: {exc}")
            wrapper = self.config.project_root / "scripts" / "deepstream.sh"
            reference = self.config.project_root / "reports" / "environment" / "deepstream92" / "image-reference.txt"
            if wrapper.is_file() and reference.is_file():
                try:
                    result = subprocess.run(
                        [
                            "wsl.exe", "-d", self.config.runtime.telemetry.wsl_distro, "-e",
                            "bash", wsl_path(wrapper), name, *arguments,
                        ],
                        capture_output=True, text=True,
                        timeout=self.config.runtime.telemetry.command_timeout_seconds, check=False,
                    )
                    detail = (result.stdout or result.stderr).strip()[:1000]
                    if result.returncode == 0:
                        return {
                            "available": True, "scope": "pinned-deepstream-container",
                            "detail": detail, "unavailable_reason": None,
                        }
                    errors.append(f"Pinned container: {detail or f'exit {result.returncode}'}")
                except (OSError, subprocess.SubprocessError) as exc:
                    errors.append(f"Pinned container: {exc}")
        return {"available": False, "scope": "unavailable", "detail": None,
                "unavailable_reason": "; ".join(errors)[:1000]}

    def runtime_capabilities(self) -> dict[str, dict[str, Any]]:
        if self._commands and time.monotonic() - self._commands_at < self.config.runtime.telemetry.failure_cache_seconds:
            return self._commands
        self._commands = {
            "deepstream": self._command("deepstream-app", ["--version"]),
            "gstreamer": self._command("gst-launch-1.0", ["--version"]),
            "nvstreammux": self._command("gst-inspect-1.0", ["nvstreammux"]),
            "nvinfer": self._command("gst-inspect-1.0", ["nvinfer"]),
            "nvv4l2decoder": self._command("gst-inspect-1.0", ["nvv4l2decoder"]),
        }
        self._commands_at = time.monotonic()
        return self._commands

    @staticmethod
    def _file_available(path: Path | None, expected_sha256: str | None = None) -> tuple[bool, str | None]:
        if path is None or not path.is_file():
            return False, "Local artifact is not installed"
        try:
            if path.stat().st_size == 0:
                return False, "Local artifact is empty"
            if expected_sha256:
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest().lower() != expected_sha256.lower():
                    return False, "Artifact checksum does not match the pinned SHA-256"
        except OSError as exc:
            return False, f"Artifact is unreadable: {exc}"
        return True, None

    def source_report(self) -> list[dict[str, Any]]:
        report: list[dict[str, Any]] = []
        for camera in self.config.cameras:
            if not camera.enabled:
                available, reason = False, "Camera is disabled"
            elif camera.source_type == "file":
                available, reason = self._file_available(camera.source_path)
            else:
                available, reason = False, "RTSP connectivity requires an available source/perception adapter"
            report.append({
                "id": camera.id,
                "source_type": camera.source_type,
                "source_label": camera.source_label,
                "available": available,
                "unavailable_reason": reason,
            })
        return report

    def model_report(self) -> dict[str, Any]:
        selected = next((model for model in self.config.models.models if model.id == self.config.models.selected_model), None)
        models: list[dict[str, Any]] = []
        for model in self.config.models.models:
            checkpoint, checkpoint_reason = self._file_available(model.checkpoint_path, model.sha256)
            engine, engine_reason = self._file_available(model.engine_path, model.engine_sha256)
            models.append({
                "id": model.id,
                "role": model.role,
                "enabled": model.enabled,
                "selected": model.id == self.config.models.selected_model,
                "revision": model.revision,
                "checkpoint_available": checkpoint,
                "engine_available": engine,
                "unavailable_reason": "; ".join(filter(None, (checkpoint_reason, engine_reason))) or None,
            })
        tracker_path = self.config.models.tracker.config_path
        tracker_available, tracker_reason = self._file_available(tracker_path)
        return {
            "selected_model": self.config.models.selected_model,
            "selection_mode": self.config.models.selection_mode,
            "evaluated_baseline_selected": self.config.models.selection_mode == "evaluated_baseline" and selected is not None,
            "selected_available": self._selected_available(selected),
            "models": models,
            "tracker": {
                "name": self.config.models.tracker.name,
                "enabled": self.config.models.tracker.enabled,
                "revision": self.config.models.tracker.revision,
                "available": self.config.models.tracker.enabled and tracker_available,
                "unavailable_reason": None if self.config.models.tracker.enabled and tracker_available else (
                    tracker_reason if self.config.models.tracker.enabled else "Tracker is not enabled/pinned"
                ),
            },
        }

    @staticmethod
    def _selected_available(model: ModelArtifact | None) -> bool:
        if model is None or not model.enabled:
            return False
        checkpoint, _ = EnvironmentService._file_available(model.checkpoint_path, model.sha256)
        engine, _ = EnvironmentService._file_available(model.engine_path, model.engine_sha256)
        return checkpoint and engine

    def environment(self) -> dict[str, Any]:
        gpu = self.telemetry.collector.gpu()
        adapter = self.adapter.availability()
        capabilities = self.runtime_capabilities()
        sources = self.source_report()
        models = self.model_report()
        blockers = [f"Source {source['id']}: {source['unavailable_reason']}" for source in sources if not source["available"]]
        blockers.extend(adapter.blockers)
        return {
            "host": {
                "hostname": platform.node(),
                "os": platform.system(),
                "os_release": platform.release(),
                "platform": platform.platform(),
                "architecture": platform.machine(),
                "python": platform.python_version(),
            },
            "gpu": {
                "name": gpu.name,
                "driver_version": gpu.driver_version,
                "vram_total_mib": gpu.memory_total,
                "scope": gpu.scope,
                "unavailable_reason": gpu.unavailable_reason,
            },
            "runtime": {
                "offline": self.config.runtime.offline,
                "adapter": adapter.name,
                "available": adapter.available,
                "runtime_detected": adapter.runtime_detected,
                "detail": adapter.detail,
                "capabilities": capabilities,
            },
            "sources": sources,
            "models": models,
            "configuration_revision": self.config.configuration_revision,
            "blockers": blockers,
        }

    def readiness(self) -> tuple[bool, list[dict[str, Any]]]:
        sources = self.source_report()
        enabled_ids = {camera.id for camera in self.config.cameras if camera.enabled}
        missing_sources = [source["id"] for source in sources if source["id"] in enabled_ids and not source["available"]]
        models = self.model_report()
        adapter = self.adapter.availability()
        database_ready = self.repository.health_check()
        checks = [
            {"name": "configuration", "ready": True, "detail": f"Validated revision {self.config.configuration_revision[:12]}"},
            {"name": "persistence", "ready": database_ready,
             "detail": "SQLite query succeeded with foreign keys and WAL migration" if database_ready else "SQLite query failed"},
            {"name": "sources", "ready": not missing_sources,
             "detail": "All enabled source files are present" if not missing_sources else "Unavailable sources: " + ", ".join(missing_sources)},
            {"name": "models", "ready": bool(models["selected_available"] and models["tracker"]["available"]),
             "detail": "Pinned detector/engine/tracker artifacts available" if models["selected_available"] and models["tracker"]["available"]
             else "Selected detector/engine/tracker artifacts or revisions are unavailable"},
            {"name": "perception", "ready": adapter.available,
             "detail": adapter.detail if adapter.available else "; ".join(adapter.blockers) or adapter.detail},
        ]
        return all(check["ready"] for check in checks), checks
