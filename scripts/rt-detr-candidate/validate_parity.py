#!/usr/bin/env python3
"""Validate offline HF vs ONNX Runtime raw and person-decode parity."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
import numpy as np
import onnx
import onnxruntime as ort

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from perception.candidates.rt_detr.decode import DecodeGeometry, decode_person

ONNX_PATH = ROOT / "models" / "rt-detr-person-candidate" / "rt_detr_r18vd_raw.onnx"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    export = json.loads((run_dir / "export.json").read_text(encoding="utf-8"))
    if export["onnx_sha256"] != sha256(ONNX_PATH):
        raise SystemExit("ONNX differs from frozen export evidence")
    graph = onnx.load(str(ONNX_PATH))
    onnx.checker.check_model(graph, full_check=True)
    session = ort.InferenceSession(str(ONNX_PATH), providers=["CPUExecutionProvider"])
    actual_inputs = [{"name": item.name, "shape": item.shape, "dtype": item.type} for item in session.get_inputs()]
    actual_outputs = [{"name": item.name, "shape": item.shape, "dtype": item.type} for item in session.get_outputs()]
    if actual_inputs != [{"name": "pixel_values", "shape": [1, 3, 640, 640], "dtype": "tensor(float)"}]:
        raise RuntimeError(f"ORT input contract mismatch: {actual_inputs}")
    if actual_outputs != [{"name": "logits", "shape": [1, 300, 80], "dtype": "tensor(float)"}, {"name": "pred_boxes", "shape": [1, 300, 4], "dtype": "tensor(float)"}]:
        raise RuntimeError(f"ORT output contract mismatch: {actual_outputs}")
    frames = []
    for reference in export["frames"]:
        index = reference["frame_index"]
        with np.load(run_dir / reference["reference_npz"]) as data:
            pixel_values = data["pixel_values"]
            hf_logits = data["logits"]
            hf_boxes = data["pred_boxes"]
        onnx_logits, onnx_boxes = session.run(["logits", "pred_boxes"], {"pixel_values": pixel_values})
        logits_max_abs = float(np.max(np.abs(hf_logits - onnx_logits)))
        boxes_max_abs = float(np.max(np.abs(hf_boxes - onnx_boxes)))
        np.testing.assert_allclose(onnx_logits, hf_logits, rtol=2e-4, atol=2e-5)
        np.testing.assert_allclose(onnx_boxes, hf_boxes, rtol=2e-4, atol=2e-5)
        hf_decode = json.loads((run_dir / reference["decode_json"]).read_text(encoding="utf-8"))["detections"]
        width, height = reference["source_size"]
        onnx_decode = decode_person(onnx_logits, onnx_boxes, DecodeGeometry(width, height), threshold=0.25, top_k=300)
        if [item["query_index"] for item in hf_decode] != [item["query_index"] for item in onnx_decode]:
            raise RuntimeError(f"Frame {index}: HF/ONNX person query ordering mismatch")
        if not onnx_decode:
            raise RuntimeError(f"Frame {index}: no ONNX person detections on known-positive input")
        score_max_abs = float(np.max(np.abs(np.array([item["confidence"] for item in hf_decode]) - np.array([item["confidence"] for item in onnx_decode]))))
        box_max_abs = float(np.max(np.abs(np.array([item["bbox_xyxy_pixels"] for item in hf_decode]) - np.array([item["bbox_xyxy_pixels"] for item in onnx_decode]))))
        if score_max_abs > 1e-5 or box_max_abs > 0.05:
            raise RuntimeError(f"Frame {index}: decoded parity exceeds tolerance: score={score_max_abs} box={box_max_abs}")
        np.savez_compressed(run_dir / f"onnx_frame_{index:06d}.npz", logits=onnx_logits, pred_boxes=onnx_boxes)
        (run_dir / f"onnx_decode_frame_{index:06d}.json").write_text(json.dumps({"frame_index": index, "threshold": 0.25, "detections": onnx_decode}, indent=2) + "\n", encoding="utf-8")
        frames.append({"frame_index": index, "raw_logits_max_abs": logits_max_abs, "raw_pred_boxes_max_abs": boxes_max_abs,
                       "person_detections_hf": len(hf_decode), "person_detections_onnx": len(onnx_decode),
                       "person_query_order_equal": True, "person_score_max_abs": score_max_abs,
                       "person_box_xyxy_pixels_max_abs": box_max_abs, "known_positive_onnx_decode": "PASS"})
    result = {
        "schema_version": 1, "result": "PASS", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "onnx": str(ONNX_PATH.relative_to(ROOT)), "onnx_sha256": export["onnx_sha256"],
        "onnxruntime_version": ort.__version__, "providers": session.get_providers(),
        "actual_ort_contract": {"inputs": actual_inputs, "outputs": actual_outputs},
        "tolerances": {"raw_rtol": 2e-4, "raw_atol": 2e-5, "person_score_max_abs": 1e-5, "person_box_xyxy_pixels_max_abs": 0.05},
        "frames": frames, "decode_code_sha256": sha256(ROOT / "perception" / "candidates" / "rt_detr" / "decode.py"),
        "scope": "HF/ONNX raw tensor and deterministic person decode parity on real known-positive frames; not labeled accuracy"
    }
    destination = run_dir / "parity.json"
    if destination.exists():
        raise SystemExit(f"Refusing to overwrite {destination}")
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
