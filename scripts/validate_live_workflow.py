#!/usr/bin/env python3
"""REAL single-camera application workflow: video -> DeepStream/NvDCF -> source-time analytics -> SQLite -> REST/WebSocket.

Two real runs of demo/cam01.mp4 through the in-process FastAPI app, the supervisor and the pinned
WSL/Docker Service Maker container:

1. paced replay (demo mode) to natural EOS: pacing against source time, live counts over REST and
   WebSocket, visible-count history persisted *during* the run, stale state after EOS, then stop;
2. unpaced replay (evaluation mode) to natural EOS: throughput disclosure.

Recorded real detections (the paced run's extractor JSONL) are replayed twice, with distinct scopes:
- *supplied receive timelines*: batches carry receive timestamps spaced 0 s and 0.05 s apart but are
  emitted back to back, which proves the analytics ignore receive time, not two delivery speeds;
- *real delivery speeds*: the same records go through the real ServiceMakerSessions._consume loop
  paced (real asyncio waits on the source timeline, about 23.7 s) and unpaced, ending in a clean EOF.
  The measured wall durations are asserted, and the persisted history, including the EOF-flushed
  final bucket, must be identical.
Raw perception agreement between the two GPU runs is a measurement only: the pinned FP16 TensorRT +
NvDCF pipeline is not bit-deterministic run to run, so identical recorded observations are compared.

Every run writes to a new evidence directory (scripts/lib/evidence_dirs.py). This is structural
integration evidence, not detection/tracking/count accuracy.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
import subprocess
import sys
import asyncio
import time
import traceback
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))
from backend.app.main import create_app  # noqa: E402
from evidence_dirs import EvidenceExistsError, resolve_run_dir  # noqa: E402
from compare_perception_runs import compare as compare_perception  # noqa: E402

CAMERA = "cam-01"
SOURCE_SECONDS = 23.69  # ffprobe duration of demo/cam01.mp4 (710 frames at 30000/1001)
DISTRO = "Ubuntu-22.04"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def camera(snapshot: dict[str, Any]) -> dict[str, Any]:
    return next(item for item in snapshot["cameras"] if item["id"] == CAMERA)


def container_absent(name: str) -> dict[str, Any]:
    try:
        result = subprocess.run(["wsl.exe", "-d", DISTRO, "-e", "docker", "ps", "-a", "--filter", f"name={name}",
                                 "--format", "{{.Names}}"], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"absent": False, "error": str(exc)}
    names = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return {"absent": result.returncode == 0 and name not in names, "returncode": result.returncode, "names_seen": sorted(names)}


def persisted_rate(history: dict[str, Any]) -> float | None:
    """Source seconds advanced per wall second, from persist times of history buckets.

    The last bucket is flushed at stop and is excluded; persist time follows bucket completion.
    """
    from datetime import datetime
    rows = history["samples"][:-1]
    if len(rows) < 3:
        return None
    first, last = rows[0], rows[-1]
    t0 = datetime.fromisoformat(first["received_at_utc"].replace("Z", "+00:00"))
    t1 = datetime.fromisoformat(last["received_at_utc"].replace("Z", "+00:00"))
    wall = (t1 - t0).total_seconds()
    return (last["source_time_s"] - first["source_time_s"]) / wall if wall > 0 else None


def replay_recorded_observations(jsonl: Path, wall_steps: tuple[float, ...], work: Path) -> list[Any]:
    """Feed real recorded frames through the live observation path + supervisor at different wall speeds."""
    from backend.app.analytics.timebase import SourceTimeline
    from backend.app.configuration import load_configuration
    from backend.app.db.repository import SQLiteRepository
    from backend.app.perception.base import AdapterAvailability, ObservationBatch, PerceptionAdapter
    from backend.app.perception.service_maker import PersonClass, ServiceMakerSessions
    from backend.app.services.runtime import CameraRuntimeSupervisor
    from backend.app.services.sequence import SequenceState

    class RecordedAdapter(PerceptionAdapter):
        def __init__(self) -> None:
            self.sink = None

        def availability(self) -> AdapterAvailability:
            return AdapterAvailability("recorded-jsonl-replay", True, "Recorded real extractor output", runtime_detected=False)

        async def start_camera(self, camera, camera_epoch, sink, replay_mode="paced") -> None:
            self.sink = sink

        async def stop_camera(self, camera_id, camera_epoch) -> None:
            self.sink = None

        async def shutdown(self) -> None:
            return None

    records = [json.loads(line) for line in jsonl.open(encoding="utf-8")]
    outputs = []
    for index, step in enumerate(wall_steps):
        config = load_configuration(ROOT / "configs", ROOT)
        repository = SQLiteRepository(work / f"replay-{index}.db")
        repository.migrate()
        repository.seed_configuration(config)
        adapter = RecordedAdapter()
        supervisor = CameraRuntimeSupervisor(config, repository, adapter, SequenceState())
        camera_config = config.camera_map[CAMERA]

        async def run() -> list[dict[str, Any]]:
            await supervisor.start("unpaced")
            run_id = supervisor.current_run()[0]
            epoch = supervisor.states()[CAMERA].epoch
            timeline = SourceTimeline()
            received = time.monotonic()
            for record in records:
                source = timeline.observe(record["pts_ns"])
                observations = tuple(o for obj in record["objects"]
                                     for o in [ServiceMakerSessions._observation(obj, camera_config, epoch, source.seconds, source.segment, person=PersonClass.of(config.models.selected.runtime_profile))]
                                     if o is not None)
                await adapter.sink.emit(ObservationBatch(
                    camera_id=CAMERA, camera_epoch=epoch, observations=observations, received_at=received,
                    source_time=source.seconds, source_pts_ns=record["pts_ns"], source_segment=source.segment,
                    source_discontinuity=source.discontinuity, frame_number=record["frame_number"],
                    received_at_utc="recorded", replay_mode="recorded"))
                received += step
            await supervisor.stop()
            rows = repository.camera_count_history(run_id, CAMERA, epoch)
            return [{k: v for k, v in row.items() if k not in ("received_at_utc", "camera_epoch")} for row in rows]

        outputs.append(asyncio.run(run()))
    return outputs


def replay_through_consumer(jsonl: Path, mode: str, work: Path) -> dict[str, Any]:
    """Deliver recorded frames through the real consumer/pacer with real waits; returns timing + history."""
    import queue as queue_module

    from backend.app.configuration import load_configuration
    from backend.app.db.repository import SQLiteRepository
    from backend.app.perception.base import AdapterAvailability, PerceptionAdapter
    from backend.app.perception.service_maker import ServiceMakerSessions, _Session
    from backend.app.services.runtime import CameraRuntimeSupervisor
    from backend.app.services.sequence import SequenceState

    class ExitedProcess:
        returncode = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    records = [json.loads(line) for line in jsonl.open(encoding="utf-8")]

    class ConsumerAdapter(PerceptionAdapter):
        def __init__(self, config) -> None:
            self.manager = ServiceMakerSessions(config)
            self.task = None
            self.session = None

        def availability(self):
            return AdapterAvailability("recorded-consumer", True, "Recorded real frames via the real consumer")

        async def start_camera(self, camera, epoch, sink, replay_mode="paced"):
            self.session = _Session(camera.id, epoch, "aegis-recorded", ExitedProcess(), jsonl, None, asyncio.Event(),
                                    frame_queue=queue_module.Queue(), replay_mode=mode)
            for record in records:
                self.session.frame_queue.put(record)
            self.session.reader_done.set()  # the recorded stream ends in a clean EOF
            self.task = asyncio.create_task(self.manager._consume(self.session, camera, sink))

        async def stop_camera(self, camera_id, epoch):
            return None

        async def shutdown(self):
            return None

    config = load_configuration(ROOT / "configs", ROOT)
    repository = SQLiteRepository(work / f"consumer-{mode}.db")
    repository.migrate()
    repository.seed_configuration(config)
    adapter = ConsumerAdapter(config)
    supervisor = CameraRuntimeSupervisor(config, repository, adapter, SequenceState())

    async def run() -> dict[str, Any]:
        await supervisor.start(mode)
        run_id, epoch = supervisor.current_run()[0], supervisor.states()[CAMERA].epoch
        started = time.perf_counter()
        await adapter.task
        elapsed = time.perf_counter() - started
        outcome = supervisor.states()[CAMERA].stream_outcome
        rows = repository.camera_count_history(run_id, CAMERA, epoch)  # read before any stop: EOF flush only
        await supervisor.stop()
        after_stop = repository.camera_count_history(run_id, CAMERA, epoch)
        strip = lambda items: [{k: v for k, v in r.items() if k not in ("received_at_utc", "camera_epoch", "replay_mode")}  # noqa: E731
                               for r in items]
        return {"mode": mode, "wall_seconds": elapsed, "outcome": outcome, "rows": strip(rows),
                "stop_added_rows": len(after_stop) - len(rows), "frames": sum(r["frames"] for r in rows)}

    return asyncio.run(run())


def run_to_eos(client: TestClient, mode: str, checks: dict[str, bool], observed: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    prefix = mode
    t0 = time.perf_counter()
    response = client.post(f"/api/v1/demo/start?replay_mode={mode}")
    start_seconds = time.perf_counter() - t0
    body = response.json()
    cam = camera(body)
    epoch = cam.get("epoch")
    checks[f"{prefix}_start_ok"] = response.status_code == 200 and body.get("run_status") == "RUNNING" and bool(epoch)
    run_id = body.get("run_id")
    samples: list[dict[str, Any]] = []
    history_during_run: int | None = None
    deadline = time.perf_counter() + 120
    stale_snapshot = None
    while time.perf_counter() < deadline:
        snapshot = client.get("/api/v1/facility/snapshot").json()
        current = camera(snapshot)
        now = time.perf_counter() - t0
        samples.append({"wall_s": now, "source_position_s": current.get("source_position_s"),
                        "visible_count": current.get("visible_count"), "analytics_fps": current.get("analytics_fps"),
                        "source_fps": current.get("source_fps"), "health": current.get("health"),
                        "replay_mode": current.get("replay_mode"), "persisted": current.get("persisted_count_samples"),
                        "backend_queue_ms": current.get("backend_queue_ms"), "segment": current.get("source_segment")})
        position = current.get("source_position_s")
        if history_during_run is None and isinstance(position, (int, float)) and position > 8:
            history = client.get(f"/api/v1/cameras/{CAMERA}/count-history").json()
            history_during_run = len(history["samples"])
            observed[f"{prefix}_history_during_run"] = {"source_position_s": position, "samples": history_during_run}
        live_positions = [s for s in samples if isinstance(s["source_position_s"], (int, float))]
        if live_positions and current.get("source_position_s") is None and current.get("health") == "DEGRADED":
            stale_snapshot = current
            break
        time.sleep(0.25)
    (run_dir / f"{prefix}-snapshot-samples.json").write_text(json.dumps(samples, indent=1) + "\n", encoding="utf-8")
    live = [s for s in samples if isinstance(s["source_position_s"], (int, float))]
    checks[f"{prefix}_real_positive_counts"] = any(isinstance(s["visible_count"], int) and s["visible_count"] > 0 for s in live)
    checks[f"{prefix}_replay_mode_reported"] = bool(live) and all(s["replay_mode"] == mode for s in live)
    checks[f"{prefix}_history_persisted_during_run"] = bool(history_during_run)
    checks[f"{prefix}_natural_eos_goes_stale_not_zero"] = (
        stale_snapshot is not None and stale_snapshot.get("visible_count") is None and stale_snapshot.get("source_position_s") is None
    )
    first, last = (live[0], live[-1]) if live else (None, None)
    if first and last and last["wall_s"] > first["wall_s"]:
        slope = (last["source_position_s"] - first["source_position_s"]) / (last["wall_s"] - first["wall_s"])
    else:
        slope = None
    fps_values = [s["analytics_fps"] for s in live if isinstance(s["analytics_fps"], (int, float))]
    queue_values = [s["backend_queue_ms"] for s in live if isinstance(s["backend_queue_ms"], (int, float))]
    observed[f"{prefix}_timing"] = {
        "start_http_seconds": start_seconds,
        "source_seconds_per_wall_second": slope,
        "live_window_wall_seconds": (last["wall_s"] - first["wall_s"]) if live else None,
        "last_source_position_s": last["source_position_s"] if live else None,
        "analytics_fps_median": sorted(fps_values)[len(fps_values) // 2] if fps_values else None,
        "analytics_fps_max": max(fps_values) if fps_values else None,
        "backend_queue_ms_median": sorted(queue_values)[len(queue_values) // 2] if queue_values else None,
        "snapshot_samples": len(samples),
        "stale_snapshot": stale_snapshot,
    }
    history = client.get(f"/api/v1/cameras/{CAMERA}/count-history").json()
    # Clean EOF must complete the history without an operator stop (review finding 3).
    eof_outcome = stale_snapshot.get("stream_outcome") if stale_snapshot else None
    if eof_outcome is None:
        eof_outcome = camera(client.get("/api/v1/facility/snapshot").json()).get("stream_outcome")
    frames_before_stop = sum(sample["frames"] for sample in history["samples"])
    observed[f"{prefix}_eof"] = {"stream_outcome": eof_outcome, "frames_in_history_before_stop": frames_before_stop,
                                 "buckets_before_stop": len(history["samples"])}
    checks[f"{prefix}_clean_eof_reported"] = eof_outcome == "CLEAN_EOF"
    checks[f"{prefix}_all_frames_in_history_before_stop"] = frames_before_stop == 710
    t1 = time.perf_counter()
    stopped = client.post("/api/v1/demo/stop")
    stop_seconds = time.perf_counter() - t1
    checks[f"{prefix}_stop_ok"] = stopped.status_code == 200 and stopped.json().get("run_status") == "STOPPED"
    container = f"aegis-{CAMERA}-{epoch}"
    absence = container_absent(container)
    checks[f"{prefix}_exact_container_absent"] = absence["absent"]
    final_history = client.get(f"/api/v1/cameras/{CAMERA}/count-history", params={"run_id": run_id, "epoch": epoch}).json()
    observed[f"{prefix}_timing"]["persisted_source_seconds_per_wall_second"] = persisted_rate(final_history)
    first_queue = next((s["backend_queue_ms"] for s in samples if isinstance(s["backend_queue_ms"], (int, float))), None)
    observed[f"{prefix}_timing"]["first_observed_backend_queue_ms"] = first_queue
    observed[f"{prefix}_stop"] = {"http_seconds": stop_seconds, "container": container, **absence}
    observed[f"{prefix}_history_before_stop_samples"] = len(history["samples"])
    checks[f"{prefix}_stop_after_eof_adds_no_bucket"] = len(final_history["samples"]) == len(history["samples"])
    # The run used the CONFIGURED extractor (identity recorded at start) with the pacing this mode requires.
    pipeline = client.app.state.services.config.models.pipeline
    provenance = client.get(f"/api/v1/runs/{run_id}/provenance").json()
    extractor_check = next((c for c in provenance.get("artifact_checks", []) if c.get("name") == "pipeline.extractor"), {})
    summary_path = ROOT / "backend" / "var" / "perception" / CAMERA / f"{epoch}.summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    expected_pace = "source" if mode == "paced" and pipeline.extractor_pacing == "source" else "none"
    observed[f"{prefix}_extractor"] = {
        "configured_path": str(pipeline.extractor_path), "provenance_check": extractor_check,
        "summary": {k: summary.get(k) for k in ("extractor_version", "schema_version", "pace", "pace_lead_ms", "frames_written",
                                                "clean_eos", "pace_total_wait_seconds", "pace_frames_late_over_1s")},
        "expected_pace": expected_pace,
    }
    checks[f"{prefix}_run_provenance_extractor_matches_configuration"] = (
        extractor_check.get("status") == "MATCH" and extractor_check.get("expected_sha256") == pipeline.extractor_sha256
        and Path(extractor_check.get("path", "")).name == pipeline.extractor_path.name)
    checks[f"{prefix}_extractor_ran_with_configured_pacing"] = (
        summary.get("schema_version") == pipeline.extractor_schema_version and summary.get("pace") == expected_pace
        and summary.get("frames_written") == 710 and summary.get("clean_eos") is True)
    (run_dir / f"{prefix}-count-history.json").write_text(json.dumps(final_history, indent=1) + "\n", encoding="utf-8")
    return {"run_id": run_id, "epoch": epoch, "history": final_history, "slope": slope}


def hardware_restart_recovery(run_dir: Path) -> dict[str, Any]:
    """A backend dies with its container running; a new backend on the same database must reconcile it."""
    database = run_dir / "restart-recovery.db"
    # The first backend runs in its own process and is hard-killed mid-run, like a real crash: its pipes
    # close with it. (An in-process "abandon" left a live, non-draining reader that blocked Docker.)
    crashed = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "repro" / "abandon_backend.py"), str(database)],
                               cwd=ROOT, stdout=subprocess.PIPE, stderr=(run_dir / "crashed-backend.log").open("w"), text=True)
    identity = json.loads(crashed.stdout.readline())
    run_id, epoch = identity["run_id"], identity["epoch"]
    name = f"aegis-{CAMERA}-{epoch}"
    time.sleep(2)
    subprocess.run(["taskkill", "/PID", str(crashed.pid), "/T", "/F"], capture_output=True, check=False)
    crashed.wait(30)
    from backend.app.db.repository import SQLiteRepository
    recorded = [dict(row) for row in SQLiteRepository(database).unreleased_resources()]
    present_before = container_absent(name)
    started = time.perf_counter()
    second = create_app(database_path=database)
    with TestClient(second) as client:  # lifespan runs recover() before serving
        recovery_seconds = time.perf_counter() - started
        snapshot = client.get("/api/v1/facility/snapshot").json()
        repository = second.state.services.repository
        unreleased_after = repository.unreleased_resources()
        run_status = repository.get_run(run_id)["status"]
    absent_after = container_absent(name)
    return {
        "run_id": run_id, "container": name, "recorded_before_crash": recorded,
        "container_present_before_recovery": not present_before["absent"],
        "recovery_seconds_including_app_start": recovery_seconds, "unreleased_after_recovery": unreleased_after,
        "persisted_run_status_after_recovery": run_status, "snapshot_run_status": snapshot.get("run_status"),
        "container_absent_after_recovery": absent_after["absent"],
    }


def main() -> int:
    try:
        run = resolve_run_dir(ROOT, "live-workflow-cam01", "AEGIS_LIVE_WORKFLOW_RUN_DIR")
    except EvidenceExistsError as exc:
        raise SystemExit(str(exc)) from exc
    run_dir = run.directory
    checks: dict[str, bool] = {}
    observed: dict[str, Any] = {}
    errors: list[str] = []
    app = create_app(database_path=run_dir / "workflow.db")
    try:
        with TestClient(app) as client:
            config = app.state.services.config
            # Identities of what the SELECTED configuration actually uses: recorded expected hash, recomputed
            # current hash, and their comparison (never a hard-coded file name).
            from backend.app.perception.artifacts import HashMemo, runtime_artifact_checks
            artifact_checks = [c.as_dict() if hasattr(c, "as_dict") else c.__dict__ for c in runtime_artifact_checks(config, HashMemo())]
            camera_config = config.camera_map[CAMERA]
            observed["identity"] = {
                "configuration_revision": config.configuration_revision,
                "selected_model": config.models.selected_model, "selection_mode": config.models.selection_mode,
                "source": str(camera_config.source_path), "source_sha256": sha256(camera_config.source_path),
                "artifacts": artifact_checks,
                "python": sys.version.split()[0],
            }
            checks["configured_artifacts_all_match"] = bool(artifact_checks) and all(c["status"] == "MATCH" for c in artifact_checks)
            with client.websocket_connect("/api/v1/live") as websocket:
                initial = websocket.receive_json()
                checks["websocket_initial_snapshot"] = initial.get("type") == "snapshot"
                paced = run_to_eos(client, "paced", checks, observed, run_dir)
                # Live channel delivered real camera state during the run (drain what was queued).
                messages = []
                deadline = time.perf_counter() + 5
                while time.perf_counter() < deadline and len(messages) < 400:
                    messages.append(websocket.receive_json())
                live_counts = [camera(m["data"]).get("visible_count") for m in messages if m.get("type") == "snapshot"]
                observed["websocket"] = {"messages_read_after_run": len(messages),
                                         "snapshot_messages": len(live_counts),
                                         "positive_count_messages": sum(isinstance(c, int) and c > 0 for c in live_counts)}
                checks["websocket_delivered_positive_live_counts"] = any(isinstance(c, int) and c > 0 for c in live_counts)
            unpaced = run_to_eos(client, "unpaced", checks, observed, run_dir)

            paced_rate = observed["paced_timing"]["persisted_source_seconds_per_wall_second"]
            unpaced_rate = observed["unpaced_timing"]["persisted_source_seconds_per_wall_second"]
            checks["paced_follows_source_time"] = paced_rate is not None and 0.95 <= paced_rate <= 1.05
            checks["unpaced_faster_than_source_time"] = unpaced_rate is not None and unpaced_rate > 1.5
            checks["start_does_not_stall_consumer"] = all(
                isinstance(observed[f"{m}_timing"]["first_observed_backend_queue_ms"], (int, float))
                and observed[f"{m}_timing"]["first_observed_backend_queue_ms"] < 2000 for m in ("paced", "unpaced"))
            p_samples, u_samples = paced["history"]["samples"], unpaced["history"]["samples"]
            checks["paced_history_covers_clip"] = bool(p_samples) and p_samples[-1]["source_time_s"] >= SOURCE_SECONDS - 1.0
            checks["history_single_segment"] = {s["source_segment"] for s in p_samples} == {0}
            checks["paced_unpaced_identical_source_time_buckets"] = (
                sorted(s["bucket_index"] for s in p_samples) == sorted(s["bucket_index"] for s in u_samples))
            perception_dir = ROOT / "backend" / "var" / "perception" / CAMERA
            paced_jsonl = perception_dir / f"{paced['epoch']}.jsonl"
            unpaced_jsonl = perception_dir / f"{unpaced['epoch']}.jsonl"
            observed["raw_perception_agreement_paced_vs_unpaced"] = compare_perception(paced_jsonl, unpaced_jsonl)
            replay_started = time.perf_counter()
            fast, slow = replay_recorded_observations(paced_jsonl, (0.0, 0.05), run_dir)
            observed["recorded_real_replay_supplied_receive_timelines"] = {
                "source": str(paced_jsonl.relative_to(ROOT)), "receive_timestamp_spacing_s": [0.0, 0.05],
                "buckets": len(fast), "identical": fast == slow, "wall_seconds_for_both": time.perf_counter() - replay_started,
                "scope": "Batches are emitted back to back with different supplied receive timestamps; this proves the "
                         "analytics ignore receive time. It is NOT a test of two real delivery speeds.",
            }
            checks["recorded_real_observations_identical_under_two_supplied_receive_timelines"] = bool(fast) and fast == slow
            paced_delivery = replay_through_consumer(paced_jsonl, "paced", run_dir)
            unpaced_delivery = replay_through_consumer(paced_jsonl, "unpaced", run_dir)
            observed["recorded_real_delivery_through_consumer"] = {
                mode_result["mode"]: {k: v for k, v in mode_result.items() if k != "rows"} | {"buckets": len(mode_result["rows"])}
                for mode_result in (paced_delivery, unpaced_delivery)
            }
            checks["recorded_paced_delivery_actually_waited"] = paced_delivery["wall_seconds"] >= 0.95 * (SOURCE_SECONDS - 1 / 30)
            checks["recorded_unpaced_delivery_much_faster"] = unpaced_delivery["wall_seconds"] < paced_delivery["wall_seconds"] / 5
            checks["recorded_delivery_speeds_identical_history_incl_eof_bucket"] = (
                paced_delivery["rows"] == unpaced_delivery["rows"] and paced_delivery["frames"] == unpaced_delivery["frames"] == 710
                and paced_delivery["outcome"] == unpaced_delivery["outcome"] == "CLEAN_EOF"
                and paced_delivery["stop_added_rows"] == unpaced_delivery["stop_added_rows"] == 0)
            live_vs_replay = [r for r in fast]
            # bucket_span/buckets_present are API presentation fields (1/1 when not downsampled), not persisted data.
            paced_rows = [{k: v for k, v in row.items() if k not in ("received_at_utc", "camera_epoch", "replay_mode",
                                                                     "bucket_span", "buckets_present")} for row in p_samples]
            observed["recorded_replay_matches_live_paced_history"] = (
                [{k: v for k, v in row.items() if k != "replay_mode"} for row in live_vs_replay] == paced_rows)
            checks["recorded_replay_matches_live_paced_history"] = observed["recorded_replay_matches_live_paced_history"]
            ownership = app.state.services.repository
            with ownership._connection() as connection:
                rows = [dict(r) for r in connection.execute(
                    "SELECT camera_epoch, resource_name, runtime, state, attempts FROM owned_resources ORDER BY created_at")]
            observed["owned_resources"] = rows
            checks["owned_resources_recorded_and_released"] = (
                len(rows) == 2 and all(r["state"] == "RELEASED" and r["resource_name"] == f"aegis-{CAMERA}-{r['camera_epoch']}"
                                       and r["runtime"] == f"docker@wsl:{DISTRO}" for r in rows))
            crossings = client.get(f"/api/v1/cameras/{CAMERA}/line-crossings").json()
            observed["line_crossings"] = crossings
            checks["no_image_line_configured_for_moving_camera"] = crossings["configured_image_lines"] == [] and crossings["crossings"] == []
            index = client.get("/")
            # The refactored UI code-splits its views: follow the entry's lazy chunk imports, then look for the
            # inspector's history section (CountHistory.tsx) in what the server actually serves.
            pending = [line for line in index.text.split('"') if line.startswith("/assets/") and line.endswith(".js")]
            served: dict[str, str] = {}
            while pending:
                path = pending.pop()
                if path in served:
                    continue
                response = client.get(path)
                served[path] = response.text if response.status_code == 200 else ""
                pending += [f"/assets/{name}" for name in re.findall(r'(?:\./|/assets/|assets/)([\w.-]+\.js)', served[path])]
            observed["frontend_js_assets_served"] = sorted(served)
            checks["frontend_bundle_served_with_history_view"] = (
                index.status_code == 200 and any("Camera-visible track history" in text for text in served.values()))
    except Exception as exc:  # recorded, never hidden
        errors.append(f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    try:
        recovery = hardware_restart_recovery(run_dir)
        observed["hardware_restart_recovery"] = recovery
        checks["restart_recovery_found_running_owned_container"] = (
            recovery["container_present_before_recovery"]
            and [(r["resource_name"], r["state"]) for r in recovery["recorded_before_crash"]] == [(recovery["container"], "ACTIVE")])
        checks["restart_recovery_released_exact_container"] = (
            recovery["container_absent_after_recovery"] and recovery["unreleased_after_recovery"] == []
            and recovery["persisted_run_status_after_recovery"] == "STOPPED")
    except Exception as exc:
        errors.append(f"restart recovery: {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        checks["restart_recovery_released_exact_container"] = False
    leftover = subprocess.run(["wsl.exe", "-d", DISTRO, "-e", "docker", "ps", "-a", "--filter", "name=aegis-cam-01-",
                               "--format", "{{.Names}}"], capture_output=True, text=True, timeout=20, check=False)
    observed["leftover_aegis_cam01_containers"] = [n for n in leftover.stdout.splitlines() if n.strip()]
    checks["no_leftover_cam01_containers"] = leftover.returncode == 0 and not observed["leftover_aegis_cam01_containers"]
    report = {
        "schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": run.command, "run_dir": str(run_dir.relative_to(ROOT)),
        "result": "PASS" if checks and all(checks.values()) and not errors else "FAIL",
        "checks": {k: "PASS" if v else "FAIL" for k, v in checks.items()},
        "observed": observed, "errors": errors,
        "scope": "One real clip, one camera, laptop; structural integration and time-model behaviour. Not detection, tracking, "
                 "count or crossing accuracy; not multi-camera, calibration or capacity evidence.",
    }
    (run_dir / "result.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"], "checks": report["checks"], "run_dir": report["run_dir"]}, indent=1))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
