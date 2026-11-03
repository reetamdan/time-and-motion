"""Capability cache and artifact identity: expected vs current vs compared, reuse, and invalidation."""

from __future__ import annotations

import threading
from pathlib import Path

from backend.app.configuration import load_configuration
from backend.app.perception.artifacts import (
    MATCH, MISMATCH, MISSING_FILE, NO_EXPECTED_IDENTITY, HashMemo, check, runtime_artifact_checks,
)
from backend.app.perception.capability import CapabilityCache

from .support import PROJECT_ROOT, copy_configuration
from .support import requires_local_artifacts

PROBE_OK = "\n".join([
    "probe_version=1", "deepstream_app=ok", "gst_nvstreammux=ok", "gst_nvinfer=ok", "gst_nvtracker=ok",
    "gst_nvv4l2decoder=ok", "extractor=ok", "gpu=ok", "gpu_detail=Test GPU", "probe_complete=1",
])


class FakeRunner:
    def __init__(self) -> None:
        self.image_id = "sha256:" + "a" * 64
        self.liveness_ok = True
        self.probe_output = PROBE_OK
        self.calls: list[str] = []
        self.lock = threading.Lock()
        self.probe_gate: threading.Event | None = None

    def __call__(self, command: list[str], timeout: float):
        kind = "liveness" if "inspect" in command else "probe"
        with self.lock:
            self.calls.append(kind)
        if kind == "liveness":
            return (0, self.image_id + "\n", "") if self.liveness_ok else (1, "", "Cannot connect to the Docker daemon")
        if self.probe_gate is not None:
            self.probe_gate.wait(5)
        return 0, self.probe_output, ""


class Clock:
    def __init__(self) -> None:
        self.wall = 1_000_000.0
        self.mono = 100.0


def cache_for(config, runner, clock, memo=None) -> CapabilityCache:
    return CapabilityCache(
        config.runtime.capability, config.models.pipeline, "Ubuntu-22.04", lambda: "/mnt/c/x/deepstream.sh",
        lambda path: f"/workspace/{path.name}", memo or HashMemo(), runner=runner,
        wall_clock=lambda: clock.wall, monotonic=lambda: clock.mono, windows=True,
    )


def configured(tmp_path: Path):
    directory = copy_configuration(tmp_path)
    config = load_configuration(directory, PROJECT_ROOT)
    config.runtime.capability.cache_path = tmp_path / "capability-cache.json"
    return config


def test_check_distinguishes_expected_current_and_comparison(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"real bytes")
    memo = HashMemo()
    current = memo.sha256(artifact)
    assert check(memo, "a", artifact, current).status == MATCH
    assert check(memo, "a", artifact, "0" * 64).status == MISMATCH
    # A computed hash with nothing recorded to compare against is never a pass.
    unrecorded = check(memo, "a", artifact, None)
    assert unrecorded.status == NO_EXPECTED_IDENTITY and unrecorded.current_sha256 == current and not unrecorded.ok
    missing = check(memo, "a", tmp_path / "absent", current)
    assert missing.status == MISSING_FILE and missing.current_sha256 is None


def test_hash_memo_recomputes_after_the_file_changes(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"one")
    memo = HashMemo()
    first = memo.sha256(artifact)
    artifact.write_bytes(b"two, longer")
    assert memo.sha256(artifact) != first


@requires_local_artifacts
def test_shipped_configuration_identities_all_match(config) -> None:
    checks = runtime_artifact_checks(config, HashMemo())
    names = {c.name for c in checks}
    assert {"detector.checkpoint", "detector.engine", "detector.infer_config", "tracker.config", "pipeline.wrapper",
            "pipeline.extractor", "pipeline.capability_probe", "pipeline.image_reference"} <= names
    assert [(c.name, c.status) for c in checks if c.status != MATCH] == []


def test_tracker_edit_is_detected_as_mismatch(tmp_path: Path) -> None:
    config = configured(tmp_path)
    tracker = tmp_path / "tracker.yml"
    tracker.write_text(config.models.tracker.config_path.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    config.models.tracker.config_path = tracker
    statuses = {c.name: c.status for c in runtime_artifact_checks(config, HashMemo())}
    assert statuses["tracker.config"] == MISMATCH


def test_probe_runs_once_and_is_reused_across_starts_and_restarts(tmp_path: Path) -> None:
    config, runner, clock = configured(tmp_path), FakeRunner(), Clock()
    cache = cache_for(config, runner, clock)
    first = cache.get()
    assert first.available and first.source == "probe" and runner.calls.count("probe") == 1
    clock.mono += 60  # beyond the liveness memo: liveness re-runs, the probe does not
    second = cache.get()
    assert second.available and second.source == "memory"
    assert runner.calls.count("probe") == 1 and runner.calls.count("liveness") == 2
    restarted = cache_for(config, runner, clock)  # new backend process, same persisted cache
    third = restarted.get()
    assert third.available and third.source == "disk" and runner.calls.count("probe") == 1


def test_liveness_failure_never_serves_a_cached_available(tmp_path: Path) -> None:
    config, runner, clock = configured(tmp_path), FakeRunner(), Clock()
    cache = cache_for(config, runner, clock)
    assert cache.get().available
    runner.liveness_ok = False
    clock.mono += 60
    result = cache.get()
    assert not result.available and result.source == "liveness_failed"
    assert "liveness failed" in result.blockers[0]
    runner.liveness_ok = True
    clock.mono += 60
    assert cache.get().available  # served from disk again only after liveness passes


def test_image_change_expiry_and_explicit_invalidation_force_a_new_probe(tmp_path: Path) -> None:
    config, runner, clock = configured(tmp_path), FakeRunner(), Clock()
    cache = cache_for(config, runner, clock)
    cache.get()
    runner.image_id = "sha256:" + "b" * 64  # image replaced under the same reference
    clock.mono += 60
    assert cache.get().source == "probe" and runner.calls.count("probe") == 2
    clock.wall += config.runtime.capability.max_age_seconds + 1
    clock.mono += 60
    assert cache.get().source == "probe" and runner.calls.count("probe") == 3
    cache.invalidate("observed start failure")
    assert not config.runtime.capability.cache_path.exists()
    assert cache.get().source == "probe" and runner.calls.count("probe") == 4
    assert cache.last_invalidation["reason"] == "observed start failure"


def test_failed_probe_is_not_persisted_and_retries_after_the_window(tmp_path: Path) -> None:
    config, runner, clock = configured(tmp_path), FakeRunner(), Clock()
    runner.probe_output = PROBE_OK.replace("gst_nvinfer=ok", "gst_nvinfer=missing")
    cache = cache_for(config, runner, clock)
    failed = cache.get()
    assert not failed.available and "gst_nvinfer missing" in failed.blockers
    assert not config.runtime.capability.cache_path.exists()
    assert cache.get().source == "memory" and runner.calls.count("probe") == 1  # within the retry window
    runner.probe_output = PROBE_OK
    clock.mono += config.runtime.capability.failure_retry_seconds + 1
    assert cache.get().available and runner.calls.count("probe") == 2


def test_concurrent_readers_share_one_probe(tmp_path: Path) -> None:
    config, runner, clock = configured(tmp_path), FakeRunner(), Clock()
    runner.probe_gate = threading.Event()
    cache = cache_for(config, runner, clock)
    results = []
    threads = [threading.Thread(target=lambda: results.append(cache.get())) for _ in range(5)]
    for thread in threads:
        thread.start()
    runner.probe_gate.set()
    for thread in threads:
        thread.join(10)
    assert len(results) == 5 and all(result.available for result in results)
    assert runner.calls.count("probe") == 1
