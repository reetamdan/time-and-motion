"""End-of-stream handling through the real ServiceMakerSessions._consume loop and the supervisor."""

from __future__ import annotations

import asyncio
import queue
from pathlib import Path

from backend.app.configuration import load_configuration
from backend.app.db.repository import SQLiteRepository
from backend.app.perception.base import AdapterAvailability, PerceptionAdapter
from backend.app.perception.service_maker import ServiceMakerSessions, _Session
from backend.app.services.runtime import CameraRuntimeSupervisor
from backend.app.services.sequence import SequenceState

from .support import PROJECT_ROOT, copy_configuration, update_yaml

FPS = 30.0


class FakeProcess:
    def __init__(self, returncode: int | None = 0) -> None:
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            import subprocess
            raise subprocess.TimeoutExpired("extractor", timeout)
        return self.returncode


def frame(n: int, pts_ns: int | None = None, people: int = 2) -> dict:
    return {"frame_number": n, "pts_ns": int(n / FPS * 1e9) if pts_ns is None else pts_ns, "timestamp": n / FPS,
            "objects": [{"class_id": 2, "label": "person", "confidence": 0.9, "tracker_confidence": 0.9,
                         "track_id": str(k + 1), "bbox": {"x": 10.0 * k, "y": 1.0, "width": 10.0, "height": 20.0}}
                        for k in range(people)]}


class ConsumerAdapter(PerceptionAdapter):
    """Feeds prepared frames through the real consumer; `eof` controls whether the reader reaches EOF."""

    def __init__(self, config, frames, *, returncode: int | None = 0, eof: bool = True, mode: str = "unpaced") -> None:
        self.manager = ServiceMakerSessions(config)
        self.frames, self.returncode, self.eof, self.mode = frames, returncode, eof, mode
        self.session: _Session | None = None
        self.task: asyncio.Task | None = None

    def availability(self):
        return AdapterAvailability("consumer-harness", True, "test", runtime_detected=True)

    async def start_camera(self, camera, epoch, sink, replay_mode="paced"):
        self.session = _Session(camera.id, epoch, "aegis-harness", FakeProcess(self.returncode), Path("x"), None,
                                asyncio.Event(), frame_queue=queue.Queue(), replay_mode=self.mode)
        for record in self.frames:
            self.session.frame_queue.put(record)
        if self.eof:
            self.session.reader_done.set()
        self.task = asyncio.create_task(self.manager._consume(self.session, camera, sink))

    async def stop_camera(self, camera_id, epoch):
        if self.session is not None and self.task is not None:
            self.session.stop_event.set()
            await asyncio.gather(self.task, return_exceptions=True)

    async def shutdown(self):
        return None


def harness(tmp_path: Path, frames, **kwargs):
    directory = copy_configuration(tmp_path)
    clip = tmp_path / "cam01.mp4"
    clip.write_bytes(b"unit-test-video-fixture")
    update_yaml(directory, "cameras", lambda document: document["cameras"][0].update(source=str(clip)))
    config = load_configuration(directory, PROJECT_ROOT)
    repository = SQLiteRepository(tmp_path / "completion.db")
    repository.migrate()
    repository.seed_configuration(config)
    adapter = ConsumerAdapter(config, frames, **kwargs)
    return CameraRuntimeSupervisor(config, repository, adapter, SequenceState()), repository, adapter



def frames_in(rows) -> int:
    return sum(row["frames"] for row in rows)


def test_clean_eof_flushes_complete_final_bucket_without_stop(tmp_path: Path) -> None:
    supervisor, repository, adapter = harness(tmp_path, [frame(n) for n in range(30)])

    _scope: tuple = ()

    async def run():
        nonlocal _scope
        await supervisor.start("unpaced")
        _scope = (supervisor.current_run()[0], "cam-01", supervisor.states()["cam-01"].epoch)
        await adapter.task
        rows = repository.camera_count_history(*_scope)
        assert [(r["bucket_index"], r["frames"]) for r in rows] == [(0, 15), (1, 15)]
        state = supervisor.states()["cam-01"]
        assert state.stream_outcome == "CLEAN_EOF" and state.persisted_count_samples == 2
        assert supervisor.current_run()[1] == "RUNNING"
        supervisor.complete_stream("cam-01", _scope[2], "CLEAN_EOF")  # repeated completion: no-op
        await supervisor.stop()
        rows_after = repository.camera_count_history(*_scope)
        assert rows_after == rows

    asyncio.run(run())


def test_clean_eof_flushes_partial_final_bucket(tmp_path: Path) -> None:
    supervisor, repository, adapter = harness(tmp_path, [frame(n) for n in range(20)])

    async def run():
        await supervisor.start("unpaced")
        scope = (supervisor.current_run()[0], "cam-01", supervisor.states()["cam-01"].epoch)
        await adapter.task
        rows = repository.camera_count_history(*scope)
        assert [(r["bucket_index"], r["frames"]) for r in rows] == [(0, 15), (1, 5)]
        await supervisor.stop()
        assert repository.camera_count_history(*scope) == rows

    asyncio.run(run())


def test_clean_eof_after_discontinuity_flushes_last_segment(tmp_path: Path) -> None:
    frames = [frame(n) for n in range(20)] + [frame(20 + n, pts_ns=int(n / FPS * 1e9)) for n in range(10)]

    async def run():
        supervisor, repository, adapter = harness(tmp_path, frames)
        await supervisor.start("unpaced")
        scope = (supervisor.current_run()[0], "cam-01", supervisor.states()["cam-01"].epoch)
        await adapter.task
        rows = repository.camera_count_history(*scope)
        assert frames_in(rows) == 30
        assert {r["source_segment"] for r in rows} == {0, 1}
        assert [(r["source_segment"], r["frames"]) for r in rows if r["source_segment"] == 1] == [(1, 10)]

    asyncio.run(run())


def test_early_operator_stop_reports_no_outcome_and_flushes_once(tmp_path: Path) -> None:
    # The source never reaches EOF; the operator stops mid-stream.
    supervisor, repository, adapter = harness(tmp_path, [frame(n) for n in range(20)], eof=False, returncode=None)

    async def run():
        await supervisor.start("unpaced")
        scope = (supervisor.current_run()[0], "cam-01", supervisor.states()["cam-01"].epoch)
        for _ in range(100):
            if adapter.session.frame_queue.empty():
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        assert supervisor.states()["cam-01"].stream_outcome is None  # a stalled source is not EOF
        await supervisor.stop()
        rows = repository.camera_count_history(*scope)
        assert frames_in(rows) == 20 and len(rows) == 2
        assert adapter.session.end_outcome is None

    asyncio.run(run())


def test_abnormal_exit_is_not_treated_as_clean_eof(tmp_path: Path) -> None:
    supervisor, repository, adapter = harness(tmp_path, [frame(n) for n in range(20)], returncode=1)

    async def run():
        await supervisor.start("unpaced")
        run_id = supervisor.current_run()[0]
        scope = (run_id, "cam-01", supervisor.states()["cam-01"].epoch)
        await adapter.task
        state = supervisor.states()["cam-01"]
        assert state.stream_outcome == "ABNORMAL_TERMINATION" and "code 1" in state.stream_outcome_detail
        # The open bucket is not declared complete at an abnormal end ...
        assert frames_in(repository.camera_count_history(*scope)) == 15
        assert any(a["title"] == "Camera stream ended abnormally" for a in repository.list_alerts(run_id=run_id))
        # ... the stop flush still persists the frames that were really observed, exactly once.
        await supervisor.stop()
        assert frames_in(repository.camera_count_history(*scope)) == 20

    asyncio.run(run())


def test_eof_with_process_still_running_is_abnormal(tmp_path: Path) -> None:
    supervisor, repository, adapter = harness(tmp_path, [frame(n) for n in range(5)], returncode=None)

    async def run():
        await supervisor.start("unpaced")
        await adapter.task
        assert supervisor.states()["cam-01"].stream_outcome == "ABNORMAL_TERMINATION"
        await supervisor.stop()

    asyncio.run(run())


def test_consumer_failure_is_reported_distinctly(tmp_path: Path) -> None:
    bad = {"frame_number": 10, "objects": []}  # missing pts_ns: the consumer raises on this record
    supervisor, repository, adapter = harness(tmp_path, [frame(n) for n in range(10)] + [bad])

    async def run():
        await supervisor.start("unpaced")
        await adapter.task
        state = supervisor.states()["cam-01"]
        assert state.stream_outcome == "CONSUMER_FAILED" and "KeyError" in state.stream_outcome_detail
        await supervisor.stop()

    asyncio.run(run())


def test_completion_for_another_epoch_is_ignored(tmp_path: Path) -> None:
    supervisor, repository, adapter = harness(tmp_path, [frame(n) for n in range(20)], eof=False, returncode=None)

    async def run():
        await supervisor.start("unpaced")
        supervisor.complete_stream("cam-01", "some-other-epoch", "CLEAN_EOF")
        assert supervisor.states()["cam-01"].stream_outcome is None
        await supervisor.stop()

    asyncio.run(run())


def test_identical_frames_delivered_paced_and_unpaced_persist_identical_history(tmp_path: Path) -> None:
    """Real asynchronous delivery speeds through the real consumer and pacer (not supplied timestamps)."""
    import time

    frames = [frame(n, people=1 + (n // 7) % 3) for n in range(24)]  # 0.8 s of source time, varying counts
    results = {}
    for mode in ("paced", "unpaced"):
        directory = tmp_path / mode
        directory.mkdir()
        supervisor, repository, adapter = harness(directory, frames, mode=mode)

        async def run(supervisor=supervisor, repository=repository, adapter=adapter):
            started = time.perf_counter()
            await supervisor.start("unpaced")
            scope = (supervisor.current_run()[0], "cam-01", supervisor.states()["cam-01"].epoch)
            await adapter.task
            elapsed = time.perf_counter() - started
            rows = repository.camera_count_history(*scope)
            return elapsed, [{k: v for k, v in r.items() if k not in ("received_at_utc", "camera_epoch", "replay_mode")}
                             for r in rows], supervisor.states()["cam-01"].stream_outcome

        results[mode] = asyncio.run(run())
    paced_elapsed, paced_rows, paced_outcome = results["paced"]
    unpaced_elapsed, unpaced_rows, unpaced_outcome = results["unpaced"]
    assert paced_elapsed >= 23 / FPS * 0.95  # the real pacer actually waited ~0.77 s
    assert unpaced_elapsed < paced_elapsed / 2
    assert paced_outcome == unpaced_outcome == "CLEAN_EOF"
    assert paced_rows == unpaced_rows and sum(r["frames"] for r in paced_rows) == 24
