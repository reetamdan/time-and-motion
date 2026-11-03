#!/usr/bin/env python3
"""Build the cam01 annotation package: frames to label, unreviewed detector proposals, and templates.

Nothing here is ground truth. Proposals are copied from recorded detector runs and marked
PROPOSAL_UNREVIEWED; they may be imported into an annotation tool to speed up labelling but must be
corrected and then marked REVIEWED by a human before evaluate_detections.py will use them.

Frame identity: frame_NNNNNN.jpg is the NNNNNN-th decoded frame (ffmpeg ``n``), which equals the
DeepStream ``frame_number`` of the same frame (both decode every frame of the file in order; the
pipeline's PTS is offset by the stream's B-frame reorder delay, so frames are identified by index).

Run: .venv/Scripts/python.exe scripts/evaluation/build_annotation_package.py
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "demo" / "cam01.mp4"
PACKAGE = ROOT / "data" / "annotations" / "cam01-v1"
WIDTH, HEIGHT = 1920, 1080
# Detection set: every 35th frame from 15 (spread across the camera pan). Tier 1 = the first 12.
DETECTION_FRAMES = list(range(15, 710, 35))
# Tracking segments: consecutive frames for MOTA / IDF1 / ID switches.
TRACKING_SEGMENTS = {"tracking-A": list(range(200, 220)), "tracking-B": list(range(500, 520))}
PROPOSAL_SOURCES = {
    "trafficcamnet-smoke": ("reports/validation/service-maker-cam01/frames.jsonl", 2),
    "rt-detr-person-candidate": ("reports/model-evaluation/rt-detr/deepstream/run-20261026T143055Z/frames.jsonl", 0),
    "rf-detr-person-candidate": ("reports/model-evaluation/rf-detr/deepstream/integration-20261026T162321Z/frames.jsonl", 0),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_frames(numbers: list[int], directory: Path) -> None:
    """One decoding pass; select by decoded index n, written in ascending order then renamed."""
    temporary = directory / "_extract"
    temporary.mkdir()
    expression = "+".join(f"eq(n\\,{n})" for n in numbers)
    command = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", str(SOURCE), "-vf", f"select={expression}",
               "-fps_mode", "passthrough", "-q:v", "2", str(temporary / "%06d.jpg")]
    subprocess.run(command, check=True, timeout=600)
    written = sorted(temporary.glob("*.jpg"))
    if len(written) != len(numbers):
        raise RuntimeError(f"expected {len(numbers)} frames, ffmpeg wrote {len(written)}")
    for path, number in zip(written, sorted(numbers)):
        path.rename(directory / f"frame_{number:06d}.jpg")
    temporary.rmdir()


def proposals(model: str, relative: str, person_class: int, images: list[dict]) -> dict:
    wanted = {image["frame_number"]: image["id"] for image in images}
    annotations = []
    source = ROOT / relative
    for line in source.open(encoding="utf-8"):
        record = json.loads(line)
        if record["frame_number"] not in wanted:
            continue
        for obj in record["objects"]:
            if obj.get("class_id") != person_class or obj.get("label") != "person":
                continue
            box = obj["bbox"]
            annotations.append({
                "id": len(annotations) + 1, "image_id": wanted[record["frame_number"]], "category_id": 1,
                "bbox": [round(box["x"], 2), round(box["y"], 2), round(box["width"], 2), round(box["height"], 2)],
                "area": round(box["width"] * box["height"], 2), "iscrowd": 0, "score": obj.get("confidence"),
                "attributes": {"proposal_track_id": obj.get("track_id")},
            })
    return {
        "info": {"dataset": "cam01-v1", "status": "PROPOSAL_UNREVIEWED", "model": model, "source_jsonl": relative,
                 "source_jsonl_sha256": sha256(source), "stage": "post_tracker production pipeline output (bbox field)",
                 "note": "Detector output, not ground truth. Correct every box before marking a copy REVIEWED."},
        "images": images, "categories": [{"id": 1, "name": "person"}], "annotations": annotations,
    }


def main() -> int:
    if PACKAGE.exists():
        raise SystemExit(f"{PACKAGE} already exists; the package is versioned - bump the version instead of overwriting")
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is required")
    frames_dir = PACKAGE / "frames"
    frames_dir.mkdir(parents=True)
    sets = {"detection": DETECTION_FRAMES, **TRACKING_SEGMENTS}
    numbers = sorted({n for frames in sets.values() for n in frames})
    extract_frames(numbers, frames_dir)
    membership = {n: [name for name, frames in sets.items() if n in frames] for n in numbers}
    images = [{"id": n, "file_name": f"frames/frame_{n:06d}.jpg", "width": WIDTH, "height": HEIGHT, "frame_number": n,
               "sets": membership[n], "tier": 1 if n in DETECTION_FRAMES[:12] else (2 if "tracking-A" in membership[n] else 3),
               "ignore_regions": [], "annotation_status": "UNREVIEWED", "sha256": sha256(frames_dir / f"frame_{n:06d}.jpg")} for n in numbers]
    source_sha = sha256(SOURCE)
    template = {
        "info": {"dataset": "cam01-v1", "source": "demo/cam01.mp4", "source_sha256": source_sha,
                 "frame_index_basis": "decoded frame index n == DeepStream frame_number",
                 "review": {"status": "UNREVIEWED", "annotators": [], "reviewer": None, "reviewed_at": None,
                            "tool": None, "notes": None}},
        "images": images, "categories": [{"id": 1, "name": "person"}], "annotations": [],
    }
    (PACKAGE / "annotations.template.json").write_text(json.dumps(template, indent=1) + "\n", encoding="utf-8")
    (PACKAGE / "proposals").mkdir()
    counts = {}
    for model, (relative, person_class) in PROPOSAL_SOURCES.items():
        document = proposals(model, relative, person_class, images)
        counts[model] = len(document["annotations"])
        (PACKAGE / "proposals" / f"{model}.coco.json").write_text(json.dumps(document) + "\n", encoding="utf-8")
    shutil.copy(ROOT / "scripts" / "evaluation" / "evaluation-settings.json", PACKAGE / "evaluation-settings.json")
    manifest = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(), "source_sha256": source_sha,
        "sets": {name: frames for name, frames in sets.items()}, "frames": len(numbers),
        "proposal_boxes": counts, "status": "AWAITING_HUMAN_ANNOTATION",
        "files": {str(p.relative_to(PACKAGE)).replace("\\", "/"): sha256(p) for p in sorted(PACKAGE.rglob("*")) if p.is_file()},
    }
    (PACKAGE / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"package": str(PACKAGE.relative_to(ROOT)), "frames": len(numbers), "proposal_boxes": counts}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
