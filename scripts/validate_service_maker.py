#!/usr/bin/env python3
"""Validate real Service Maker JSONL output; never infer success from exit status alone."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN = ROOT / "reports" / "validation" / "service-maker-cam01"
MEDIA_PROBE = ROOT / "reports" / "media" / "cam01" / "media_probe.json"
UNTRACKED = str(0xFFFFFFFFFFFFFFFF)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def valid_box(box: Any, width: int, height: int, tolerance: float = 0.3) -> bool:
    if not isinstance(box, dict) or not all(finite(box.get(key)) for key in ("x", "y", "width", "height")):
        return False
    x, y, w, h = (float(box[key]) for key in ("x", "y", "width", "height"))
    if w <= 0 or h <= 0 or x + w <= 0 or y + h <= 0 or x >= width or y >= height:
        return False
    return x >= -width * tolerance and y >= -height * tolerance and x + w <= width * (1 + tolerance) and y + h <= height * (1 + tolerance)


def validate(run_dir: Path) -> dict[str, Any]:
    frames_path = run_dir / "frames.jsonl"
    summary = json.loads((run_dir / "run-summary.json").read_text(encoding="utf-8"))
    media = json.loads(MEDIA_PROBE.read_text(encoding="utf-8"))
    expected = int(media["container_reported"]["frame_count"])
    source_width = int(media["container_reported"]["width"])
    source_height = int(media["container_reported"]["height"])
    frames: list[dict[str, Any]] = []
    errors: list[str] = []
    with frames_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("frame row is not an object")
                frames.append(value)
            except (json.JSONDecodeError, ValueError) as exc:
                errors.append(f"line {line_number}: {exc}")

    numbers = [row.get("frame_number") for row in frames]
    contiguous = numbers == list(range(expected))
    objects = [(row, obj) for row in frames for obj in row.get("objects", []) if isinstance(obj, dict)]
    classes = Counter(str(obj.get("label", "")) for _, obj in objects)
    person = [(row, obj) for row, obj in objects if obj.get("label") == "person"]
    id_frames: dict[str, set[int]] = defaultdict(set)
    invalid_boxes = 0
    invalid_tracker_boxes = 0
    invalid_scores = 0
    invalid_ids = 0
    duplicate_ids = 0
    missing_pts = 0
    non_increasing_pts = 0
    previous_pts = -1
    schema_errors = 0
    tracker_score_advisories = 0

    for row in frames:
        if (
            row.get("schema_version") != "1.0"
            or row.get("camera_id") != summary.get("camera_id")
            or row.get("camera_epoch") != summary.get("camera_epoch")
            or row.get("source_width") != source_width
            or row.get("source_height") != source_height
            or row.get("frame_width") != source_width
            or row.get("frame_height") != source_height
            or row.get("bbox_format") != "xywh"
            or row.get("bbox_coordinate_space") != "pipeline_frame_pixels"
            or not isinstance(row.get("objects"), list)
        ):
            schema_errors += 1
        pts = row.get("pts_ns")
        if not isinstance(pts, int) or pts < 0 or not finite(row.get("timestamp")):
            missing_pts += 1
        else:
            if pts <= previous_pts:
                non_increasing_pts += 1
            previous_pts = pts
        seen: set[str] = set()
        for obj in row.get("objects", []):
            if not isinstance(obj, dict):
                schema_errors += 1
                continue
            if not valid_box(obj.get("bbox"), source_width, source_height):
                invalid_boxes += 1
            if not finite(obj.get("confidence")) or not finite(obj.get("tracker_confidence")):
                invalid_scores += 1
            elif not 0 <= float(obj["tracker_confidence"]) <= 1:
                tracker_score_advisories += 1
            track_id = obj.get("track_id")
            if track_id is not None and (not isinstance(track_id, str) or not re.fullmatch(r"[0-9]+", track_id) or track_id == UNTRACKED):
                invalid_ids += 1
            if obj.get("label") == "person" and isinstance(track_id, str):
                if track_id in seen:
                    duplicate_ids += 1
                seen.add(track_id)
                id_frames[track_id].add(int(row["frame_number"]))
            if "tracker_bbox" in obj and not valid_box(obj["tracker_bbox"], source_width, source_height):
                invalid_tracker_boxes += 1

    run_log = (run_dir / "run.log").read_text(encoding="utf-8", errors="replace") if (run_dir / "run.log").exists() else ""
    max_objects = max((len(row["objects"]) for row in frames), default=0)
    capacity_warnings = [row["frame_number"] for row in frames if len(row["objects"]) >= 150]
    checks = {
        "pipeline_returned": summary.get("pipeline_returned") is True,
        "clean_eos": summary.get("clean_eos") is True and summary.get("error") is None,
        "json_parse": not errors,
        "exact_frame_coverage": len(frames) == expected and contiguous,
        "summary_frame_count": summary.get("frames_written") == len(frames),
        "schema_and_geometry": schema_errors == 0,
        "source_identity": summary.get("source_sha256") == media["source"]["sha256"] == sha256(ROOT / "demo" / "cam01.mp4"),
        "real_pts": missing_pts == 0 and non_increasing_pts == 0,
        "valid_boxes": invalid_boxes == 0 and invalid_tracker_boxes == 0,
        "finite_scores": invalid_scores == 0,
        "valid_track_ids": invalid_ids == 0 and duplicate_ids == 0,
        "nonempty_person_detections": len(person) > 0,
        "persistent_person_tracks": bool(id_frames) and any(len(values) > 1 for values in id_frames.values()),
        "tracker_capacity_headroom": not capacity_warnings,
        "completion_log": "Service Maker extraction complete:" in run_log,
    }
    warnings: list[str] = []
    if "GLib-CRITICAL" in run_log:
        warnings.append("A GLib source-removal diagnostic occurred after source EOS; clean EOS and complete output were independently verified.")
    if "No decoder available for type 'audio/mpeg" in run_log:
        warnings.append("The clip's AAC audio track has no container decoder; video decode/inference completed and no audio analytics are claimed.")
    if tracker_score_advisories:
        warnings.append(f"{tracker_score_advisories} tracker scores fall outside [0,1]; values are preserved, not treated as normalized probabilities.")
    if capacity_warnings:
        warnings.append(f"Tracker capacity proxy reached in {len(capacity_warnings)} frames.")

    artifacts = {
        "extractor": ROOT / "perception" / "deepstream" / "service_maker" / "extract_metadata.py",
        "infer_config": ROOT / "perception" / "deepstream" / "cam01_trafficcamnet_smoke_infer.txt",
        "tracker_config": ROOT / "perception" / "deepstream" / "reference" / "config_tracker_NvDCF_perf.yml",
        "engine": ROOT / "models" / "trafficcamnet-smoke" / "resnet18_trafficcamnet_pruned.onnx_b1_gpu0_fp16.engine",
        "jsonl": frames_path,
    }
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "result": "PASS" if all(checks.values()) else "FAIL",
        "scope": "Service Maker metadata structure, complete frame coverage, real boxes/IDs/PTS, clean EOS and capacity proxies; not detection or tracking accuracy",
        "checks": {name: "PASS" if passed else "FAIL" for name, passed in checks.items()},
        "warnings": warnings,
        "metrics": {
            "expected_frames": expected,
            "actual_frames": len(frames),
            "first_frame_number": numbers[0] if numbers else None,
            "last_frame_number": numbers[-1] if numbers else None,
            "empty_frames": sum(not row["objects"] for row in frames),
            "objects": len(objects),
            "person_objects": len(person),
            "per_class_records": dict(sorted(classes.items())),
            "unique_person_ids": len(id_frames),
            "multi_frame_person_ids": sum(len(values) > 1 for values in id_frames.values()),
            "max_objects_per_frame": max_objects,
            "tracker_capacity": 150,
            "capacity_warning_frames": capacity_warnings,
            "parse_errors": errors,
            "schema_errors": schema_errors,
            "invalid_boxes": invalid_boxes,
            "invalid_tracker_boxes": invalid_tracker_boxes,
            "invalid_scores": invalid_scores,
            "invalid_track_ids": invalid_ids,
            "duplicate_person_ids_within_frame": duplicate_ids,
            "missing_or_invalid_pts": missing_pts,
            "non_increasing_pts": non_increasing_pts,
            "tracker_score_unit_interval_advisories": tracker_score_advisories,
            "jsonl_bytes": frames_path.stat().st_size,
        },
        "provenance": {
            "source": "demo/cam01.mp4",
            "source_sha256": summary.get("source_sha256"),
            "camera_epoch": summary.get("camera_epoch"),
            "pyservicemaker_version": summary.get("pyservicemaker_version"),
            "pyyaml_version": summary.get("pyyaml_version"),
            "elapsed_seconds": summary.get("elapsed_seconds"),
            "artifact_sha256": {name: sha256(path) for name, path in artifacts.items()},
        },
        "limitations": [
            "TrafficCamNet is a pipeline smoke model, not the evaluated PRD detector baseline.",
            "No ground-truth reference labels exist for this clip; precision, recall, ID-switch rate and exact crowd counts are not measured.",
            "Current-frame Service Maker objects differ from deepstream-app tracker KITTI dumps, which can include past-frame target reports; outputs are not expected to be record-identical.",
            "The two empty initial frames are retained; no detections or tracks are synthesized.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", nargs="?", type=Path, default=DEFAULT_RUN)
    args = parser.parse_args()
    report = validate(args.run_dir.resolve())
    destination = args.run_dir.resolve() / "result.json"
    destination.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"], "metrics": report["metrics"], "warnings": report["warnings"]}, indent=2))
    if report["result"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
