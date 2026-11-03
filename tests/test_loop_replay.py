"""Continuous (looped) file replay: configuration, extractor command, file mapping, evidence, overlay API."""

from __future__ import annotations

from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.configuration import load_configuration
from backend.app.main import create_app
from backend.app.perception.base import ObservationBatch
from backend.app.perception.service_maker import ServiceMakerSessions
from backend.app.services.evidence import MediaTools, ToolResult
from backend.app.analytics.types import Observation, TrackKey

from .support import PROJECT_ROOT, SuppliedObservationAdapter, copy_configuration, update_yaml
from .test_evidence import FakeMedia

FRAMES = 710  # frames in one pass of the clip
PERIOD = 23.7567  # DeepStream file_loop: next pass starts this much later on the pipeline timeline
ORIGIN = 0.0667  # the pipeline's first PTS (B-frame delay); the file's first frame is at 0.0


class LoopMedia(FakeMedia):
    def frame_count(self, source: Path) -> int | None:
        return FRAMES


def looping(tmp_path: Path, loop: bool = True):
    directory = copy_configuration(tmp_path)
    clip = tmp_path / "cam01.mp4"
    clip.write_bytes(b"unit-test-video-fixture")
    update_yaml(directory, "cameras", lambda document: document["cameras"][0].update(source=str(clip), loop=loop))
    return directory


def pipeline_time(frame: int) -> float:
    passes, offset = divmod(frame, FRAMES)
    return ORIGIN + passes * PERIOD + offset * 1001 / 30000


def observation(epoch: str, track: str, at: float) -> Observation:
    return Observation(key=TrackKey("cam-01", epoch, track), timestamp=at, position=None, world_position=None,
                       metadata={"tracker_bbox_xywh_pixels": {"x": 10.0, "y": 20.0, "width": 30.0, "height": 60.0}})


def batch(epoch: str, frame: int, tracks: tuple[str, ...] = ()) -> ObservationBatch:
    at = pipeline_time(frame)
    return ObservationBatch(camera_id="cam-01", camera_epoch=epoch, observations=tuple(observation(epoch, t, at) for t in tracks),
                            received_at=time.monotonic(), source_fps=29.97, analytics_fps=29.97, frame_age_ms=None, frame_age_basis="unit",
                            dropped_frames=0, metadata={"frame_width": 1920, "frame_height": 1080}, source_time=at,
                            source_pts_ns=int(at * 1e9), source_segment=0, frame_number=frame)


def test_loop_is_a_file_only_option(tmp_path: Path) -> None:
    config = load_configuration(looping(tmp_path), PROJECT_ROOT)
    assert config.camera_map["cam-01"].loop is True
    directory = copy_configuration(tmp_path / "rtsp")
    update_yaml(directory, "cameras", lambda document: document["cameras"][1].update(
        enabled=True, source_type="rtsp", source="rtsp://camera-host:554/stream", loop=True))
    with pytest.raises(Exception, match="loop applies to file sources only"):
        load_configuration(directory, PROJECT_ROOT)


def test_looping_camera_passes_loop_to_the_extractor_and_others_do_not(monkeypatch, tmp_path: Path) -> None:
    from backend.app.perception import service_maker
    monkeypatch.setattr(service_maker, "host_route", lambda: "windows-wsl")
    monkeypatch.setattr(service_maker, "runtime_prefix", lambda distro: ())
    monkeypatch.setattr(service_maker, "wsl_path", lambda path: "/mnt/x/" + Path(path).name)
    base = load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT)
    manager = ServiceMakerSessions(base)
    output, summary = PROJECT_ROOT / "backend" / "var" / "o.jsonl", PROJECT_ROOT / "backend" / "var" / "s.json"
    camera = base.camera_map["cam-01"]
    assert "--loop" not in manager._command(camera.model_copy(update={"loop": False}), "e", output, summary, "paced")
    paced = manager._command(camera.model_copy(update={"loop": True}), "e", output, summary, "paced")
    assert "--loop" in paced and paced[paced.index("--pace") + 1] == "source"  # looping keeps source pacing
    # The per-epoch frame file is size-capped (rotated), so a looping run cannot fill the disk.
    assert paced[paced.index("--output-max-mib") + 1] == f"{base.runtime.replay.frame_log_max_mib:g}"


def test_frame_count_reads_the_header_then_falls_back_to_counting_packets(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"x")
    calls: list[list[str]] = []

    def runner(command: list[str], timeout: float) -> ToolResult:
        calls.append(command)
        if "-count_packets" in command:
            return ToolResult(0, "712\n", "")
        return ToolResult(0, "N/A\n", "")

    tools = MediaTools(5, runner=runner, ffmpeg="ffmpeg", ffprobe="ffprobe")
    assert tools.frame_count(source) == 712
    assert tools.frame_count(source) == 712 and len(calls) == 2  # memoized per file version
    assert MediaTools(5, ffmpeg=None, ffprobe=None).frame_count(source) is None


def test_looped_replay_maps_each_pass_onto_the_file_and_never_reports_eof(tmp_path: Path) -> None:
    media = LoopMedia()
    adapter = SuppliedObservationAdapter()
    app = create_app(config_dir=looping(tmp_path), database_path=tmp_path / "l.db", perception_adapter=adapter,
                     media_tools=media, evidence_root=tmp_path / "evidence")
    with TestClient(app) as client:
        start = client.post("/api/v1/demo/start").json()
        epoch = next(c for c in start["cameras"] if c["id"] == "cam-01")["epoch"]
        for frame in (0, 1, 709, 710, 711, 1000):
            client.portal.call(adapter.emit, batch(epoch, frame, ("4", "9")))
        camera = next(c for c in client.get("/api/v1/facility/snapshot").json()["cameras"] if c["id"] == "cam-01")
        assert camera["source_loop"] is True and camera["source_loop_index"] == 1
        # Frame 1000 is file frame 290 of the second pass.
        assert camera["source_file_position_s"] == pytest.approx(290 * 1001 / 30000, abs=1e-6)
        assert camera["source_segment"] == 0 and camera["stream_outcome"] is None

        # A marker in the second pass is aligned on that pass's first frame, not the epoch's.
        created = client.post("/api/v1/cameras/cam-01/evidence-markers", json={"note": "second pass"})
        assert created.status_code == 201
        assert media.calls[0] == ("snapshot", pytest.approx(290 * 1001 / 30000, abs=1e-6))
        mapping = created.json()["snapshot"]["metadata"]["timeline_mapping"]
        assert mapping["pipeline_origin_s"] == pytest.approx(pipeline_time(710))
        # An explicit time in the first pass still maps onto the first pass.
        media.calls.clear()
        first = client.post("/api/v1/cameras/cam-01/evidence-markers", json={"source_time_s": pipeline_time(300)})
        assert first.status_code == 201
        assert media.calls[0] == ("snapshot", pytest.approx(300 * 1001 / 30000, abs=1e-6))
        client.post("/api/v1/demo/stop")


def test_detections_endpoint_returns_recent_boxes_with_file_positions(tmp_path: Path) -> None:
    adapter = SuppliedObservationAdapter()
    app = create_app(config_dir=looping(tmp_path), database_path=tmp_path / "d.db", perception_adapter=adapter,
                     media_tools=LoopMedia(), evidence_root=tmp_path / "evidence")
    with TestClient(app) as client:
        assert client.get("/api/v1/cameras/nope/detections").status_code == 404
        start = client.post("/api/v1/demo/start").json()
        epoch = next(c for c in start["cameras"] if c["id"] == "cam-01")["epoch"]
        for frame in range(5):
            client.portal.call(adapter.emit, batch(epoch, frame, ("4", "9")))
        body = client.get("/api/v1/cameras/cam-01/detections").json()
        assert body["camera_epoch"] == epoch and (body["frame_width"], body["frame_height"]) == (1920, 1080)
        assert [frame["frame_number"] for frame in body["frames"]] == [0, 1, 2, 3, 4]
        assert body["frames"][0]["file_position_s"] == pytest.approx(0.0)
        assert sorted(body["frames"][0]["boxes"]) == [[10.0, 20.0, 30.0, 60.0, "4"], [10.0, 20.0, 30.0, 60.0, "9"]]
        newer = client.get("/api/v1/cameras/cam-01/detections", params={"after_frame": 2}).json()
        assert [frame["frame_number"] for frame in newer["frames"]] == [3, 4]
        client.post("/api/v1/demo/stop")
        assert client.get("/api/v1/cameras/cam-01/detections").json()["frames"] == []  # cleared with the epoch


def test_looping_without_a_frame_count_refuses_evidence_instead_of_guessing(tmp_path: Path) -> None:
    adapter = SuppliedObservationAdapter()
    app = create_app(config_dir=looping(tmp_path), database_path=tmp_path / "n.db", perception_adapter=adapter,
                     media_tools=FakeMedia(), evidence_root=tmp_path / "evidence")  # FakeMedia cannot count frames
    with TestClient(app) as client:
        start = client.post("/api/v1/demo/start").json()
        epoch = next(c for c in start["cameras"] if c["id"] == "cam-01")["epoch"]
        client.portal.call(adapter.emit, batch(epoch, 0))
        refused = client.post("/api/v1/cameras/cam-01/evidence-markers", json={})
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "SOURCE_LOOP_UNMAPPED"
        client.post("/api/v1/demo/stop")
