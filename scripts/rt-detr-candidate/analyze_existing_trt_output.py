#!/usr/bin/env python3
"""Analyze the already-captured frame-141 trtexec output; runs no new inference."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from perception.candidates.rt_detr.decode import DecodeGeometry, decode_person


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export-run", type=Path, required=True)
    parser.add_argument("--trt-output", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    entries = json.loads(args.trt_output.read_text(encoding="utf-8"))
    tensors = {entry["name"]: entry for entry in entries}
    if set(tensors) != {"logits", "pred_boxes"}:
        raise SystemExit(f"Unexpected TensorRT outputs: {sorted(tensors)}")
    if tensors["logits"]["dimensions"] != "1x300x80" or tensors["pred_boxes"]["dimensions"] != "1x300x4":
        raise SystemExit(f"Unexpected dimensions: {[(k, v['dimensions']) for k, v in tensors.items()]}")
    trt_logits = np.asarray(tensors["logits"]["values"], dtype=np.float32).reshape(1, 300, 80)
    trt_boxes = np.asarray(tensors["pred_boxes"]["values"], dtype=np.float32).reshape(1, 300, 4)
    with np.load(args.export_run / "hf_frame_000141.npz") as data:
        hf_logits, hf_boxes = data["logits"], data["pred_boxes"]
    with np.load(args.export_run / "onnx_frame_000141.npz") as data:
        onnx_logits, onnx_boxes = data["logits"], data["pred_boxes"]
    metrics = {
        "hf_to_trt_logits_max_abs": float(np.max(np.abs(hf_logits - trt_logits))),
        "hf_to_trt_pred_boxes_max_abs": float(np.max(np.abs(hf_boxes - trt_boxes))),
        "onnx_to_trt_logits_max_abs": float(np.max(np.abs(onnx_logits - trt_logits))),
        "onnx_to_trt_pred_boxes_max_abs": float(np.max(np.abs(onnx_boxes - trt_boxes))),
        "onnx_to_trt_logits_mean_abs": float(np.mean(np.abs(onnx_logits - trt_logits))),
        "onnx_to_trt_pred_boxes_mean_abs": float(np.mean(np.abs(onnx_boxes - trt_boxes))),
    }
    onnx_decode = json.loads((args.export_run / "onnx_decode_frame_000141.json").read_text(encoding="utf-8"))["detections"]
    trt_decode = decode_person(trt_logits, trt_boxes, DecodeGeometry(1920, 1080), threshold=0.25, top_k=300)
    (args.destination.parent / "trt_decode_frame_000141.json").write_text(json.dumps({"frame_index": 141, "threshold": 0.25, "detections": trt_decode}, indent=2) + "\n", encoding="utf-8")
    common = min(len(onnx_decode), len(trt_decode))
    same_order = [item["query_index"] for item in onnx_decode] == [item["query_index"] for item in trt_decode]
    metrics.update({"onnx_person_decodes": len(onnx_decode), "trt_person_decodes": len(trt_decode), "person_query_order_equal": same_order})
    if common and same_order:
        metrics["person_score_max_abs"] = float(np.max(np.abs(np.array([x["confidence"] for x in onnx_decode]) - np.array([x["confidence"] for x in trt_decode]))))
        metrics["person_box_xyxy_pixels_max_abs"] = float(np.max(np.abs(np.array([x["bbox_xyxy_pixels"] for x in onnx_decode]) - np.array([x["bbox_xyxy_pixels"] for x in trt_decode]))))
    raw_pass = bool(np.allclose(onnx_logits, trt_logits, rtol=2e-4, atol=2e-5) and np.allclose(onnx_boxes, trt_boxes, rtol=2e-4, atol=2e-5))
    decode_pass = same_order and len(onnx_decode) == len(trt_decode) and metrics.get("person_score_max_abs", float("inf")) <= 1e-5 and metrics.get("person_box_xyxy_pixels_max_abs", float("inf")) <= 0.05
    result = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "result": "PASS" if raw_pass and decode_pass else "FAIL", "frame_index": 141,
        "analysis_only_no_new_inference": True, "metrics": metrics,
        "checks": {"raw_allclose_same_as_hf_onnx_tolerances": "PASS" if raw_pass else "FAIL",
                   "person_decode_parity": "PASS" if decode_pass else "FAIL"},
        "scope": "One exact saved HF input tensor through the serialized TensorRT engine, analyzed offline against HF/ONNX. Does not observe raw tensors inside DeepStream and does not run frame 283.",
        "trtexec_input_log": "reports/model-evaluation/rt-detr/engine/trt-parity/trtexec_000141.log"
    }
    if args.destination.exists():
        raise SystemExit(f"Refusing to overwrite {args.destination}")
    args.destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
