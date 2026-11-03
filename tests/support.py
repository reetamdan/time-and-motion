"""Synthetic unit fixtures only; these are never used by the live application."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import yaml

from backend.app.analytics.types import Observation, TrackKey
from backend.app.configuration import CameraConfig
from backend.app.perception.base import AdapterAvailability, ObservationBatch, ObservationSink, PerceptionAdapter

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def artifact_present(path: Path | None) -> bool:
    """LFS pointer files are placeholders, not usable footage or model binaries."""
    if path is None or not path.is_file():
        return False
    with path.open("rb") as stream:
        return not stream.read(128).startswith(b"version https://git-lfs.github.com/spec/v1")


def missing_local_artifacts() -> list[str]:
    """Footage and selected-model files missing from a checkout without hydrated Git LFS."""
    from backend.app.configuration import load_configuration

    config = load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT)
    paths = [camera.source_path for camera in config.cameras if camera.enabled]
    selected = next(model for model in config.models.models if model.id == config.models.selected_model)
    paths += [selected.checkpoint_path, selected.engine_path]
    return [str(path.relative_to(PROJECT_ROOT)) if path and path.is_relative_to(PROJECT_ROOT) else str(path)
            for path in paths if not artifact_present(path)]


_MISSING_LOCAL_ARTIFACTS = missing_local_artifacts()

# A fresh clone has no footage or model binaries (without Git LFS; see README), so the tests that
# exercise them skip there with the missing paths named. Set AEGIS_REQUIRE_LOCAL_ARTIFACTS=1 to
# make them fail instead, e.g. when validating a fully provisioned machine.
requires_local_artifacts = pytest.mark.skipif(
    bool(_MISSING_LOCAL_ARTIFACTS) and os.environ.get("AEGIS_REQUIRE_LOCAL_ARTIFACTS") != "1",
    reason=f"local footage/model artifacts not present: {', '.join(_MISSING_LOCAL_ARTIFACTS)}",
)


def key(camera: str = "cam-01", epoch: str = "epoch-1", local: str = "1") -> TrackKey:
    return TrackKey(camera, epoch, local)


def observation(track: TrackKey, timestamp: float, y: float, **kwargs) -> Observation:
    return Observation(key=track, timestamp=timestamp, position=(50, y), **kwargs)


def copy_configuration(destination: Path) -> Path:
    target = destination / "configs"
    shutil.copytree(PROJECT_ROOT / "configs", target, dirs_exist_ok=True)
    return target


def update_yaml(config_dir: Path, name: str, update) -> None:
    path = config_dir / f"{name}.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    update(value)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def event_envelope(run_id: str, idempotency_key: str = "event-1") -> dict[str, object]:
    return {
        "event_id": f"event-{idempotency_key}",
        "schema_version": "1.0",
        "event_type": "test.event",
        "run_id": run_id,
        "producer_id": "pytest",
        "producer_sequence": 1,
        "idempotency_key": idempotency_key,
        "occurred_at": "2026-01-01T00:00:00.000Z",
        "emitted_at": "2026-01-01T00:00:00.001Z",
        "payload": {"value": 1},
    }


class UnavailableAdapter(PerceptionAdapter):
    def availability(self) -> AdapterAvailability:
        return AdapterAvailability("unit-unavailable", False, "Unit-test perception is unavailable", ("test blocker",))

    async def start_camera(self, camera: CameraConfig, camera_epoch: str, sink: ObservationSink, replay_mode: str = "paced") -> None:
        raise RuntimeError("Unavailable adapter must not start")

    async def stop_camera(self, camera_id: str, camera_epoch: str) -> None:
        return None

    async def shutdown(self) -> None:
        return None


class SuppliedObservationAdapter(PerceptionAdapter):
    """Test adapter that emits only observations explicitly supplied by a unit test."""

    def __init__(self) -> None:
        self.sinks: dict[str, ObservationSink] = {}
        self.started: list[tuple[str, str]] = []
        self.stopped: list[tuple[str, str]] = []
        self.replay_modes: list[str] = []

    def availability(self) -> AdapterAvailability:
        return AdapterAvailability("unit-supplied", True, "Supplied-observation test adapter", runtime_detected=True)

    async def start_camera(self, camera: CameraConfig, camera_epoch: str, sink: ObservationSink, replay_mode: str = "paced") -> None:
        self.sinks[camera.id] = sink
        self.started.append((camera.id, camera_epoch))
        self.replay_modes.append(replay_mode)

    async def stop_camera(self, camera_id: str, camera_epoch: str) -> None:
        self.stopped.append((camera_id, camera_epoch))
        self.sinks.pop(camera_id, None)

    async def emit(self, batch: ObservationBatch) -> None:
        await self.sinks[batch.camera_id].emit(batch)

    async def shutdown(self) -> None:
        return None


def write_calibration(config_dir: Path, camera_id: str = "cam-01", floor_id: str = "floor-1", revision: str = "rev-1",
                      image_size: tuple[int, int] = (1920, 1080), error: float = 0.0) -> str:
    """Synthetic calibration fixture: a pure scaling of the frame onto the 1000 x 600 floor map."""
    import json

    sx, sy = 1000 / image_size[0], 600 / image_size[1]
    ref = f"{camera_id}-{revision}"
    points = [[u, v, u * sx + error, v * sy] for u, v in ((100, 100), (1800, 200), (960, 1000))]
    (config_dir / "calibration").mkdir(exist_ok=True)
    (config_dir / "calibration" / f"{ref}.json").write_text(json.dumps({
        "camera_id": camera_id, "revision": revision, "floor_id": floor_id, "image_size": list(image_size),
        "homography_image_to_floor": [[sx, 0, 0], [0, sy, 0], [0, 0, 1]],
        "validation_points": points, "max_validation_error": 2.0, "measured_by": "unit-test fixture (synthetic)",
    }), encoding="utf-8")
    return ref
