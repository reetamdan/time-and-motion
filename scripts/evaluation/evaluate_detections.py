#!/usr/bin/env python3
"""Score detector/tracker outputs against HUMAN-REVIEWED annotations of the cam01 package.

Refuses to run when the annotations are not marked REVIEWED by a named reviewer, when they are
byte-identical to (or box-identical with) a detector proposal file - proposals are never ground
truth - or when images lack an explicit per-image ``annotation_status``.

Per-image status (``images[].annotation_status``):
  COMPLETE    every person in the image is boxed and a reviewer checked it. A COMPLETE image with
              no boxes is a reviewed NEGATIVE frame and is scored (its detections are false positives).
  UNREVIEWED  not yet done; excluded from scoring and reported as coverage, never counted as empty.
  EXCLUDED    deliberately left out (``exclusion_reason`` required).

A tracking sequence is scored only when every frame of it is COMPLETE, every reviewed person box has
a ``track_id``, and the predictions carry tracker ids; otherwise its result is NOT_EVALUATED with the
reason (never a score).

Inputs per model: a pipeline JSONL (extractor schema 1.0 or a pre_tracker diagnostic JSONL) and the
model's person class id. Writes a fresh report directory under reports/model-evaluation/labeled/.

Run:
  .venv/Scripts/python.exe scripts/evaluation/evaluate_detections.py \
      --annotations data/annotations/cam01-v1/annotations.json \
      --model trafficcamnet-smoke=reports/validation/service-maker-cam01/frames.jsonl:2 \
      --model rt-detr-person-candidate=<jsonl>:0 --model rf-detr-person-candidate=<jsonl>:0
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from metrics import detection_summary, tracking_identity_problems, tracking_summary  # noqa: E402

IMAGE_STATUSES = {"COMPLETE", "UNREVIEWED", "EXCLUDED"}


class NotGroundTruth(ValueError):
    pass


def _box_set(document: dict) -> set:
    return {(a["image_id"], tuple(round(v, 1) for v in a["bbox"])) for a in document.get("annotations", [])}


def load_reviewed(path: Path, package: Path) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"))
    review = (document.get("info") or {}).get("review") or {}
    if review.get("status") != "REVIEWED" or not review.get("reviewer"):
        raise NotGroundTruth("annotations are not marked REVIEWED by a named reviewer (info.review.status/reviewer)")
    images = document.get("images") or []
    missing = [image.get("id") for image in images if image.get("annotation_status") not in IMAGE_STATUSES]
    if missing:
        raise NotGroundTruth(f"{len(missing)} image(s) lack a valid annotation_status (COMPLETE/UNREVIEWED/EXCLUDED), "
                             f"e.g. image id {missing[0]}; a reviewed empty frame cannot be told from an unreviewed one")
    unexplained = [image["id"] for image in images
                   if image["annotation_status"] == "EXCLUDED" and not image.get("exclusion_reason")]
    if unexplained:
        raise NotGroundTruth(f"EXCLUDED image(s) without exclusion_reason: {unexplained[:5]}")
    if not any(image["annotation_status"] == "COMPLETE" for image in images):
        raise NotGroundTruth("no image is COMPLETE: nothing has been reviewed")
    ids = {image["id"] for image in images}
    orphans = [a.get("id") for a in document.get("annotations", []) if a.get("image_id") not in ids]
    if orphans:
        raise NotGroundTruth(f"{len(orphans)} annotation(s) reference unknown image ids")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    boxes = _box_set(document)
    for proposal in sorted((package / "proposals").glob("*.json")):
        if hashlib.sha256(proposal.read_bytes()).hexdigest() == digest:
            raise NotGroundTruth(f"annotations are byte-identical to proposal file {proposal.name}")
        proposed = json.loads(proposal.read_text(encoding="utf-8"))
        if boxes and boxes == _box_set(proposed):
            raise NotGroundTruth(f"annotation boxes are identical to unreviewed proposals {proposal.name}")
    return document


def ground_truth(document: dict, min_height: float) -> dict[int, tuple[list[dict], list]]:
    """Ground truth of COMPLETE images only, keyed by frame number (an empty list = reviewed negative)."""
    complete = {image["id"]: image for image in document["images"] if image["annotation_status"] == "COMPLETE"}
    by_image: dict[int, tuple[list[dict], list]] = {
        image["frame_number"]: ([], image.get("ignore_regions") or []) for image in complete.values()}
    for item in document.get("annotations", []):
        image = complete.get(item["image_id"])
        if image is None:
            continue  # boxes on unreviewed/excluded images are not ground truth
        ignore = bool(item.get("iscrowd")) or item["bbox"][3] < min_height or bool(item.get("ignore"))
        by_image[image["frame_number"]][0].append({"bbox": item["bbox"], "ignore": ignore,
                                                   "track_id": item.get("track_id")})
    return by_image


def coverage(document: dict) -> tuple[dict[str, Any], dict[str, list[int]], dict[int, str]]:
    status_of = {image["frame_number"]: image["annotation_status"] for image in document["images"]}
    sets: dict[str, list[int]] = {}
    for image in document["images"]:
        for name in image.get("sets", []):
            sets.setdefault(name, []).append(image["frame_number"])
    complete_ids = {image["id"] for image in document["images"] if image["annotation_status"] == "COMPLETE"}
    boxes_per_image: dict[int, int] = {}
    for item in document.get("annotations", []):
        boxes_per_image[item["image_id"]] = boxes_per_image.get(item["image_id"], 0) + 1
    return {
        "images": {status: sum(s == status for s in status_of.values()) for status in sorted(IMAGE_STATUSES)},
        "reviewed_negative_images": sum(1 for i in complete_ids if not boxes_per_image.get(i)),
        "excluded": [{"frame_number": image["frame_number"], "reason": image["exclusion_reason"]}
                     for image in document["images"] if image["annotation_status"] == "EXCLUDED"],
        "unreviewed_frames": sorted(image["frame_number"] for image in document["images"] if image["annotation_status"] == "UNREVIEWED"),
        "boxes_on_non_complete_images_ignored": sum(n for i, n in boxes_per_image.items() if i not in complete_ids),
        "sets": {name: {status: sum(status_of[f] == status for f in frames) for status in sorted(IMAGE_STATUSES)}
                 for name, frames in sorted(sets.items())},
    }, sets, status_of


def predictions(jsonl: Path, person_class: int, frames: set[int]) -> tuple[dict[int, list[dict]], set[int]]:
    result: dict[int, list[dict]] = {}
    for line in jsonl.open(encoding="utf-8"):
        record = json.loads(line)
        if record["frame_number"] not in frames:
            continue
        result[record["frame_number"]] = [
            {"bbox": [o["bbox"]["x"], o["bbox"]["y"], o["bbox"]["width"], o["bbox"]["height"]],
             "score": o.get("confidence"), "track_id": o.get("track_id")}
            for o in record["objects"] if o.get("class_id") == person_class and o.get("label") == "person"]
    return result, set(result)


def evaluate_model(gt: dict, sets: dict[str, list[int]], status_of: dict[int, str], predicted: dict[int, list[dict]],
                   present: set[int], settings: dict) -> dict[str, Any]:
    scored = [f for f in sorted(sets.get("detection", [])) if f in gt]
    missing_output = [f for f in scored if f not in present]
    detection: dict[str, Any]
    if missing_output:
        # A frame without any pipeline record is not "no detections": refuse to score it as empty.
        detection = {"status": "NOT_EVALUATED",
                     "reason": f"pipeline output has no record for reviewed frame(s) {missing_output[:10]}"}
    elif not scored:
        detection = {"status": "NOT_EVALUATED", "reason": "no COMPLETE image in the detection set"}
    else:
        detection = {"status": "EVALUATED", **detection_summary(
            [(gt[f][0], predicted.get(f, []), gt[f][1]) for f in scored], settings["detection"]["iou_threshold"])}
    tracking: dict[str, Any] = {}
    for name, frames in sorted(sets.items()):
        if not name.startswith("tracking"):
            continue
        incomplete = [f for f in frames if status_of.get(f) != "COMPLETE"]
        if incomplete:
            tracking[name] = {"status": "NOT_EVALUATED",
                              "reason": f"{len(incomplete)} of {len(frames)} frames are not COMPLETE (a sequence needs every frame)"}
            continue
        absent = [f for f in frames if f not in present]
        if absent:
            tracking[name] = {"status": "NOT_EVALUATED", "reason": f"pipeline output missing frames {absent[:10]}"}
            continue
        sequence = [(gt[f][0], predicted.get(f, []), gt[f][1]) for f in sorted(frames)]
        problems = tracking_identity_problems(sequence)
        if problems:
            tracking[name] = {"status": "NOT_EVALUATED", "reason": "; ".join(problems)}
            continue
        tracking[name] = {"status": "EVALUATED", **tracking_summary(sequence, settings["tracking"]["iou_threshold"])}
    return {"detection": detection, "tracking": tracking}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--model", action="append", required=True, help="id=path/to.jsonl:person_class_id")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    package = args.annotations.resolve().parent
    settings = json.loads((package / "evaluation-settings.json").read_text(encoding="utf-8"))
    try:
        document = load_reviewed(args.annotations, package)
    except NotGroundTruth as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    gt = ground_truth(document, settings["detection"]["min_gt_height_px"])
    summary, sets, status_of = coverage(document)
    results = {}
    for spec in args.model:
        model, _, rest = spec.partition("=")
        path, _, person = rest.rpartition(":")
        jsonl = (ROOT / path).resolve()
        predicted, present = predictions(jsonl, int(person), set(gt))
        results[model] = {"jsonl": path, "jsonl_sha256": hashlib.sha256(jsonl.read_bytes()).hexdigest(),
                          "person_class_id": int(person),
                          **evaluate_model(gt, sets, status_of, predicted, present, settings)}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = args.output_dir or ROOT / "reports" / "model-evaluation" / "labeled" / f"cam01-v1-{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": 2, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
              "annotations": str(args.annotations), "annotations_sha256": hashlib.sha256(args.annotations.read_bytes()).hexdigest(),
              "review": document["info"]["review"], "coverage": summary, "settings": settings, "results": results,
              "scope": "Labelled accuracy on one clip (cam01) over human-reviewed COMPLETE images only (reviewed "
                       "negatives included). Same-input numerical consistency and cross-pipeline agreement are "
                       "separate reports."}
    (output / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({m: {k: r["detection"].get(k) for k in ("status", "precision", "recall", "f1", "count_mae")}
                      for m, r in results.items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
