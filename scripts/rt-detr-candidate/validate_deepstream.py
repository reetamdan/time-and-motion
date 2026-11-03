#!/usr/bin/env python3
"""Validate real RT-DETR candidate Service Maker output, never process exit alone."""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_SOURCE = "4201f7162eb0b9bcd3910f87b1a67a5bedac89c2b0a0bb4222e67d12f816b503"
EXPECTED_ONNX = "8048e1534bd0864ae53003a3f273a6155f14ff9cb39454c5141baac3babc2e07"
EXPECTED_EXTRACTOR = "6093b5d5d06089225b3bf60b0342fedccc50efde477f9663c5ef61ff91abad25"
UNTRACKED = str(0xFFFFFFFFFFFFFFFF)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def valid_box(box, width=1920, height=1080) -> bool:
    if not isinstance(box, dict) or not all(finite(box.get(key)) for key in ("x", "y", "width", "height")):
        return False
    x, y, w, h = (float(box[key]) for key in ("x", "y", "width", "height"))
    return w > 0 and h > 0 and x >= 0 and y >= 0 and x + w <= width + 0.5 and y + h <= height + 0.5


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--extractor-exit", type=int, required=True)
    parser.add_argument("--builder-result", type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    errors = []
    summary_path = run / "run-summary.json"
    frames_path = run / "frames.jsonl"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception as exc:
        summary = {}
        errors.append(f"run-summary: {type(exc).__name__}: {exc}")
    frames = []
    if frames_path.exists():
        with frames_path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("not an object")
                    frames.append(row)
                except Exception as exc:
                    errors.append(f"frames.jsonl:{line_number}: {exc}")
    else:
        errors.append("frames.jsonl missing")
    objects = [(row, obj) for row in frames for obj in row.get("objects", []) if isinstance(obj, dict)]
    person = [(row, obj) for row, obj in objects if obj.get("label") == "person" and obj.get("class_id") == 0]
    unexpected_classes = len(objects) - len(person)
    invalid_boxes = sum(not valid_box(obj.get("bbox")) for _, obj in person)
    invalid_tracker_boxes = sum("tracker_bbox" in obj and not valid_box(obj["tracker_bbox"]) for _, obj in person)
    invalid_scores = sum(not finite(obj.get("confidence")) or not 0 <= float(obj["confidence"]) <= 1 for _, obj in person)
    id_frames = defaultdict(set)
    invalid_ids = 0
    duplicate_ids = 0
    schema_errors = 0
    non_increasing_pts = 0
    previous_pts = -1
    for row in frames:
        if (row.get("schema_version") != "1.0" or row.get("bbox_format") != "xywh" or
                row.get("bbox_coordinate_space") != "pipeline_frame_pixels" or
                row.get("source_width") != 1920 or row.get("source_height") != 1080 or
                row.get("frame_width") != 1920 or row.get("frame_height") != 1080):
            schema_errors += 1
        pts = row.get("pts_ns")
        if not isinstance(pts, int) or pts <= previous_pts:
            non_increasing_pts += 1
        elif isinstance(pts, int):
            previous_pts = pts
        seen = set()
        for obj in row.get("objects", []):
            if not isinstance(obj, dict) or obj.get("label") != "person" or obj.get("class_id") != 0:
                continue
            track_id = obj.get("track_id")
            if track_id is None:
                continue
            if not isinstance(track_id, str) or not re.fullmatch(r"[0-9]+", track_id) or track_id == UNTRACKED:
                invalid_ids += 1
                continue
            if track_id in seen:
                duplicate_ids += 1
            seen.add(track_id)
            id_frames[track_id].add(int(row.get("frame_number", -1)))
    numbers = [row.get("frame_number") for row in frames]
    source_path = ROOT / "demo" / "cam01.mp4"
    onnx_path = ROOT / "models" / "rt-detr-person-candidate" / "rt_detr_r18vd_raw.onnx"
    engine_path = ROOT / "models" / "rt-detr-person-candidate" / "rt_detr_r18vd_raw_fp32_sm120.engine"
    extractor_path = ROOT / "perception" / "deepstream" / "service_maker" / "extract_metadata.py"
    infer_path = ROOT / "perception" / "candidates" / "rt_detr" / "deepstream" / "cam01_rt_detr_infer.txt"
    tracker_path = ROOT / "perception" / "candidates" / "rt_detr" / "deepstream" / "config_tracker_NvDCF_candidate.yml"
    parser_path = ROOT / "perception" / "candidates" / "rt_detr" / "parser" / "libnvds_infercustomparser_rtdetr.so"
    source_hash = sha256(source_path)
    onnx_hash = sha256(onnx_path)
    extractor_hash = sha256(extractor_path)
    engine_hash = sha256(engine_path) if engine_path.is_file() and engine_path.stat().st_size else None
    infer_text = infer_path.read_text(encoding="utf-8")
    run_log = (run / "run.log").read_text(encoding="utf-8", errors="replace") if (run / "run.log").exists() else ""
    checks = {
        "extractor_exit_zero": args.extractor_exit == 0,
        "pipeline_returned": summary.get("pipeline_returned") is True,
        "clean_eos": summary.get("clean_eos") is True and summary.get("error") is None,
        "json_parse": not errors,
        "exact_710_frame_coverage": len(frames) == 710 and numbers == list(range(710)),
        "summary_frame_count": summary.get("frames_written") == len(frames),
        "source_identity": source_hash == EXPECTED_SOURCE == summary.get("source_sha256"),
        "onnx_identity": onnx_hash == EXPECTED_ONNX,
        "unmodified_extractor_identity": extractor_hash == EXPECTED_EXTRACTOR,
        "engine_nonempty": engine_hash is not None,
        "strongly_typed_fp32_config": "network-mode=0" in infer_text and "strongly-typed=1" in infer_text,
        "no_nms_config": "cluster-mode=4" in infer_text,
        "output_tensor_meta_enabled": "output-tensor-meta=1" in infer_text,
        "schema_and_source_coordinates": schema_errors == 0,
        "real_strictly_increasing_pts": bool(frames) and non_increasing_pts == 0,
        "person_class_zero_only": unexpected_classes == 0,
        "nonempty_valid_person_boxes": bool(person) and invalid_boxes == 0 and invalid_tracker_boxes == 0,
        "finite_unit_interval_detector_scores": bool(person) and invalid_scores == 0,
        "valid_track_ids": invalid_ids == 0 and duplicate_ids == 0,
        "persistent_person_tracks": bool(id_frames) and any(len(values) > 1 for values in id_frames.values()),
        "completion_log": "Service Maker extraction complete:" in run_log,
    }
    passed = all(checks.values())
    multi_frame = sum(len(values) > 1 for values in id_frames.values())
    elapsed = summary.get("elapsed_seconds") if finite(summary.get("elapsed_seconds")) else None
    result = {
        "schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "result": "PASS" if passed else "FAIL", "checks": {key: "PASS" if value else "FAIL" for key, value in checks.items()},
        "errors": errors,
        "metrics": {
            "frames": len(frames), "objects": len(objects), "person_detections": len(person),
            "valid_person_bounding_boxes": len(person) - invalid_boxes,
            "invalid_boxes": invalid_boxes, "invalid_tracker_boxes": invalid_tracker_boxes,
            "unexpected_class_records": unexpected_classes, "unique_person_ids": len(id_frames),
            "multi_frame_person_ids": multi_frame, "max_objects_per_frame": max((len(row.get("objects", [])) for row in frames), default=0),
            "empty_frames": sum(not row.get("objects") for row in frames), "schema_errors": schema_errors,
            "invalid_scores": invalid_scores, "invalid_track_ids": invalid_ids,
            "duplicate_person_ids_within_frame": duplicate_ids, "non_increasing_or_missing_pts": non_increasing_pts,
            "elapsed_seconds": elapsed,
            "decoded_frames_per_elapsed_second": (len(frames) / elapsed if passed and elapsed and elapsed > 0 else None),
        },
        "performance_scope": "One 710-frame 1920x1080 cam01 file, batch 1, FP32 strongly typed TensorRT, RT-DETR parser and NvDCF; end-to-end extractor elapsed only. No realtime claim or multi-stream capacity extrapolation.",
        "output_tensor_meta": {"enabled_in_config": True, "raw_tensor_observation": "not captured by the unmodified object-metadata extractor"},
        "provenance": {
            "source": "demo/cam01.mp4", "source_sha256": source_hash, "onnx_sha256": onnx_hash, "engine_sha256": engine_hash,
            "extractor": str(extractor_path.relative_to(ROOT)), "extractor_sha256": extractor_hash,
            "infer_config": str(infer_path.relative_to(ROOT)), "infer_config_sha256": sha256(infer_path),
            "tracker_config": str(tracker_path.relative_to(ROOT)), "tracker_config_sha256": sha256(tracker_path),
            "parser_library": str(parser_path.relative_to(ROOT)), "parser_library_sha256": sha256(parser_path) if parser_path.exists() else None,
            "camera_epoch": summary.get("camera_epoch"), "pyservicemaker_version": summary.get("pyservicemaker_version"),
        },
        "quality_limitations": ["No ground-truth labels; precision, recall, ID-switch rate, exact crowd counts and quality-baseline selection are not measured.",
                                "Known-positive boxes and persistent IDs prove integration behavior only, not detection or tracking accuracy."]
    }
    destination = run / "result.json"
    if destination.exists():
        raise SystemExit(f"Refusing to overwrite {destination}")
    destination.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    builder = {
        "status": "pass" if passed else "fail", "person_detections": len(person),
        "bounding_boxes": len(person) - invalid_boxes, "metadata_observed": bool(frames),
        "known_clip_sha256": source_hash, "persistent_person_track_ids": multi_frame,
        "candidate_result": str(destination), "extractor_exit": args.extractor_exit,
        "failed_checks": [key for key, value in checks.items() if not value], "errors": errors,
    }
    builder_path = args.builder_result.resolve()
    if builder_path.exists():
        raise SystemExit(f"Refusing to overwrite {builder_path}")
    builder_path.parent.mkdir(parents=True, exist_ok=True)
    builder_path.write_text(json.dumps(builder, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"result": result["result"], "metrics": result["metrics"], "failed_checks": builder["failed_checks"], "run_dir": str(run)}, indent=2))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
