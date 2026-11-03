"""Evidence: source-time-linked snapshot/clip records, unavailable states, retention, API."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil

import pytest
from fastapi.testclient import TestClient

from backend.app.configuration import load_configuration
from backend.app.main import create_app
from backend.app.services.evidence import EvidenceService, EvidenceUnavailable, MediaTools, ToolResult

from .support import PROJECT_ROOT, SuppliedObservationAdapter, copy_configuration, update_yaml
from .support import requires_local_artifacts
from backend.app.perception.base import ObservationBatch


class FakeMedia(MediaTools):
    """Writes small placeholder bytes; records the requested times so the bookkeeping can be checked."""

    def __init__(self) -> None:
        super().__init__(10, runner=lambda c, t: ToolResult(0, "ffmpeg version test", ""), ffmpeg="ffmpeg", ffprobe="ffprobe")
        self.calls: list[tuple] = []

    def stream_timing(self, source: Path):
        return 0.0, [0.0, 3.003, 6.006, 9.009], 0.0

    def snapshot(self, source: Path, pts_time: float, output: Path) -> float:
        self.calls.append(("snapshot", pts_time))
        output.write_bytes(b"\xff\xd8jpeg")
        return pts_time + 0.01  # the next decoded frame

    def clip(self, source: Path, start: float, duration: float, output: Path) -> None:
        self.calls.append(("clip", start, duration))
        output.write_bytes(b"mp4" * 10)

    def duration(self, path: Path) -> float:
        return 7.5


def configured(tmp_path: Path):
    directory = copy_configuration(tmp_path)
    clip = tmp_path / "cam01.mp4"
    clip.write_bytes(b"unit-test-video-fixture")
    update_yaml(directory, "cameras", lambda document: document["cameras"][0].update(source=str(clip)))
    config = load_configuration(directory, PROJECT_ROOT)
    config.runtime.evidence_root = tmp_path / "evidence"
    return directory, config


def test_capture_records_requested_and_actual_source_bounds(tmp_path: Path, repository) -> None:
    _, config = configured(tmp_path)
    service = EvidenceService(config, repository, FakeMedia())
    run_id = repository.create_run("rev")["id"]
    result = service.capture_marker(run_id=run_id, camera_id="cam-01", camera_epoch="e1", source_time_s=10.0,
                                    note="crowd at gate", provenance={"detector_id": "trafficcamnet-smoke"})
    snapshot, clip = result["snapshot"], result["clip"]
    assert snapshot["camera_epoch"] == "e1" and snapshot["source_marked_s"] == 10.0
    assert snapshot["source_start_s"] == pytest.approx(10.01)  # actual decoded frame, not the request
    assert clip["metadata"]["requested_source_start_s"] == 7.0
    assert clip["source_start_s"] == pytest.approx(6.006)  # keyframe at/before the requested start
    assert clip["source_end_s"] == pytest.approx(6.006 + 7.5)
    assert snapshot["trigger"] == "operator_marker" and "not an analytics" in snapshot["label"]
    assert snapshot["file_available"] and clip["file_available"] and snapshot["checksum"]
    assert snapshot["group_id"] == clip["group_id"] == result["group_id"]


def test_pipeline_pts_offset_is_aligned_on_the_first_frame(tmp_path: Path, repository) -> None:
    _, config = configured(tmp_path)
    media = FakeMedia()
    service = EvidenceService(config, repository, media)
    run_id = repository.create_run("rev")["id"]
    # cam01: the pipeline's first PTS is 0.0667 s (B-frame reorder delay); the file's first frame is 0.0.
    result = service.capture_marker(run_id=run_id, camera_id="cam-01", camera_epoch="e1", source_time_s=10.0667,
                                    note=None, provenance={}, pipeline_origin_s=0.0667, frame_number=300)
    assert media.calls[0] == ("snapshot", pytest.approx(10.0))  # file PTS, not the pipeline PTS
    mapping = result["snapshot"]["metadata"]["timeline_mapping"]
    assert mapping["file_pts_minus_source_s"] == pytest.approx(-0.0667)
    assert result["snapshot"]["source_start_s"] == pytest.approx(10.0767)  # reported back on the pipeline timeline
    assert result["clip"]["source_start_s"] == pytest.approx(6.006 + 0.0667)
    assert result["snapshot"]["metadata"]["pipeline_frame_number"] == 300


def test_missing_file_is_reported_unavailable_not_hidden(tmp_path: Path, repository) -> None:
    _, config = configured(tmp_path)
    service = EvidenceService(config, repository, FakeMedia())
    run_id = repository.create_run("rev")["id"]
    result = service.capture_marker(run_id=run_id, camera_id="cam-01", camera_epoch="e1", source_time_s=1.0,
                                    note=None, provenance={})
    (config.runtime.evidence_root / repository.get_evidence(result["clip"]["id"])["path"]).unlink()
    described = service.describe(repository.get_evidence(result["clip"]["id"]))
    assert not described["file_available"] and "missing" in described["unavailable_reason"]


def test_tools_missing_names_the_exact_capability(tmp_path: Path, repository) -> None:
    _, config = configured(tmp_path)
    tools = MediaTools(10, ffmpeg=None, ffprobe=None)
    tools.ffmpeg = tools.ffprobe = None
    service = EvidenceService(config, repository, tools)
    with pytest.raises(EvidenceUnavailable, match="ffmpeg and ffprobe not found"):
        service.capture_marker(run_id="r", camera_id="cam-01", camera_epoch="e", source_time_s=0, note=None, provenance={})


def test_retention_removes_oldest_beyond_count_and_age(tmp_path: Path, repository) -> None:
    _, config = configured(tmp_path)
    config.runtime.evidence.max_records = 4
    service = EvidenceService(config, repository, FakeMedia())
    run_id = repository.create_run("rev")["id"]
    groups = [service.capture_marker(run_id=run_id, camera_id="cam-01", camera_epoch="e", source_time_s=float(i),
                                     note=None, provenance={}) for i in range(3)]
    remaining = {item["id"] for item in service.list()}
    assert len(remaining) == 4  # 6 created, the two oldest removed
    oldest = groups[0]
    assert oldest["snapshot"]["id"] not in remaining and oldest["clip"]["id"] not in remaining
    assert not (config.runtime.evidence_root / run_id / "cam-01" / oldest["group_id"]).exists()
    future = datetime.now(timezone.utc) + timedelta(days=config.analytics.retention.evidence_days + 1)
    assert len(service.enforce_retention(now=future)["removed"]) == 4
    assert service.list() == []


def test_a_marker_is_always_returned_even_when_it_alone_exceeds_the_limits(tmp_path: Path, repository) -> None:
    _, config = configured(tmp_path)
    config.runtime.evidence.max_records = 1  # each marker is a snapshot + clip group of two
    config.analytics.retention.max_evidence_mib = 1  # smaller than one group (1.5 MiB clip below)

    class LargeClip(FakeMedia):
        def clip(self, source: Path, start: float, duration: float, output: Path) -> None:
            output.write_bytes(b"0" * (3 * 1024 * 1024 // 2))

    service = EvidenceService(config, repository, LargeClip())
    run_id = repository.create_run("rev")["id"]
    first = service.capture_marker(run_id=run_id, camera_id="cam-01", camera_epoch="e", source_time_s=1.0,
                                   note=None, provenance={})
    assert first["snapshot"] and first["clip"] and first["snapshot"]["file_available"]
    second = service.capture_marker(run_id=run_id, camera_id="cam-01", camera_epoch="e", source_time_s=2.0,
                                    note=None, provenance={})
    assert second["snapshot"]["file_available"] and second["clip"]["file_available"]
    # The older group went as a whole; the newest group was kept intact.
    assert {item["group_id"] for item in service.list()} == {second["group_id"]}
    assert len(service.list()) == 2


def test_retention_evicts_whole_groups_never_half_a_marker(tmp_path: Path, repository) -> None:
    _, config = configured(tmp_path)
    config.runtime.evidence.max_records = 5  # odd: a per-record policy would leave a lone clip behind
    service = EvidenceService(config, repository, FakeMedia())
    run_id = repository.create_run("rev")["id"]
    for i in range(4):
        service.capture_marker(run_id=run_id, camera_id="cam-01", camera_epoch="e", source_time_s=float(i),
                               note=None, provenance={})
    listed = service.list()
    groups: dict[str, int] = {}
    for item in listed:
        groups[item["group_id"]] = groups.get(item["group_id"], 0) + 1
    assert len(listed) == 4 and set(groups.values()) == {2}


def test_concurrent_markers_all_succeed_within_the_limit(tmp_path: Path, repository) -> None:
    from concurrent.futures import ThreadPoolExecutor

    _, config = configured(tmp_path)
    config.runtime.evidence.max_records = 2
    service = EvidenceService(config, repository, FakeMedia())
    run_id = repository.create_run("rev")["id"]
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda i: service.capture_marker(
            run_id=run_id, camera_id="cam-01", camera_epoch="e", source_time_s=float(i), note=None, provenance={}), range(8)))
    assert all(result["snapshot"] and result["clip"] for result in results)
    assert len(service.list()) == 2


def test_failed_registration_leaves_no_records_or_files(tmp_path: Path, repository, monkeypatch) -> None:
    _, config = configured(tmp_path)
    service = EvidenceService(config, repository, FakeMedia())
    run_id = repository.create_run("rev")["id"]
    original = repository._insert_evidence
    calls = {"n": 0}

    def fail_second(connection, record):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full while registering the clip")
        return original(connection, record)

    monkeypatch.setattr(repository, "_insert_evidence", fail_second)
    with pytest.raises(RuntimeError):
        service.capture_marker(run_id=run_id, camera_id="cam-01", camera_epoch="e", source_time_s=1.0, note=None, provenance={})
    assert service.list() == []
    assert not any((config.runtime.evidence_root / run_id / "cam-01").iterdir())


def test_retention_never_deletes_outside_the_root(tmp_path: Path, repository) -> None:
    _, config = configured(tmp_path)
    outside = tmp_path / "keep.txt"
    outside.write_text("not evidence")
    repository.register_evidence({"path": str(outside), "media_type": "text/plain", "kind": "foreign", "size_bytes": 1})
    service = EvidenceService(config, repository, FakeMedia())
    future = datetime.now(timezone.utc) + timedelta(days=365)
    service.enforce_retention(now=future)
    assert outside.exists()


def test_marker_api_links_epoch_source_time_alert_and_streams_files(tmp_path: Path) -> None:
    directory, config = configured(tmp_path)
    adapter = SuppliedObservationAdapter()
    app = create_app(config_dir=directory, database_path=tmp_path / "e.db", perception_adapter=adapter,
                     media_tools=FakeMedia(), evidence_root=tmp_path / "evidence")
    with TestClient(app) as client:
        refused = client.post("/api/v1/cameras/cam-01/evidence-markers", json={})
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "NO_ACTIVE_EPOCH"
        start = client.post("/api/v1/demo/start").json()
        epoch = next(c for c in start["cameras"] if c["id"] == "cam-01")["epoch"]
        assert client.post("/api/v1/cameras/cam-01/evidence-markers", json={}).json()["error"]["code"] == "NO_SOURCE_TIME"
        def batch(source_time: float, frame: int) -> ObservationBatch:
            return ObservationBatch(camera_id="cam-01", camera_epoch=epoch, observations=(), received_at=1.0,
                                    source_fps=30.0, analytics_fps=30.0, frame_age_ms=None, frame_age_basis="unit",
                                    dropped_frames=0, metadata={}, source_time=source_time,
                                    source_pts_ns=int(source_time * 1e9), source_segment=0, frame_number=frame)
        # The pipeline's first frame is at PTS 0.0667 s (B-frame delay); the file's first frame is at 0.0.
        client.portal.call(adapter.emit, batch(0.0667, 0))
        client.portal.call(adapter.emit, batch(12.5, 373))
        beyond = client.post("/api/v1/cameras/cam-01/evidence-markers", json={"source_time_s": 20})
        assert beyond.status_code == 422
        created = client.post("/api/v1/cameras/cam-01/evidence-markers", json={"note": "unit"})
        assert created.status_code == 201
        body = created.json()
        assert body["snapshot"]["source_marked_s"] == 12.5 and body["snapshot"]["camera_epoch"] == epoch
        alert = next(a for a in client.get("/api/v1/alerts").json() if a["id"] == body["alert_id"])
        assert alert["severity"] == "INFO" and alert["category"] == "operator_marker"
        assert alert["evidence_id"] == body["snapshot"]["id"]
        listing = client.get("/api/v1/evidence", params={"camera_id": "cam-01"}).json()
        assert {item["id"] for item in listing["items"]} == {body["snapshot"]["id"], body["clip"]["id"]}
        media = client.get(f"/api/v1/evidence/{body['clip']['id']}", headers={"Range": "bytes=0-3"})
        assert media.status_code == 206 and media.content == b"mp4m"
        meta = client.get(f"/api/v1/evidence/{body['clip']['id']}/meta").json()
        assert meta["file_available"] and meta["source_start_s"] == pytest.approx(9.009 + 0.0667)
        assert meta["metadata"]["pipeline_frame_number"] == 373
        client.post("/api/v1/demo/stop")


@requires_local_artifacts
@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed")
def test_real_ffmpeg_extraction_on_the_supplied_clip(tmp_path: Path, repository, config) -> None:
    config = config.model_copy(deep=True)
    config.runtime.evidence_root = tmp_path / "evidence"
    service = EvidenceService(config, repository, MediaTools(60))
    run_id = repository.create_run("rev")["id"]
    result = service.capture_marker(run_id=run_id, camera_id="cam-01", camera_epoch="e", source_time_s=10.0,
                                    note=None, provenance={})
    snapshot, clip = result["snapshot"], result["clip"]
    assert abs(snapshot["source_start_s"] - 10.0) < 0.05  # 29.97 fps: within about one frame
    assert clip["source_start_s"] == pytest.approx(6.006, abs=1e-3)  # cam01 keyframes every 3.003 s
    assert 6.0 < clip["source_end_s"] - clip["source_start_s"] < 8.0
    data = (config.runtime.evidence_root / repository.get_evidence(snapshot["id"])["path"]).read_bytes()
    assert data[:2] == b"\xff\xd8" and len(data) > 10_000


def test_retention_order_is_insertion_order_when_timestamps_tie(monkeypatch, repository) -> None:
    """Fast inserts share a millisecond; "oldest" must then be the first inserted, not a random UUID order."""
    from backend.app.db import repository as repository_module
    monkeypatch.setattr(repository_module, "iso_now", lambda: "2026-11-04T00:00:00.000Z")
    ids = [repository.register_evidence({"path": f"r/{index}.jpg", "media_type": "image/jpeg", "kind": "unit",
                                         "size_bytes": 1}) for index in range(12)]
    assert [row["id"] for row in repository.evidence_for_retention()] == ids
    assert [row["id"] for row in repository.list_evidence()] == list(reversed(ids))
