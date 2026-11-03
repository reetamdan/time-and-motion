from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.configuration import CameraConfig, ConfigurationError, ModelArtifact, load_configuration

from .support import PROJECT_ROOT, copy_configuration, update_yaml
from .support import requires_local_artifacts


def test_fixture_has_four_cameras_and_two_independent_uncalibrated_floors(config) -> None:
    assert [camera.id for camera in config.cameras] == ["cam-01", "cam-02", "cam-03", "cam-04"]
    assert [floor.id for floor in config.floors] == ["floor-1", "floor-2"]
    assert {floor.fixture_status for floor in config.floors} == {"ILLUSTRATIVE_UNCALIBRATED"}
    assert all(camera.calibration_status == "MISSING" for camera in config.cameras)
    assert config.models.selected_model == "trafficcamnet-smoke"
    assert config.models.selection_mode == "provisional_smoke"
    assert config.models.tracker.enabled
    assert config.runtime.host == "127.0.0.1"
    assert config.runtime.offline is True


@requires_local_artifacts
def test_missing_assets_do_not_create_fake_availability(config) -> None:
    assert [camera.id for camera in config.cameras if camera.enabled] == ["cam-01"]
    for camera in config.cameras:
        assert camera.source_path is not None
        if camera.enabled:
            assert camera.source_path.is_file() and camera.source_path.stat().st_size > 0
        else:
            assert not camera.source_path.exists()
    smoke = next(model for model in config.models.models if model.id == "trafficcamnet-smoke")
    assert smoke.enabled and smoke.checkpoint_path.is_file() and smoke.engine_path.is_file()
    candidates = [model for model in config.models.models if model.id != "trafficcamnet-smoke"]
    assert {model.id for model in candidates} == {"rt-detr-person-candidate", "rf-detr-person-candidate"}
    # Candidates are runnable for evaluation but explicitly unaccepted, and never selected by default.
    assert all(model.acceptance == "candidate_unaccepted" and model.runtime_profile is not None for model in candidates)
    assert smoke.acceptance == "provisional_smoke"


def test_person_class_is_normalized_explicitly_per_profile(config) -> None:
    profiles = {model.id: model.runtime_profile for model in config.models.models}
    assert (profiles["trafficcamnet-smoke"].person_class_id, profiles["trafficcamnet-smoke"].person_label) == (2, "person")
    assert profiles["rt-detr-person-candidate"].person_class_id == 0
    assert profiles["rf-detr-person-candidate"].person_class_id == 0
    assert profiles["rf-detr-person-candidate"].source_geometry_policy == "require_exact"


def test_person_class_must_agree_with_class_map(tmp_path) -> None:
    directory = copy_configuration(tmp_path)
    update_yaml(directory, "models", lambda doc: doc["models"][0]["runtime_profile"].update(person_class_id=0))
    with pytest.raises(ConfigurationError, match="maps to 'car'"):
        load_configuration(directory, PROJECT_ROOT)


@pytest.mark.parametrize("mode", ["evaluated_baseline", "candidate_evaluation"])
def test_selection_mode_must_match_selected_acceptance(tmp_path, mode) -> None:
    directory = copy_configuration(tmp_path)
    update_yaml(directory, "models", lambda doc: doc.update(selection_mode=mode))
    with pytest.raises(ConfigurationError, match="requires a model with acceptance"):
        load_configuration(directory, PROJECT_ROOT)


def test_candidate_can_be_selected_only_as_candidate_evaluation(tmp_path) -> None:
    directory = copy_configuration(tmp_path)
    update_yaml(directory, "models", lambda doc: doc.update(selected_model="rf-detr-person-candidate",
                                                            selection_mode="candidate_evaluation"))
    loaded = load_configuration(directory, PROJECT_ROOT)
    assert loaded.models.selected.acceptance == "candidate_unaccepted"
    update_yaml(directory, "models", lambda doc: doc.update(selection_mode="evaluated_baseline"))
    with pytest.raises(ConfigurationError):
        load_configuration(directory, PROJECT_ROOT)


def test_enabled_tracker_requires_recorded_sha256(tmp_path) -> None:
    directory = copy_configuration(tmp_path)
    update_yaml(directory, "models", lambda doc: doc["tracker"].pop("sha256"))
    with pytest.raises(ConfigurationError, match="sha256"):
        load_configuration(directory, PROJECT_ROOT)


def test_revision_is_stable_and_file_specific(config) -> None:
    reloaded = load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT)
    assert reloaded.configuration_revision == config.configuration_revision
    assert len(config.configuration_revision) == 64
    assert set(config.file_revisions) == {"cameras", "floors", "zones", "portals", "analytics", "models", "runtime"}


def test_duplicate_camera_ids_are_rejected(tmp_path) -> None:
    directory = copy_configuration(tmp_path)
    def duplicate(document):
        document["cameras"].append(document["cameras"][0].copy())
    update_yaml(directory, "cameras", duplicate)
    with pytest.raises(ConfigurationError, match="Duplicate camera IDs"):
        load_configuration(directory, PROJECT_ROOT)


def test_unknown_floor_reference_is_rejected(tmp_path) -> None:
    directory = copy_configuration(tmp_path)
    update_yaml(directory, "zones", lambda document: document["zones"][0].update(floor_id="missing-floor"))
    with pytest.raises(ConfigurationError, match="unknown floor"):
        load_configuration(directory, PROJECT_ROOT)


def test_missing_required_file_is_rejected(tmp_path) -> None:
    directory = copy_configuration(tmp_path)
    (directory / "portals.yaml").unlink()
    with pytest.raises(ConfigurationError, match="portals.yaml"):
        load_configuration(directory, PROJECT_ROOT)


def test_invalid_polygon_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least three"):
        CameraConfig(
            id="cam-x", name="Invalid", floor_id="floor-1", source_type="file", source="clip.mp4",
            analytics_fps=8, preview_fps=5, position=(0, 0), coverage_polygon=[(0, 0), (1, 1)],
        )


def test_enabled_model_requires_immutable_provenance() -> None:
    with pytest.raises(ValidationError, match="missing pinned fields"):
        ModelArtifact(id="unpinned", role="detector", enabled=True, checkpoint_path="model.onnx")


def test_rtsp_source_label_redacts_credentials() -> None:
    camera = CameraConfig(
        id="cam-x", name="RTSP", floor_id="floor-1", source_type="rtsp",
        source="rtsp://user:secret@camera.local/stream?token=secret", analytics_fps=8, preview_fps=5,
        position=(0, 0), coverage_polygon=[(0, 0), (10, 0), (10, 10)],
    )
    assert camera.source_label == "RTSP camera.local"
    assert "secret" not in camera.source_label
