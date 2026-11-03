#!/usr/bin/env python3
"""Export RF-DETR variant 2: 1920x1080 RGB/255 input with the HF resize inside the graph.

DeepStream 9.2 nvinfer scaling (default and scaling-filter=1) was measured to be
closest to top-left nearest sampling, not the HF processor's half-pixel bilinear
resize without antialias (reports/model-evaluation/rf-detr/deepstream/*/pretracker-comparison.json).
This variant takes the full-resolution pipeline frame, so nvinfer only performs a
same-size colour conversion, and the graph performs
F.interpolate(bilinear, align_corners=False, antialias=False) -> (x-mean)/std -> RF-DETR.

Checks (parity.json): HF processor path vs this graph (eager and ONNX Runtime) on
the same BT.709 reference frames; raw-slot and person-decode parity reported separately.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image
import torch
from torch import nn
import torch.nn.functional as F
import onnx
import onnxruntime as ort
import transformers
from transformers import RfDetrForObjectDetection, RfDetrImageProcessor

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_onnx import (ROOT, UPSTREAM, EXPECTED_CHECKPOINT, REFERENCE_DIR, FRAMES, THRESHOLD, MEAN, STD, RAW_TOL,  # noqa: E402
                         install_static_pos_encoding_patch, sha256, max_abs)
from perception.candidates.rf_detr.decode import DecodeGeometry, decode_person, PERSON_LOGIT_SLOT  # noqa: E402

ONNX_PATH = ROOT / "models" / "rf-detr-person-candidate" / "rf_detr_small_1080p_rgb01_resize_norm_in_graph.onnx"
HEIGHT, WIDTH = 1080, 1920
PERSON_SCORE_TOL = 5e-3


class ResizeNormalizedRawWrapper(nn.Module):
    def __init__(self, model: RfDetrForObjectDetection):
        super().__init__()
        self.model = model.eval()
        self.register_buffer("mean", torch.tensor(MEAN, dtype=torch.float32).view(1, 3, 1, 1))
        self.register_buffer("std", torch.tensor(STD, dtype=torch.float32).view(1, 3, 1, 1))
        self.eval()

    def forward(self, frame01: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        resized = F.interpolate(frame01, size=(512, 512), mode="bilinear", align_corners=False, antialias=False)
        outputs = self.model(pixel_values=(resized - self.mean) / self.std, return_dict=True)
        return outputs.logits, outputs.pred_boxes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--opset", type=int, default=17)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    if run.exists() or ONNX_PATH.exists():
        raise SystemExit(f"Refusing to overwrite export evidence or ONNX: {run} {ONNX_PATH}")
    run.mkdir(parents=True)
    if sha256(UPSTREAM / "model.safetensors") != EXPECTED_CHECKPOINT:
        raise SystemExit("Checkpoint SHA-256 mismatch")
    processor = RfDetrImageProcessor.from_pretrained(str(UPSTREAM), local_files_only=True)
    model = RfDetrForObjectDetection.from_pretrained(str(UPSTREAM), local_files_only=True).eval()
    wrapper = ResizeNormalizedRawWrapper(model)
    patched = install_static_pos_encoding_patch()
    with torch.no_grad():
        torch.onnx.export(wrapper, (torch.rand(1, 3, HEIGHT, WIDTH),), str(ONNX_PATH), input_names=["input"],
                          output_names=["logits", "pred_boxes"], opset_version=args.opset, dynamo=False, do_constant_folding=True)
    onnx_model = onnx.load(str(ONNX_PATH))
    onnx.checker.check_model(onnx_model)
    resize_nodes = [{"name": n.name, "attributes": {a.name: onnx.helper.get_attribute_value(a) for a in n.attribute}}
                    for n in onnx_model.graph.node if n.op_type == "Resize"]
    for node in resize_nodes:
        node["attributes"] = {k: (v.decode() if isinstance(v, bytes) else v) for k, v in node["attributes"].items()}
    io = {"inputs": [{"name": i.name, "shape": [d.dim_value for d in i.type.tensor_type.shape.dim]} for i in onnx_model.graph.input],
          "outputs": [{"name": o.name, "shape": [d.dim_value for d in o.type.tensor_type.shape.dim]} for o in onnx_model.graph.output],
          "resize_nodes": resize_nodes}
    session = ort.InferenceSession(str(ONNX_PATH), providers=["CPUExecutionProvider"])
    frames = []
    geometry = DecodeGeometry(WIDTH, HEIGHT)
    for index in FRAMES:
        png = REFERENCE_DIR / f"frame_{index:06d}_bt709.png"
        with Image.open(png) as source:
            image = source.convert("RGB")
        frame01 = torch.from_numpy(np.asarray(image, dtype=np.uint8).copy()).permute(2, 0, 1).float()[None] / 255.0
        frame_path = run / f"frame01_{index:06d}.f32le"
        np.ascontiguousarray(frame01.numpy(), dtype="<f4").tofile(frame_path)
        processed = processor(images=image, return_tensors="pt")
        with torch.no_grad():
            hf = model(pixel_values=processed["pixel_values"], return_dict=True)
            el, eb = wrapper(frame01)
        ol, ob = session.run(["logits", "pred_boxes"], {"input": frame01.numpy()})
        hl, hb = hf.logits.numpy(), hf.pred_boxes.numpy()
        hd, ed, od = (decode_person(a, b, geometry, THRESHOLD) for a, b in ((hl, hb), (el.numpy(), eb.numpy()), (ol, ob)))
        official = processor.post_process_object_detection(hf, threshold=THRESHOLD, target_sizes=[(HEIGHT, WIDTH)], top_k=300)[0]
        (run / f"onnx_decode_frame_{index:06d}.json").write_text(json.dumps({"frame_index": index, "threshold": THRESHOLD, "detections": od}, indent=1) + "\n", encoding="utf-8")
        np.savez_compressed(run / f"onnx_frame_{index:06d}.npz", logits=ol, pred_boxes=ob)

        def person_parity(a, b):
            return {"count": [len(a), len(b)], "query_order_equal": [d["query_index"] for d in a] == [d["query_index"] for d in b],
                    "score_max_abs": max((abs(x["confidence"] - y["confidence"]) for x, y in zip(a, b)), default=0.0),
                    "box_max_abs_px": max((max(abs(p - q) for p, q in zip(x["bbox_xyxy_pixels"], y["bbox_xyxy_pixels"])) for x, y in zip(a, b)), default=0.0)}
        rec = {"frame_index": index, "source_image": str(png.relative_to(ROOT)), "frame_input": frame_path.name, "frame_input_sha256": sha256(frame_path),
               "eager_vs_hf_logits_max_abs": max_abs(el, hl), "eager_vs_hf_boxes_max_abs": max_abs(eb, hb),
               "onnx_vs_hf_logits_max_abs": max_abs(ol, hl), "onnx_vs_hf_boxes_max_abs": max_abs(ob, hb),
               "onnx_vs_hf_slots_logits_delta_gt_1e-3": int((np.abs(ol - hl).max(-1) > RAW_TOL).sum()),
               "person_eager_vs_hf": person_parity(hd, ed), "person_onnx_vs_hf": person_parity(hd, od),
               "official_postprocessor_person_detections": int((official["labels"] == PERSON_LOGIT_SLOT).sum())}
        p = rec["person_onnx_vs_hf"]
        rec["raw_slot_result"] = "PASS" if rec["onnx_vs_hf_logits_max_abs"] <= RAW_TOL and rec["onnx_vs_hf_boxes_max_abs"] <= 1e-4 else "FAIL"
        rec["person_decode_result"] = "PASS" if (p["query_order_equal"] and p["count"][0] == p["count"][1] == rec["official_postprocessor_person_detections"]
                                               and p["score_max_abs"] <= PERSON_SCORE_TOL) else "FAIL"
        frames.append(rec)
    export = {"schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(), "variant": "1080p_resize_norm_in_graph",
              "model_id": "Roboflow/rf-detr-small", "revision": "3bdc465063270f99769da5a1b4c00c68bd2d439d", "checkpoint_sha256": EXPECTED_CHECKPOINT,
              "onnx_path": str(ONNX_PATH.relative_to(ROOT)), "onnx_sha256": sha256(ONNX_PATH), "onnx_size_bytes": ONNX_PATH.stat().st_size,
              "onnx_io": io, "torch_onnx_export": {"opset_version": args.opset, "dynamo": False}, "export_patch": patched,
              "input_contract": "input [1,3,1080,1920] float32 RGB in [0,1] (nvinfer net-scale-factor=1/255, same-size, no scaling); graph: bilinear half-pixel no-antialias resize to 512, (x-mean)/std",
              "versions": {"torch": torch.__version__, "transformers": transformers.__version__, "onnx": onnx.__version__, "onnxruntime": ort.__version__}}
    parity = {"schema_version": 1, "raw_slot_result": "PASS" if all(f["raw_slot_result"] == "PASS" for f in frames) else "FAIL",
              "person_decode_result": "PASS" if all(f["person_decode_result"] == "PASS" for f in frames) else "FAIL", "frames": frames,
              "tolerances": {"raw_logits": RAW_TOL, "raw_boxes": 1e-4, "person_score": PERSON_SCORE_TOL}}
    (run / "export.json").write_text(json.dumps(export, indent=2) + "\n", encoding="utf-8")
    (run / "parity.json").write_text(json.dumps(parity, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"io": io, "raw": parity["raw_slot_result"], "person": parity["person_decode_result"], "frames": frames}, indent=1))


if __name__ == "__main__":
    main()
