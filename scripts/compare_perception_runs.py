#!/usr/bin/env python3
"""Frame-by-frame comparison of two extractor JSONL outputs for the same source (perception determinism)."""
import argparse
import json
from pathlib import Path


def load(path: Path) -> dict[int, dict]:
    return {row["frame_number"]: row for row in map(json.loads, path.open(encoding="utf-8"))}


def persons(row: dict) -> list[dict]:
    return [o for o in row["objects"] if o["label"] == "person"]


def compare(a_path: Path, b_path: Path) -> dict:
    a, b = load(a_path), load(b_path)
    frames = sorted(set(a) & set(b))
    same_boxes = same_ids = 0
    first = None
    for n in frames:
        boxes_a = sorted((round(o["bbox"]["x"], 2), round(o["bbox"]["y"], 2), round(o["bbox"]["width"], 2), round(o["confidence"], 5)) for o in persons(a[n]))
        boxes_b = sorted((round(o["bbox"]["x"], 2), round(o["bbox"]["y"], 2), round(o["bbox"]["width"], 2), round(o["confidence"], 5)) for o in persons(b[n]))
        same_boxes += boxes_a == boxes_b
        same_ids += sorted(o["track_id"] or "-" for o in persons(a[n])) == sorted(o["track_id"] or "-" for o in persons(b[n]))
        if first is None and boxes_a != boxes_b:
            first = n
    return {"a": a_path.name, "b": b_path.name, "common_frames": len(frames),
            "frames_identical_person_boxes_scores": same_boxes, "frames_identical_track_id_sets": same_ids,
            "first_differing_frame": first}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pair", nargs=3, action="append", metavar=("LABEL", "A", "B"), required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit(f"Refusing to overwrite {args.out}")
    result = {"schema_version": 1, "comparisons": {label: compare(Path(a), Path(b)) for label, a, b in args.pair},
              "note": "Same source clip, same pinned container/model/config; differences are run-to-run perception variation."}
    args.out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
