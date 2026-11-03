#!/usr/bin/env python3
"""Crosscheck post-NvDCF metadata against independent ONNX person decodes."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def iou(left, right):
    lx1, ly1, lx2, ly2 = left
    rx1, ry1, rx2, ry2 = right
    ix1, iy1, ix2, iy2 = max(lx1, rx1), max(ly1, ry1), min(lx2, rx2), min(ly2, ry2)
    intersection = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = (lx2 - lx1) * (ly2 - ly1) + (rx2 - rx1) * (ry2 - ry1) - intersection
    return intersection / union if union > 0 else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--export-run", type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    export = args.export_run.resolve()
    rows = {row["frame_number"]: row for row in map(json.loads, (run / "frames.jsonl").open(encoding="utf-8"))}
    frame_results = []
    for index in (141, 283):
        reference = json.loads((export / f"onnx_decode_frame_{index:06d}.json").read_text(encoding="utf-8"))["detections"]
        objects = rows[index]["objects"]
        pairs = []
        for ds_index, obj in enumerate(objects):
            box = obj["bbox"]
            ds_xyxy = [box["x"], box["y"], box["x"] + box["width"], box["y"] + box["height"]]
            for ref_index, ref in enumerate(reference):
                score_delta = abs(float(obj["confidence"]) - float(ref["confidence"]))
                overlap = iou(ds_xyxy, ref["bbox_xyxy_pixels"])
                if score_delta <= 0.02 and overlap >= 0.5:
                    pairs.append((overlap, score_delta, ds_index, ref_index, ds_xyxy, ref["bbox_xyxy_pixels"]))
        pairs.sort(key=lambda item: (-item[0], item[1], item[2], item[3]))
        used_ds, used_ref, matches = set(), set(), []
        for overlap, score_delta, ds_index, ref_index, ds_xyxy, ref_xyxy in pairs:
            if ds_index in used_ds or ref_index in used_ref:
                continue
            used_ds.add(ds_index)
            used_ref.add(ref_index)
            matches.append({"ds_index": ds_index, "onnx_query_index": reference[ref_index]["query_index"],
                            "iou": overlap, "score_abs_delta": score_delta,
                            "bbox_xyxy_max_abs_delta": max(abs(a - b) for a, b in zip(ds_xyxy, ref_xyxy))})
        unmatched = sorted(set(range(len(objects))) - used_ds)
        frame_results.append({
            "frame_index": index, "onnx_person_decodes": len(reference), "post_nvdcf_person_objects": len(objects),
            "one_to_one_matches": len(matches), "unmatched_post_nvdcf_objects": unmatched,
            "match_criteria": {"score_abs_delta_max": 0.02, "bbox_iou_min": 0.5},
            "minimum_matched_iou": min((item["iou"] for item in matches), default=None),
            "maximum_matched_score_abs_delta": max((item["score_abs_delta"] for item in matches), default=None),
            "maximum_matched_bbox_xyxy_abs_delta_pixels": max((item["bbox_xyxy_max_abs_delta"] for item in matches), default=None),
            "all_post_nvdcf_objects_crosschecked": not unmatched and len(objects) > 0,
            "matches": matches,
        })
    passed = all(item["all_post_nvdcf_objects_crosschecked"] for item in frame_results)
    result = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "result": "PASS" if passed else "FAIL", "frames": frame_results,
        "scope": "Post-NvDCF objects matched one-to-one to independent ONNX Runtime person decodes on two real frames. This is implementation/geometry consistency, not raw pre-tracker TensorRT tensor parity or labeled accuracy.",
        "count_note": "Post-tracker object counts may be lower than ONNX raw person decode counts because NvDCF target management can reject/merge targets; no raw detector count is synthesized."
    }
    destination = run / "onnx-deepstream-crosscheck.json"
    if destination.exists():
        raise SystemExit(f"Refusing to overwrite {destination}")
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**result, "frames": [{key: value for key, value in frame.items() if key != "matches"} for frame in frame_results]}, indent=2))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
