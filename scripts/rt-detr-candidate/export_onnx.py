#!/usr/bin/env python3
"""Offline HF evaluation export: fixed input and only raw logits/pred_boxes."""
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
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import numpy as np
from PIL import Image
import torch
from torch import nn
import onnx
from transformers import RTDetrImageProcessor, RTDetrV2ForObjectDetection

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from perception.candidates.rt_detr.decode import DecodeGeometry, decode_person

UPSTREAM = ROOT / "models" / "rt-detr-person-candidate" / "upstream"
ONNX_PATH = ROOT / "models" / "rt-detr-person-candidate" / "rt_detr_r18vd_raw.onnx"
EXPECTED_CHECKPOINT = "d18309d0d7ea57048138885c4c6ecfcb1e24506fc6153b94ad484f8ab62c7115"
THRESHOLD = 0.25


class RawOutputWrapper(nn.Module):
    """Thin evaluation-mode wrapper exposing no auxiliary/training outputs."""
    def __init__(self, model: RTDetrV2ForObjectDetection):
        super().__init__()
        self.model = model
        self.model.eval()
        self.eval()

    def forward(self, pixel_values: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        outputs = self.model(pixel_values=pixel_values, return_dict=True)
        return outputs.logits, outputs.pred_boxes


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def torch_reference_person(logits: torch.Tensor, boxes: torch.Tensor, width: int, height: int):
    """Independent Torch person-slot decode used to check the NumPy app decode."""
    scores = torch.sigmoid(logits[0, :, 0])
    query_indices = torch.nonzero(scores >= THRESHOLD, as_tuple=False).flatten()
    scores = scores[query_indices]
    order = torch.argsort(scores, descending=True, stable=True)
    query_indices = query_indices[order]
    scores = scores[order]
    selected = boxes[0, query_indices]
    xyxy = torch.stack((selected[:, 0] - selected[:, 2] / 2, selected[:, 1] - selected[:, 3] / 2,
                        selected[:, 0] + selected[:, 2] / 2, selected[:, 1] + selected[:, 3] / 2), dim=1)
    xyxy = xyxy * torch.tensor([width, height, width, height], dtype=xyxy.dtype)
    xyxy[:, 0::2] = xyxy[:, 0::2].clamp(0, width)
    xyxy[:, 1::2] = xyxy[:, 1::2].clamp(0, height)
    return query_indices.cpu().numpy(), scores.cpu().numpy(), xyxy.cpu().numpy()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    result_path = run_dir / "export.json"
    if result_path.exists() or ONNX_PATH.exists():
        raise SystemExit(f"Refusing to overwrite export evidence or ONNX: {result_path} {ONNX_PATH}")
    checkpoint = UPSTREAM / "model.safetensors"
    if checkpoint.stat().st_size != 80_904_640 or sha256(checkpoint) != EXPECTED_CHECKPOINT:
        raise SystemExit("Pinned checkpoint local verification failed")
    processor = RTDetrImageProcessor.from_pretrained(str(UPSTREAM), local_files_only=True)
    model, loading_info = RTDetrV2ForObjectDetection.from_pretrained(
        str(UPSTREAM), local_files_only=True, use_safetensors=True, output_loading_info=True,
    )
    for key in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"):
        if loading_info.get(key):
            raise RuntimeError(f"Checkpoint loading {key}: {loading_info[key]}")
    model.eval()
    wrapper = RawOutputWrapper(model)
    if wrapper.training or model.training:
        raise RuntimeError("Model/wrapper is not in evaluation mode")

    frame_records = []
    example = None
    for frame_index in (141, 283):
        image_path = ROOT / "reports" / "model-evaluation" / "rt-detr" / "frames" / f"frame_{frame_index:06d}.png"
        with Image.open(image_path) as source:
            image = source.convert("RGB")
        inputs = processor(images=image, return_tensors="pt")
        pixel_values = inputs["pixel_values"]
        if tuple(pixel_values.shape) != (1, 3, 640, 640) or pixel_values.dtype != torch.float32:
            raise RuntimeError(f"Unexpected processor contract: {pixel_values.shape} {pixel_values.dtype}")
        manual = image.resize((640, 640), Image.Resampling.BILINEAR)
        manual_values = torch.from_numpy(np.asarray(manual, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0)
        preprocessing_max_abs = float(torch.max(torch.abs(pixel_values - manual_values)))
        if preprocessing_max_abs > 1e-6:
            raise RuntimeError(f"Direct-resize preprocessing mismatch: {preprocessing_max_abs}")
        with torch.inference_mode():
            outputs = model(pixel_values=pixel_values, return_dict=True)
            wrapped_logits, wrapped_boxes = wrapper(pixel_values)
        if not torch.equal(outputs.logits, wrapped_logits) or not torch.equal(outputs.pred_boxes, wrapped_boxes):
            raise RuntimeError("Thin wrapper changed raw model outputs")
        if tuple(outputs.logits.shape) != (1, 300, 80) or tuple(outputs.pred_boxes.shape) != (1, 300, 4):
            raise RuntimeError(f"Unexpected HF raw output contract: {outputs.logits.shape} {outputs.pred_boxes.shape}")
        logits = outputs.logits.cpu().numpy()
        boxes = outputs.pred_boxes.cpu().numpy()
        detections = decode_person(logits, boxes, DecodeGeometry(image.width, image.height), THRESHOLD, 300)
        reference_indices, reference_scores, reference_boxes = torch_reference_person(outputs.logits, outputs.pred_boxes, image.width, image.height)
        if [item["query_index"] for item in detections] != reference_indices.tolist():
            raise RuntimeError("NumPy/Torch person query ordering mismatch")
        np.testing.assert_allclose([item["confidence"] for item in detections], reference_scores, rtol=1e-6, atol=1e-7)
        np.testing.assert_allclose([item["bbox_xyxy_pixels"] for item in detections], reference_boxes, rtol=1e-6, atol=1e-4)
        official = processor.post_process_object_detection(outputs, threshold=THRESHOLD, target_sizes=torch.tensor([[image.height, image.width]]), use_focal_loss=True)[0]
        official_person = int((official["labels"] == 0).sum())
        if official_person != len(detections):
            raise RuntimeError(f"Official postprocessor/person decode count mismatch: {official_person} vs {len(detections)}")
        if not detections:
            raise RuntimeError(f"Known-positive frame {frame_index} produced no HF person decode")
        reference_path = run_dir / f"hf_frame_{frame_index:06d}.npz"
        decode_path = run_dir / f"hf_decode_frame_{frame_index:06d}.json"
        if reference_path.exists() or decode_path.exists():
            raise SystemExit("Refusing to overwrite HF reference evidence")
        np.savez_compressed(reference_path, pixel_values=pixel_values.numpy(), logits=logits, pred_boxes=boxes)
        decode_path.write_text(json.dumps({"frame_index": frame_index, "threshold": THRESHOLD, "detections": detections}, indent=2) + "\n", encoding="utf-8")
        frame_records.append({
            "frame_index": frame_index, "source_image": str(image_path.relative_to(ROOT)),
            "source_image_sha256": sha256(image_path), "source_size": [image.width, image.height],
            "pixel_values_shape": list(pixel_values.shape), "pixel_values_dtype": str(pixel_values.dtype),
            "preprocessing_manual_max_abs": preprocessing_max_abs,
            "hf_logits_shape": list(logits.shape), "hf_pred_boxes_shape": list(boxes.shape),
            "hf_person_detections": len(detections), "official_postprocessor_person_detections": official_person,
            "hf_decode_torch_reference_parity": "PASS", "reference_npz": reference_path.name,
            "decode_json": decode_path.name, "max_person_confidence": max(item["confidence"] for item in detections),
        })
        if example is None:
            example = pixel_values.clone()

    temporary = ONNX_PATH.with_name(ONNX_PATH.stem + ".partial.onnx")
    if temporary.exists():
        raise SystemExit(f"Incomplete temporary export exists; inspect before resume: {temporary}")
    export_command = {
        "function": "torch.onnx.export", "args": ["RawOutputWrapper(model.eval())", "pixel_values[1,3,640,640]", str(ONNX_PATH)],
        "kwargs": {"input_names": ["pixel_values"], "output_names": ["logits", "pred_boxes"], "opset_version": 17,
                   "do_constant_folding": True, "dynamic_axes": None, "dynamo": False, "export_params": True}
    }
    torch.onnx.export(wrapper, (example,), str(temporary), input_names=["pixel_values"], output_names=["logits", "pred_boxes"],
                      opset_version=17, do_constant_folding=True, dynamic_axes=None, dynamo=False, export_params=True, verbose=False)
    exported = onnx.load(str(temporary))
    onnx.checker.check_model(exported, full_check=True)
    os.replace(temporary, ONNX_PATH)
    inputs = [{"name": value.name, "dtype": onnx.helper.tensor_dtype_to_np_dtype(value.type.tensor_type.elem_type).name,
               "shape": [dim.dim_value if dim.HasField("dim_value") else dim.dim_param for dim in value.type.tensor_type.shape.dim]} for value in exported.graph.input]
    outputs = [{"name": value.name, "dtype": onnx.helper.tensor_dtype_to_np_dtype(value.type.tensor_type.elem_type).name,
                "shape": [dim.dim_value if dim.HasField("dim_value") else dim.dim_param for dim in value.type.tensor_type.shape.dim]} for value in exported.graph.output]
    expected_inputs = [{"name": "pixel_values", "dtype": "float32", "shape": [1, 3, 640, 640]}]
    expected_outputs = [{"name": "logits", "dtype": "float32", "shape": [1, 300, 80]}, {"name": "pred_boxes", "dtype": "float32", "shape": [1, 300, 4]}]
    if inputs != expected_inputs or outputs != expected_outputs:
        raise RuntimeError(f"Actual ONNX contract mismatch: inputs={inputs} outputs={outputs}")
    operator_counts = {}
    for node in exported.graph.node:
        operator_counts[node.op_type] = operator_counts.get(node.op_type, 0) + 1
    result = {
        "schema_version": 1, "result": "PASS", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": [sys.executable, *sys.argv], "offline": {"HF_HUB_OFFLINE": os.environ["HF_HUB_OFFLINE"], "TRANSFORMERS_OFFLINE": os.environ["TRANSFORMERS_OFFLINE"]},
        "upstream": str(UPSTREAM.relative_to(ROOT)), "checkpoint_sha256": EXPECTED_CHECKPOINT,
        "implementation_class": "transformers.models.rt_detr_v2.modeling_rt_detr_v2.RTDetrV2ForObjectDetection",
        "hf_api_declared_architecture": "RtDetrV2ForObjectDetection", "loading_info": loading_info,
        "evaluation_mode": True, "wrapper": "RawOutputWrapper returns only outputs.logits and outputs.pred_boxes",
        "export_command": export_command, "export_code_sha256": sha256(Path(__file__).resolve()),
        "decode_code_sha256": sha256(ROOT / "perception" / "candidates" / "rt_detr" / "decode.py"),
        "onnx": str(ONNX_PATH.relative_to(ROOT)), "onnx_sha256": sha256(ONNX_PATH), "onnx_size_bytes": ONNX_PATH.stat().st_size,
        "onnx_ir_version": exported.ir_version, "onnx_opset": [{"domain": item.domain, "version": item.version} for item in exported.opset_import],
        "actual_onnx_contract": {"inputs": inputs, "outputs": outputs}, "operator_counts": dict(sorted(operator_counts.items())),
        "frames": frame_records, "quality_scope": "Implementation parity and known-positive integration only; no ground-truth labels or precision/recall"
    }
    result_path.write_text(json.dumps(result, indent=2, default=lambda value: value.tolist() if hasattr(value, "tolist") else str(value)) + "\n", encoding="utf-8")
    print(json.dumps({"result": "PASS", "onnx": result["onnx"], "onnx_sha256": result["onnx_sha256"], "actual_onnx_contract": result["actual_onnx_contract"], "frames": frame_records}, indent=2))


if __name__ == "__main__":
    main()
