#!/usr/bin/env python3
"""Validate DeepStream KITTI smoke output and render tracker-only evidence."""
from __future__ import annotations

import argparse
from collections import Counter
import configparser
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Iterable

import cv2
import numpy as np


FRAME_NAME = re.compile(r"^00_000_(\d{6})\.txt$")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
DEFAULT_SAMPLE_FRAMES = (0, 141, 283, 425, 567, 709)
EXPECTED_LABELS = ("car", "bicycle", "person", "road_sign")
PERSON_CLASS_ID = 2
IMAGE_SIZE_LIMIT_BYTES_EXCLUSIVE = 3_000_000
DETECTOR_FIELD_COUNT = 16
TRACKER_FIELD_COUNT = 17


class Checks:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, name: str, status: str, detail: str, **evidence: Any) -> None:
        if status not in {"PASS", "FAIL", "WARN"}:
            raise ValueError(f"Unsupported check status: {status}")
        item: dict[str, Any] = {"name": name, "status": status, "detail": detail}
        if evidence:
            item["evidence"] = evidence
        self.items.append(item)
        print(f"[{status}] {name}: {detail}")

    def require(self, name: str, passed: bool, detail: str, **evidence: Any) -> None:
        self.add(name, "PASS" if passed else "FAIL", detail, **evidence)

    @property
    def failures(self) -> int:
        return sum(item["status"] == "FAIL" for item in self.items)

    @property
    def warnings(self) -> int:
        return sum(item["status"] == "WARN" for item in self.items)


class Record:
    def __init__(
        self,
        label: str,
        box: tuple[float, float, float, float],
        confidence: float,
        line: int,
        track_id: int | None = None,
    ) -> None:
        self.label = label
        self.track_id = track_id
        self.box = box
        self.confidence = confidence
        self.line = line


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def file_identity(path: Path, root: Path, *, role: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "path": relative_path(path, root),
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path),
    }
    if role:
        item["role"] = role
    return item


def frame_filename(frame_index: int) -> str:
    return f"00_000_{frame_index:06d}.txt"


def parse_kitti_line(line: str, kind: str, line_number: int = 1) -> Record:
    """Parse one detector or tracker row without altering its numeric values."""
    if kind not in {"detector", "tracker"}:
        raise ValueError("kind must be 'detector' or 'tracker'")
    fields = line.strip().split()
    expected = DETECTOR_FIELD_COUNT if kind == "detector" else TRACKER_FIELD_COUNT
    if len(fields) != expected:
        raise ValueError(f"{kind} row has {len(fields)} fields; expected {expected}")
    if not fields[0]:
        raise ValueError("class label is empty")

    track_id: int | None = None
    box_start = 4
    if kind == "tracker":
        if not re.fullmatch(r"[+-]?\d+", fields[1]):
            raise ValueError(f"track ID is not an integer: {fields[1]!r}")
        track_id = int(fields[1])
        box_start = 5
    try:
        coordinates = tuple(float(value) for value in fields[box_start : box_start + 4])
        confidence = float(fields[-1])
    except ValueError as exc:
        raise ValueError(f"invalid floating-point field: {exc}") from exc
    return Record(fields[0], coordinates, confidence, line_number, track_id)


def parse_kitti_file(path: Path, kind: str, frame_index: int) -> tuple[list[Record], list[dict[str, Any]]]:
    records: list[Record] = []
    errors: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        return records, [{"frame": frame_index, "line": None, "error": f"cannot read UTF-8 file: {exc}"}]
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            records.append(parse_kitti_line(line, kind, line_number))
        except ValueError as exc:
            errors.append({"frame": frame_index, "line": line_number, "error": str(exc)})
    return records, errors


def discover_frame_files(directory: Path, expected_frame_count: int) -> dict[str, Any]:
    expected = list(range(expected_frame_count))
    actual: list[int] = []
    unexpected: list[str] = []
    directory_exists = directory.is_dir()
    if directory_exists:
        for entry in sorted(directory.iterdir(), key=lambda item: item.name):
            match = FRAME_NAME.fullmatch(entry.name)
            if entry.is_file() and match:
                actual.append(int(match.group(1)))
            else:
                unexpected.append(entry.name)
    actual.sort()
    actual_set = set(actual)
    expected_set = set(expected)
    missing = sorted(expected_set - actual_set)
    out_of_range = sorted(actual_set - expected_set)
    exact = directory_exists and actual == expected and not unexpected
    return {
        "directory_exists": directory_exists,
        "filename_pattern": FRAME_NAME.pattern,
        "expected_frame_count": expected_frame_count,
        "expected_first_frame": expected[0] if expected else None,
        "expected_last_frame": expected[-1] if expected else None,
        "actual_frame_count": len(actual),
        "actual_frames": actual,
        "missing_frames": missing,
        "out_of_range_frames": out_of_range,
        "unexpected_entries": unexpected,
        "exact_contiguous_coverage": exact,
    }


def load_output(
    directory: Path, kind: str, coverage: dict[str, Any]
) -> tuple[dict[int, list[Record]], list[dict[str, Any]]]:
    records: dict[int, list[Record]] = {}
    errors: list[dict[str, Any]] = []
    for frame_index in coverage["actual_frames"]:
        frame_records, frame_errors = parse_kitti_file(directory / frame_filename(frame_index), kind, frame_index)
        records[frame_index] = frame_records
        errors.extend(frame_errors)
    return records, errors


def safe_number(value: float) -> float | str:
    return value if math.isfinite(value) else str(value)


def validate_records(
    records_by_frame: dict[int, list[Record]],
    kind: str,
    width: int,
    height: int,
    tolerance_ratio: float,
    labels: Iterable[str] = EXPECTED_LABELS,
) -> list[dict[str, Any]]:
    """Check labels, finite plausible boxes, confidence, and person track IDs."""
    allowed_labels = set(labels)
    tolerance_x = width * tolerance_ratio
    tolerance_y = height * tolerance_ratio
    issues: list[dict[str, Any]] = []

    def add(frame: int, record: Record, category: str, detail: str, severity: str = "FAIL") -> None:
        issues.append(
            {
                "frame": frame,
                "line": record.line,
                "category": category,
                "severity": severity,
                "label": record.label,
                "detail": detail,
                "box": [safe_number(value) for value in record.box],
            }
        )

    for frame, records in sorted(records_by_frame.items()):
        seen_person_ids: set[int] = set()
        for record in records:
            if record.label not in allowed_labels:
                add(frame, record, "unknown_label", f"label {record.label!r} is not in the TrafficCamNet label map")
            finite_box = all(math.isfinite(value) for value in record.box)
            if not finite_box:
                add(frame, record, "nonfinite_box", "all box coordinates must be finite")
            else:
                x1, y1, x2, y2 = record.box
                if x2 <= x1 or y2 <= y1:
                    add(frame, record, "nonpositive_box_area", "box must satisfy x2 > x1 and y2 > y1")
                if x1 < -tolerance_x or x2 < -tolerance_x or x1 > width + tolerance_x or x2 > width + tolerance_x:
                    add(
                        frame,
                        record,
                        "horizontal_bound_tolerance",
                        f"x coordinates exceed [{-tolerance_x:g}, {width + tolerance_x:g}]",
                    )
                if y1 < -tolerance_y or y2 < -tolerance_y or y1 > height + tolerance_y or y2 > height + tolerance_y:
                    add(
                        frame,
                        record,
                        "vertical_bound_tolerance",
                        f"y coordinates exceed [{-tolerance_y:g}, {height + tolerance_y:g}]",
                    )
                if x2 <= 0 or x1 >= width or y2 <= 0 or y1 >= height:
                    add(frame, record, "no_frame_intersection", "box does not have positive-area intersection with the source frame")
            if not math.isfinite(record.confidence):
                add(frame, record, "nonfinite_confidence", f"confidence is not finite: {record.confidence}")
            elif not 0.0 <= record.confidence <= 1.0:
                if kind == "detector":
                    add(frame, record, "confidence_out_of_range", f"detector confidence is outside [0, 1]: {record.confidence}")
                else:
                    add(
                        frame,
                        record,
                        "tracker_confidence_outside_unit_interval",
                        f"finite tracker score is outside [0, 1] and is not assumed to be a normalized probability: {record.confidence}",
                        "WARN",
                    )
            if kind == "tracker" and record.label == "person":
                if record.track_id is None or record.track_id < 0:
                    add(frame, record, "invalid_person_track_id", f"person track ID must be a nonnegative integer: {record.track_id}")
                elif record.track_id in seen_person_ids:
                    add(frame, record, "duplicate_person_track_id_in_frame", f"person track ID {record.track_id} occurs more than once in this frame")
                else:
                    seen_person_ids.add(record.track_id)
    return issues


def max_per_frame(records_by_frame: dict[int, list[Record]], expected_frames: Iterable[int], label: str | None = None) -> dict[str, Any]:
    counts = {
        frame: sum(record.label == label for record in records_by_frame.get(frame, [])) if label else len(records_by_frame.get(frame, []))
        for frame in expected_frames
    }
    maximum = max(counts.values(), default=0)
    return {"count": maximum, "frames": [frame for frame, count in counts.items() if count == maximum]}


def box_extrema(records_by_frame: dict[int, list[Record]]) -> dict[str, float | None]:
    boxes = [record.box for records in records_by_frame.values() for record in records if all(math.isfinite(v) for v in record.box)]
    if not boxes:
        return {"min_x1": None, "min_y1": None, "max_x2": None, "max_y2": None}
    return {
        "min_x1": min(box[0] for box in boxes),
        "min_y1": min(box[1] for box in boxes),
        "max_x2": max(box[2] for box in boxes),
        "max_y2": max(box[3] for box in boxes),
    }


def per_class_records(records_by_frame: dict[int, list[Record]], labels: Iterable[str]) -> dict[str, int]:
    counts = Counter(record.label for records in records_by_frame.values() for record in records)
    ordered: dict[str, int] = {label: counts.get(label, 0) for label in labels}
    for label in sorted(set(counts) - set(ordered)):
        ordered[label] = counts[label]
    return ordered


def output_metrics(
    records_by_frame: dict[int, list[Record]],
    kind: str,
    expected_frames: list[int],
    labels: Iterable[str],
    parse_errors: list[dict[str, Any]],
    validation_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    all_records = [record for records in records_by_frame.values() for record in records]
    person_records = [record for record in all_records if record.label == "person"]
    issue_counts = Counter(issue["category"] for issue in validation_issues)
    severity_counts = Counter(issue.get("severity", "FAIL") for issue in validation_issues)
    metrics: dict[str, Any] = {
        "records": len(all_records),
        "detections" if kind == "detector" else "tracker_records": len(all_records),
        "person_detections" if kind == "detector" else "person_tracker_records": len(person_records),
        "per_class_records": per_class_records(records_by_frame, labels),
        "frames_with_records": sum(bool(records_by_frame.get(frame)) for frame in expected_frames),
        "frames_with_person_records": sum(
            any(record.label == "person" for record in records_by_frame.get(frame, [])) for frame in expected_frames
        ),
        "max_objects_per_frame": max_per_frame(records_by_frame, expected_frames),
        "max_person_objects_per_frame": max_per_frame(records_by_frame, expected_frames, "person"),
        "observed_box_coordinate_extrema": box_extrema(records_by_frame),
        "parse_error_count": len(parse_errors),
        "parse_errors": parse_errors,
        "validation_issue_count": len(validation_issues),
        "validation_error_count": severity_counts.get("FAIL", 0),
        "validation_advisory_count": severity_counts.get("WARN", 0),
        "validation_issue_counts_by_category": dict(sorted(issue_counts.items())),
        "validation_issues": validation_issues,
    }
    if kind == "tracker":
        frames_by_id: dict[int, list[int]] = {}
        for frame, records in sorted(records_by_frame.items()):
            for record in records:
                if record.label == "person" and record.track_id is not None and record.track_id >= 0:
                    if frame not in frames_by_id.setdefault(record.track_id, []):
                        frames_by_id[record.track_id].append(frame)
        unique_ids = sorted(frames_by_id)
        multi_frame_ids = [track_id for track_id in unique_ids if len(frames_by_id[track_id]) > 1]
        metrics.update(
            {
                "unique_person_ids": unique_ids,
                "unique_person_id_count": len(unique_ids),
                "multi_frame_person_ids": multi_frame_ids,
                "multi_frame_person_id_count": len(multi_frame_ids),
                "person_id_frame_counts": {str(track_id): len(frames_by_id[track_id]) for track_id in unique_ids},
                "person_id_frames": {str(track_id): frames_by_id[track_id] for track_id in unique_ids},
            }
        )
    return metrics


def capacity_metrics(
    records_by_frame: dict[int, list[Record]], expected_frames: list[int], limit: int, basis: str
) -> dict[str, Any]:
    counts = {frame: len(records_by_frame.get(frame, [])) for frame in expected_frames}
    warning_frames = [frame for frame, count in counts.items() if count >= limit]
    return {
        "limit": limit,
        "comparison": "objects_per_frame >= limit",
        "basis": basis,
        "warning_frames": warning_frames,
        "warning_count": len(warning_frames),
        "warnings": [
            {"frame": frame, "objects": counts[frame], "limit": limit, "detail": "output reached or exceeded the configured capacity warning threshold"}
            for frame in warning_frames
        ],
        "max_objects_per_frame": max_per_frame(records_by_frame, expected_frames),
    }


def output_set_sha256(directory: Path, actual_frames: Iterable[int]) -> dict[str, str]:
    digest = hashlib.sha256()
    for frame in sorted(actual_frames):
        path = directory / frame_filename(frame)
        digest.update(f"{path.name}\0{sha256(path)}\n".encode("utf-8"))
    return {
        "algorithm": "SHA-256 over sorted lines of filename, NUL, file SHA-256, newline",
        "value": digest.hexdigest(),
    }


def analyze_run_log(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = ANSI_ESCAPE.sub("", raw).splitlines()

    def matches(fragment: str) -> list[dict[str, Any]]:
        return [{"line": number, "text": line.strip()} for number, line in enumerate(lines, 1) if fragment in line]

    deserialize_path: str | None = None
    deserialize_line: int | None = None
    deserialize_pattern = re.compile(r"deserialized trt engine from\s*:(\S+)")
    for number, line in enumerate(lines, 1):
        match = deserialize_pattern.search(line)
        if match:
            deserialize_path = match.group(1)
            deserialize_line = number
            break
    use_matches = matches("Use deserialized engine model:")
    used_path: str | None = None
    if use_matches:
        used_path = use_matches[0]["text"].split("Use deserialized engine model:", 1)[1].strip()
    pipeline_running = matches("Pipeline running")
    source_eos = matches("Successfully handled EOS for source_id=0")
    received_eos = matches("Received EOS. Exiting")
    successful = matches("App run successful")
    warnings = [
        {"line": number, "text": line.strip()}
        for number, line in enumerate(lines, 1)
        if "WARNING" in line
    ]
    engine_deserialized = bool(
        deserialize_path
        and used_path
        and deserialize_path == used_path
        and matches("Load new model:")
    )
    run_successful = bool(pipeline_running and source_eos and received_eos and successful)
    return {
        "path": str(path),
        "sha256": sha256(path),
        "deepstream_version_evidence": matches("DeepStreamSDK"),
        "engine_deserialization": {
            "passed": engine_deserialized,
            "deserialized_path": deserialize_path,
            "used_path": used_path,
            "evidence": ([{"line": deserialize_line, "text": lines[deserialize_line - 1].strip()}] if deserialize_line else []) + use_matches + matches("Load new model:"),
        },
        "pipeline_running": pipeline_running,
        "source_eos": source_eos,
        "received_eos": received_eos,
        "application_success": successful,
        "status": "PASS" if engine_deserialized and run_successful else "FAIL",
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def workspace_path(value: str, root: Path) -> Path:
    if value.startswith("/workspace/"):
        return (root / value.removeprefix("/workspace/")).resolve()
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def read_ini(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    with path.open("r", encoding="utf-8") as handle:
        parser.read_file(handle)
    return parser


def configured_tracker_capacity(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    values = re.findall(r"^\s*maxTargetsPerStream\s*:\s*(\d+)\s*(?:#.*)?$", text, flags=re.MULTILINE)
    if len(values) != 1:
        raise ValueError(f"expected exactly one maxTargetsPerStream value in {path}; found {len(values)}")
    return int(values[0])


def encode_jpeg_under_limit(image: np.ndarray, path: Path, limit: int = IMAGE_SIZE_LIMIT_BYTES_EXCLUSIVE) -> dict[str, Any]:
    """Encode without changing source data; reduce only the evidence image if needed."""
    source_height, source_width = image.shape[:2]
    attempts: list[dict[str, Any]] = []
    for scale in (1.0, 0.85, 0.70, 0.55, 0.40):
        width = max(1, round(source_width * scale))
        height = max(1, round(source_height * scale))
        resized = image if scale == 1.0 else cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
        for quality in (92, 88, 82, 76, 70, 60):
            encoded_ok, encoded = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if not encoded_ok:
                continue
            size = int(encoded.size)
            attempts.append({"scale": scale, "quality": quality, "size_bytes": size})
            if size < limit:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(encoded.tobytes())
                return {
                    "size_bytes": size,
                    "width": width,
                    "height": height,
                    "jpeg_quality": quality,
                    "resize_scale_from_annotated_image": scale,
                    "encoding_attempts": attempts,
                }
    raise ValueError(f"could not encode {path} below {limit} bytes")


def track_color(track_id: int) -> tuple[int, int, int]:
    palette = (
        (80, 220, 80),
        (80, 200, 255),
        (220, 160, 80),
        (200, 120, 240),
        (100, 240, 240),
        (180, 220, 100),
        (240, 180, 180),
        (160, 240, 200),
    )
    return palette[track_id % len(palette)] if track_id >= 0 else (80, 80, 255)


def annotate_tracker_persons(
    frame: np.ndarray,
    records: list[Record],
    frame_index: int,
    timestamp_ms: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Draw only person rows from one tracker KITTI file; detector rows are never inputs."""
    height, width = frame.shape[:2]
    header_height = 52
    canvas = np.full((height + header_height, width, 3), (24, 28, 34), dtype=np.uint8)
    canvas[header_height:, :] = frame
    persons = [record for record in records if record.label == "person"]
    header = f"frame {frame_index:06d} | t={timestamp_ms:.3f} ms | tracker person records={len(persons)}"
    cv2.putText(canvas, header, (16, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.78, (245, 245, 245), 2, cv2.LINE_AA)
    drawn_ids: list[int] = []
    skipped: list[dict[str, Any]] = []
    for record in persons:
        if record.track_id is None or not all(math.isfinite(value) for value in record.box):
            skipped.append({"line": record.line, "reason": "missing ID or nonfinite box"})
            continue
        x1, y1, x2, y2 = record.box
        left = min(max(round(x1), 0), width - 1)
        top = min(max(round(y1), 0), height - 1)
        right = min(max(round(x2), 0), width - 1)
        bottom = min(max(round(y2), 0), height - 1)
        if right <= left or bottom <= top:
            skipped.append({"line": record.line, "track_id": record.track_id, "reason": "box has no visible area after drawing clip"})
            continue
        color = track_color(record.track_id)
        top += header_height
        bottom += header_height
        cv2.rectangle(canvas, (left, top), (right, bottom), color, 2, cv2.LINE_AA)
        label = f"person ID {record.track_id}"
        (text_width, text_height), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.52, 1)
        label_bottom = top - 6 if top - 6 >= header_height + text_height + 4 else min(top + text_height + 10, canvas.shape[0] - 2)
        label_left = min(left, max(0, width - text_width - 8))
        cv2.rectangle(
            canvas,
            (label_left, label_bottom - text_height - 5),
            (min(label_left + text_width + 8, width - 1), min(label_bottom + baseline, canvas.shape[0] - 1)),
            (20, 24, 28),
            -1,
        )
        cv2.putText(canvas, label, (label_left + 4, label_bottom), cv2.FONT_HERSHEY_SIMPLEX, 0.52, color, 1, cv2.LINE_AA)
        drawn_ids.append(record.track_id)
    return canvas, {
        "tracker_person_records": len(persons),
        "drawn_person_boxes": len(drawn_ids),
        "drawn_track_ids": drawn_ids,
        "skipped_person_records": skipped,
        "drawing_clips_boxes_to_frame": True,
    }


def decode_selected_frames(
    source: Path, sample_frames: list[int], expected_frames: int, width: int, height: int
) -> tuple[dict[str, Any], dict[int, np.ndarray]]:
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        capture.release()
        return {"opened": False, "error": "OpenCV could not open the source video"}, {}
    reported = {
        "width": int(round(capture.get(cv2.CAP_PROP_FRAME_WIDTH))),
        "height": int(round(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))),
        "fps": float(capture.get(cv2.CAP_PROP_FPS)),
        "frame_count": int(round(capture.get(cv2.CAP_PROP_FRAME_COUNT))),
    }
    wanted = set(sample_frames)
    selected: dict[int, np.ndarray] = {}
    decode_errors: list[dict[str, Any]] = []
    decoded = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if frame is None or frame.size == 0:
            decode_errors.append({"frame": decoded, "error": "empty decoded frame"})
            break
        if frame.shape[1] != width or frame.shape[0] != height:
            decode_errors.append({"frame": decoded, "error": f"decoded shape {frame.shape[1]}x{frame.shape[0]} != {width}x{height}"})
        if decoded in wanted:
            selected[decoded] = frame.copy()
        decoded += 1
    capture.release()
    info = {
        "opened": True,
        "capture_reported": reported,
        "decoded_frame_count": decoded,
        "expected_frame_count": expected_frames,
        "selected_frames_decoded": sorted(selected),
        "missing_selected_frames": sorted(wanted - set(selected)),
        "decode_errors": decode_errors,
        "clean_expected_eof_observed": not decode_errors and decoded == expected_frames,
        "note": "The false read after the expected final frame is treated as EOF; this host decode is for evidence, not a DeepStream rerun.",
    }
    return info, selected


def make_contact_sheet(annotated: dict[int, np.ndarray], sample_frames: list[int]) -> np.ndarray:
    columns = 3
    rows = math.ceil(len(sample_frames) / columns)
    tile_width = 640
    first = annotated[sample_frames[0]]
    tile_height = round(first.shape[0] * tile_width / first.shape[1])
    margin = 12
    title_height = 58
    sheet_width = margin + columns * (tile_width + margin)
    sheet_height = title_height + rows * (tile_height + margin)
    sheet = np.full((sheet_height, sheet_width, 3), (18, 22, 28), dtype=np.uint8)
    cv2.putText(
        sheet,
        "DeepStream cam01 smoke | tracker person boxes and IDs | not ground truth",
        (margin, 37),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.72,
        (245, 245, 245),
        2,
        cv2.LINE_AA,
    )
    for position, frame_index in enumerate(sample_frames):
        row, column = divmod(position, columns)
        x = margin + column * (tile_width + margin)
        y = title_height + row * (tile_height + margin)
        tile = cv2.resize(annotated[frame_index], (tile_width, tile_height), interpolation=cv2.INTER_AREA)
        sheet[y : y + tile_height, x : x + tile_width] = tile
    return sheet


def generate_evidence(
    root: Path,
    run_dir: Path,
    evidence_dir: Path,
    source: Path,
    fps: float,
    expected_frames: int,
    width: int,
    height: int,
    sample_frames: list[int],
    tracker_records: dict[int, list[Record]],
) -> dict[str, Any]:
    decode_info, decoded = decode_selected_frames(source, sample_frames, expected_frames, width, height)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    images: list[dict[str, Any]] = []
    annotated: dict[int, np.ndarray] = {}
    for frame_index in sample_frames:
        if frame_index not in decoded:
            continue
        timestamp_ms = frame_index * 1000.0 / fps
        canvas, annotation = annotate_tracker_persons(decoded[frame_index], tracker_records.get(frame_index, []), frame_index, timestamp_ms)
        path = evidence_dir / f"frame_{frame_index:06d}.jpg"
        encoding = encode_jpeg_under_limit(canvas, path)
        tracker_path = run_dir / "tracker" / frame_filename(frame_index)
        item = {
            "frame_index": frame_index,
            "timestamp_ms": timestamp_ms,
            "path": relative_path(path, root),
            "annotation_source": relative_path(tracker_path, root),
            "annotation_source_sha256": sha256(tracker_path),
            "annotation_policy": "person boxes and track IDs from tracker rows only; no detector overlays",
            "source_width": width,
            "source_height": height,
            **annotation,
            **encoding,
            "sha256": sha256(path),
        }
        images.append(item)
        annotated[frame_index] = canvas
    contact_sheet: dict[str, Any] | None = None
    if len(annotated) == len(sample_frames) and sample_frames:
        sheet = make_contact_sheet(annotated, sample_frames)
        sheet_path = evidence_dir / "contact_sheet.jpg"
        contact_sheet = {
            "path": relative_path(sheet_path, root),
            "sampled_frame_indices": sample_frames,
            **encode_jpeg_under_limit(sheet, sheet_path),
            "sha256": sha256(sheet_path),
        }
    all_images = images + ([contact_sheet] if contact_sheet else [])
    return {
        "sample_frames": sample_frames,
        "image_size_limit_bytes_exclusive": IMAGE_SIZE_LIMIT_BYTES_EXCLUSIVE,
        "video_decode": decode_info,
        "images": images,
        "contact_sheet": contact_sheet,
        "all_generated_images_under_limit": all(item["size_bytes"] < IMAGE_SIZE_LIMIT_BYTES_EXCLUSIVE for item in all_images),
    }


def build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=root)
    parser.add_argument("--run-dir", type=Path, default=root / "reports/validation/deepstream-cam01")
    parser.add_argument("--media-probe", type=Path, default=root / "reports/media/cam01/media_probe.json")
    parser.add_argument("--app-config", type=Path, default=root / "perception/deepstream/cam01_trafficcamnet_smoke.txt")
    parser.add_argument("--output-dir", type=Path, help="Defaults to run-dir; result.json and evidence/ are written here")
    parser.add_argument("--sample-frames", type=int, nargs="+", default=list(DEFAULT_SAMPLE_FRAMES))
    parser.add_argument("--detector-boundary-tolerance-ratio", type=float, default=0.10)
    parser.add_argument(
        "--tracker-boundary-tolerance-ratio",
        type=float,
        default=0.30,
        help="Expanded frame-bound allowance for NvDCF unclipped boxes (default: 30%%)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    run_dir = args.run_dir.resolve()
    output_dir = args.output_dir.resolve() if args.output_dir else run_dir
    media_probe_path = args.media_probe.resolve()
    app_config_path = args.app_config.resolve()
    checks = Checks()

    media_probe = json.loads(media_probe_path.read_text(encoding="utf-8"))
    container = media_probe["container_reported"]
    sequential = media_probe["sequential_decode"]
    width = int(container["width"])
    height = int(container["height"])
    fps = float(container["fps"])
    expected_frame_count = int(container["frame_count"])
    source = workspace_path(media_probe["source"]["path"], root)
    sample_frames = sorted(set(args.sample_frames))
    if sample_frames != args.sample_frames:
        raise ValueError("--sample-frames must be unique and sorted")
    if not sample_frames or sample_frames[0] < 0 or sample_frames[-1] >= expected_frame_count:
        raise ValueError("sample frames must be within the expected frame range")
    tolerance_ratios = {
        "detector": args.detector_boundary_tolerance_ratio,
        "tracker": args.tracker_boundary_tolerance_ratio,
    }
    if any(not 0.0 <= ratio <= 0.50 for ratio in tolerance_ratios.values()):
        raise ValueError("boundary tolerance ratios must be between 0 and 0.50")

    app_config = read_ini(app_config_path)
    infer_config_path = workspace_path(app_config["primary-gie"]["config-file"], root)
    tracker_config_path = workspace_path(app_config["tracker"]["ll-config-file"], root)
    engine_path = workspace_path(app_config["primary-gie"]["model-engine-file"], root)
    infer_config = read_ini(infer_config_path)
    labels_path = workspace_path(infer_config["property"]["labelfile-path"], root)
    labels = tuple(line.strip() for line in labels_path.read_text(encoding="utf-8").splitlines() if line.strip())
    detector_topk = infer_config.getint("class-attrs-all", "topk")
    tracker_capacity = configured_tracker_capacity(tracker_config_path)
    tracker_config_text = tracker_config_path.read_text(encoding="utf-8")
    unclipping_match = re.search(r"^\s*enableBboxUnClipping\s*:\s*(\d+)", tracker_config_text, flags=re.MULTILINE)
    tracker_unclipping_enabled = bool(unclipping_match and unclipping_match.group(1) == "1")

    checks.require("media_probe_frame_count", expected_frame_count == 710 and sequential["decoded_frame_count"] == expected_frame_count, f"Expected and sequentially decoded frames: {expected_frame_count}; probe decoded: {sequential['decoded_frame_count']}")
    checks.require("media_probe_geometry", (width, height) == (1920, 1080), f"Source geometry: {width}x{height}")
    checks.require("media_probe_clean_decode", sequential["clean_eof"] is True and not sequential["decode_errors"], f"Probe clean EOF: {sequential['clean_eof']}; decode errors: {len(sequential['decode_errors'])}")
    checks.require("trafficcamnet_label_map", labels == EXPECTED_LABELS and labels.index("person") == PERSON_CLASS_ID, f"Labels: {list(labels)}; person class ID: {labels.index('person') if 'person' in labels else 'missing'}")
    checks.require("detector_topk_configuration", detector_topk == 300, f"class-attrs-all topk={detector_topk}; class-agnostic warning threshold uses all detector records per frame")
    checks.require("tracker_capacity_configuration", tracker_capacity == 150, f"NvDCF maxTargetsPerStream={tracker_capacity}")

    source_identity = file_identity(source, root, role="unmodified source video used by the smoke run and host evidence decoder")
    source_identity["declared_sha256_in_media_probe"] = media_probe["source"]["sha256"]
    source_identity["sha256_matches_media_probe"] = source_identity["sha256"] == media_probe["source"]["sha256"]
    checks.require("source_video_identity", source_identity["sha256_matches_media_probe"], f"Source SHA-256: {source_identity['sha256']}")

    artifacts = {
        "source_video": source_identity,
        "media_probe": file_identity(media_probe_path, root),
        "application_config": file_identity(app_config_path, root),
        "inference_config": file_identity(infer_config_path, root),
        "tracker_config": file_identity(tracker_config_path, root),
        "labels": file_identity(labels_path, root),
        "models_config": file_identity(root / "configs/models.yaml", root, role="PRD model-selection configuration; not the TrafficCamNet smoke baseline selection"),
        "engine": file_identity(engine_path, root, role="local TensorRT engine deserialized by the validated run"),
        "run_log": file_identity(run_dir / "run.log", root),
    }
    engine_build_log = run_dir / "engine-build.log"
    if engine_build_log.is_file():
        artifacts["engine_build_log"] = file_identity(engine_build_log, root)

    run_log = analyze_run_log(run_dir / "run.log")
    run_log["path"] = relative_path(run_dir / "run.log", root)
    checks.require("run_log_local_engine_deserialization", run_log["engine_deserialization"]["passed"], f"Deserialized and used local engine: {run_log['engine_deserialization']['deserialized_path']}")
    checks.require("run_log_successful_eos", run_log["status"] == "PASS", f"Pipeline running, source EOS, received EOS, and App run successful markers present; status={run_log['status']}")
    if run_log["warning_count"]:
        checks.add("run_log_warnings", "WARN", f"{run_log['warning_count']} warning lines occurred in the otherwise successful run log", warning_count=run_log["warning_count"])

    expected_frames = list(range(expected_frame_count))
    output_data: dict[str, dict[str, Any]] = {}
    parsed: dict[str, dict[int, list[Record]]] = {}
    for kind in ("detector", "tracker"):
        directory = run_dir / kind
        coverage = discover_frame_files(directory, expected_frame_count)
        records_by_frame, parse_errors = load_output(directory, kind, coverage)
        issues = validate_records(records_by_frame, kind, width, height, tolerance_ratios[kind], labels)
        metrics = output_metrics(records_by_frame, kind, expected_frames, labels, parse_errors, issues)
        metrics["frame_coverage"] = coverage
        metrics["output_set_sha256"] = output_set_sha256(directory, coverage["actual_frames"])
        output_data[kind] = metrics
        parsed[kind] = records_by_frame
        checks.require(
            f"{kind}_exact_frame_coverage",
            coverage["exact_contiguous_coverage"],
            f"Actual {coverage['actual_frame_count']} / expected {expected_frame_count} frames; missing={len(coverage['missing_frames'])}; out-of-range={len(coverage['out_of_range_frames'])}; unexpected={len(coverage['unexpected_entries'])}",
        )
        checks.require(f"{kind}_row_parsing", not parse_errors, f"Parsed records: {metrics['records']}; parse errors: {len(parse_errors)}")
        checks.require(f"{kind}_labels", "unknown_label" not in metrics["validation_issue_counts_by_category"], f"Per-class records: {metrics['per_class_records']}")
        box_categories = {"nonfinite_box", "nonpositive_box_area", "horizontal_bound_tolerance", "vertical_bound_tolerance", "no_frame_intersection"}
        box_issue_count = sum(metrics["validation_issue_counts_by_category"].get(category, 0) for category in box_categories)
        checks.require(
            f"{kind}_finite_plausible_boxes",
            box_issue_count == 0,
            f"Box issues: {box_issue_count}; tolerance ratio: {tolerance_ratios[kind]}; observed extrema: {metrics['observed_box_coordinate_extrema']}",
        )
        confidence_failure_categories = {"nonfinite_confidence"}
        if kind == "detector":
            confidence_failure_categories.add("confidence_out_of_range")
        confidence_issue_count = sum(metrics["validation_issue_counts_by_category"].get(category, 0) for category in confidence_failure_categories)
        checks.require(f"{kind}_confidence_fields", confidence_issue_count == 0, f"Nonfinite or disallowed confidence fields: {confidence_issue_count}")
        if kind == "tracker":
            confidence_advisories = metrics["validation_issue_counts_by_category"].get("tracker_confidence_outside_unit_interval", 0)
            if confidence_advisories:
                checks.add(
                    "tracker_confidence_unit_interval_advisory",
                    "WARN",
                    f"{confidence_advisories} finite tracker scores fall outside [0, 1]; they are retained and not interpreted as normalized probabilities",
                    count=confidence_advisories,
                )

    detector = output_data["detector"]
    tracker = output_data["tracker"]
    checks.require("nonempty_person_detections", detector["person_detections"] > 0, f"Detector person records: {detector['person_detections']}")
    invalid_person_ids = tracker["validation_issue_counts_by_category"].get("invalid_person_track_id", 0)
    checks.require("tracker_nonnegative_person_ids", invalid_person_ids == 0 and tracker["parse_error_count"] == 0, f"Invalid person IDs: {invalid_person_ids}; tracker parse errors: {tracker['parse_error_count']}")
    duplicate_person_ids = tracker["validation_issue_counts_by_category"].get("duplicate_person_track_id_in_frame", 0)
    checks.require("tracker_person_id_unique_within_frame", duplicate_person_ids == 0, f"Duplicate person ID records within a frame: {duplicate_person_ids}")
    checks.require("person_id_observed_across_multiple_frames", tracker["multi_frame_person_id_count"] > 0, f"Unique person IDs: {tracker['unique_person_id_count']}; multi-frame person IDs: {tracker['multi_frame_person_id_count']}")

    capacity = {
        "detector": capacity_metrics(parsed["detector"], expected_frames, detector_topk, "class-agnostic: all detector labels per frame"),
        "tracker": capacity_metrics(parsed["tracker"], expected_frames, tracker_capacity, "all tracker labels per frame; a proxy for NvDCF stream capacity"),
    }
    capacity["warning_count"] = capacity["detector"]["warning_count"] + capacity["tracker"]["warning_count"]
    for kind in ("detector", "tracker"):
        item = capacity[kind]
        maximum = item["max_objects_per_frame"]["count"]
        detail = f"Maximum {kind} objects/frame: {maximum}; limit: {item['limit']}; warning frames: {item['warning_count']}"
        checks.add(f"{kind}_capacity_headroom", "WARN" if item["warning_count"] else "PASS", detail, warning_frames=item["warning_frames"])

    evidence: dict[str, Any]
    try:
        evidence = generate_evidence(
            root,
            run_dir,
            output_dir / "evidence",
            source,
            fps,
            expected_frame_count,
            width,
            height,
            sample_frames,
            parsed["tracker"],
        )
        decode = evidence["video_decode"]
        decode_ok = bool(decode.get("opened")) and decode.get("decoded_frame_count") == expected_frame_count and not decode.get("decode_errors") and not decode.get("missing_selected_frames")
        checks.require("source_video_sequential_decode_for_evidence", decode_ok, f"Host decoded frames: {decode.get('decoded_frame_count')}; missing selected frames: {decode.get('missing_selected_frames')}; errors: {len(decode.get('decode_errors', []))}")
        images = evidence["images"] + ([evidence["contact_sheet"]] if evidence["contact_sheet"] else [])
        checks.require("tracker_evidence_frame_coverage", [item["frame_index"] for item in evidence["images"]] == sample_frames and evidence["contact_sheet"] is not None, f"Annotated frames: {[item['frame_index'] for item in evidence['images']]}")
        checks.require("tracker_evidence_image_sizes", bool(images) and all(item["size_bytes"] < IMAGE_SIZE_LIMIT_BYTES_EXCLUSIVE for item in images), f"Generated {len(images)} images; maximum bytes: {max((item['size_bytes'] for item in images), default=0)}; exclusive limit: {IMAGE_SIZE_LIMIT_BYTES_EXCLUSIVE}")
        annotation_errors = [
            {"frame": item["frame_index"], "tracker_person_records": item["tracker_person_records"], "drawn_person_boxes": item["drawn_person_boxes"], "skipped": item["skipped_person_records"]}
            for item in evidence["images"]
            if item["tracker_person_records"] != item["drawn_person_boxes"] or item["skipped_person_records"]
        ]
        checks.require("tracker_evidence_annotation_completeness", not annotation_errors, f"Frames with skipped or undrawn tracker person records: {len(annotation_errors)}")
    except Exception as exc:
        evidence = {"error": str(exc), "sample_frames": sample_frames}
        checks.add("tracker_evidence_generation", "FAIL", str(exc))

    warnings: list[dict[str, Any]] = []
    if run_log["warning_count"]:
        warnings.append({"type": "run_log", "count": run_log["warning_count"], "detail": "Nonfatal GStreamer/plugin/audio warnings are preserved under run_log.warnings."})
    tracker_confidence_advisories = tracker["validation_issue_counts_by_category"].get("tracker_confidence_outside_unit_interval", 0)
    if tracker_confidence_advisories:
        warnings.append(
            {
                "type": "tracker_confidence_range",
                "count": tracker_confidence_advisories,
                "detail": "Finite tracker scores outside [0, 1] are retained as non-normalized-score advisories; detector confidence remains strictly bounded.",
            }
        )
    for kind in ("detector", "tracker"):
        if capacity[kind]["warning_count"]:
            warnings.append({"type": f"{kind}_capacity", "count": capacity[kind]["warning_count"], "detail": f"Frames at or above the {kind} output capacity warning threshold."})

    limitations = [
        "This is a TrafficCamNet plus NvDCF plumbing smoke validation, not the selected PRD detector baseline and not a baseline qualification.",
        "No ground-truth annotations were supplied or created. Precision, recall, localization accuracy, IDF1/HOTA, exact people counts, and detection correctness are not measured or claimed.",
        "Dense crowds and occlusion can cause missed, merged, duplicate, or spurious boxes and track-ID switches. Record counts are model/tracker output counts, not true person counts.",
        "The person class ID 2 is established from the four-line TrafficCamNet label file. KITTI rows contain labels rather than numeric class IDs, so per-row numeric class-ID emission cannot be independently checked here.",
        "Tracker confidence fields are validated as finite scores but are not assumed to be calibrated probabilities; values outside [0, 1] are reported as advisories rather than silently clipped.",
        "Capacity warnings are output-count proxies. Counts below topk do not prove the absence of internal truncation, and tracker output does not expose all NvDCF shadow targets included by maxTargetsPerStream.",
        "Annotated images are mechanical overlays of tracker person rows, clipped only for drawing. They are not human-verified boxes and do not establish detection or association accuracy.",
        "The host OpenCV decode supplies image backgrounds and frame alignment evidence only; it does not rerun DeepStream or compare inference numerically with another runtime.",
        "The log evidences local engine deserialization and a successful EOS run; the supplied run ordinal (second run) is contextual and is not independently counted by this validator.",
    ]

    payload = {
        "schema_version": 1,
        "generated_at_utc": utcnow(),
        "result": "PASS" if checks.failures == 0 else "FAIL",
        "scope": "Real DeepStream smoke-output structure, record plumbing, capacity proxies, run-log status, and tracker-only visual evidence",
        "project_root": str(root),
        "run_directory": relative_path(run_dir, root),
        "model_context": {
            "name": "TrafficCamNet",
            "tracker": "NvDCF",
            "labels": list(labels),
            "person_label": "person",
            "person_class_id": PERSON_CLASS_ID,
            "selected_prd_baseline": False,
        },
        "media": {
            "source": relative_path(source, root),
            "width": width,
            "height": height,
            "fps": fps,
            "expected_frames": expected_frame_count,
            "duration_seconds_at_reported_fps": expected_frame_count / fps,
        },
        "validation_policy": {
            "detector_fields": DETECTOR_FIELD_COUNT,
            "detector_box_indices_zero_based": [4, 5, 6, 7],
            "tracker_fields": TRACKER_FIELD_COUNT,
            "tracker_id_index_zero_based": 1,
            "tracker_box_indices_zero_based": [5, 6, 7, 8],
            "confidence_index": "last",
            "blank_lines": "ignored",
            "confidence_requirements": {
                "detector": "finite and within [0, 1]",
                "tracker": "finite; values outside [0, 1] are preserved as warnings, not assumed to be normalized probabilities",
            },
            "frame_boundary_tolerance_ratio": tolerance_ratios,
            "frame_boundary_tolerance_pixels": {
                kind: {"x": width * ratio, "y": height * ratio} for kind, ratio in tolerance_ratios.items()
            },
            "tracker_tolerance_rationale": f"NvDCF enableBboxUnClipping=1 can extrapolate border boxes; configured allowances are {tolerance_ratios['tracker']:.0%} for tracker and {tolerance_ratios['detector']:.0%} for detector.",
            "box_requirements": "finite; x2 > x1; y2 > y1; positive-area frame intersection; all coordinates within the expanded frame bounds",
        },
        "configuration": {
            "detector_topk": detector_topk,
            "detector_capacity_warning_basis": "class-agnostic total detector records per frame >= topk",
            "nvdcf_max_targets_per_stream": tracker_capacity,
            "nvdcf_bbox_unclipping_enabled": tracker_unclipping_enabled,
            "tracker_capacity_warning_basis": "total tracker records per frame >= maxTargetsPerStream",
        },
        "summary": {
            "check_count": len(checks.items),
            "failed_check_count": checks.failures,
            "warning_check_count": checks.warnings,
            "capacity_warning_count": capacity["warning_count"],
        },
        "checks": checks.items,
        "detector": detector,
        "tracker": tracker,
        "capacity": capacity,
        "artifacts": artifacts,
        "run_log": run_log,
        "warnings": warnings,
        "evidence": evidence,
        "limitations": limitations,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "result.json"
    result_path.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"DeepStream smoke validation: {payload['result']}")
    print(f"Detector records/person: {detector['records']} / {detector['person_detections']}")
    print(f"Tracker records/person: {tracker['records']} / {tracker['person_tracker_records']}")
    print(f"Unique/multi-frame person IDs: {tracker['unique_person_id_count']} / {tracker['multi_frame_person_id_count']}")
    print(f"Result: {result_path}")
    return 2 if checks.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
