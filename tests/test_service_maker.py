from __future__ import annotations

import asyncio
import io
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

import pytest

from backend.app.configuration import load_configuration
from backend.app.perception.base import PerceptionCleanupError, PerceptionUnavailable
from backend.app.perception.service_maker import (
    PersonClass,
    ArrivalRate,
    ContainerCleanup,
    DockerResult,
    FRAME_PREFIX,
    STOP_GRACE_SECONDS,
    ServiceMakerSessions,
    _Session,
    wsl_path,
    workspace_path,
)

from .support import PROJECT_ROOT


def camera():
    return load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT).camera_map["cam-01"]


def object_record(**changes):
    record = {
        "class_id": 2,
        "label": "person",
        "confidence": 0.6,
        "tracker_confidence": 0.7,
        "track_id": "42",
        "bbox": {"x": 10.0, "y": 20.0, "width": 30.0, "height": 40.0},
        "tracker_bbox": {"x": 11.0, "y": 21.0, "width": 28.0, "height": 38.0},
    }
    record.update(changes)
    return record


TRAFFICCAMNET_PERSON = PersonClass(2, "person")


def test_person_metadata_becomes_scoped_uncalibrated_observation() -> None:
    observation = ServiceMakerSessions._observation(object_record(), camera(), "epoch-real", 123.5, person=TRAFFICCAMNET_PERSON)
    assert observation is not None
    assert observation.key.as_string() == "cam-01:epoch-real:42"
    assert observation.timestamp == 123.5
    assert observation.position is None and observation.world_position is None
    assert observation.metadata["tracker_bbox_xywh_pixels"]["width"] == 28.0
    assert observation.metadata["image_bottom_center_pixels"] == [25.0, 59.0]
    assert observation.metadata["geometry_status"] == "UNCALIBRATED_PIXEL_EVIDENCE_ONLY"


@pytest.mark.parametrize("changes", [
    {"label": "bicycle"},
    {"class_id": 1},
    {"track_id": None},
    {"track_id": "not-numeric"},
    {"bbox": None, "tracker_bbox": None},
    {"tracker_bbox": {"x": 0, "y": 0, "width": 0, "height": 10}},
    {"tracker_bbox": {"x": 0, "y": 0, "width": math.nan, "height": 10}},
])
def test_invalid_or_non_person_records_never_become_tracks(changes) -> None:
    assert ServiceMakerSessions._observation(object_record(**changes), camera(), "epoch-real", 123.5, person=TRAFFICCAMNET_PERSON) is None


def test_workspace_paths_cannot_escape_project() -> None:
    inside = workspace_path(PROJECT_ROOT / "demo" / "cam01.mp4", PROJECT_ROOT)
    assert inside == "/workspace/demo/cam01.mp4"
    with pytest.raises(ValueError, match="inside the project workspace"):
        workspace_path(Path(PROJECT_ROOT).parent / "outside.mp4", PROJECT_ROOT)


@pytest.mark.skipif(os.name != "nt", reason="Windows-to-WSL path conversion")
def test_windows_path_conversion_uses_mounted_drive() -> None:
    converted = wsl_path(PROJECT_ROOT / "scripts" / "deepstream.sh")
    assert converted.startswith(f"/mnt/{PROJECT_ROOT.drive[0].lower()}/")  # CI checks out on D:, this PC on C:
    assert converted.endswith("/scripts/deepstream.sh")
    assert " " not in converted


def frame_record(epoch: str, frame_number: int) -> dict:
    pts_ns = 1_000_000_000 + frame_number * 40_000_000
    return {
        "schema_version": "1.0",
        "camera_id": "cam-01",
        "camera_epoch": epoch,
        "frame_number": frame_number,
        "pts_ns": pts_ns,
        "timestamp": pts_ns / 1_000_000_000,
        "frame_width": 1920,
        "frame_height": 1080,
        "bbox_format": "xywh",
        "objects": [object_record()],
    }


def frame_line(record: dict) -> bytes:
    payload = json.dumps(record, allow_nan=False, separators=(",", ":"))
    return (FRAME_PREFIX + payload + "\n").encode("utf-8")


class CollectingSink:
    def __init__(self) -> None:
        self.batches = []
        self.arrived = asyncio.Queue()

    async def emit(self, batch) -> None:
        self.batches.append(batch)
        self.arrived.put_nowait(batch)


class CompletedProcess:
    def __init__(self, stdout) -> None:
        self.stdout = stdout

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0

    def kill(self) -> None:
        return None


class BackpressureQueue(queue.Queue):
    def __init__(self, maxsize: int) -> None:
        super().__init__(maxsize=maxsize)
        self.blocked_put = threading.Event()

    def put(self, item, block=True, timeout=None):
        if self.full():
            self.blocked_put.set()
        return super().put(item, block=block, timeout=timeout)


def make_session(process, epoch: str, log_handle, tmp_path: Path) -> _Session:
    return _Session(
        camera_id="cam-01",
        camera_epoch=epoch,
        container_name=f"test-{epoch}",
        process=process,
        output_path=tmp_path / f"{epoch}.jsonl",
        log_handle=log_handle,
        stop_event=asyncio.Event(),
    )


def test_stdout_delivers_frame_zero_before_eos_and_drains_valid_frames(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        config = load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT)
        manager = ServiceMakerSessions(config)
        camera_config = config.camera_map["cam-01"]
        epoch = "epoch-live"
        records = [frame_record(epoch, 0), frame_record(epoch, 1)]
        records[1]["objects"] = []
        child_script = """
import json
import sys
records = json.loads(sys.argv[1])
prefix = sys.argv[2]
def emit(record):
    sys.stdout.write(prefix + json.dumps(record, separators=(",", ":")) + "\\n")
    sys.stdout.flush()
emit(records[0])
sys.stdout.write("child diagnostic retained\\n")
sys.stdout.flush()
sys.stdin.readline()
emit(records[1])
sys.stdout.write(prefix + "{not-json}\\n")
sys.stdout.flush()
"""
        log_path = tmp_path / "live.log"
        log_handle = log_path.open("w", encoding="utf-8", newline="\n")
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", child_script, json.dumps(records), FRAME_PREFIX],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log_handle,
        )
        session = make_session(process, epoch, log_handle, tmp_path)
        monkeypatch.setattr(manager, "_ensure_container_absent", lambda name, request_stop=True: (process.kill(), ContainerCleanup(name, True))[1])
        try:
            manager._start_reader(session)
            await asyncio.wait_for(manager._wait_for_metadata(session), timeout=2)
            assert process.poll() is None
            assert session.frame_queue.qsize() == 1

            sink = CollectingSink()
            session.task = asyncio.create_task(manager._consume(session, camera_config, sink))
            first = await asyncio.wait_for(sink.arrived.get(), timeout=2)
            assert first.metadata["frame_number"] == 0
            assert process.poll() is None
            assert len(sink.batches) == 1

            assert process.stdin is not None
            process.stdin.write(b"\n")
            process.stdin.flush()
            second = await asyncio.wait_for(sink.arrived.get(), timeout=2)
            assert second.metadata["frame_number"] == 1
            await asyncio.wait_for(session.task, timeout=2)
            assert await asyncio.to_thread(process.wait, 2) == 0

            assert [batch.metadata["frame_number"] for batch in sink.batches] == [0, 1]
            assert [batch.metadata["source_pts_ns"] for batch in sink.batches] == [
                records[0]["pts_ns"], records[1]["pts_ns"],
            ]
            assert len(first.observations) == 1
            observation = first.observations[0]
            assert observation.key.as_string() == f"cam-01:{epoch}:42"
            assert observation.metadata["bbox_xywh_pixels"] == records[0]["objects"][0]["bbox"]
            assert observation.metadata["tracker_bbox_xywh_pixels"] == records[0]["objects"][0]["tracker_bbox"]
            assert second.observations == ()
            assert session.frames_consumed == 2
            assert session.malformed_frames == 1
            assert session.reader_error is None
            assert session.reader_done.is_set()
            assert session.frame_queue.empty()
            assert "child diagnostic retained" in log_path.read_text(encoding="utf-8")
            assert "Skipping malformed Service Maker stdout frame" in log_path.read_text(encoding="utf-8")
        finally:
            try:
                await manager._finish(session)
            finally:
                if process.stdin is not None:
                    process.stdin.close()
        assert log_handle.closed
        assert session.reader is not None and not session.reader.is_alive()

    asyncio.run(scenario())


def test_bounded_queue_backpressures_reader_then_natural_eof_drains_every_frame(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT)
        manager = ServiceMakerSessions(config)
        camera_config = config.camera_map["cam-01"]
        epoch = "epoch-bounded"
        records = [frame_record(epoch, number) for number in range(5)]
        process = CompletedProcess(io.BytesIO(b"".join(frame_line(record) for record in records)))
        log_handle = (tmp_path / "bounded.log").open("w", encoding="utf-8", newline="\n")
        session = make_session(process, epoch, log_handle, tmp_path)
        bounded_queue = BackpressureQueue(maxsize=2)
        session.frame_queue = bounded_queue
        try:
            manager._start_reader(session)
            assert await asyncio.to_thread(bounded_queue.blocked_put.wait, 2)
            assert bounded_queue.full()
            assert not session.reader_done.is_set()

            sink = CollectingSink()
            session.task = asyncio.create_task(manager._consume(session, camera_config, sink))
            for expected_frame in range(5):
                batch = await asyncio.wait_for(sink.arrived.get(), timeout=2)
                assert batch.metadata["frame_number"] == expected_frame
            await asyncio.wait_for(session.task, timeout=2)
            assert session.reader_done.is_set()
            assert session.frames_consumed == 5
            assert session.frame_queue.empty()
            assert session.malformed_frames == 0
            assert session.reader_error is None
        finally:
            await manager._finish(session)
        assert log_handle.closed
        assert session.reader is not None and not session.reader.is_alive()

    asyncio.run(scenario())


def test_stop_camera_joins_only_the_requested_epoch(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        config = load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT)
        manager = ServiceMakerSessions(config)
        sessions = {}
        processes = {}
        for epoch in ("epoch-one", "epoch-two"):
            log_handle = (tmp_path / f"{epoch}.log").open("w", encoding="utf-8", newline="\n")
            process = subprocess.Popen(
                [sys.executable, "-u", "-c", "import sys; sys.stdin.readline()"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log_handle,
            )
            session = make_session(process, epoch, log_handle, tmp_path)
            sessions[epoch] = session
            processes[session.container_name] = process
            manager._sessions[("cam-01", epoch)] = session
            manager._start_reader(session)
        monkeypatch.setattr(manager, "_ensure_container_absent", lambda name, request_stop=True: (processes[name].kill(), ContainerCleanup(name, True))[1])
        try:
            first = sessions["epoch-one"]
            second = sessions["epoch-two"]
            await asyncio.wait_for(manager.stop_camera("cam-01", "epoch-one"), timeout=3)
            assert ("cam-01", "epoch-one") not in manager._sessions
            assert first.process.poll() is not None
            assert first.reader_done.is_set()
            assert first.reader is not None and not first.reader.is_alive()
            assert first.log_handle.closed
            assert second.process.poll() is None
            assert not second.reader_done.is_set()
            assert second.reader is not None and second.reader.is_alive()
            assert not second.log_handle.closed
            assert ("cam-01", "epoch-two") in manager._sessions

            await asyncio.wait_for(manager.stop_camera("cam-01", "epoch-two"), timeout=3)
            assert second.process.poll() is not None
            assert second.reader_done.is_set()
            assert second.reader is not None and not second.reader.is_alive()
            assert second.log_handle.closed
            assert first.reader_error is None and second.reader_error is None
            assert not manager._sessions
        finally:
            for epoch in sessions:
                await asyncio.wait_for(manager.stop_camera("cam-01", epoch), timeout=3)
            for process in processes.values():
                if process.poll() is None:
                    process.kill()
                if process.stdin is not None:
                    process.stdin.close()

    asyncio.run(scenario())


class FakeDocker:
    """Container state machine standing in for `wsl.exe -- docker ...` (fault injection)."""

    def __init__(self, name: str, *, stop: str = "ok", survives_stop: bool = False,
                 kill_effective_after: int = 1, unreachable: bool = False) -> None:
        self.name = name
        self.present = True
        self.stop_mode = stop
        self.survives_stop = survives_stop
        self.kill_effective_after = kill_effective_after
        self.unreachable = unreachable
        self.kills = 0
        self.calls: list[list[str]] = []

    def __call__(self, arguments, timeout):
        self.calls.append(list(arguments))
        command = ("wsl.exe", "--", "docker", *arguments)
        verb = arguments[0]
        if self.unreachable:
            return DockerResult(command, None, error="OSError: wsl.exe unavailable")
        if verb == "ps":
            return DockerResult(command, 0, self.name + "\n" if self.present else "")
        if verb == "stop":
            if not self.survives_stop:
                self.present = False
            if self.stop_mode == "timeout":
                return DockerResult(command, None, timed_out=True)
            if self.stop_mode == "nonzero":
                return DockerResult(command, 1, stderr="Error response from daemon: cannot stop container")
            return DockerResult(command, 0, self.name)
        if verb == "kill":
            self.kills += 1
            return DockerResult(command, 0 if self.present else 1, stderr="" if self.present else "No such container")
        if verb == "rm":
            if self.kills >= self.kill_effective_after:
                self.present = False
                return DockerResult(command, 0, self.name)
            return DockerResult(command, 1, stderr="removal in progress")
        raise AssertionError(f"unexpected docker verb {verb}")

    def touched_names(self) -> set[str]:
        return {call[-1] for call in self.calls if call[0] in ("stop", "kill", "rm")}


OWNED = "aegis-cam-01-0b1c2d3e-epoch"


def cleanup_manager(monkeypatch, fake: FakeDocker) -> ServiceMakerSessions:
    from backend.app.perception import service_maker
    manager = ServiceMakerSessions(load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT))
    monkeypatch.setattr(service_maker, "host_route", lambda: "windows-wsl")
    monkeypatch.setattr(service_maker, "runtime_prefix", lambda distro: ("wsl.exe", "-d", distro, "-e"))
    monkeypatch.setattr(service_maker, "REMOVAL_VERIFY_SECONDS", 0.0)
    monkeypatch.setattr(manager, "_docker", fake)
    return manager


def test_docker_stop_uses_bounded_grace_inside_subprocess_timeout(monkeypatch) -> None:
    manager = ServiceMakerSessions(load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT))
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    manager._docker(["stop", "--time", str(STOP_GRACE_SECONDS), OWNED], STOP_GRACE_SECONDS + 10)
    command, kwargs = calls[0]
    assert command[-4:] == ("stop", "--time", str(STOP_GRACE_SECONDS), OWNED)
    # Docker's own SIGKILL escalation must finish before the subprocess timeout fires.
    assert kwargs["timeout"] > STOP_GRACE_SECONDS


def test_normal_stop_confirms_absence_without_escalation(monkeypatch) -> None:
    fake = FakeDocker(OWNED)
    result = cleanup_manager(monkeypatch, fake)._ensure_container_absent(OWNED)
    assert result.confirmed_absent
    assert [call[0] for call in fake.calls] == ["stop", "ps"]


def test_stop_timeout_is_not_trusted_but_verified(monkeypatch) -> None:
    # The CLI timed out, yet the container did stop: verification (not the timeout) decides.
    fake = FakeDocker(OWNED, stop="timeout")
    result = cleanup_manager(monkeypatch, fake)._ensure_container_absent(OWNED)
    assert result.confirmed_absent
    assert result.attempts[0]["timed_out"] is True
    assert fake.kills == 0


def test_nonzero_stop_with_surviving_container_escalates_to_exact_kill_and_remove(monkeypatch) -> None:
    fake = FakeDocker(OWNED, stop="nonzero", survives_stop=True)
    result = cleanup_manager(monkeypatch, fake)._ensure_container_absent(OWNED)
    assert result.confirmed_absent
    assert fake.kills == 1
    assert any(step["step"] == "escalate-1" for step in result.attempts)
    assert fake.touched_names() == {OWNED}


def test_persisting_container_is_reported_unresolved_after_bounded_retries(monkeypatch) -> None:
    fake = FakeDocker(OWNED, stop="timeout", survives_stop=True, kill_effective_after=99)
    result = cleanup_manager(monkeypatch, fake)._ensure_container_absent(OWNED)
    assert not result.confirmed_absent
    assert result.detail == "container still present"
    assert fake.kills == 2  # CLEANUP_ESCALATION_ATTEMPTS: bounded, never unbounded
    assert fake.touched_names() == {OWNED}


def test_unreachable_docker_is_unknown_not_success(monkeypatch) -> None:
    fake = FakeDocker(OWNED, unreachable=True)
    result = cleanup_manager(monkeypatch, fake)._ensure_container_absent(OWNED)
    assert not result.confirmed_absent
    assert result.detail == "Docker did not answer the presence check"


def test_unowned_container_names_are_never_touched(monkeypatch) -> None:
    fake = FakeDocker("someone-elses-db")
    for name in ("someone-elses-db", "aegis-cam-01; docker rm -f other", "aegis-"):
        result = cleanup_manager(monkeypatch, fake)._ensure_container_absent(name)
        assert not result.confirmed_absent
    assert fake.calls == []


def test_failed_cleanup_keeps_record_blocks_restart_and_retry_resolves(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        fake = FakeDocker(OWNED, stop="timeout", survives_stop=True, kill_effective_after=99)
        manager = cleanup_manager(monkeypatch, fake)
        log_handle = (tmp_path / "cleanup.log").open("w", encoding="utf-8", newline="\n")
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.5)"], stdout=subprocess.PIPE)
        session = make_session(process, "epoch-x", log_handle, tmp_path)
        session.container_name = OWNED
        manager._sessions[("cam-01", "epoch-x")] = session
        with pytest.raises(PerceptionCleanupError):
            await manager.stop_camera("cam-01", "epoch-x")
        assert ("cam-01", "epoch-x") in manager.unresolved_cleanups()
        assert ("cam-01", "epoch-x") not in manager._sessions
        assert "Container cleanup unresolved" in (tmp_path / "cleanup.log").read_text(encoding="utf-8")
        # A new epoch for the same camera must not start while the old container may still run.
        with pytest.raises(PerceptionUnavailable, match="not confirmed removed"):
            await manager.start_camera(camera(), "epoch-y", CollectingSink())
        # Retry after the fault clears: the same exact container is released and the record dropped.
        fake.kill_effective_after = 0
        await manager.stop_camera("cam-01", "epoch-x")
        assert manager.unresolved_cleanups() == {}
        assert fake.touched_names() == {OWNED}

    asyncio.run(scenario())


def test_deepstream_wrapper_runs_container_under_init() -> None:
    # Without an init process the image's bash entrypoint (PID 1) ignores SIGTERM,
    # so every stop waited the full docker grace period.
    wrapper = (PROJECT_ROOT / "scripts" / "deepstream.sh").read_text(encoding="utf-8")
    assert "docker run --rm --init " in wrapper


def test_reader_keeps_draining_after_stop_so_the_wrapper_can_exit(tmp_path: Path) -> None:
    # Regression: the reader used to stop reading on stop, leaving wsl.exe blocked on a full
    # stdout pipe; operator stop then waited out process.wait() and fell back to kill().
    config = load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT)
    manager = ServiceMakerSessions(config)
    epoch = "epoch-drain"
    payload = json.dumps(frame_record(epoch, 0))
    child_script = """
import sys
line = (sys.argv[2] + sys.argv[1]).encode() + bytes([10])
sys.stdout.buffer.write(line)
sys.stdout.buffer.flush()
sys.stdin.readline()
for _ in range(4000):  # several MB: far more than any pipe buffer
    sys.stdout.buffer.write(line)
sys.stdout.buffer.flush()
"""
    log_handle = (tmp_path / "drain.log").open("w", encoding="utf-8", newline="\n")
    process = subprocess.Popen([sys.executable, "-u", "-c", child_script, payload, FRAME_PREFIX],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log_handle)
    session = make_session(process, epoch, log_handle, tmp_path)
    try:
        manager._start_reader(session)
        assert session.first_frame_event.wait(5)
        session.stop_event.set()
        assert process.stdin is not None
        process.stdin.write(b"go\n")
        process.stdin.flush()
        assert process.wait(timeout=10) == 0
        assert session.reader_done.wait(5)
        assert session.frames_discarded_after_stop == 4000
        assert session.frame_queue.qsize() == 1
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        log_handle.close()


def test_arrival_rate_is_time_windowed_and_burst_robust() -> None:
    rate = ArrivalRate(horizon=2.0, min_span=0.5)
    # A burst inside one instant has no measurable span: unknown, never a huge rate.
    assert all(rate.add(10.0) is None for _ in range(30))
    assert rate.add(10.4) is None  # still below the minimum span
    # 32 arrivals spanning 0.5 s -> 31 intervals / 0.5 s.
    assert math.isclose(rate.add(10.5), 31 / 0.5)
    # Steady 30 fps for 3 s: old burst falls out of the 2 s horizon.
    last = None
    for i in range(1, 91):
        last = rate.add(10.5 + i / 30)
    assert math.isclose(last, 30.0, rel_tol=0.02)


def test_presence_check_is_exact_even_when_docker_filter_over_matches(monkeypatch) -> None:
    from backend.app.perception import service_maker

    calls = []

    def docker(arguments, timeout):
        calls.append(arguments)
        # Substring filter returns a longer, different container name: that is not ours.
        return DockerResult(("docker", *arguments), 0, OWNED + "-other\n" + "x" + OWNED + "\n")

    manager = ServiceMakerSessions(load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT))
    monkeypatch.setattr(manager, "_docker", docker)
    assert manager._container_present(OWNED) is False
    assert not any(ch in "".join(calls[0]) for ch in "?*^$|;")


def test_post_launch_startup_failure_reports_allocation_and_true_cleanup_outcome(tmp_path: Path, monkeypatch) -> None:
    from backend.app.perception.base import PerceptionStartError

    async def scenario() -> None:
        config = load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT)
        manager = ServiceMakerSessions(config)
        epoch = "epoch-nometa"
        name = manager.container_name("cam-01", epoch)
        fake = FakeDocker(name, stop="timeout", survives_stop=True, kill_effective_after=99)
        from backend.app.perception import service_maker
        monkeypatch.setattr(service_maker, "host_route", lambda: "windows-wsl")
        monkeypatch.setattr(service_maker, "runtime_prefix", lambda distro: ("wsl.exe", "-d", distro, "-e"))
        monkeypatch.setattr(service_maker, "REMOVAL_VERIFY_SECONDS", 0.0)
        monkeypatch.setattr(manager, "_docker", fake)
        # A real child process stands in for wsl.exe: it is launched, then exits without metadata.
        monkeypatch.setattr(manager, "_command", lambda *args: [sys.executable, "-c", "print('booting')"])

        async def no_metadata(session):
            raise PerceptionUnavailable("no metadata before startup deadline")

        monkeypatch.setattr(manager, "_wait_for_metadata", no_metadata)
        with pytest.raises(PerceptionStartError) as raised:
            await manager.start_camera(camera(), epoch, CollectingSink(), "unpaced")
        assert raised.value.resource_allocated is True and raised.value.cleanup_confirmed is False
        assert ("cam-01", epoch) in manager.unresolved_cleanups()
        assert fake.touched_names() == {name}

    asyncio.run(scenario())


def test_pre_launch_refusals_report_no_allocation(monkeypatch) -> None:
    from backend.app.perception.base import PerceptionStartError

    async def scenario() -> None:
        manager = ServiceMakerSessions(load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT))
        with pytest.raises(PerceptionStartError) as raised:
            await manager.start_camera(camera(), "e1", CollectingSink(), "live")
        assert raised.value.resource_allocated is False

    asyncio.run(scenario())


def test_adoption_accepts_only_the_exact_generated_identity_and_releases_it(monkeypatch) -> None:
    from backend.app.perception.base import ResourceIdentity

    async def scenario() -> None:
        config = load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT)
        manager = ServiceMakerSessions(config)
        identity = manager.resource_identity("cam-01", "epoch-old")
        assert identity.name == "aegis-cam-01-epoch-old" and identity.runtime == "docker@wsl:Ubuntu-22.04"
        for wrong in (ResourceIdentity("container", "aegis-cam-01-other", identity.runtime),
                      ResourceIdentity("container", identity.name, "docker@wsl:OtherDistro"), None):
            with pytest.raises(PerceptionCleanupError):
                manager.adopt("cam-01", "epoch-old", wrong)
        assert manager.unresolved_cleanups() == {}
        fake = FakeDocker(identity.name)
        from backend.app.perception import service_maker
        monkeypatch.setattr(service_maker, "host_route", lambda: "windows-wsl")
        monkeypatch.setattr(service_maker, "runtime_prefix", lambda distro: ("wsl.exe", "-d", distro, "-e"))
        monkeypatch.setattr(service_maker, "REMOVAL_VERIFY_SECONDS", 0.0)
        monkeypatch.setattr(manager, "_docker", fake)
        manager.adopt("cam-01", "epoch-old", identity)
        await manager.stop_camera("cam-01", "epoch-old")
        assert manager.unresolved_cleanups() == {} and not fake.present
        assert fake.touched_names() == {identity.name}

    asyncio.run(scenario())


def test_geometry_policy_rejects_or_discloses_but_never_silently_stretches() -> None:
    from backend.app.perception.service_maker import geometry_check
    config = load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT)
    profiles = {model.id: model.runtime_profile for model in config.models.models}
    full_hd = {"source_width": 1920, "source_height": 1080, "frame_width": 1920, "frame_height": 1080}
    portrait = {**full_hd, "source_width": 1080, "source_height": 1920}
    assert geometry_check(profiles["rf-detr-person-candidate"], full_hd) == (None, None)
    error, _ = geometry_check(profiles["rf-detr-person-candidate"], portrait)
    assert error and "refusing to stretch" in error
    error, disclosure = geometry_check(profiles["trafficcamnet-smoke"], portrait)
    assert error is None and "aspect ratio changed" in disclosure
    error, _ = geometry_check(profiles["trafficcamnet-smoke"], {**full_hd, "frame_width": 1280})
    assert error and "pipeline frame" in error


def test_candidate_person_class_zero_is_normalized_and_other_classes_reported() -> None:
    candidate = PersonClass(0, "person")
    record = object_record(class_id=0)
    observation = ServiceMakerSessions._observation(record, camera(), "e", 1.0, person=candidate)
    assert observation is not None and observation.metadata["label"] == "person"
    assert observation.metadata["detector_class_id"] == 0
    # The TrafficCamNet mapping must not accept the candidate's class 0 (car there).
    assert ServiceMakerSessions._observation(record, camera(), "e", 1.0, person=TRAFFICCAMNET_PERSON) is None
    summary = ServiceMakerSessions._object_summary(
        [object_record(), object_record(class_id=0, label="car"), object_record(track_id=None)], TRAFFICCAMNET_PERSON)
    assert summary == {"detector_classes": {"2:person": 2, "0:car": 1}, "untracked_person_detections": 1}


# ------------------------------------------------------------------ v2 extractor: source-side pacing


EXTRACTOR_DIR = PROJECT_ROOT / "perception" / "deepstream" / "service_maker"


def test_v1_extractor_is_preserved_unchanged_and_v2_is_configured() -> None:
    import hashlib
    v1 = hashlib.sha256((EXTRACTOR_DIR / "extract_metadata.py").read_bytes()).hexdigest()
    assert v1 == "6093b5d5d06089225b3bf60b0342fedccc50efde477f9663c5ef61ff91abad25"
    pipeline = load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT).models.pipeline
    assert pipeline.extractor_path.name == "extract_metadata_v2.py"
    assert pipeline.extractor_schema_version == "1.1" and pipeline.extractor_pacing == "source"
    assert hashlib.sha256(pipeline.extractor_path.read_bytes()).hexdigest() == pipeline.extractor_sha256


def test_paced_command_paces_at_the_source_and_unpaced_never_waits(monkeypatch, tmp_path: Path) -> None:
    from backend.app.perception import service_maker
    monkeypatch.setattr(service_maker, "host_route", lambda: "windows-wsl")
    monkeypatch.setattr(service_maker, "runtime_prefix", lambda distro: ("wsl.exe", "-d", distro, "-e"))
    monkeypatch.setattr(service_maker, "wsl_path", lambda path: "/mnt/x/" + Path(path).name)
    manager = ServiceMakerSessions(load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT))
    paced = manager._command(camera(), "e", PROJECT_ROOT / "backend" / "var" / "o.jsonl",
                             PROJECT_ROOT / "backend" / "var" / "s.json", "paced")
    unpaced = manager._command(camera(), "e", PROJECT_ROOT / "backend" / "var" / "o.jsonl",
                               PROJECT_ROOT / "backend" / "var" / "s.json", "unpaced")
    assert paced[paced.index("--pace") + 1] == "source" and paced[paced.index("--pace-lead-ms") + 1] == "200"
    assert "--pace" not in unpaced
    assert any(part.endswith("extract_metadata_v2.py") for part in paced)


def test_linux_route_calls_docker_directly(monkeypatch) -> None:
    """Backend inside WSL/Linux: same container command without the wsl.exe hop."""
    from backend.app.perception import service_maker
    monkeypatch.setattr(service_maker, "host_route", lambda: "linux-docker")
    monkeypatch.setattr(service_maker, "runtime_prefix", lambda distro: ())
    monkeypatch.setattr(service_maker, "wsl_path", lambda path: "/mnt/x/" + Path(path).name)
    manager = ServiceMakerSessions(load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT))
    command = manager._command(camera(), "e", PROJECT_ROOT / "backend" / "var" / "o.jsonl", PROJECT_ROOT / "backend" / "var" / "s.json")
    assert command[0] == "env" and "wsl.exe" not in command and command[command.index("bash") + 1].endswith("deepstream.sh")
    monkeypatch.setattr(service_maker, "host_route", lambda: "unsupported")
    with pytest.raises(PerceptionUnavailable, match="No container runtime"):
        manager._command(camera(), "e", PROJECT_ROOT / "backend" / "var" / "o.jsonl", PROJECT_ROOT / "backend" / "var" / "s.json")


def test_schema_1_1_frames_are_accepted_and_unknown_schema_refused() -> None:
    session = type("S", (), {"camera_id": "cam-01", "camera_epoch": "e"})()
    record = frame_record("e", 0) | {"schema_version": "1.1", "emitted_unix_ns": 1}
    assert ServiceMakerSessions._validate_frame(session, json.dumps(record).encode())["emitted_unix_ns"] == 1
    with pytest.raises(ValueError):
        ServiceMakerSessions._validate_frame(session, json.dumps(record | {"schema_version": "2.0"}).encode())


def test_source_pacing_requires_the_v2_schema(tmp_path: Path) -> None:
    from backend.app.configuration import ConfigurationError
    from .support import copy_configuration, update_yaml
    directory = copy_configuration(tmp_path)
    update_yaml(directory, "models", lambda d: d["pipeline"].update(extractor_schema_version="1.0"))
    with pytest.raises(ConfigurationError, match="v2 extractor"):
        load_configuration(directory, PROJECT_ROOT)


def _v2_pacing_classes() -> dict:
    """GuestHostClock and SourcePacer from the v2 extractor source (pyservicemaker is not importable on the host)."""
    import ast
    tree = ast.parse((EXTRACTOR_DIR / "extract_metadata_v2.py").read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in ("GuestHostClock", "SourcePacer")]
    namespace = {"time": __import__("time")}
    exec(compile(ast.Module(nodes, []), "extract_metadata_v2.py", "exec"), namespace)
    return namespace


def _source_pacer():
    return _v2_pacing_classes()["SourcePacer"]


def test_v2_source_pacer_bounds_the_producer_lead(monkeypatch) -> None:
    SourcePacer = _source_pacer()
    clock = {"t": 100.0}
    sleeps: list[float] = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds

    pacer = SourcePacer("source", 0.2, clock=lambda: clock["t"], sleep=sleep)
    for frame in range(90):  # a pipeline that could run at 300 fps (3.3 ms per frame)
        pacer.wait(int(frame * 1e9 / 30))
        lead = (frame / 30) - (clock["t"] - 100.0)
        assert lead <= 0.2 + 1e-9
        clock["t"] += 1 / 300
    assert clock["t"] - 100.0 == pytest.approx(89 / 30 - 0.2 + 1 / 300, abs=0.02)  # held to real time
    unpaced = SourcePacer("none", 0.2, clock=lambda: clock["t"], sleep=sleep)
    before = len(sleeps)
    unpaced.wait(int(10e9))
    assert len(sleeps) == before


def _v2_functions(*names: str) -> dict:
    import ast
    from urllib.parse import urlsplit
    tree = ast.parse((EXTRACTOR_DIR / "extract_metadata_v2.py").read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    namespace = {"urlsplit": urlsplit}
    exec(compile(ast.Module(nodes, []), "extract_metadata_v2.py", "exec"), namespace)
    return namespace


def test_v2_live_sources_are_recognised_and_redacted() -> None:
    ns = _v2_functions("is_live", "redact_source", "scrub")
    url = "rtsp://operator:s3cr%40t@10.0.0.5:8554/cam02?token=abc"
    assert ns["is_live"](url) and ns["is_live"]("RTSPS://h/x") and not ns["is_live"]("/workspace/demo/cam01.mp4")
    assert ns["redact_source"](url) == "rtsp://10.0.0.5:8554/cam02"
    assert ns["redact_source"]("/workspace/demo/cam01.mp4") == "/workspace/demo/cam01.mp4"
    message = f"gst-resource-error: Could not open resource for reading: {url} (operator:s3cr%40t)"
    scrubbed = ns["scrub"](message, url)
    assert "s3cr" not in scrubbed and "operator" not in scrubbed and "token" not in scrubbed
    assert "rtsp://10.0.0.5:8554/cam02" in scrubbed
    assert ns["scrub"]("file error", "/workspace/demo/cam01.mp4") == "file error"


def test_v2_live_source_never_hashes_the_url_and_refuses_source_pacing() -> None:
    text = (EXTRACTOR_DIR / "extract_metadata_v2.py").read_text(encoding="utf-8")
    assert '"source_sha256": None if is_live(args.source) else sha256_file(args.source)' in text
    assert "live_source=is_live(args.source)" in text
    assert 'if is_live(args.source) and args.pace != "none":' in text


def _v2_pacer_class():
    return _v2_pacing_classes()["SourcePacer"]


class _WslClocks:
    """Simulated WSL guest clocks over host time, as measured 2026-10-28: monotonic runs ``fast`` (4.9 %),
    the wall clock runs with it and is stepped back to host time - 0.85 s every ``period`` host seconds."""

    def __init__(self, host: float = 5000.0, fast: float = 0.049, period: float = 30.5):
        self.host, self.fast, self.period = host, fast, period
        self._last_sync = host
        self._wall_at_sync = host - 0.85
        self._mono_at_sync = 100.0

    def _sync(self) -> None:
        while self.host - self._last_sync >= self.period:
            self._mono_at_sync += self.period * (1 + self.fast)
            self._last_sync += self.period
            self._wall_at_sync = self._last_sync - 0.85

    def mono(self) -> float:
        self._sync()
        return self._mono_at_sync + (self.host - self._last_sync) * (1 + self.fast)

    def wall(self) -> float:
        self._sync()
        return self._wall_at_sync + (self.host - self._last_sync) * (1 + self.fast)

    def sleep(self, seconds: float) -> None:  # the guest sleeps in guest (monotonic) seconds
        self.host += seconds / (1 + self.fast)


def test_v2_pacer_holds_the_producer_to_host_time_through_wsl_clock_syncs() -> None:
    """2026-10-28: inside WSL neither clock is host time. Pacing on monotonic ran producers ~3.5 % fast;
    pacing on the wall clock and re-anchoring on its periodic sync steps ran them 1-4 % fast. On the
    host-time estimate, 10 minutes of 29.97 fps frames take 10 host minutes, and after the first two sync
    steps the producer is never more than the 200 ms lead (+ one sleep slice) ahead of host time."""
    classes = _v2_pacing_classes()
    sim = _WslClocks()
    clock = classes["GuestHostClock"](wall=sim.wall, mono=sim.mono)
    pacer = classes["SourcePacer"]("source", 0.2, clock=clock, sleep=sim.sleep)
    frame_ns = 1_000_000_000 * 1001 // 30000
    start = sim.host
    worst_after_settle = 0.0
    for n in range(17982):  # 600 s; the pipeline itself produces instantly
        pacer.wait(n * frame_ns)
        ahead = n * frame_ns / 1e9 - (sim.host - start)
        if sim.host - start > 70:
            worst_after_settle = max(worst_after_settle, ahead)
    assert clock.sync_steps >= 18 and abs(clock.rate() - 1.049) < 1e-3
    assert pacer.reanchors == 0
    assert worst_after_settle <= 0.2 + 0.06
    assert abs((sim.host - start) - (17981 * frame_ns / 1e9 - 0.2)) < 0.3  # 600 s of source took 600 s of host time


def test_v2_raw_wall_clock_pacing_would_run_ahead_of_host_time() -> None:
    """Negative control: the same simulation paced on the raw guest monotonic clock ends > 20 s ahead."""
    sim = _WslClocks()
    pacer = _v2_pacer_class()("source", 0.2, clock=sim.mono, sleep=sim.sleep)
    frame_ns = 1_000_000_000 * 1001 // 30000
    start = sim.host
    for n in range(17982):
        pacer.wait(n * frame_ns)
    assert 17981 * frame_ns / 1e9 - (sim.host - start) > 20


def test_guest_host_clock_is_the_wall_clock_without_sync_steps_and_resets_on_a_large_jump() -> None:
    clock_class = _v2_pacing_classes()["GuestHostClock"]
    t = [1000.0]
    clock = clock_class(wall=lambda: t[0] + 50.0, mono=lambda: t[0])
    for _ in range(100):
        t[0] += 0.5
        assert clock() == t[0] + 50.0  # native Linux / slewed clock: unchanged
    assert clock.rate() is None and clock.sync_steps == 0
    offset = [0.0]
    clock = clock_class(wall=lambda: t[0] + offset[0], mono=lambda: t[0])
    for step in (-1.0, -1.0):
        t[0] += 30.0
        clock()
        offset[0] += step
        clock()
    assert clock.sync_steps == 2 and clock.rate() is not None
    offset[0] += 3600.0  # a real clock reset
    t[0] += 1.0
    assert clock() == t[0] + offset[0] and clock.rate() is None


def test_v2_pacer_reanchors_on_a_real_clock_reset_instead_of_stalling() -> None:
    pacer_class = _v2_pacer_class()
    wall = [500.0]
    slept: list[float] = []
    pacer = pacer_class("source", 0.2, clock=lambda: wall[0], sleep=lambda s: (slept.append(s), wall.__setitem__(0, wall[0] + s)))
    frame_ns = 33_366_666
    for n in range(100):
        pacer.wait(n * frame_ns)
    wall[0] -= 30.0  # the clock is set back 30 s
    before = len(slept)
    for n in range(100, 110):
        pacer.wait(n * frame_ns)
    assert pacer.reanchors == 1 and max(slept) <= 0.1 + 1e-9
    assert sum(slept[before:]) < 0.6  # confirmed within a few reads, no 30 s stall


def test_v2_pacer_waits_out_a_sync_step_instead_of_reanchoring() -> None:
    """A 1.5 s step back is how far the producer is ahead of host time: wait it out (8a2340e2/8dd882fd re-anchored)."""
    pacer_class = _v2_pacer_class()
    wall = [500.0]
    pacer = pacer_class("source", 0.2, clock=lambda: wall[0], sleep=lambda s: wall.__setitem__(0, wall[0] + s))
    frame_ns = 33_366_666
    for n in range(100):
        pacer.wait(n * frame_ns)
    wall[0] -= 1.5
    before = wall[0]
    pacer.wait(100 * frame_ns)
    assert pacer.reanchors == 0 and wall[0] - before == pytest.approx(1.5 + frame_ns / 1e9, abs=0.06)


def test_windows_runtime_prefix_passes_arguments_verbatim(monkeypatch) -> None:
    """`wsl.exe -- cmd args` re-parses the joined arguments in the login shell ($VAR, $(...), ';' are
    interpreted), so an RTSP credential containing ';' or '$' would be executed or mangled. `-e` executes
    the command directly with its argv unchanged."""
    import re
    from backend.app.perception import service_maker
    offenders = [str(p) for p in (PROJECT_ROOT / "backend" / "app").rglob("*.py")
                 if re.search(r'"wsl\.exe", "-d", [^)\]\n]*?, "--"', p.read_text(encoding="utf-8"))]
    assert offenders == []
    # Only runtime_prefix runs while os.name is faked (pathlib must not see it on Linux).
    monkeypatch.setattr(service_maker.os, "name", "nt")
    prefix = service_maker.runtime_prefix("Ubuntu-22.04")
    monkeypatch.undo()
    assert prefix == ("wsl.exe", "-d", "Ubuntu-22.04", "-e")
