from __future__ import annotations

import math

import numpy as np
import pytest

from scripts.validate_deepstream_smoke import (
    Record,
    analyze_run_log,
    annotate_tracker_persons,
    capacity_metrics,
    discover_frame_files,
    frame_filename,
    output_metrics,
    parse_kitti_line,
    validate_records,
)


DETECTOR_ROW = "person 0.0 0 0.0 10.5 20.25 30.75 40.125 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.75"
TRACKER_ROW = "person 42 0.0 0 0.0 10.5 20.25 30.75 40.125 0.0 0.0 0.0 -1.0 -1.0 0.0 0.0 0.50"


def record(label: str = "person", track_id: int | None = 1, box=(10.0, 10.0, 50.0, 80.0)) -> Record:
    return Record(label, box, 0.5, 1, track_id)


def test_detector_row_uses_kitti_box_and_last_confidence() -> None:
    parsed = parse_kitti_line(DETECTOR_ROW, "detector", 3)
    assert parsed.label == "person"
    assert parsed.track_id is None
    assert parsed.box == (10.5, 20.25, 30.75, 40.125)
    assert parsed.confidence == 0.75
    assert parsed.line == 3


def test_tracker_row_uses_integer_id_and_shifted_box_indices() -> None:
    parsed = parse_kitti_line(TRACKER_ROW, "tracker")
    assert parsed.label == "person"
    assert parsed.track_id == 42
    assert parsed.box == (10.5, 20.25, 30.75, 40.125)
    assert parsed.confidence == 0.5


@pytest.mark.parametrize(
    ("kind", "line", "message"),
    [
        ("detector", "person 0 0", "expected 16"),
        ("tracker", DETECTOR_ROW, "expected 17"),
        ("tracker", TRACKER_ROW.replace("person 42", "person 4.2"), "track ID is not an integer"),
        ("detector", DETECTOR_ROW.replace("10.5", "not-a-number"), "invalid floating-point"),
    ],
)
def test_malformed_rows_are_rejected(kind: str, line: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_kitti_line(line, kind)


def test_frame_coverage_requires_exact_contiguous_expected_names(tmp_path) -> None:
    for frame in range(3):
        (tmp_path / frame_filename(frame)).touch()
    assert discover_frame_files(tmp_path, 3)["exact_contiguous_coverage"] is True

    (tmp_path / frame_filename(1)).unlink()
    (tmp_path / frame_filename(3)).touch()
    (tmp_path / "notes.txt").touch()
    coverage = discover_frame_files(tmp_path, 3)
    assert coverage["actual_frames"] == [0, 2, 3]
    assert coverage["missing_frames"] == [1]
    assert coverage["out_of_range_frames"] == [3]
    assert coverage["unexpected_entries"] == ["notes.txt"]
    assert coverage["exact_contiguous_coverage"] is False


def test_box_validation_accepts_small_tracker_unclipping_overshoot() -> None:
    records = {0: [record(box=(-9.0, -2.0, 1921.0, 1135.0))]}
    assert validate_records(records, "tracker", 1920, 1080, 0.10) == []


def test_tracker_unclipping_policy_has_explicit_wider_frame_tolerance() -> None:
    records = {0: [record(box=(-145.0, -143.0, 2093.0, 1365.0))]}
    strict_issues = validate_records(records, "tracker", 1920, 1080, 0.10)
    assert "vertical_bound_tolerance" in {issue["category"] for issue in strict_issues}
    assert validate_records(records, "tracker", 1920, 1080, 0.30) == []


def test_only_detector_confidence_is_required_to_be_unit_bounded() -> None:
    detector_record = record(track_id=None)
    detector_record.confidence = 1.02
    detector_issues = validate_records({0: [detector_record]}, "detector", 1920, 1080, 0.10)
    assert [(issue["category"], issue["severity"]) for issue in detector_issues] == [("confidence_out_of_range", "FAIL")]

    tracker_record = record()
    tracker_record.confidence = 1.02
    tracker_issues = validate_records({0: [tracker_record]}, "tracker", 1920, 1080, 0.30)
    assert [(issue["category"], issue["severity"]) for issue in tracker_issues] == [("tracker_confidence_outside_unit_interval", "WARN")]


@pytest.mark.parametrize(
    ("box", "category"),
    [
        ((math.nan, 0.0, 10.0, 10.0), "nonfinite_box"),
        ((10.0, 10.0, 10.0, 20.0), "nonpositive_box_area"),
        ((-300.0, 0.0, 10.0, 20.0), "horizontal_bound_tolerance"),
        ((-100.0, 0.0, -10.0, 20.0), "no_frame_intersection"),
    ],
)
def test_box_validation_rejects_nonfinite_implausible_or_unusable_boxes(box, category: str) -> None:
    issues = validate_records({0: [record(box=box)]}, "tracker", 1920, 1080, 0.10)
    assert category in {issue["category"] for issue in issues}


def test_person_track_ids_must_be_nonnegative_and_unique_within_frame() -> None:
    records = {0: [record(track_id=7), record(track_id=7), record(track_id=-1), record(label="car", track_id=-1)]}
    issues = validate_records(records, "tracker", 1920, 1080, 0.10)
    categories = [issue["category"] for issue in issues]
    assert categories.count("duplicate_person_track_id_in_frame") == 1
    assert categories.count("invalid_person_track_id") == 1


def test_tracker_metrics_report_unique_and_multi_frame_person_ids() -> None:
    records = {
        0: [record(track_id=7), record(track_id=9)],
        1: [record(track_id=7)],
        2: [record(label="car", track_id=2)],
        3: [record(track_id=9), record(track_id=10)],
    }
    metrics = output_metrics(records, "tracker", [0, 1, 2, 3], ("car", "bicycle", "person", "road_sign"), [], [])
    assert metrics["records"] == 6
    assert metrics["person_tracker_records"] == 5
    assert metrics["per_class_records"] == {"car": 1, "bicycle": 0, "person": 5, "road_sign": 0}
    assert metrics["unique_person_ids"] == [7, 9, 10]
    assert metrics["multi_frame_person_ids"] == [7, 9]
    assert metrics["person_id_frame_counts"] == {"7": 2, "9": 2, "10": 1}
    assert metrics["max_objects_per_frame"] == {"count": 2, "frames": [0, 3]}
    assert metrics["max_person_objects_per_frame"] == {"count": 2, "frames": [0, 3]}


def test_capacity_warning_is_class_agnostic_and_triggers_at_limit() -> None:
    records = {
        0: [record(label="car", track_id=None) for _ in range(299)],
        1: [record(label="car", track_id=None) for _ in range(150)]
        + [record(label="person", track_id=None) for _ in range(150)],
    }
    capacity = capacity_metrics(records, [0, 1], 300, "class-agnostic test")
    assert capacity["warning_frames"] == [1]
    assert capacity["warning_count"] == 1
    assert capacity["warnings"][0]["objects"] == 300
    assert capacity["max_objects_per_frame"] == {"count": 300, "frames": [1]}


def test_run_log_evidence_ignores_ansi_and_requires_engine_and_eos(tmp_path) -> None:
    log = tmp_path / "run.log"
    log.write_text(
        "DeepStreamSDK 9.2\n"
        "\x1b[31mINFO\x1b[00m deserialized trt engine from :/workspace/model.engine\n"
        "Use deserialized engine model: /workspace/model.engine\n"
        "Load new model:/workspace/infer.txt sucessfully\n"
        "Pipeline running\n"
        "GStreamer-WARNING **: optional plugin unavailable\n"
        "nvstreammux: Successfully handled EOS for source_id=0\n"
        "Received EOS. Exiting ...\n"
        "App run successful\n",
        encoding="utf-8",
    )
    evidence = analyze_run_log(log)
    assert evidence["status"] == "PASS"
    assert evidence["engine_deserialization"]["passed"] is True
    assert evidence["engine_deserialization"]["deserialized_path"] == "/workspace/model.engine"
    assert evidence["warning_count"] == 1
    assert evidence["application_success"][0]["line"] == 9


def test_annotation_uses_only_tracker_person_records() -> None:
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    canvas, metadata = annotate_tracker_persons(
        frame,
        [record(label="person", track_id=12), record(label="car", track_id=99)],
        0,
        0.0,
    )
    assert canvas.shape == (152, 200, 3)
    assert metadata["tracker_person_records"] == 1
    assert metadata["drawn_person_boxes"] == 1
    assert metadata["drawn_track_ids"] == [12]
    assert metadata["skipped_person_records"] == []
