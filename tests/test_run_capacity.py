"""Regression tests for the capacity orchestrator (scripts/benchmark/run_capacity.py).

Covers the defects found after the 2026-10-28 host memory stop: whole-file JSONL materialisation,
first-record reparsing, a fixed 4 KiB tail buffer, producer-only stage-D gating, and a missing
processing age treated as zero.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tracemalloc

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("run_capacity", PROJECT_ROOT / "scripts" / "benchmark" / "run_capacity.py")
rc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rc)  # type: ignore[union-attr]

FRAME_NS = 33_366_700  # 29.97 fps


def _producer_file(path: Path, frames: int, objects: int = 0, skip: tuple[int, ...] = (), lead_ms: float = 200.0) -> None:
    t0 = 1_700_000_000_000_000_000
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for n in range(frames):
            if n in skip:
                continue
            pts = n * FRAME_NS
            record = {"frame_number": n, "pts_ns": pts, "emitted_unix_ns": t0 + pts - int(lead_ms * 1e6) * (n > 0),
                      "objects": [{"class_id": 0, "bbox": [1.0, 2.0, 3.0, 4.0], "confidence": 0.5, "track_id": i}
                                  for i in range(objects)]}
            handle.write(json.dumps(record) + "\n")


def _release_file(path: Path, frames: int, skip: tuple[int, ...] = (), age_ms: float = 230.0) -> None:
    t0 = 1_800_000_000_000_000_000
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for n in range(frames):
            if n in skip:
                continue
            read = t0 + n * FRAME_NS
            handle.write(json.dumps({"frame_number": n, "pts_ns": n * FRAME_NS, "emitted_unix_ns": read - 5_000_000,
                                     "read_unix_ns": read, "released_unix_ns": read + int(200e6),
                                     "analytics_done_unix_ns": read + int(age_ms * 1e6), "queue_depth": 2}) + "\n")


def test_first_record_reads_only_the_first_line(tmp_path: Path) -> None:
    path = tmp_path / "f.jsonl"
    path.write_text('\n{"frame_number": 7}\n' + "not json\n" * 1000, encoding="utf-8")
    assert rc.first_record(path) == {"frame_number": 7}
    assert rc.first_record(tmp_path / "absent.jsonl") is None


def test_last_record_handles_records_larger_than_the_block(tmp_path: Path) -> None:
    path = tmp_path / "f.jsonl"
    big = {"frame_number": 2, "objects": [{"bbox": [i, i, i, i]} for i in range(2000)]}  # ~50 KiB, > 4 KiB block
    path.write_text(json.dumps({"frame_number": 1}) + "\n" + json.dumps(big) + "\n", encoding="utf-8")
    assert rc.last_record(path, chunk=4096)["frame_number"] == 2
    # A final line cut by the stop is skipped in favour of the previous complete record.
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"frame_number": 3, "objects": [')
    assert rc.last_record(path, chunk=4096)["frame_number"] == 2
    single = tmp_path / "single.jsonl"
    single.write_text(json.dumps(big), encoding="utf-8")  # one record, no newline, larger than the block
    assert rc.last_record(single, chunk=1024)["frame_number"] == 2


def test_producer_metrics_streams_without_materialising_the_file(tmp_path: Path) -> None:
    path = tmp_path / "feed.jsonl"
    _producer_file(path, frames=3000, objects=60)  # ~100 s of 29.97 fps, >8 MiB with objects
    size = path.stat().st_size
    tracemalloc.start()
    metrics = rc.producer_metrics(path, warmup=10, measure=80)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert size > 8 * 2**20
    assert peak < size / 4, f"peak {peak} bytes for a {size} byte file: records are being retained"
    assert metrics["frame_gaps"] == 0
    assert abs(metrics["cadence_fps"] - 29.97) < 0.05
    assert metrics["schedule_lag_ms"]["p95"] == pytest.approx(-200, abs=1)
    assert metrics["schedule_backlog_growth_ms"] == pytest.approx(0, abs=1)
    assert metrics["objects_per_frame_mean"] == 60


def test_producer_gaps_are_counted(tmp_path: Path) -> None:
    path = tmp_path / "feed.jsonl"
    _producer_file(path, frames=600, skip=(100, 101, 300))
    assert rc.producer_metrics(path, warmup=0, measure=19)["frame_gaps"] == 3


def test_release_metrics_count_missing_deliveries_and_use_backend_clock(tmp_path: Path) -> None:
    producer, release = tmp_path / "p.jsonl", tmp_path / "r.jsonl"
    _producer_file(producer, frames=2400)
    _release_file(release, frames=2400, skip=(900, 901))
    produced = rc.producer_metrics(producer, warmup=10, measure=60)["_window_frames"]
    metrics = rc.release_metrics(release, warmup=10, measure=60, produced_in_window=produced, offset_ns=0)
    assert metrics["missing_deliveries"] == 2
    assert metrics["release_frame_gaps"] == 2
    assert metrics["receive_to_result_ms"]["p95"] == pytest.approx(230, abs=0.01)
    assert metrics["pacing_hold_ms"]["p50"] == pytest.approx(200, abs=0.01)
    assert metrics["delivery_coverage"] < 1


def _feed(cadence: float = 29.9, p95: float | None = 230.0, backend: bool = True) -> dict:
    feed = {"producer": {"frames_in_window": 17982, "covered_window_s": 600, "frame_gaps": 0, "cadence_fps": cadence,
                         "schedule_lag_ms": {"p95": -199}, "schedule_backlog_growth_ms": 0.0}}
    if backend:
        feed["backend"] = {"consumed_cadence_fps": cadence, "missing_deliveries": 0, "release_frame_gaps": 0,
                           "receive_to_result_ms": {"p95": p95}, "receive_to_result_backlog_growth_ms": 1.0}
    return feed


RESOURCES = {"gpu_temp_c": {"max": 70.0}, "backend_rss_growth_mib": 5.0, "container_rss_growth_mib": {}}


def test_stage_d_passes_only_on_backend_measurements() -> None:
    feeds = {"cam-01": _feed(), "cam-02": _feed()}
    assert rc.gate("D", ["cam-01", "cam-02"], feeds, 600, [], RESOURCES)["pass"]
    starved = {"cam-01": _feed(), "cam-02": _feed()}
    starved["cam-02"]["backend"]["consumed_cadence_fps"] = 3.0  # producer still emitted 29.9 fps
    verdict = rc.gate("D", ["cam-01", "cam-02"], starved, 600, [], RESOURCES)
    assert not verdict["pass"] and any("cam-02: backend consumed cadence" in f for f in verdict["failures"])
    no_backend = {"cam-01": _feed(), "cam-02": _feed(backend=False)}
    assert not rc.gate("D", ["cam-01", "cam-02"], no_backend, 600, [], RESOURCES)["pass"]


def test_missing_processing_age_is_a_failure_not_zero() -> None:
    verdict = rc.gate("D", ["cam-01"], {"cam-01": _feed(p95=None)}, 600, [], RESOURCES)
    assert not verdict["pass"]
    assert any("required measurement unavailable: p95 receive-to-result" in f for f in verdict["failures"])


def test_absent_expected_feed_fails() -> None:
    verdict = rc.gate("D", ["cam-01", "cam-02"], {"cam-01": _feed()}, 600, [], RESOURCES)
    assert not verdict["pass"] and "cam-02: expected feed was not measured" in verdict["failures"]


def test_missing_resources_or_temperature_fail() -> None:
    feeds = {"bench-01": _feed(backend=False)}
    assert rc.gate("C", ["bench-01"], feeds, 600, [], RESOURCES)["pass"]
    assert not rc.gate("C", ["bench-01"], feeds, 600, [], None)["pass"]
    assert not rc.gate("C", ["bench-01"], feeds, 600, [], {"gpu_temp_c": None})["pass"]


def test_interrupted_step_never_passes() -> None:
    verdict = {"pass": True, "failures": []}
    assert rc.step_status({"resource_stop": "host available memory 400 MiB < hard stop 512 MiB"}, verdict) == "INTERRUPTED"
    assert verdict["pass"] is False and verdict["failures"][0].startswith("step interrupted")
    assert rc.step_status({"ended": "covered"}, {"pass": True, "failures": []}) == "PASS"


def test_resource_guard_trips(tmp_path: Path) -> None:
    sampler = rc.Sampler(tmp_path / "raw.jsonl")
    sampler._check({"t": 0.0, "host_available_mib": 2000.0})
    assert sampler.tripped is None
    sampler._check({"t": 1.0, "host_available_mib": 700.0})
    sampler._check({"t": 20.0, "host_available_mib": 700.0})
    assert sampler.tripped is None  # not yet sustained for 30 s
    sampler._check({"t": 31.5, "host_available_mib": 700.0})
    assert "below 768 MiB" in sampler.tripped
    hard = rc.Sampler(tmp_path / "raw2.jsonl")
    hard._check({"t": 0.0, "host_available_mib": 400.0})
    assert "hard stop" in hard.tripped
    guest = rc.Sampler(tmp_path / "raw3.jsonl")
    guest._check({"t": 0.0, "host_available_mib": 4000.0, "wsl_available_mib": 300.0})
    assert "WSL guest" in guest.tripped
    for s in (sampler, hard, guest):
        s.raw.close()


def test_per_repeat_variance_is_not_pooled_across_feeds() -> None:
    def item(repeat: int, cadences: list[float]) -> dict:
        return {"stage": "C", "feeds": 2, "repeat": repeat, "gate": {"pass": True},
                "result": {"feeds": {f"bench-{i}": _feed(cadence=c, backend=False) for i, c in enumerate(cadences)},
                           "resources": {"gpu_util_percent": {"p95": 50}}}}
    table = rc.summarize([item(1, [29.9, 20.0]), item(2, [29.9, 29.9])])["C-2feeds"]
    assert [r["min_feed_cadence_fps"] for r in table["per_repeat"]] == [20.0, 29.9]
    assert table["across_repeats"]["min_feed_cadence_fps"]["n"] == 2
