"""Long-run count history: recent window, paging, downsampling, segments, epochs, gaps, reset."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.analytics.history import downsample
from backend.app.main import create_app

from .support import UnavailableAdapter


def sample(segment: int, bucket: int, mean: float = 2.0, frames: int = 15, epoch: str = "e1") -> dict:
    return {"camera_id": "cam-01", "camera_epoch": epoch, "source_segment": segment, "bucket_index": bucket,
            "source_time_s": bucket * 0.5, "frame_number": bucket * 15, "visible_count": int(mean),
            "visible_mean": mean, "visible_max": int(mean) + 1, "frames": frames, "replay_mode": "paced",
            "received_at_utc": "2026-10-27T00:00:00Z", "count_basis": "unit"}


def seed(repository, run_id: str, samples: list[dict]) -> None:
    repository.record_count_samples(run_id, samples)


def history(repository, run_id, epoch="e1", **kwargs):
    return repository.camera_count_history_window(run_id, "cam-01", epoch, **kwargs)


def test_recent_window_keeps_updating_beyond_the_default_limit(repository) -> None:
    run_id = repository.create_run("rev")["id"]
    seed(repository, run_id, [sample(0, b) for b in range(5000)])
    samples, stats = history(repository, run_id, limit=2000)
    assert stats["total_samples"] == 5000 and len(samples) == 2000
    assert samples[0]["bucket_index"] == 3000 and samples[-1]["bucket_index"] == 4999  # newest, chronological
    seed(repository, run_id, [sample(0, 5000)])
    samples, _ = history(repository, run_id, limit=2000)
    assert samples[-1]["bucket_index"] == 5000  # the chart sees the newest bucket


def test_range_paging_visits_every_sample_once_across_segments(repository) -> None:
    run_id = repository.create_run("rev")["id"]
    seed(repository, run_id, [sample(0, b) for b in range(2500)] + [sample(1, b) for b in range(1200)])
    seen, cursor = [], None
    while True:
        page, stats = history(repository, run_id, limit=1000, window="range",
                              after=tuple(int(v) for v in cursor.split(":")) if cursor else None)
        seen += [(s["source_segment"], s["bucket_index"]) for s in page]
        cursor = stats["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 3700 and len(set(seen)) == 3700 and seen == sorted(seen)
    assert stats["segments"] == 2


def test_source_time_range_and_epoch_scoping(repository) -> None:
    run_id = repository.create_run("rev")["id"]
    seed(repository, run_id, [sample(0, b) for b in range(100)] + [sample(0, b, epoch="e2") for b in range(10)])
    page, _ = history(repository, run_id, limit=1000, window="range", from_source_s=10.0, to_source_s=12.0)
    assert [s["source_time_s"] for s in page] == [10.0, 10.5, 11.0, 11.5, 12.0]
    other, stats = history(repository, run_id, epoch="e2", limit=1000)
    assert len(other) == 10 and stats["total_samples"] == 10  # previous/next epochs never mix


def test_downsampling_never_crosses_segments_or_fills_gaps() -> None:
    samples = [sample(0, b, mean=1.0) for b in range(10)] + [sample(0, b, mean=5.0) for b in range(30, 40)]
    samples += [sample(1, b, mean=9.0) for b in range(10)]
    points, factor = downsample(samples, 10)
    assert factor == 3
    assert all(len({p["source_segment"]}) == 1 for p in points)
    segment_zero = [p for p in points if p["source_segment"] == 0]
    # Bins only exist where buckets exist: nothing between bucket 9 and 30 is invented.
    assert all(p["bucket_index"] < 10 or p["bucket_index"] >= 30 for p in segment_zero)
    assert {p["visible_mean"] for p in segment_zero if p["bucket_index"] < 10} == {1.0}
    partial = [p for p in points if p["buckets_present"] < p["bucket_span"]]
    assert partial  # partial bins are flagged, not padded
    unchanged, one = downsample(samples[:5], 10)
    assert one == 1 and all(p["bucket_span"] == 1 for p in unchanged)


def test_weighted_mean_and_max_in_bins() -> None:
    points, _ = downsample([sample(0, 0, mean=2.0, frames=10), sample(0, 1, mean=4.0, frames=30)], 10)
    assert len(points) == 2
    merged, factor = downsample([sample(0, b, mean=float(b), frames=1) for b in range(20)], 10)
    assert factor == 2 and merged[0]["visible_mean"] == 0.5 and merged[0]["visible_max"] == 2


def test_api_window_downsampling_empty_and_reset(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "h.db", perception_adapter=UnavailableAdapter())
    with TestClient(app) as client:
        empty = client.get("/api/v1/cameras/cam-01/count-history").json()
        assert empty["samples"] == [] and empty["unavailable_reason"] and empty["total_samples"] == 0
        repository = app.state.services.repository
        run_id = repository.create_run("rev")["id"]
        repository.start_camera_session(run_id, "cam-01", "e1", "SOURCE_AVAILABLE", "unit")
        seed(repository, run_id, [sample(0, b) for b in range(3000)])
        body = client.get("/api/v1/cameras/cam-01/count-history", params={"run_id": run_id, "epoch": "e1",
                                                                          "max_points": 500}).json()
        assert body["total_samples"] == 3000 and body["returned_samples"] == 2000 and body["window"] == "recent"
        assert body["downsample_factor"] == 4 and len(body["samples"]) == 500
        assert body["samples"][-1]["bucket_index"] == 2996 and body["last_source_s"] == 2999 * 0.5
        bad = client.get("/api/v1/cameras/cam-01/count-history", params={"run_id": run_id, "epoch": "e1",
                                                                         "window": "range", "after": "x"})
        assert bad.status_code == 422
        assert client.post("/api/v1/demo/reset").status_code == 200
        after = client.get("/api/v1/cameras/cam-01/count-history", params={"run_id": run_id, "epoch": "e1"}).json()
        assert after["samples"] == [] and after["total_samples"] == 0
