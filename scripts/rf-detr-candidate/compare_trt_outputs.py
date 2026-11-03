#!/usr/bin/env python3
"""Compare trtexec-exported RF-DETR engine outputs with ONNX Runtime on byte-identical inputs."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import onnxruntime as ort

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "rt-detr-candidate"))
from perception.candidates.rf_detr.decode import DecodeGeometry, decode_person  # noqa: E402
from compare_pretracker import strict_match  # noqa: E402

EXPORT = ROOT / "reports" / "model-evaluation" / "rf-detr" / "export" / "run-002"
ONNX = ROOT / "models" / "rf-detr-person-candidate" / "rf_detr_small_rgb01_norm_in_graph.onnx"


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--onnx", type=Path, default=ONNX)
    parser.add_argument("--export-run", type=Path, default=EXPORT)
    parser.add_argument("--input-prefix", default="rgb01")
    parser.add_argument("--input-shape", default="1,3,512,512")
    args = parser.parse_args()
    shape = tuple(int(v) for v in args.input_shape.split(","))
    out_dir = args.out_dir.resolve()
    dest = out_dir / "trt-parity.json"
    if dest.exists():
        raise SystemExit(f"Refusing to overwrite {dest}")
    session = ort.InferenceSession(str(args.onnx), providers=["CPUExecutionProvider"])
    frames = []
    for index in (141, 283):
        x = np.fromfile(args.export_run / f"{args.input_prefix}_{index:06d}.f32le", dtype="<f4").reshape(shape)
        ol, ob = session.run(["logits", "pred_boxes"], {"input": x})
        trt = {o["name"]: np.asarray(o["values"], np.float32).reshape([int(d) for d in str(o["dimensions"]).split("x")])
               for o in json.loads((out_dir / f"trt_outputs_{index:06d}.json").read_text())}
        tl, tb = trt["logits"], trt["pred_boxes"]
        g = DecodeGeometry(1920, 1080)
        od, td = decode_person(ol, ob, g), decode_person(tl, tb, g)
        rec = {"frame_index": index, "logits_max_abs": float(np.abs(tl - ol).max()), "pred_boxes_max_abs": float(np.abs(tb - ob).max()),
               "slots_logits_delta_gt_1e-3": int((np.abs(tl - ol).max(-1) > 1e-3).sum()),
               "onnx_person_decodes": len(od), "trt_person_decodes": len(td),
               "person_query_order_equal": [d["query_index"] for d in od] == [d["query_index"] for d in td],
               "person_score_max_abs": max((abs(a["confidence"] - b["confidence"]) for a, b in zip(od, td)), default=None),
               "person_box_max_abs_px": max((max(abs(p - q) for p, q in zip(a["bbox_xyxy_pixels"], b["bbox_xyxy_pixels"])) for a, b in zip(od, td)), default=None)}
        sets = strict_match([(d["bbox_xyxy_pixels"], d["confidence"]) for d in od], [(d["bbox_xyxy_pixels"], d["confidence"]) for d in td])
        rec["person_set_strict"] = {k: sets[k] for k in ("matches", "left_count", "right_count", "max_score_abs_delta", "max_bbox_abs_delta_px")}
        rec["person_set_strict"]["result"] = "PASS" if sets["matches"] == sets["left_count"] == sets["right_count"] else "FAIL"
        rec["raw_slot_parity"] = "PASS" if rec["logits_max_abs"] <= 1e-3 and rec["pred_boxes_max_abs"] <= 1e-4 else "FAIL"
        rec["person_decode_parity"] = "PASS" if rec["person_query_order_equal"] and len(od) == len(td) and (rec["person_score_max_abs"] or 0) <= 5e-3 else "FAIL"
        frames.append(rec)
    result = {"schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(), "onnx": str(args.onnx), "export_run": str(args.export_run), "frames": frames,
              "tolerances": {"raw_logits": 1e-3, "raw_boxes": 1e-4, "person_score": 5e-3},
              "note": "Raw-slot parity is order-sensitive on frames whose encoder top-300 proposal scores contain near-ties (see export/run-002/topk-margin-diagnosis.json)."}
    dest.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(frames, indent=1))


if __name__ == "__main__":
    main()
