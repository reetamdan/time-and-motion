#!/usr/bin/env python3
"""Control: strict crosscheck between two CPU-only HF references that differ only in YUV->RGB matrix."""
import json
import sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "rt-detr-candidate")); sys.path.insert(0, str(ROOT))
from compare_pretracker import strict_match, xyxy
D = ROOT / Path("reports/model-evaluation/rt-detr/diagnostics")
corr = sorted(D.glob("corrected-reference-*"))[-1]
sup = sorted(D.glob("pretracker-supersample-scaling-*"))[-1]
out = {}
for i in (141, 283):
    a = json.load(open(ROOT / f"reports/model-evaluation/rt-detr/export/run-001/onnx_decode_frame_{i:06d}.json"))["detections"]
    b = json.load(open(corr / f"onnx_decode_frame_{i:06d}_bt709.json"))["detections"]
    pa = [(d["bbox_xyxy_pixels"], d["confidence"]) for d in a]; pb = [(d["bbox_xyxy_pixels"], d["confidence"]) for d in b]
    m = strict_match(pa, pb); mi = strict_match(pa, pb, score_max=None)
    pre = [r for r in map(json.loads, open(sup / "pre_tracker.jsonl")) if r["frame_number"] == i][0]["objects"]
    pp = [(xyxy(o), o["confidence"]) for o in pre]
    ms = strict_match(pp, pb)
    un_conf = sorted(round(pp[k][1], 3) for k in ms["unmatched_left"])
    matched_conf = [pp[k][1] for k in set(range(len(pp))) - set(ms["unmatched_left"])]
    mi2 = strict_match(pp, pb, score_max=None)
    hi = [(x, s) for x, s in pp if s >= 0.5]; hib = [(x, s) for x, s in pb if s >= 0.5]
    mh = strict_match(hi, hib)
    out[i] = {"cpu_bt601_vs_cpu_bt709_strict": f"{m['matches']}/{m['left_count']} vs {m['right_count']}", "cpu_iou_only": mi["matches"], "cpu_iou_only_max_dscore": mi["max_score_abs_delta"],
              "ds_supersample_unmatched_confidences": un_conf, "ds_supersample_iou_only": f"{mi2['matches']}/{mi2['left_count']}", "ds_iou_only_max_dscore": mi2["max_score_abs_delta"],
              "ds_conf>=0.5_strict": f"{mh['matches']}/{mh['left_count']} vs {mh['right_count']}"}
dest = corr / "reference-sensitivity-control.json"
if dest.exists(): raise SystemExit(f"Refusing to overwrite {dest}")
dest.write_text(json.dumps({"schema_version": 1, "supersample_run": str(sup.relative_to(ROOT)), "criteria": "unchanged strict one-to-one IoU>=0.5, score delta<=0.02", "frames": out}, indent=1) + "\n", encoding="utf-8")
print(json.dumps(out, indent=1))
