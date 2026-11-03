"""Cached capability probing for the pinned DeepStream container runtime.

Two checks with different costs and lifetimes:

* liveness (cheap, ~0.5 s): ``docker image inspect`` of the pinned image through WSL. Confirms WSL,
  the Docker daemon, and the exact image are present, and yields the local image ID. Run before
  every reuse of a cached capability (memoized for ``liveness_cache_seconds``).
* capability (expensive, one container, ~6 s): scripts/lib/capability-probe.sh inside the pinned
  image checks deepstream-app, the NVIDIA GStreamer elements, the extractor, and the GPU.

A capability result is reused across starts, concurrent readers, and backend restarts (persisted at
``cache_path``) only while its fingerprint matches - image ID + image reference + current hashes of
the wrapper, probe script, and extractor + WSL distro - it is younger than ``max_age_seconds``, and
liveness passes. A liveness failure, a fingerprint change, an explicit invalidation (e.g. an observed
container start failure), or expiry discards it. Failed probes are never persisted and are retried
after ``failure_retry_seconds``. One lock makes probing single-flight.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Callable

from ..configuration import CapabilitySettings, PipelineArtifacts
from .artifacts import HashMemo

LOGGER = logging.getLogger("cctv.perception.capability")
PROBE_VERSION = "1"
REQUIRED_OK = ("deepstream_app", "gst_nvstreammux", "gst_nvinfer", "gst_nvtracker", "gst_nvv4l2decoder", "extractor", "gpu")
LIVENESS_TIMEOUT_SECONDS = 15

Runner = Callable[[list[str], float], tuple[int | None, str, str]]


def _default_runner(command: list[str], timeout: float) -> tuple[int | None, str, str]:
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, "", f"timed out after {timeout:.0f} s"
    except OSError as exc:
        return None, "", f"{type(exc).__name__}: {exc}"
    return result.returncode, result.stdout.decode(errors="replace"), result.stderr.decode(errors="replace")


@dataclass(slots=True)
class Liveness:
    ok: bool
    image_id: str | None
    detail: str
    checked_at: float
    seconds: float


@dataclass(slots=True)
class CapabilityResult:
    available: bool
    fingerprint: str | None
    blockers: list[str]
    fields: dict[str, str] = field(default_factory=dict)
    validated_at_utc: str | None = None
    validated_at_epoch: float | None = None
    probe_seconds: float | None = None
    # probe | memory | disk | liveness_failed
    source: str = "probe"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


class CapabilityCache:
    def __init__(self, settings: CapabilitySettings, pipeline: PipelineArtifacts | None, distro: str,
                 wrapper_wsl_path: Callable[[], str], workspace_path: Callable[[Path], str], memo: HashMemo,
                 runner: Runner | None = None, wall_clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic, windows: bool | None = None) -> None:
        self.settings = settings
        self.pipeline = pipeline
        self.distro = distro
        self._wrapper_wsl_path = wrapper_wsl_path
        self._workspace_path = workspace_path
        self._memo = memo
        self._run = runner or _default_runner
        self._wall = wall_clock
        self._mono = monotonic
        # windows: reach docker through wsl.exe; otherwise call docker directly (backend inside WSL/Linux).
        self._windows = os.name == "nt" if windows is None else windows
        self._prefix: tuple[str, ...] = ("wsl.exe", "-d", distro, "-e") if self._windows else ()
        self._supported = self._windows or shutil.which("docker") is not None
        self._lock = threading.Lock()
        self._liveness_lock = threading.Lock()
        self._memory: CapabilityResult | None = None
        self._failure_at: float | None = None
        self._liveness: Liveness | None = None
        self.probe_count = 0
        self.liveness_count = 0
        self.last_invalidation: dict[str, Any] | None = None

    # ------------------------------------------------------------------ liveness

    def liveness(self, force: bool = False) -> Liveness:
        with self._liveness_lock:
            cached = self._liveness
            if (not force and cached is not None and cached.ok
                    and self._mono() - cached.checked_at < self.settings.liveness_cache_seconds):
                return cached
            started = self._mono()
            if not self._supported:
                result = Liveness(False, None, "No container runtime: docker is not reachable from this backend", started, 0.0)
            elif self.pipeline is None:
                result = Liveness(False, None, "No pinned pipeline image is configured", started, 0.0)
            else:
                self.liveness_count += 1
                code, out, err = self._run([*self._prefix, "docker", "image", "inspect",
                                            "--format", "{{.Id}}", self.pipeline.image], LIVENESS_TIMEOUT_SECONDS)
                image_id = out.strip().splitlines()[0] if out.strip() else ""
                ok = code == 0 and image_id.startswith("sha256:")
                detail = "pinned image present; docker daemon reachable" if ok else (
                    f"docker image inspect failed (exit {code}): {(err or out).strip()[:200]}")
                result = Liveness(ok, image_id if ok else None, detail, started, self._mono() - started)
            self._liveness = result
            return result

    # ------------------------------------------------------------------ fingerprint / persistence

    def fingerprint(self, image_id: str) -> str:
        pipeline = self.pipeline
        assert pipeline is not None
        material = {
            "probe_version": PROBE_VERSION, "image_id": image_id, "image": pipeline.image, "distro": self.distro,
            "wrapper": self._memo.sha256(pipeline.wrapper_path), "probe": self._memo.sha256(pipeline.probe_script_path),
            "extractor": self._memo.sha256(pipeline.extractor_path),
        }
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()

    def _load_disk(self) -> CapabilityResult | None:
        try:
            data = json.loads(self.settings.cache_path.read_text(encoding="utf-8"))
            result = CapabilityResult(**{k: data[k] for k in CapabilityResult.__dataclass_fields__ if k in data})
        except (OSError, ValueError, TypeError, KeyError):
            return None
        return result if result.available else None

    def _save_disk(self, result: CapabilityResult) -> None:
        try:
            self.settings.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings.cache_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(result.as_dict(), indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.settings.cache_path)
        except OSError as exc:
            LOGGER.warning("Could not persist capability cache: %s", exc)

    def _fresh(self, result: CapabilityResult | None, fingerprint: str) -> bool:
        return (result is not None and result.available and result.fingerprint == fingerprint
                and result.validated_at_epoch is not None
                and 0 <= self._wall() - result.validated_at_epoch < self.settings.max_age_seconds)

    # ------------------------------------------------------------------ public

    def invalidate(self, reason: str) -> None:
        with self._lock:
            self._memory = None
            self._failure_at = None
            self._liveness = None
            self.last_invalidation = {"reason": reason, "at_utc": _utc(self._wall())}
            try:
                self.settings.cache_path.unlink(missing_ok=True)
            except OSError as exc:
                LOGGER.warning("Could not remove capability cache file: %s", exc)
        LOGGER.info("Capability cache invalidated: %s", reason)

    def peek(self) -> CapabilityResult | None:
        return self._memory

    def get(self) -> CapabilityResult:
        live = self.liveness()
        if not live.ok:
            # Never serve a cached "available" when the runtime is not live.
            with self._lock:
                if self._memory is not None:
                    self.last_invalidation = {"reason": f"liveness failed: {live.detail}", "at_utc": _utc(self._wall())}
                self._memory = None
            return CapabilityResult(False, None, [f"Container runtime liveness failed: {live.detail}"],
                                    source="liveness_failed")
        fingerprint = self.fingerprint(live.image_id or "")
        with self._lock:  # single-flight: concurrent callers wait here and reuse the fresh result
            memory = self._memory
            if self._fresh(memory, fingerprint):
                return CapabilityResult(**{**memory.as_dict(), "source": "memory"})  # type: ignore[union-attr]
            if (memory is not None and not memory.available and memory.fingerprint == fingerprint
                    and self._failure_at is not None
                    and self._mono() - self._failure_at < self.settings.failure_retry_seconds):
                return CapabilityResult(**{**memory.as_dict(), "source": "memory"})
            disk = self._load_disk()
            if self._fresh(disk, fingerprint):
                self._memory = disk
                return CapabilityResult(**{**disk.as_dict(), "source": "disk"})  # type: ignore[union-attr]
            if memory is not None and memory.fingerprint != fingerprint:
                self.last_invalidation = {"reason": "fingerprint changed", "at_utc": _utc(self._wall())}
            result = self._probe(fingerprint)
            self._memory = result
            if result.available:
                self._failure_at = None
                self._save_disk(result)
            else:
                self._failure_at = self._mono()
            return result

    def _probe(self, fingerprint: str) -> CapabilityResult:
        pipeline = self.pipeline
        assert pipeline is not None
        self.probe_count += 1
        started = self._mono()
        command = [*self._prefix, "bash", self._wrapper_wsl_path(), "bash",
                   self._workspace_path(pipeline.probe_script_path), self._workspace_path(pipeline.extractor_path)]
        code, out, err = self._run(command, self.settings.probe_timeout_seconds)
        seconds = self._mono() - started
        fields: dict[str, str] = {}
        for line in out.splitlines():
            key, sep, value = line.partition("=")
            if sep and key.replace("_", "").isalnum():
                fields[key.strip()] = value.strip()
        blockers = [f"{key} {fields.get(key, 'not reported')}" for key in REQUIRED_OK if fields.get(key) != "ok"]
        if fields.get("probe_complete") != "1":
            blockers.append(f"capability probe did not complete (exit {code}): {(err or '').strip()[-300:]}")
        now = self._wall()
        return CapabilityResult(not blockers, fingerprint, blockers, fields, _utc(now), now, round(seconds, 3), "probe")

    def status(self) -> dict[str, Any]:
        live = self._liveness
        memory = self._memory
        return {
            "cache_path": str(self.settings.cache_path),
            "max_age_seconds": self.settings.max_age_seconds,
            "probe_count": self.probe_count,
            "liveness_count": self.liveness_count,
            "liveness": None if live is None else {"ok": live.ok, "image_id": live.image_id, "detail": live.detail,
                                                    "seconds": round(live.seconds, 3)},
            "cached": None if memory is None else {k: v for k, v in memory.as_dict().items() if k != "source"},
            "last_invalidation": self.last_invalidation,
        }
