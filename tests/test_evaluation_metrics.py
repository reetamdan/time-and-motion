"""Synthetic checks of the evaluation metrics (no real labels are implied)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "evaluation"))
from metrics import detection_summary, hungarian, iou, match_frame, tracking_summary  # noqa: E402


def box(x, y, w=10, h=20, **extra):
    return {"bbox": [x, y, w, h], **extra}


def test_iou_basics() -> None:
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert iou((0, 0, 10, 10), (20, 20, 5, 5)) == 0.0
    assert iou((0, 0, 10, 10), (5, 0, 10, 10)) == pytest.approx(50 / 150)


def test_matching_counts_fp_fn_and_respects_ignore() -> None:
    gt = [box(0, 0), box(100, 0), box(300, 0, ignore=True)]
    predictions = [box(0, 0, score=0.9), box(300, 0, score=0.8), box(500, 0, score=0.7)]
    result = match_frame(gt, predictions, 0.5)
    assert (result.true_positives, result.false_positives, result.false_negatives) == (1, 1, 1)
    # A prediction whose foot point lies in an ignore region is neither TP nor FP.
    region = [[[490, 0], [520, 0], [520, 40], [490, 40]]]
    assert match_frame(gt, predictions, 0.5, region).false_positives == 0


def test_detection_summary_precision_recall_and_count_error() -> None:
    frames = [([box(0, 0), box(50, 0)], [box(0, 0, score=1)], []),
              ([box(0, 0)], [box(0, 0, score=1), box(80, 0, score=0.5)], [])]
    summary = detection_summary(frames, 0.5)
    assert summary["precision"] == pytest.approx(2 / 3) and summary["recall"] == pytest.approx(2 / 3)
    assert summary["count_mae"] == 1.0 and summary["count_mean_signed_error"] == 0.0


def test_hungarian_finds_the_optimal_assignment() -> None:
    cost = [[4, 1, 3], [2, 0, 5], [3, 2, 2]]
    assignment = hungarian(cost)
    assert sum(cost[r][c] for r, c in enumerate(assignment)) == 5


def test_tracking_id_switch_and_idf1() -> None:
    def frame(pred_id):
        return ([box(0, 0, track_id="g1")], [box(0, 0, score=1, track_id=pred_id)], [])
    perfect = tracking_summary([frame("a"), frame("a"), frame("a")], 0.5)
    assert perfect["id_switches"] == 0 and perfect["mota"] == 1.0 and perfect["idf1"] == 1.0
    switched = tracking_summary([frame("a"), frame("a"), frame("b"), frame("b")], 0.5)
    assert switched["id_switches"] == 1 and switched["mota"] == pytest.approx(0.75)
    assert switched["idf1"] == pytest.approx(0.5)


# ------------------------------------------------------------------ zero denominators


def test_zero_true_positives_with_errors_gives_f1_zero() -> None:
    summary = detection_summary([([box(0, 0)], [box(200, 0, score=0.9)], [])], 0.5)
    assert (summary["true_positives"], summary["false_positives"], summary["false_negatives"]) == (0, 1, 1)
    assert summary["precision"] == 0 and summary["recall"] == 0 and summary["f1"] == 0
    assert summary["undefined"] == {}


def test_genuinely_undefined_metrics_are_none_with_reasons() -> None:
    nothing = detection_summary([([], [], [])], 0.5)  # reviewed empty frame, no detections
    assert nothing["precision"] is None and nothing["recall"] is None and nothing["f1"] is None
    assert set(nothing["undefined"]) == {"precision", "recall", "f1"}
    only_fp = detection_summary([([], [box(0, 0, score=0.5)], [])], 0.5)
    assert only_fp["precision"] == 0 and only_fp["recall"] is None and only_fp["f1"] == 0
    assert only_fp["false_positives_on_empty_frames"] == 1 and only_fp["empty_frames_with_false_positives"] == 1


def test_tracking_requires_identities() -> None:
    from metrics import tracking_identity_problems
    missing = [([box(0, 0)], [box(0, 0, score=1, track_id="a")], [])]
    assert "no track_id" in tracking_identity_problems(missing)[0]
    with pytest.raises(ValueError):
        tracking_summary(missing, 0.5)
    detector_only = [([box(0, 0, track_id="g")], [box(0, 0, score=1)], [])]
    assert "detector-only" in tracking_identity_problems(detector_only)[0]


# ------------------------------------------------------------------ evaluator over a synthetic package

import json  # noqa: E402
import shutil  # noqa: E402

from evaluate_detections import main as evaluate_main  # noqa: E402

PACKAGE = Path(__file__).resolve().parents[1] / "data" / "annotations" / "cam01-v1"


def package(tmp_path: Path, images: list[dict], annotations: list[dict], review: str = "REVIEWED") -> Path:
    directory = tmp_path / "pkg"
    (directory / "proposals").mkdir(parents=True)
    shutil.copy(PACKAGE / "evaluation-settings.json", directory)
    (directory / "proposals" / "proposal.coco.json").write_text(json.dumps(
        {"images": images, "annotations": [{"id": 1, "image_id": images[0]["id"], "bbox": [999, 999, 5, 5]}]}))
    document = {"info": {"review": {"status": review, "reviewer": "synthetic-test" if review == "REVIEWED" else None}},
                "images": images, "annotations": annotations}
    path = directory / "annotations.json"
    path.write_text(json.dumps(document))
    return path


def image(n: int, status: str | None = "COMPLETE", sets=("detection",), **extra) -> dict:
    item = {"id": n, "frame_number": n, "sets": list(sets), "ignore_regions": [], **extra}
    if status is not None:
        item["annotation_status"] = status
    return item


def gt(n: int, x: float, track: str | None = None) -> dict:
    return {"id": n * 1000 + int(x), "image_id": n, "bbox": [x, 0, 40, 100], "track_id": track}


def jsonl(tmp_path: Path, frames: dict[int, list[tuple[float, str | None]]]) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "pred.jsonl"
    path.write_text("".join(json.dumps({"frame_number": n, "objects": [
        {"class_id": 2, "label": "person", "confidence": 0.9, "track_id": track,
         "bbox": {"x": x, "y": 0, "width": 40, "height": 100}} for x, track in objects]}) + "\n"
        for n, objects in frames.items()))
    return str(path)


def run(tmp_path: Path, annotations: Path, predictions: str, capsys) -> tuple[int, dict | None, str]:
    output = tmp_path / "out"
    code = evaluate_main(["--annotations", str(annotations), "--model", f"m={predictions}:2", "--output-dir", str(output)])
    err = capsys.readouterr().err
    report = json.loads((output / "result.json").read_text()) if code == 0 else None
    return code, report, err


def test_reviewed_empty_frames_with_false_detections_are_scored(tmp_path: Path, capsys) -> None:
    path = package(tmp_path, [image(1), image(2)], [gt(1, 0)])  # frame 2: reviewed, nobody present
    code, report, _ = run(tmp_path, path, jsonl(tmp_path, {1: [(0, None)], 2: [(500, None)]}), capsys)
    assert code == 0
    detection = report["results"]["m"]["detection"]
    assert detection["frames"] == 2 and detection["false_positives"] == 1 and detection["precision"] == 0.5
    assert detection["reviewed_empty_frames"] == 1 and detection["false_positives_on_empty_frames"] == 1
    assert report["coverage"]["reviewed_negative_images"] == 1


def test_reviewed_empty_frames_without_detections(tmp_path: Path, capsys) -> None:
    path = package(tmp_path, [image(1), image(2)], [gt(1, 0)])
    code, report, _ = run(tmp_path, path, jsonl(tmp_path, {1: [(0, None)], 2: []}), capsys)
    detection = report["results"]["m"]["detection"]
    assert code == 0 and detection["frames"] == 2 and detection["f1"] == 1.0 and detection["false_positives"] == 0


def test_partial_package_scores_only_complete_images(tmp_path: Path, capsys) -> None:
    images = [image(1), image(2, "UNREVIEWED"), image(3, "EXCLUDED", exclusion_reason="motion blur")]
    path = package(tmp_path, images, [gt(1, 0), gt(2, 0)])
    code, report, _ = run(tmp_path, path, jsonl(tmp_path, {1: [(0, None)], 2: [(300, None)], 3: [(0, None)]}), capsys)
    assert code == 0
    assert report["results"]["m"]["detection"]["frames"] == 1  # unreviewed/excluded never become empty frames
    assert report["coverage"]["images"] == {"COMPLETE": 1, "EXCLUDED": 1, "UNREVIEWED": 1}
    assert report["coverage"]["boxes_on_non_complete_images_ignored"] == 1
    assert report["coverage"]["excluded"] == [{"frame_number": 3, "reason": "motion blur"}]
    assert report["coverage"]["unreviewed_frames"] == [2]


def test_missing_review_state_is_refused(tmp_path: Path, capsys) -> None:
    path = package(tmp_path, [image(1, status=None)], [gt(1, 0)])
    code, _, err = run(tmp_path, path, jsonl(tmp_path, {1: [(0, None)]}), capsys)
    assert code == 2 and "annotation_status" in err
    unreviewed = package(tmp_path / "b", [image(1)], [gt(1, 0)], review="UNREVIEWED")
    code, _, err = run(tmp_path / "b", unreviewed, jsonl(tmp_path, {1: [(0, None)]}), capsys)
    assert code == 2 and "REVIEWED" in err
    excluded = package(tmp_path / "c", [image(1), image(2, "EXCLUDED")], [gt(1, 0)])
    code, _, err = run(tmp_path / "c", excluded, jsonl(tmp_path, {1: [(0, None)]}), capsys)
    assert code == 2 and "exclusion_reason" in err


def test_missing_pipeline_record_is_not_an_empty_prediction(tmp_path: Path, capsys) -> None:
    path = package(tmp_path, [image(1), image(2)], [gt(1, 0)])
    code, report, _ = run(tmp_path, path, jsonl(tmp_path, {1: [(0, None)]}), capsys)  # frame 2 absent
    assert code == 0 and report["results"]["m"]["detection"]["status"] == "NOT_EVALUATED"


def test_valid_reviewed_labels_and_tracking_identities(tmp_path: Path, capsys) -> None:
    images = [image(n, sets=("tracking-A",)) for n in (10, 11, 12)] + [image(1)]
    annotations = [gt(n, 0, "p1") for n in (10, 11, 12)] + [gt(n, 200, "p2") for n in (10, 11, 12)] + [gt(1, 0)]
    path = package(tmp_path, images, annotations)
    frames = {n: [(0, "7"), (200, "8")] for n in (10, 11, 12)} | {1: [(0, None)]}
    code, report, _ = run(tmp_path, path, jsonl(tmp_path, frames), capsys)
    tracking = report["results"]["m"]["tracking"]["tracking-A"]
    assert code == 0 and tracking["status"] == "EVALUATED" and tracking["mota"] == 1.0 and tracking["idf1"] == 1.0
    # Same package with one track_id missing: not a score.
    annotations[0] = gt(10, 0, None)
    path = package(tmp_path / "b", images, annotations)
    code, report, _ = run(tmp_path / "b", path, jsonl(tmp_path / "b", frames), capsys)
    tracking = report["results"]["m"]["tracking"]["tracking-A"]
    assert tracking["status"] == "NOT_EVALUATED" and "no track_id" in tracking["reason"] and "mota" not in tracking
    # A sequence with an unreviewed frame is not evaluated either.
    images[1] = image(11, "UNREVIEWED", sets=("tracking-A",))
    path = package(tmp_path / "c", images, [gt(n, 0, "p1") for n in (10, 12)] + [gt(1, 0)])
    code, report, _ = run(tmp_path / "c", path, jsonl(tmp_path / "c", frames), capsys)
    assert "not COMPLETE" in report["results"]["m"]["tracking"]["tracking-A"]["reason"]


def test_shipped_package_remains_unreviewed() -> None:
    template = json.loads((PACKAGE / "annotations.template.json").read_text(encoding="utf-8"))
    assert template["info"]["review"]["status"] == "UNREVIEWED" and template["annotations"] == []
    assert {i["annotation_status"] for i in template["images"]} == {"UNREVIEWED"}
