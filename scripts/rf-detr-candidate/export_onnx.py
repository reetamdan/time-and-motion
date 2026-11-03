#!/usr/bin/env python3
"""Offline RF-DETR export: RGB/255 input, ImageNet normalization inside the graph.

Stock nvinfer applies one scalar net-scale-factor and per-channel offsets, so it
cannot express RF-DETR's per-channel std. The exported graph therefore takes
direct-resized RGB in [0,1] ([1,3,512,512], nvinfer net-scale-factor=1/255,
zero offsets) and performs (x - mean) / std itself before the HF model. Only
raw logits [1,300,91] and pred_boxes [1,300,4] are exported.

Checks, all recorded in export.json / parity.json:
  1. HF processor tensor == manual contract (rescale -> bilinear antialias=False -> normalize).
  2. Wrapper(unnormalized) outputs == HF model(processor tensor) outputs.
  3. ONNX Runtime outputs == HF outputs (raw and person decode).
  4. Person decode count == official post_process_object_detection person count.
"""
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
from PIL import Image
import torch
from torch import nn
import torch.nn.functional as F
import onnx
import onnxruntime as ort
import transformers
from transformers import RfDetrForObjectDetection, RfDetrImageProcessor

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from perception.candidates.rf_detr.decode import DecodeGeometry, decode_person, PERSON_LOGIT_SLOT  # noqa: E402

UPSTREAM = ROOT / "models" / "rf-detr-person-candidate" / "upstream"
ONNX_PATH = ROOT / "models" / "rf-detr-person-candidate" / "rf_detr_small_rgb01_norm_in_graph.onnx"
EXPECTED_CHECKPOINT = "2bfab2fa5f5fcc4870e756c8dc8d2d5acca3a68ffcc0f7e84972661873eb5a1d"
REFERENCE_DIR = ROOT / "reports" / "model-evaluation" / "rt-detr" / "diagnostics" / "corrected-reference-20261026T160220Z"
FRAMES = (141, 283)
THRESHOLD = 0.25
MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)
RAW_TOL = 1e-3


class NormalizedRawWrapper(nn.Module):
    def __init__(self, model: RfDetrForObjectDetection):
        super().__init__()
        self.model = model.eval()
        self.register_buffer("mean", torch.tensor(MEAN, dtype=torch.float32).view(1, 3, 1, 1))
        self.register_buffer("std", torch.tensor(STD, dtype=torch.float32).view(1, 3, 1, 1))
        self.eval()

    def forward(self, rgb01: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        outputs = self.model(pixel_values=(rgb01 - self.mean) / self.std, return_dict=True)
        return outputs.logits, outputs.pred_boxes


def install_static_pos_encoding_patch() -> str:
    """Make tracing follow HF eager semantics for the static 512x512 export.

    HF interpolate_pos_encoding returns the stored position embeddings unchanged in
    eager mode when the patch grid matches, but always interpolates (bicubic,
    antialias=True; not exportable to ONNX) while tracing. For a fixed input whose
    grid matches the stored grid, returning the stored embeddings is exactly the
    eager computation; any other geometry is refused rather than approximated.
    """
    from transformers.models.rf_detr import modeling_rf_detr

    owners = [obj for obj in vars(modeling_rf_detr).values()
              if isinstance(obj, type) and "interpolate_pos_encoding" in vars(obj)]
    if len(owners) != 1:
        raise SystemExit(f"Expected exactly one interpolate_pos_encoding owner, found {[o.__name__ for o in owners]}")
    owner = owners[0]

    def static_interpolate_pos_encoding(self, embeddings, height, width):
        num_patches = embeddings.shape[1] - 1
        num_positions = self.position_embeddings.shape[1] - 1
        if num_patches != num_positions or height != width:
            raise RuntimeError(f"static export patch refuses geometry {height}x{width} ({num_patches} vs {num_positions} patches)")
        return self.position_embeddings

    owner.interpolate_pos_encoding = static_interpolate_pos_encoding
    return f"{owner.__module__}.{owner.__name__}.interpolate_pos_encoding"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manual_rgb01(image: Image.Image) -> torch.Tensor:
    tensor = torch.from_numpy(np.asarray(image, dtype=np.uint8).copy()).permute(2, 0, 1).float()[None] / 255.0
    return F.interpolate(tensor, size=(512, 512), mode="bilinear", align_corners=False, antialias=False)


def max_abs(a, b) -> float:
    return float(np.max(np.abs(np.asarray(a, np.float64) - np.asarray(b, np.float64))))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--opset", type=int, default=17)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    if (run / "export.json").exists() or ONNX_PATH.exists():
        raise SystemExit(f"Refusing to overwrite export evidence or ONNX: {run} {ONNX_PATH}")
    run.mkdir(parents=True, exist_ok=True)
    checkpoint = UPSTREAM / "model.safetensors"
    if sha256(checkpoint) != EXPECTED_CHECKPOINT:
        raise SystemExit("Checkpoint SHA-256 mismatch")
    torch.manual_seed(0)
    processor = RfDetrImageProcessor.from_pretrained(str(UPSTREAM), local_files_only=True)
    model = RfDetrForObjectDetection.from_pretrained(str(UPSTREAM), local_files_only=True).eval()
    if model.config.id2label[PERSON_LOGIT_SLOT] != "person":
        raise SystemExit("Person slot mismatch in pinned config")
    wrapper = NormalizedRawWrapper(model)
    patched = install_static_pos_encoding_patch()
    dummy = torch.rand(1, 3, 512, 512)
    with torch.no_grad():
        torch.onnx.export(wrapper, (dummy,), str(ONNX_PATH), input_names=["input"], output_names=["logits", "pred_boxes"],
                          opset_version=args.opset, dynamo=False, do_constant_folding=True)
    onnx_model = onnx.load(str(ONNX_PATH))
    onnx.checker.check_model(onnx_model)
    io = {
        "inputs": [{"name": i.name, "shape": [d.dim_value for d in i.type.tensor_type.shape.dim]} for i in onnx_model.graph.input],
        "outputs": [{"name": o.name, "shape": [d.dim_value for d in o.type.tensor_type.shape.dim]} for o in onnx_model.graph.output],
        "opset": [{"domain": o.domain, "version": o.version} for o in onnx_model.opset_import],
        "op_types": sorted({n.op_type for n in onnx_model.graph.node}),
    }
    session = ort.InferenceSession(str(ONNX_PATH), providers=["CPUExecutionProvider"])
    frames = []
    for index in FRAMES:
        png = REFERENCE_DIR / f"frame_{index:06d}_bt709.png"
        with Image.open(png) as source:
            image = source.convert("RGB")
        processed = processor(images=image, return_tensors="pt")
        pixel_values = processed["pixel_values"]
        rgb01 = manual_rgb01(image)
        manual_normalized = (rgb01 - wrapper.mean) / wrapper.std
        preprocessing_delta = max_abs(pixel_values, manual_normalized)
        with torch.no_grad():
            hf = model(pixel_values=pixel_values, pixel_mask=processed.get("pixel_mask"), return_dict=True)
            wrapped_logits, wrapped_boxes = wrapper(rgb01)
        onnx_logits, onnx_boxes = session.run(["logits", "pred_boxes"], {"input": rgb01.numpy()})
        hf_logits, hf_boxes = hf.logits.numpy(), hf.pred_boxes.numpy()
        geometry = DecodeGeometry(image.width, image.height)
        hf_dec = decode_person(hf_logits, hf_boxes, geometry, THRESHOLD)
        onnx_dec = decode_person(onnx_logits, onnx_boxes, geometry, THRESHOLD)
        official = processor.post_process_object_detection(hf, threshold=THRESHOLD, target_sizes=[(image.height, image.width)], top_k=300)[0]
        official_person = int((official["labels"] == PERSON_LOGIT_SLOT).sum())
        np.savez_compressed(run / f"hf_frame_{index:06d}.npz", rgb01=rgb01.numpy(), pixel_values=pixel_values.numpy(), logits=hf_logits, pred_boxes=hf_boxes)
        np.savez_compressed(run / f"onnx_frame_{index:06d}.npz", logits=onnx_logits, pred_boxes=onnx_boxes)
        np.ascontiguousarray(rgb01.numpy(), dtype="<f4").tofile(run / f"rgb01_{index:06d}.f32le")
        for name, dets in (("hf", hf_dec), ("onnx", onnx_dec)):
            (run / f"{name}_decode_frame_{index:06d}.json").write_text(json.dumps({"frame_index": index, "threshold": THRESHOLD, "comparison": "score > threshold", "detections": dets}, indent=1) + "\n", encoding="utf-8")
        record = {
            "frame_index": index, "source_image": str(png.relative_to(ROOT)), "source_image_sha256": sha256(png),
            "processor_pixel_values_shape": list(pixel_values.shape),
            "processor_pixel_mask_all_ones": bool(processed["pixel_mask"].all()) if "pixel_mask" in processed else None,
            "processor_vs_manual_contract_max_abs": preprocessing_delta,
            "wrapper_vs_hf_logits_max_abs": max_abs(wrapped_logits, hf_logits),
            "wrapper_vs_hf_boxes_max_abs": max_abs(wrapped_boxes, hf_boxes),
            "onnx_vs_hf_logits_max_abs": max_abs(onnx_logits, hf_logits),
            "onnx_vs_hf_boxes_max_abs": max_abs(onnx_boxes, hf_boxes),
            "logits_shape": list(hf_logits.shape), "pred_boxes_shape": list(hf_boxes.shape),
            "hf_person_decodes": len(hf_dec), "onnx_person_decodes": len(onnx_dec),
            "official_postprocessor_person_detections": official_person,
            "official_postprocessor_total_detections": int(official["labels"].numel()),
            "person_query_order_equal": [d["query_index"] for d in hf_dec] == [d["query_index"] for d in onnx_dec],
            "person_score_max_abs": max((abs(a["confidence"] - b["confidence"]) for a, b in zip(hf_dec, onnx_dec)), default=0.0),
        }
        record["result"] = "PASS" if (preprocessing_delta <= 1e-5 and record["wrapper_vs_hf_logits_max_abs"] <= RAW_TOL
                                      and record["onnx_vs_hf_logits_max_abs"] <= RAW_TOL and record["onnx_vs_hf_boxes_max_abs"] <= 1e-4
                                      and record["person_query_order_equal"] and len(hf_dec) == official_person) else "FAIL"
        frames.append(record)
    export = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_id": "Roboflow/rf-detr-small", "revision": "3bdc465063270f99769da5a1b4c00c68bd2d439d",
        "checkpoint_sha256": EXPECTED_CHECKPOINT, "onnx_path": str(ONNX_PATH.relative_to(ROOT)), "onnx_sha256": sha256(ONNX_PATH),
        "onnx_size_bytes": ONNX_PATH.stat().st_size, "onnx_io": io, "torch_onnx_export": {"opset_version": args.opset, "dynamo": False},
        "export_patch": {"target": patched, "behaviour": "returns stored position embeddings when the 32x32 patch grid matches (HF eager semantics); raises otherwise",
                         "applies_to": "ONNX export and the in-process parity forward passes; eager HF already takes this path at 512x512"},
        "input_contract": "input [1,3,512,512] float32 RGB in [0,1], direct (non-aspect-preserving) bilinear resize without antialias; (x-mean)/std inside graph",
        "mean": MEAN, "std": STD, "person_logit_slot": PERSON_LOGIT_SLOT, "threshold": THRESHOLD,
        "reference_frames": "BT.709 limited-range ffmpeg decodes from the RT-DETR corrected-reference directory (colour-correct; see RT-DETR diagnostics)",
        "versions": {"torch": torch.__version__, "transformers": transformers.__version__, "onnx": onnx.__version__, "onnxruntime": ort.__version__, "numpy": np.__version__},
    }
    parity = {"schema_version": 1, "result": "PASS" if all(f["result"] == "PASS" for f in frames) else "FAIL", "frames": frames,
              "tolerances": {"processor_vs_manual": 1e-5, "raw_logits": RAW_TOL, "raw_boxes": 1e-4}}
    (run / "export.json").write_text(json.dumps(export, indent=2) + "\n", encoding="utf-8")
    (run / "parity.json").write_text(json.dumps(parity, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"onnx_io": io["inputs"] + io["outputs"], "parity": parity["result"], "frames": frames}, indent=1))
    raise SystemExit(0 if parity["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
