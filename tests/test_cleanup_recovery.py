"""Ownership of launched camera resources across startup failure, stop failure and backend restart.

FakeRuntime stands in for Docker: its container table outlives any one adapter/supervisor, so a
"restart" is a newly constructed supervisor + adapter reading the same SQLite database.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.app.configuration import load_configuration
from backend.app.db.repository import SQLiteRepository
from backend.app.perception.base import (
    AdapterAvailability, ObservationSink, PerceptionAdapter, PerceptionCleanupError, PerceptionStartError,
    ResourceIdentity,
)
from backend.app.services.runtime import CameraRuntimeSupervisor
from backend.app.services.sequence import SequenceState

from .support import PROJECT_ROOT, copy_configuration, update_yaml


class FakeRuntime:
    def __init__(self) -> None:
        self.containers: set[str] = set()
        self.unreachable = False
        self.refuse_removal = 0  # number of removal attempts that leave the container in place

    def remove(self, name: str) -> bool:
        if self.unreachable:
            raise PerceptionCleanupError(f"runtime unreachable; {name} unknown")
        if self.refuse_removal > 0:
            self.refuse_removal -= 1
            raise PerceptionCleanupError(f"{name} still present")
        self.containers.discard(name)
        return True


class ResourceAdapter(PerceptionAdapter):
    """start: 'ok' | 'fail_before_launch' | 'fail_after_launch'."""

    def __init__(self, runtime: FakeRuntime, start: str = "ok") -> None:
        self.runtime = runtime
        self.start_mode = start
        self.stop_calls: list[tuple[str, str]] = []
        self.adopted: list[ResourceIdentity] = []
        self._owned: dict[tuple[str, str], str] = {}

    def availability(self) -> AdapterAvailability:
        return AdapterAvailability("fake-runtime", True, "test adapter", runtime_detected=True)

    def resource_identity(self, camera_id: str, camera_epoch: str) -> ResourceIdentity:
        return ResourceIdentity("container", f"aegis-{camera_id}-{camera_epoch}", "fake-runtime")

    async def start_camera(self, camera, camera_epoch: str, sink: ObservationSink, replay_mode: str = "paced") -> None:
        name = self.resource_identity(camera.id, camera_epoch).name
        if self.start_mode == "fail_before_launch":
            raise PerceptionStartError("model profile rejected before launch", resource_allocated=False)
        self.runtime.containers.add(name)
        self._owned[(camera.id, camera_epoch)] = name
        if self.start_mode == "fail_after_launch":
            raise PerceptionStartError("no metadata before startup deadline", resource_allocated=True)

    async def stop_camera(self, camera_id: str, camera_epoch: str) -> None:
        self.stop_calls.append((camera_id, camera_epoch))
        name = self._owned.get((camera_id, camera_epoch)) or self.resource_identity(camera_id, camera_epoch).name
        if name in self.runtime.containers or self.runtime.unreachable:
            self.runtime.remove(name)
        self._owned.pop((camera_id, camera_epoch), None)

    async def adopt_resource(self, camera_id: str, camera_epoch: str, identity: ResourceIdentity) -> None:
        self.adopted.append(identity)
        self._owned[(camera_id, camera_epoch)] = identity.name

    async def shutdown(self) -> None:
        return None


def environment(tmp_path: Path):
    directory = copy_configuration(tmp_path)
    clip = tmp_path / "cam01.mp4"
    clip.write_bytes(b"unit-test-video-fixture")
    update_yaml(directory, "cameras", lambda document: document["cameras"][0].update(source=str(clip)))
    config = load_configuration(directory, PROJECT_ROOT)
    database = tmp_path / "ownership.db"
    repository = SQLiteRepository(database)
    repository.migrate()
    repository.seed_configuration(config)
    return config, database


def supervisor_for(config, database, adapter):
    repository = SQLiteRepository(database)
    repository.migrate()
    supervisor = CameraRuntimeSupervisor(config, repository, adapter, SequenceState())
    return supervisor, repository


def epoch_of(supervisor) -> str:
    return supervisor.states()["cam-01"].epoch


# ------------------------------------------------------------------ 1. startup failures


def test_failure_before_launch_owns_nothing_and_stops_cleanly(tmp_path: Path) -> None:
    config, database = environment(tmp_path)
    runtime = FakeRuntime()
    adapter = ResourceAdapter(runtime, start="fail_before_launch")
    supervisor, repository = supervisor_for(config, database, adapter)

    async def scenario() -> None:
        await supervisor.start()
        assert supervisor.current_run()[1] == "DEGRADED"
        assert repository.unreleased_resources() == []
        await supervisor.stop()
        assert supervisor.current_run()[1] == "STOPPED"
        assert adapter.stop_calls == []  # nothing was launched, nothing to release

    asyncio.run(scenario())


def test_failure_after_launch_is_released_before_stopped(tmp_path: Path) -> None:
    config, database = environment(tmp_path)
    runtime = FakeRuntime()
    adapter = ResourceAdapter(runtime, start="fail_after_launch")
    supervisor, repository = supervisor_for(config, database, adapter)

    async def scenario() -> None:
        await supervisor.start()
        assert supervisor.current_run()[1] == "DEGRADED"
        # Launch-time ownership existed and was reconciled through the normal stop path.
        assert runtime.containers == set()
        assert adapter.stop_calls  # the supervisor asked the adapter to release the launched resource
        assert repository.unreleased_resources() == []
        await supervisor.stop()
        assert supervisor.current_run()[1] == "STOPPED"

    asyncio.run(scenario())


def test_failure_after_launch_with_unconfirmed_cleanup_blocks_until_retry(tmp_path: Path) -> None:
    config, database = environment(tmp_path)
    runtime = FakeRuntime()
    runtime.refuse_removal = 2  # the start-time reconciliation and the first operator stop both fail
    adapter = ResourceAdapter(runtime, start="fail_after_launch")
    supervisor, repository = supervisor_for(config, database, adapter)

    async def scenario() -> None:
        await supervisor.start()
        epoch = epoch_of(supervisor)
        name = f"aegis-cam-01-{epoch}"
        assert runtime.containers == {name}
        unreleased = repository.unreleased_resources()
        assert [(r["camera_id"], r["camera_epoch"], r["resource_name"], r["state"]) for r in unreleased] == [
            ("cam-01", epoch, name, "UNRESOLVED")]
        await supervisor.stop()
        assert supervisor.current_run()[1] == "STOP_FAILED"  # never STOPPED while the container may run
        with pytest.raises(PerceptionCleanupError, match="STOP_FAILED"):
            await supervisor.start()  # refused (HTTP 409): no new epoch over an unresolved resource
        assert epoch_of(supervisor) in (None, epoch)
        await supervisor.stop()  # retry succeeds
        assert supervisor.current_run()[1] == "STOPPED"
        assert runtime.containers == set()
        assert repository.unreleased_resources() == []

    asyncio.run(scenario())


# ------------------------------------------------------------------ 2. restart recovery


def leave_stop_failed(config, database, runtime) -> tuple[str, str]:
    runtime.refuse_removal = 1
    adapter = ResourceAdapter(runtime)
    supervisor, _ = supervisor_for(config, database, adapter)

    async def scenario() -> tuple[str, str]:
        await supervisor.start()
        run_id, epoch = supervisor.current_run()[0], epoch_of(supervisor)
        await supervisor.stop()
        assert supervisor.current_run()[1] == "STOP_FAILED"
        return run_id, epoch

    return asyncio.run(scenario())


def test_restart_with_unreachable_runtime_keeps_stop_failed_and_blocks_new_runs(tmp_path: Path) -> None:
    config, database = environment(tmp_path)
    runtime = FakeRuntime()
    run_id, epoch = leave_stop_failed(config, database, runtime)
    runtime.unreachable = True
    adapter = ResourceAdapter(runtime)
    supervisor, repository = supervisor_for(config, database, adapter)

    async def scenario() -> None:
        await supervisor.recover()
        assert repository.get_run(run_id)["status"] == "STOP_FAILED"
        assert supervisor.current_run()[:2] == (run_id, "STOP_FAILED")
        assert [i.name for i in adapter.adopted] == [f"aegis-cam-01-{epoch}"]
        with pytest.raises(PerceptionCleanupError, match="STOP_FAILED"):
            await supervisor.start()
        assert supervisor.current_run()[0] == run_id  # no new run while unresolved
        assert adapter.stop_calls == [("cam-01", epoch)]  # one bounded reconciliation attempt at recovery
        runtime.unreachable = False
        await supervisor.stop()  # explicit retry
        assert supervisor.current_run()[1] == "STOPPED"
        assert repository.get_run(run_id)["status"] == "STOPPED"
        assert runtime.containers == set() and repository.unreleased_resources() == []

    asyncio.run(scenario())


def test_restart_recovers_when_container_is_already_gone(tmp_path: Path) -> None:
    config, database = environment(tmp_path)
    runtime = FakeRuntime()
    run_id, epoch = leave_stop_failed(config, database, runtime)
    runtime.containers.clear()  # e.g. removed by the operator or by docker --rm meanwhile
    adapter = ResourceAdapter(runtime)
    supervisor, repository = supervisor_for(config, database, adapter)

    async def scenario() -> None:
        await supervisor.recover()
        assert repository.get_run(run_id)["status"] == "STOPPED"
        assert supervisor.current_run()[1] in ("IDLE", "STOPPED")
        assert repository.unreleased_resources() == []
        await supervisor.start()
        assert supervisor.current_run()[1] == "RUNNING" and supervisor.current_run()[0] != run_id

    asyncio.run(scenario())


def test_restart_reconciles_a_run_that_was_active_when_the_backend_died(tmp_path: Path) -> None:
    config, database = environment(tmp_path)
    runtime = FakeRuntime()
    first = ResourceAdapter(runtime)
    supervisor, _ = supervisor_for(config, database, first)

    async def crash() -> tuple[str, str]:
        await supervisor.start()  # backend "dies" here: no stop, container still running
        return supervisor.current_run()[0], epoch_of(supervisor)

    run_id, epoch = asyncio.run(crash())
    assert runtime.containers == {f"aegis-cam-01-{epoch}"}
    adapter = ResourceAdapter(runtime)
    restarted, repository = supervisor_for(config, database, adapter)

    async def scenario() -> None:
        await restarted.recover()
        assert adapter.stop_calls == [("cam-01", epoch)]
        assert runtime.containers == set()
        assert repository.get_run(run_id)["status"] == "STOPPED"
        assert repository.unreleased_resources() == []

    asyncio.run(scenario())


def test_restart_reconciles_a_failed_start_resource(tmp_path: Path) -> None:
    config, database = environment(tmp_path)
    runtime = FakeRuntime()
    runtime.refuse_removal = 1
    adapter = ResourceAdapter(runtime, start="fail_after_launch")
    supervisor, _ = supervisor_for(config, database, adapter)

    async def first_life() -> tuple[str, str]:
        await supervisor.start()
        return supervisor.current_run()[0], epoch_of(supervisor)

    run_id, epoch = asyncio.run(first_life())
    restarted, repository = supervisor_for(config, database, ResourceAdapter(runtime))

    async def scenario() -> None:
        await restarted.recover()
        assert runtime.containers == set()
        assert repository.unreleased_resources() == []
        assert repository.get_run(run_id)["status"] == "STOPPED"

    asyncio.run(scenario())


def test_recovery_never_touches_resources_it_does_not_own(tmp_path: Path) -> None:
    config, database = environment(tmp_path)
    runtime = FakeRuntime()
    runtime.containers |= {"aegis-cam-01-someone-else", "postgres"}
    adapter = ResourceAdapter(runtime)
    supervisor, _ = supervisor_for(config, database, adapter)

    async def scenario() -> None:
        await supervisor.recover()
        await supervisor.start()
        await supervisor.stop()

    asyncio.run(scenario())
    assert runtime.containers == {"aegis-cam-01-someone-else", "postgres"}


# ------------------------------------------------------------------ 3. cancellation and the HTTP contract


class SlowStartAdapter(ResourceAdapter):
    """Launches the resource, then blocks until cancelled (e.g. shutdown during startup)."""

    def __init__(self, runtime: FakeRuntime) -> None:
        super().__init__(runtime)
        self.launched = asyncio.Event()

    async def start_camera(self, camera, camera_epoch: str, sink: ObservationSink, replay_mode: str = "paced") -> None:
        name = self.resource_identity(camera.id, camera_epoch).name
        self.runtime.containers.add(name)
        self._owned[(camera.id, camera_epoch)] = name
        self.launched.set()
        await asyncio.sleep(3600)


@pytest.mark.parametrize("refuse", [0, 1])
def test_cancelled_start_after_launch_is_reconciled_never_false_stopped(tmp_path: Path, refuse: int) -> None:
    config, database = environment(tmp_path)
    runtime = FakeRuntime()
    adapter = SlowStartAdapter(runtime)
    supervisor, repository = supervisor_for(config, database, adapter)

    async def scenario() -> None:
        task = asyncio.create_task(supervisor.start())
        await adapter.launched.wait()
        runtime.refuse_removal = refuse
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(adapter.stop_calls) == 1  # the cancelled start released what it launched
        if refuse:
            assert len(runtime.containers) == 1
            assert [r["state"] for r in repository.unreleased_resources()] == ["UNRESOLVED"]
            assert supervisor.cleanup_failures().keys() == {"cam-01"}  # still owned, retried by stop
        else:
            assert runtime.containers == set()
            assert repository.unreleased_resources() == []
        await supervisor.stop()
        assert supervisor.current_run()[1] == "STOPPED"
        assert runtime.containers == set() and repository.unreleased_resources() == []

    asyncio.run(scenario())


def test_http_start_contract_idempotent_when_healthy_409_when_stop_failed(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from backend.app.main import create_app

    config, database = environment(tmp_path)
    runtime = FakeRuntime()
    adapter = ResourceAdapter(runtime)
    app = create_app(config_dir=config.config_dir, database_path=database, perception_adapter=adapter)
    with TestClient(app) as client:
        first = client.post("/api/v1/demo/start")
        assert first.status_code == 200 and first.json()["run_status"] == "RUNNING"
        again = client.post("/api/v1/demo/start")  # healthy run: idempotent, no new epoch
        assert again.status_code == 200 and again.json()["run_id"] == first.json()["run_id"]
        assert len(runtime.containers) == 1
        runtime.refuse_removal = 1
        assert client.post("/api/v1/demo/stop").json()["run_status"] == "STOP_FAILED"
        refused = client.post("/api/v1/demo/start")
        assert refused.status_code == 409
        error = refused.json()["error"]
        assert error["code"] == "CLEANUP_UNCONFIRMED"
        assert error["details"]["run_status"] == "STOP_FAILED" and error["details"]["cameras"] == ["cam-01"]
        assert len(runtime.containers) == 1  # nothing new launched
        reset = client.post("/api/v1/demo/reset")  # reset retries cleanup through stop; succeeds now
        assert reset.status_code == 200 and reset.json()["run_status"] == "IDLE"
        second = client.post("/api/v1/demo/start")
        assert second.status_code == 200 and second.json()["run_id"] != first.json()["run_id"]
        client.post("/api/v1/demo/stop")
    assert runtime.containers == set()
