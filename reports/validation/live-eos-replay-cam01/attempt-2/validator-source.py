#!/usr/bin/env python3
"""REAL natural-EOS, stale-snapshot, operator-stop, and replay integration validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.main import create_app  # noqa: E402

CAMERA_ID = "cam-01"
EXPECTED_FRAMES = 710
ACTIVE_RUN_STATUSES = {"STARTING", "RUNNING", "DEGRADED"}
RUN_DIR = ROOT / "reports" / "validation" / "live-eos-replay-cam01"
LOGGER = logging.getLogger("cctv.validation.live_eos")

REQUIRED_CHECKS = (
    "first_start_http_ok",
    "first_run_active",
    "first_epoch_present",
    "first_real_positive_visible_count",
    "first_positive_before_natural_eos",
    "first_pre_eos_fresh_metrics_present",
    "natural_eos_clean_completion",
    "natural_eos_exact_710_frames",
    "natural_eos_jsonl_coverage",
    "natural_eos_container_removed",
    "stale_values_null",
    "stale_health_degraded",
    "stale_last_good_age_threshold",
    "stale_last_good_age_increases",
    "stale_run_remains_active",
    "first_operator_stop_http_ok",
    "first_operator_stop_run_stopped",
    "first_container_absent_after_operator_stop",
    "second_start_http_ok",
    "second_epoch_different",
    "second_replay_fresh_positive_visible_count",
    "second_operator_stop_http_ok",
    "second_operator_stop_run_stopped",
    "both_epoch_containers_absent_after_second_stop",
    "testclient_shutdown_completed",
    "adapter_sessions_disposed",
    "final_exact_epoch_containers_absent",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def camera_from(snapshot: dict[str, Any]) -> dict[str, Any]:
    return next((camera for camera in snapshot.get("cameras", []) if camera.get("id") == CAMERA_ID), {})


def camera_view(camera: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id", "health", "calibration_status", "epoch", "visible_count", "source_fps",
        "analytics_fps", "frame_age_ms", "dropped_frames", "last_good_age_ms", "unavailable_reason",
    )
    return {key: camera.get(key) for key in keys}


def snapshot_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": snapshot.get("run_id"),
        "run_status": snapshot.get("run_status"),
        "generated_at": snapshot.get("generated_at"),
        "sequence": snapshot.get("sequence"),
        "camera": camera_view(camera_from(snapshot)),
    }


def positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def fresh_positive(camera: dict[str, Any], stale_after_ms: float) -> bool:
    age = camera.get("last_good_age_ms")
    return (
        positive_int(camera.get("visible_count"))
        and finite_number(age)
        and 0 <= age <= stale_after_ms
        and camera.get("health") in {"HEALTHY", "CALIBRATION WARNING"}
    )


class Checks:
    def __init__(self) -> None:
        self.statuses = {name: "NOT_RUN" for name in REQUIRED_CHECKS}
        self.details: dict[str, Any] = {name: "Not executed" for name in REQUIRED_CHECKS}

    def record(self, name: str, passed: bool, detail: Any) -> None:
        self.statuses[name] = "PASS" if passed else "FAIL"
        self.details[name] = detail
        LOGGER.info("%s: %s", name, self.statuses[name])

    @property
    def passed(self) -> bool:
        return all(self.statuses.get(name) == "PASS" for name in REQUIRED_CHECKS)


class DockerNames:
    """Exact-name Docker inspection and fallback removal through the configured WSL distro."""

    def __init__(self, distro: str) -> None:
        self.prefix = ["wsl.exe", "-d", distro, "--"]

    def _run(self, arguments: list[str], timeout: float = 15) -> dict[str, Any]:
        command = [*self.prefix, *arguments]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
            return {
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            }
        except (OSError, subprocess.SubprocessError) as exc:
            return {"command": command, "returncode": None, "stdout": "", "stderr": str(exc)}

    def inspect(self, names: list[str]) -> dict[str, Any]:
        expected = set(names)
        # Colons are safe through wsl.exe; shell metacharacters such as "|" are re-parsed by WSL.
        result = self._run(["docker", "ps", "-a", "--format", "{{.ID}}:{{.Names}}:{{.State}}"])
        matches: dict[str, list[dict[str, str]]] = {name: [] for name in names}
        parse_errors: list[str] = []
        if result["returncode"] == 0:
            for line in result["stdout"].splitlines():
                parts = line.split(":", 2)
                if len(parts) != 3:
                    if line.strip():
                        parse_errors.append(line)
                    continue
                container_id, name, state = parts
                if name in expected:
                    matches[name].append({"id": container_id, "name": name, "state": state})
        return {
            **result,
            "matches": matches,
            "parse_errors": parse_errors,
            "all_absent": result["returncode"] == 0 and not parse_errors and all(not rows for rows in matches.values()),
        }

    def wait_absent(self, names: list[str], timeout_seconds: float) -> dict[str, Any]:
        started = time.monotonic()
        inspection = self.inspect(names)
        while not inspection["all_absent"] and time.monotonic() - started < timeout_seconds:
            time.sleep(0.1)
            inspection = self.inspect(names)
        return {"all_absent": inspection["all_absent"], "elapsed_seconds": time.monotonic() - started, "inspection": inspection}

    def remove(self, name: str) -> dict[str, Any]:
        return self._run(["docker", "rm", "-f", name], timeout=20)


@dataclass
class EpochObservation:
    epoch: str
    first_positive: dict[str, Any] | None = None
    first_positive_at: float | None = None
    first_fresh_positive: dict[str, Any] | None = None
    first_fresh_positive_at: float | None = None
    fresh_metrics_sample: dict[str, Any] | None = None
    last_sample: dict[str, Any] | None = None
    positive_samples: int = 0
    maximum_visible_count: int = 0

    def observe(self, snapshot: dict[str, Any], sampled_at: float, stale_after_ms: float) -> dict[str, Any]:
        camera = camera_from(snapshot)
        self.last_sample = snapshot_view(snapshot)
        if camera.get("epoch") != self.epoch:
            return camera
        count = camera.get("visible_count")
        if positive_int(count):
            self.positive_samples += 1
            self.maximum_visible_count = max(self.maximum_visible_count, count)
            view = snapshot_view(snapshot)
            if self.first_positive is None:
                self.first_positive = view
                self.first_positive_at = sampled_at
            if fresh_positive(camera, stale_after_ms):
                if self.first_fresh_positive is None:
                    self.first_fresh_positive = view
                    self.first_fresh_positive_at = sampled_at
                if (
                    self.fresh_metrics_sample is None
                    and camera.get("source_fps") is not None
                    and camera.get("analytics_fps") is not None
                ):
                    self.fresh_metrics_sample = view
        return camera


def get_snapshot(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/v1/facility/snapshot")
    body = response.json()
    if response.status_code != 200:
        raise RuntimeError(f"Snapshot GET returned {response.status_code}: {body}")
    return body


def response_body(response: Any) -> dict[str, Any]:
    body = response.json()
    if not isinstance(body, dict):
        raise RuntimeError(f"Expected an API object, received {type(body).__name__}")
    return body


def wait_positive(
    client: TestClient,
    observation: EpochObservation,
    stale_after_ms: float,
    summary_path: Path,
    timeout_seconds: float = 35,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        snapshot = get_snapshot(client)
        camera = observation.observe(snapshot, time.monotonic(), stale_after_ms)
        if (
            camera.get("epoch") == observation.epoch
            and snapshot.get("run_status") in ACTIVE_RUN_STATUSES
            and fresh_positive(camera, stale_after_ms)
        ):
            return snapshot
        if summary_path.is_file():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                if isinstance(summary, dict) and "frames_written" in summary:
                    break
            except (OSError, json.JSONDecodeError):
                pass
        time.sleep(0.1)
    return None


def read_final_summary(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if isinstance(value, dict) and "frames_written" in value:
        return value, None
    return None, "Summary exists but is not a final extractor summary"


def wait_natural_eos(
    client: TestClient,
    observation: EpochObservation,
    stale_after_ms: float,
    summary_path: Path,
    timeout_seconds: float = 60,
) -> tuple[dict[str, Any] | None, str | None]:
    deadline = time.monotonic() + timeout_seconds
    last_error: str | None = None
    while time.monotonic() < deadline:
        snapshot = get_snapshot(client)
        observation.observe(snapshot, time.monotonic(), stale_after_ms)
        summary, last_error = read_final_summary(summary_path)
        if summary is not None:
            return summary, None
        time.sleep(0.1)
    return None, last_error or f"No final extractor summary within {timeout_seconds} seconds"


def inspect_jsonl(path: Path, epoch: str) -> dict[str, Any]:
    frame_numbers: list[Any] = []
    parse_errors: list[str] = []
    wrong_epoch_records = 0
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("frame row is not an object")
                    frame_numbers.append(record.get("frame_number"))
                    if record.get("camera_epoch") != epoch or record.get("camera_id") != CAMERA_ID:
                        wrong_epoch_records += 1
                except (json.JSONDecodeError, ValueError) as exc:
                    parse_errors.append(f"line {line_number}: {exc}")
    except OSError as exc:
        return {"path": str(path), "read_error": str(exc), "exact_710_contiguous": False}
    return {
        "path": str(path),
        "records": len(frame_numbers),
        "first_frame_number": frame_numbers[0] if frame_numbers else None,
        "last_frame_number": frame_numbers[-1] if frame_numbers else None,
        "wrong_epoch_or_camera_records": wrong_epoch_records,
        "parse_error_count": len(parse_errors),
        "parse_errors": parse_errors[:20],
        "exact_710_contiguous": (
            len(frame_numbers) == EXPECTED_FRAMES
            and frame_numbers == list(range(EXPECTED_FRAMES))
            and wrong_epoch_records == 0
            and not parse_errors
        ),
    }


def null_stale_values(camera: dict[str, Any]) -> bool:
    return all(camera.get(key) is None for key in ("visible_count", "source_fps", "analytics_fps"))


def wait_stale_sample(
    client: TestClient, epoch: str, stale_after_ms: float, timeout_seconds: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, Any] | None = None
    beyond_threshold: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        latest = get_snapshot(client)
        camera = camera_from(latest)
        age = camera.get("last_good_age_ms")
        if camera.get("epoch") == epoch and finite_number(age) and age > stale_after_ms:
            if beyond_threshold is None:
                beyond_threshold = latest
            if null_stale_values(camera) and camera.get("health") == "DEGRADED":
                return latest
        time.sleep(0.05)
    return beyond_threshold or latest or {}


def stop_operator(
    client: TestClient, checks: Checks, prefix: str, evidence_name: str
) -> tuple[int, dict[str, Any], float]:
    started = time.monotonic()
    response = client.post("/api/v1/demo/stop")
    elapsed = time.monotonic() - started
    body = response_body(response)
    write_json(RUN_DIR / evidence_name, {"http_status": response.status_code, "snapshot": body})
    checks.record(
        f"{prefix}_operator_stop_http_ok",
        response.status_code == 200,
        {"http_status": response.status_code, "elapsed_seconds": elapsed},
    )
    checks.record(
        f"{prefix}_operator_stop_run_stopped",
        response.status_code == 200 and body.get("run_status") == "STOPPED",
        snapshot_view(body),
    )
    return response.status_code, body, elapsed


def run_lifecycle(
    client: TestClient,
    checks: Checks,
    observed: dict[str, Any],
    artifacts: list[dict[str, Any]],
    docker: DockerNames,
    stale_after_ms: float,
) -> None:
    first_observed: dict[str, Any] = {}
    second_observed: dict[str, Any] = {}
    observed["first_epoch"] = first_observed
    observed["second_epoch"] = second_observed

    first_start_clock = time.monotonic()
    first_response = client.post("/api/v1/demo/start")
    first_start_response_clock = time.monotonic()
    first_body = response_body(first_response)
    write_json(RUN_DIR / "epoch-1-start.snapshot.json", {"http_status": first_response.status_code, "snapshot": first_body})
    checks.record("first_start_http_ok", first_response.status_code == 200, {"http_status": first_response.status_code})
    checks.record("first_run_active", first_body.get("run_status") in ACTIVE_RUN_STATUSES, snapshot_view(first_body))
    first_camera = camera_from(first_body)
    first_epoch = first_camera.get("epoch")
    checks.record("first_epoch_present", isinstance(first_epoch, str) and bool(first_epoch), {"epoch": first_epoch})
    if first_response.status_code != 200 or not isinstance(first_epoch, str) or not first_epoch:
        raise RuntimeError("First start did not return a usable cam-01 epoch")
    first_container = f"aegis-cam-01-{first_epoch}"
    first_observed.update({
        "run_id": first_body.get("run_id"), "epoch": first_epoch, "container_name": first_container,
        "start_request_seconds": first_start_response_clock - first_start_clock,
    })
    first_paths = {
        "frames": ROOT / "backend" / "var" / "perception" / CAMERA_ID / f"{first_epoch}.jsonl",
        "summary": ROOT / "backend" / "var" / "perception" / CAMERA_ID / f"{first_epoch}.summary.json",
        "log": ROOT / "backend" / "var" / "perception" / CAMERA_ID / f"{first_epoch}.log",
    }
    artifacts.append({"label": "epoch-1", "epoch": first_epoch, **first_paths})
    first_observation = EpochObservation(first_epoch)
    first_observation.observe(first_body, first_start_response_clock, stale_after_ms)

    first_positive_snapshot = wait_positive(client, first_observation, stale_after_ms, first_paths["summary"])
    if first_positive_snapshot is not None:
        write_json(RUN_DIR / "epoch-1-positive.snapshot.json", first_positive_snapshot)
    checks.record(
        "first_real_positive_visible_count",
        first_observation.first_positive is not None,
        {
            "first_positive": first_observation.first_positive,
            "last_sample": first_observation.last_sample,
            "positive_samples_before_eos_wait": first_observation.positive_samples,
        },
    )
    first_observed["time_to_first_positive_from_start_response_seconds"] = (
        first_observation.first_positive_at - first_start_response_clock
        if first_observation.first_positive_at is not None else None
    )

    eos_wait_clock = time.monotonic()
    summary, summary_error = wait_natural_eos(client, first_observation, stale_after_ms, first_paths["summary"])
    natural_eos_clock = time.monotonic()
    first_observed["natural_eos_wait_after_positive_phase_seconds"] = natural_eos_clock - eos_wait_clock
    first_observed["natural_eos_from_start_response_seconds"] = natural_eos_clock - first_start_response_clock
    first_observed["extractor_summary"] = summary
    first_observed["extractor_summary_error"] = summary_error
    first_observed["positive_samples_through_eos"] = first_observation.positive_samples
    first_observed["maximum_visible_count_through_eos"] = first_observation.maximum_visible_count
    first_observed["fresh_metrics_sample"] = first_observation.fresh_metrics_sample
    positive_timestamp = first_positive_snapshot.get("generated_at") if first_positive_snapshot is not None else None
    eos_timestamp = summary.get("ended_at_utc") if summary is not None else None
    ordering_error = None
    try:
        if not isinstance(positive_timestamp, str) or not isinstance(eos_timestamp, str):
            raise ValueError("Positive-snapshot or EOS completion timestamp is missing")
        positive_before_eos = (
            datetime.fromisoformat(positive_timestamp.replace("Z", "+00:00"))
            < datetime.fromisoformat(eos_timestamp.replace("Z", "+00:00"))
        )
    except (TypeError, ValueError) as exc:
        positive_before_eos = False
        ordering_error = str(exc)
    first_observed["positive_eos_ordering"] = {
        "positive_snapshot_generated_at": positive_timestamp,
        "natural_eos_ended_at": eos_timestamp,
        "positive_observed_before_eos": positive_before_eos,
        "error": ordering_error,
    }
    checks.record(
        "first_positive_before_natural_eos",
        positive_before_eos,
        first_observed["positive_eos_ordering"],
    )
    checks.record(
        "first_pre_eos_fresh_metrics_present",
        first_observation.fresh_metrics_sample is not None,
        first_observation.fresh_metrics_sample or {"last_sample": first_observation.last_sample},
    )
    clean_eos = bool(
        summary
        and summary.get("pipeline_returned") is True
        and summary.get("clean_eos") is True
        and summary.get("error") is None
        and summary.get("camera_epoch") == first_epoch
        and summary.get("camera_id") == CAMERA_ID
        and positive_int(summary.get("eos_message_count"))
    )
    checks.record("natural_eos_clean_completion", clean_eos, {"summary": summary, "error": summary_error})
    checks.record(
        "natural_eos_exact_710_frames",
        bool(
            summary
            and summary.get("frames_written") == EXPECTED_FRAMES
            and summary.get("first_frame_number") == 0
            and summary.get("last_frame_number") == EXPECTED_FRAMES - 1
        ),
        summary or {"error": summary_error},
    )
    jsonl_evidence = inspect_jsonl(first_paths["frames"], first_epoch)
    first_observed["jsonl"] = jsonl_evidence
    checks.record("natural_eos_jsonl_coverage", jsonl_evidence.get("exact_710_contiguous") is True, jsonl_evidence)
    if summary is None:
        raise RuntimeError(f"Natural EOS was not evidenced: {summary_error}")

    eos_container = docker.wait_absent([first_container], timeout_seconds=10)
    first_observed["natural_eos_container"] = eos_container
    checks.record(
        "natural_eos_container_removed",
        eos_container["all_absent"],
        {"container_name": first_container, **eos_container},
    )

    stale_snapshot = wait_stale_sample(
        client, first_epoch, stale_after_ms, timeout_seconds=stale_after_ms / 1000 + 6
    )
    stale_camera = camera_from(stale_snapshot)
    write_json(RUN_DIR / "epoch-1-stale.snapshot.json", stale_snapshot)
    stale_delay_seconds = 0.25
    time.sleep(stale_delay_seconds)
    later_stale_snapshot = get_snapshot(client)
    later_stale_camera = camera_from(later_stale_snapshot)
    write_json(RUN_DIR / "epoch-1-stale-later.snapshot.json", later_stale_snapshot)
    first_age = stale_camera.get("last_good_age_ms")
    later_age = later_stale_camera.get("last_good_age_ms")
    age_delta = later_age - first_age if finite_number(first_age) and finite_number(later_age) else None
    first_observed["stale_samples"] = {
        "first": snapshot_view(stale_snapshot),
        "later": snapshot_view(later_stale_snapshot),
        "sample_interval_seconds": stale_delay_seconds,
        "last_good_age_delta_ms": age_delta,
    }
    checks.record(
        "stale_values_null",
        null_stale_values(stale_camera) and null_stale_values(later_stale_camera),
        {"first": camera_view(stale_camera), "later": camera_view(later_stale_camera)},
    )
    checks.record(
        "stale_health_degraded",
        stale_camera.get("health") == "DEGRADED" and later_stale_camera.get("health") == "DEGRADED",
        {"first_health": stale_camera.get("health"), "later_health": later_stale_camera.get("health")},
    )
    checks.record(
        "stale_last_good_age_threshold",
        finite_number(first_age) and finite_number(later_age) and first_age > stale_after_ms and later_age > stale_after_ms,
        {"configured_stale_after_ms": stale_after_ms, "first_age_ms": first_age, "later_age_ms": later_age},
    )
    checks.record(
        "stale_last_good_age_increases",
        age_delta is not None and age_delta > 0,
        {"first_age_ms": first_age, "later_age_ms": later_age, "delta_ms": age_delta},
    )
    stale_run_active = all(
        snapshot.get("run_status") in ACTIVE_RUN_STATUSES and snapshot.get("run_id") == first_observed["run_id"]
        for snapshot in (stale_snapshot, later_stale_snapshot)
    )
    checks.record(
        "stale_run_remains_active",
        stale_run_active,
        {"first": snapshot_view(stale_snapshot), "later": snapshot_view(later_stale_snapshot)},
    )

    first_stop_status, first_stop_body, first_stop_seconds = stop_operator(
        client, checks, "first", "operator-stop-1.snapshot.json"
    )
    first_observed["operator_stop"] = {"elapsed_seconds": first_stop_seconds, "snapshot": snapshot_view(first_stop_body)}
    first_stop_container = docker.wait_absent([first_container], timeout_seconds=10)
    first_observed["container_after_operator_stop"] = first_stop_container
    checks.record(
        "first_container_absent_after_operator_stop",
        first_stop_container["all_absent"],
        {"container_name": first_container, **first_stop_container},
    )
    if first_stop_status != 200 or first_stop_body.get("run_status") != "STOPPED":
        raise RuntimeError("First operator stop did not reach STOPPED; replay was not attempted")

    second_start_clock = time.monotonic()
    second_response = client.post("/api/v1/demo/start")
    second_start_response_clock = time.monotonic()
    second_body = response_body(second_response)
    write_json(RUN_DIR / "epoch-2-start.snapshot.json", {"http_status": second_response.status_code, "snapshot": second_body})
    checks.record("second_start_http_ok", second_response.status_code == 200, {"http_status": second_response.status_code})
    second_camera = camera_from(second_body)
    second_epoch = second_camera.get("epoch")
    second_epoch_distinct = (
        second_response.status_code == 200
        and isinstance(second_epoch, str)
        and bool(second_epoch)
        and second_epoch != first_epoch
    )
    checks.record(
        "second_epoch_different",
        second_epoch_distinct,
        {"first_epoch": first_epoch, "second_epoch": second_epoch, "snapshot": snapshot_view(second_body)},
    )
    if not second_epoch_distinct:
        raise RuntimeError("Second start did not return a distinct cam-01 epoch")
    second_container = f"aegis-cam-01-{second_epoch}"
    second_observed.update({
        "run_id": second_body.get("run_id"), "epoch": second_epoch, "container_name": second_container,
        "start_request_seconds": second_start_response_clock - second_start_clock,
    })
    second_paths = {
        "frames": ROOT / "backend" / "var" / "perception" / CAMERA_ID / f"{second_epoch}.jsonl",
        "summary": ROOT / "backend" / "var" / "perception" / CAMERA_ID / f"{second_epoch}.summary.json",
        "log": ROOT / "backend" / "var" / "perception" / CAMERA_ID / f"{second_epoch}.log",
    }
    artifacts.append({"label": "epoch-2", "epoch": second_epoch, **second_paths})
    second_observation = EpochObservation(second_epoch)
    second_observation.observe(second_body, second_start_response_clock, stale_after_ms)
    second_positive_snapshot = wait_positive(client, second_observation, stale_after_ms, second_paths["summary"])
    if second_positive_snapshot is not None:
        write_json(RUN_DIR / "epoch-2-positive.snapshot.json", second_positive_snapshot)
    second_positive_camera = camera_from(second_positive_snapshot) if second_positive_snapshot is not None else {}
    second_positive_ok = bool(
        second_positive_snapshot is not None
        and second_positive_snapshot.get("run_status") in ACTIVE_RUN_STATUSES
        and second_positive_camera.get("epoch") == second_epoch
        and fresh_positive(second_positive_camera, stale_after_ms)
    )
    checks.record(
        "second_replay_fresh_positive_visible_count",
        second_positive_ok,
        {
            "positive_snapshot": snapshot_view(second_positive_snapshot) if second_positive_snapshot is not None else None,
            "first_positive": second_observation.first_positive,
            "last_sample": second_observation.last_sample,
        },
    )
    second_observed.update({
        "time_to_first_positive_from_start_response_seconds": (
            second_observation.first_positive_at - second_start_response_clock
            if second_observation.first_positive_at is not None else None
        ),
        "first_positive": second_observation.first_positive,
        "fresh_metrics_sample": second_observation.fresh_metrics_sample,
        "positive_samples_before_stop": second_observation.positive_samples,
        "maximum_visible_count_before_stop": second_observation.maximum_visible_count,
    })

    second_stop_status, second_stop_body, second_stop_seconds = stop_operator(
        client, checks, "second", "operator-stop-2.snapshot.json"
    )
    second_observed["operator_stop"] = {"elapsed_seconds": second_stop_seconds, "snapshot": snapshot_view(second_stop_body)}
    both_names = [first_container, second_container]
    final_stop_containers = docker.wait_absent(both_names, timeout_seconds=10)
    observed["containers_after_second_operator_stop"] = final_stop_containers
    checks.record(
        "both_epoch_containers_absent_after_second_stop",
        second_stop_status == 200 and second_stop_body.get("run_status") == "STOPPED" and final_stop_containers["all_absent"],
        {"exact_container_names": both_names, **final_stop_containers},
    )


def copy_epoch_artifacts(artifacts: list[dict[str, Any]], observed: dict[str, Any], errors: list[dict[str, Any]]) -> None:
    for item in artifacts:
        copied: dict[str, Any] = {}
        for kind in ("log", "summary", "frames"):
            source = item[kind]
            if not source.is_file():
                copied[kind] = {"source": str(source), "copied": False, "reason": "File absent"}
                continue
            suffix = {"log": ".log", "summary": ".summary.json", "frames": ".frames.jsonl"}[kind]
            destination = RUN_DIR / f"{item['label']}{suffix}"
            try:
                shutil.copy2(source, destination)
                copied[kind] = {
                    "source": str(source), "copied": True, "evidence_file": destination.name,
                    "bytes": destination.stat().st_size,
                }
            except OSError as exc:
                copied[kind] = {"source": str(source), "copied": False, "reason": str(exc)}
                errors.append({"stage": f"copy-{item['label']}-{kind}", "type": type(exc).__name__, "message": str(exc)})
        observed[item["label"].replace("-", "_") + "_artifacts"] = copied


def main() -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    database = RUN_DIR / "live-eos-replay.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(database) + suffix).unlink(missing_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(RUN_DIR / "validator.log", mode="w", encoding="utf-8"), logging.StreamHandler()],
        force=True,
    )
    checks = Checks()
    observed: dict[str, Any] = {}
    cleanup: dict[str, Any] = {"fallback_operations": []}
    errors: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    app = None
    docker = DockerNames("Ubuntu-22.04")
    entered = False
    exited = False
    total_started = time.monotonic()

    def capture_error(stage: str, exc: BaseException) -> None:
        errors.append({
            "stage": stage,
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        })
        LOGGER.error("%s: %s", stage, exc, exc_info=(type(exc), exc, exc.__traceback__))

    try:
        app = create_app(database_path=database)
        config = app.state.services.config
        docker = DockerNames(config.runtime.telemetry.wsl_distro)
        stale_after_ms = float(config.analytics.health.stale_after_ms)
        camera_config = config.camera_map[CAMERA_ID]
        observed["configuration"] = {
            "source": camera_config.source,
            "source_type": camera_config.source_type,
            "calibration_status": camera_config.calibration_status,
            "selected_model": config.models.selected_model,
            "model_selection_mode": config.models.selection_mode,
            "tracker": config.models.tracker.name,
            "tracker_revision": config.models.tracker.revision,
            "stale_after_ms": stale_after_ms,
            "expected_natural_eos_frames": EXPECTED_FRAMES,
            "wsl_distro": config.runtime.telemetry.wsl_distro,
        }
        with TestClient(app) as client:
            entered = True
            try:
                run_lifecycle(client, checks, observed, artifacts, docker, stale_after_ms)
            except Exception as exc:
                capture_error("lifecycle", exc)
            finally:
                try:
                    cleanup_started = time.monotonic()
                    cleanup_response = client.post("/api/v1/demo/stop")
                    cleanup["operator_stop"] = {
                        "http_status": cleanup_response.status_code,
                        "run_status": cleanup_response.json().get("run_status"),
                        "elapsed_seconds": time.monotonic() - cleanup_started,
                    }
                except Exception as exc:
                    capture_error("failure-safe operator stop", exc)
        exited = True
    except Exception as exc:
        capture_error("TestClient/application", exc)
    finally:
        checks.record(
            "testclient_shutdown_completed",
            entered and exited,
            {"entered": entered, "clean_exit": exited},
        )
        session_detail: dict[str, Any] = {"adapter_available": False}
        sessions_disposed = False
        if app is not None:
            try:
                sessions = app.state.services.adapter.sessions._sessions
                session_detail = {
                    "adapter_available": True,
                    "remaining_session_count": len(sessions),
                    "sessions": [
                        {"key": list(key), "process_returncode": session.process.poll()}
                        for key, session in sessions.items()
                    ],
                }
                sessions_disposed = not sessions
            except Exception as exc:
                capture_error("adapter session inspection", exc)
                session_detail = {"adapter_available": True, "inspection_error": str(exc)}
        checks.record("adapter_sessions_disposed", sessions_disposed, session_detail)
        cleanup["adapter_sessions"] = session_detail

        exact_names = [f"aegis-cam-01-{item['epoch']}" for item in artifacts]
        try:
            before_fallback = docker.inspect(exact_names)
            cleanup["before_fallback"] = before_fallback
            for name in exact_names:
                if before_fallback["returncode"] != 0 or before_fallback["matches"].get(name):
                    operation = docker.remove(name)
                    cleanup["fallback_operations"].append({"container_name": name, **operation})
                    if operation["returncode"] != 0:
                        errors.append({
                            "stage": "fallback Docker removal", "type": "DockerCommandFailed",
                            "message": f"{name}: exit={operation['returncode']} stderr={operation['stderr']}",
                        })
            final_containers = docker.wait_absent(exact_names, timeout_seconds=5) if exact_names else {
                "all_absent": False, "elapsed_seconds": 0, "inspection": {"error": "No epoch was captured"}
            }
            cleanup["final_exact_container_observation"] = final_containers
            checks.record(
                "final_exact_epoch_containers_absent",
                bool(exact_names) and final_containers["all_absent"],
                {"exact_container_names": exact_names, **final_containers},
            )
        except Exception as exc:
            capture_error("failure-safe Docker cleanup", exc)
            checks.record("final_exact_epoch_containers_absent", False, {"error": str(exc), "exact_container_names": exact_names})
        copy_epoch_artifacts(artifacts, observed, errors)
        observed["total_elapsed_seconds"] = time.monotonic() - total_started
        result = "PASS" if checks.passed and not errors else "FAIL"
        report = {
            "schema_version": 1,
            "generated_at_utc": utc_now(),
            "result": result,
            "command": ".venv/Scripts/python.exe scripts/validate_live_eos.py",
            "scope": "Real FastAPI -> supervisor -> WSL/Docker -> Service Maker natural file EOS, stale API state, operator stop, and fresh second-epoch replay",
            "checks": checks.statuses,
            "check_details": checks.details,
            "observed": observed,
            "errors": errors,
            "cleanup": cleanup,
            "limitations": [
                "Only the existing authorized demo/cam01.mp4 source and provisional TrafficCamNet + NvDCF smoke profile were exercised.",
                "Visible counts are real tracker-derived API outputs, not ground-truth accuracy, precision/recall, or calibrated facility occupancy.",
                "Camera calibration is MISSING; no calibrated geometry, crossing, occupancy, or other analytics accuracy is claimed.",
                "The first epoch runs to natural 710-frame EOS; the second epoch is intentionally stopped after a fresh positive replay count and is not a full-clip coverage run.",
                "The nominal sub-15-second EOS duration is contextual, not a performance guarantee; observed timings are reported and the natural-EOS timeout is 60 seconds.",
                "Natural-EOS and post-stop container absence are checked before fallback removal; any fallback removal is recorded separately and cannot turn an earlier failed check into a pass.",
            ],
        }
        write_json(RUN_DIR / "result.json", report)
        print(json.dumps({
            "result": result,
            "evidence": str(RUN_DIR / "result.json"),
            "checks": checks.statuses,
            "errors": errors,
            "observed": {
                "first_epoch": observed.get("first_epoch"),
                "second_epoch": observed.get("second_epoch"),
                "total_elapsed_seconds": observed.get("total_elapsed_seconds"),
            },
        }, indent=2, allow_nan=False))
        return 0 if result == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
