#!/usr/bin/env python3
"""Analyse TrafficCamNet nvinfer preprocessing variants (diagnostic, no labels).

A. Which resampling the dumped nvinfer input most resembles (BT.709 ffmpeg decode as source).
B. Per-variant person detection/track behaviour over the full clip (proxies, NOT accuracy).
C. Cross-variant person agreement on frames 141/283 (IoU >= 0.5 one-to-one, no score criterion).
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "rt-detr-candidate"))
from compare_pretracker import resize_nearest, resize_bilinear_no_aa, tensor_diff, strict_match, xyxy  # noqa: E402

REFERENCE = ROOT / "reports" / "model-evaluation" / "rt-detr" / "diagnostics" / "corrected-reference-20261026T160220Z"
H, W = 544, 960


def chw(img):
    return (np.asarray(img, np.float64) / 255.0).transpose(2, 0, 1).astype(np.float32)


def person_objects(row):
    return [o for o in row["objects"] if o["label"] == "person"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("run_dirs", nargs="+", type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit(f"Refusing to overwrite {args.out}")
    runs = {}
    for run in args.run_dirs:
        run = run.resolve()
        pre = [json.loads(l) for l in (run / "pre_tracker.jsonl").open(encoding="utf-8")]
        post = [json.loads(l) for l in (run / "post_tracker.jsonl").open(encoding="utf-8")]
        stage_a = {}
        for index in (141, 283):
            ds = np.fromfile(run / f"ds_input_{index:06d}.f32le", "<f4").reshape(3, H, W)
            rgb = np.asarray(Image.open(REFERENCE / f"frame_{index:06d}_bt709.png").convert("RGB"))
            em = {"nearest_top_left": chw(resize_nearest(rgb, H, W, "top_left")),
                  "nearest_half_pixel": chw(resize_nearest(rgb, H, W, "half_pixel")),
                  "bilinear_half_pixel_no_aa": chw(np.clip(np.rint(resize_bilinear_no_aa(rgb, H, W)), 0, 255)),
                  "pil_bilinear_antialias": chw(Image.fromarray(rgb).resize((W, H), Image.Resampling.BILINEAR)),
                  "pil_box_area": chw(Image.fromarray(rgb).resize((W, H), Image.Resampling.BOX))}
            diffs = {k: tensor_diff(ds, v)["mean_abs"] for k, v in em.items()}
            stage_a[index] = {"mean_abs_by_emulation": diffs, "closest": min(diffs, key=diffs.get)}
        counts = [len(person_objects(r)) for r in pre]
        tracks = {}
        for r in post:
            for o in person_objects(r):
                if o["track_id"] is not None:
                    tracks.setdefault(o["track_id"], []).append(r["frame_number"])
        lengths = [len(v) for v in tracks.values()]
        post_counts = [len(person_objects(r)) for r in post]
        runs[run.name] = {
            "run_dir": str(run.relative_to(ROOT)),
            "A_input_resemblance": stage_a,
            "B_behaviour_proxies": {
                "pre_tracker_person_detections_total": sum(counts),
                "pre_tracker_person_per_frame_mean": mean(counts), "pre_tracker_person_per_frame_median": median(counts),
                "pre_tracker_count_jitter_mean_abs_consecutive": mean(abs(a - b) for a, b in zip(counts, counts[1:])),
                "post_tracker_person_per_frame_mean": mean(post_counts),
                "unique_person_track_ids": len(tracks), "multi_frame_track_ids": sum(l > 1 for l in lengths),
                "track_length_frames_median": median(lengths) if lengths else None,
                "tracks_ge_30_frames": sum(l >= 30 for l in lengths),
            },
            "_pre": {r["frame_number"]: r for r in pre if r["frame_number"] in (141, 283)},
        }
    names = list(runs)
    agreement = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for index in (141, 283):
                pa = [(xyxy(o), o["confidence"]) for o in person_objects(runs[a]["_pre"][index])]
                pb = [(xyxy(o), o["confidence"]) for o in person_objects(runs[b]["_pre"][index])]
                m = strict_match(pa, pb, score_max=None)
                agreement[f"{a} vs {b} @ {index}"] = {"matches": m["matches"], "left": m["left_count"], "right": m["right_count"],
                                                     "median_bbox_abs_delta_px": m["median_bbox_abs_delta_px"]}
    for r in runs.values():
        r.pop("_pre")
    result = {"schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(), "model": "TrafficCamNet (provisional smoke, FP16)",
              "runs": runs, "C_cross_variant_person_agreement_iou_only": agreement,
              "limits": "No ground truth. Counts, jitter and track lengths are behaviour proxies only; more detections or longer tracks are not evidence of better accuracy. TrafficCamNet's own training-time resize is not documented here, so no variant is 'correct'."}
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for k, v in runs.items():
        print(k, {i: (a["closest"], round(a["mean_abs_by_emulation"][a["closest"]], 5)) for i, a in v["A_input_resemblance"].items()})
        print("   ", {kk: (round(vv, 3) if isinstance(vv, float) else vv) for kk, vv in v["B_behaviour_proxies"].items()})
    for k, v in agreement.items():
        print(k, v)


if __name__ == "__main__":
    main()
