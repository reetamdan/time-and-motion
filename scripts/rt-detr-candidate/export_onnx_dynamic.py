#!/usr/bin/env python3
"""Re-export the pinned RT-DETRv2-R18 checkpoint with a dynamic batch axis, and prove it equals the batch-1 model.

The pinned ONNX (export_onnx.py) has a fixed [1,3,640,640] input, so TensorRT can only build batch-1 engines
from it. This export is identical except ``dynamic_axes`` marks dim 0 of the input and both outputs as
``batch`` (the approach of marcoslucianops/DeepStream-Yolo utils/export_rtdetr_pytorch.py --dynamic). It refuses
to keep the file unless, with onnxruntime on the two evaluation frames:

* batch 1 matches the pinned static ONNX (same graph apart from the batch axis),
* each image of a batch of 2 matches its own batch-1 result (no cross-batch leakage),
* batch 1 matches the recorded Hugging Face reference outputs of the original export (run-001).

Environment: the pinned export venv (.install/rt-detr-export, torch 2.6.0+cpu, transformers 4.49.0,
onnx 1.17.0, onnxruntime 1.20.1). Usage: python scripts/rt-detr-candidate/export_onnx_dynamic.py
"""
from __future__ import annotations

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
import onnxruntime
from PIL import Image
import torch
from torch import nn
import transformers
from transformers import RTDetrImageProcessor, RTDetrV2ForObjectDetection

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "models" / "rt-detr-person-candidate"
UPSTREAM = MODELS / "upstream"
STATIC_ONNX = MODELS / "rt_detr_r18vd_raw.onnx"
DYNAMIC_ONNX = MODELS / "rt_detr_r18vd_raw_dynbatch.onnx"
FRAMES = ROOT / "reports" / "model-evaluation" / "rt-detr" / "frames"
HF_REFERENCE = ROOT / "reports" / "model-evaluation" / "rt-detr" / "export" / "run-001"
REPORT = ROOT / "reports" / "model-evaluation" / "rt-detr" / "export-dynamic-batch"
EXPECTED_CHECKPOINT = "d18309d0d7ea57048138885c4c6ecfcb1e24506fc6153b94ad484f8ab62c7115"
EXPECTED_STATIC_ONNX = "8048e1534bd0864ae53003a3f273a6155f14ff9cb39454c5141baac3babc2e07"
# FP32 CPU reruns of one graph agree to ~1e-5; anything larger means the export changed the model.
MAX_ABS_DIFF = 1e-3


class RawOutputWrapper(nn.Module):
    """Same thin evaluation wrapper as export_onnx.py: raw logits and pred_boxes only."""

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


def shape(value) -> list:
    return [d.dim_param or d.dim_value for d in value.type.tensor_type.shape.dim]


def main() -> None:
    if DYNAMIC_ONNX.exists():
        raise SystemExit(f"Refusing to overwrite {DYNAMIC_ONNX}")
    if sha256(UPSTREAM / "model.safetensors") != EXPECTED_CHECKPOINT or sha256(STATIC_ONNX) != EXPECTED_STATIC_ONNX:
        raise SystemExit("Pinned checkpoint or static ONNX verification failed")
    processor = RTDetrImageProcessor.from_pretrained(str(UPSTREAM), local_files_only=True)
    model = RTDetrV2ForObjectDetection.from_pretrained(str(UPSTREAM), local_files_only=True, use_safetensors=True)
    wrapper = RawOutputWrapper(model)
    frames = []
    for index in (141, 283):
        with Image.open(FRAMES / f"frame_{index:06d}.png") as source:
            frames.append(processor(images=source.convert("RGB"), return_tensors="pt")["pixel_values"])
    temporary = DYNAMIC_ONNX.with_suffix(".onnx.tmp")
    torch.onnx.export(wrapper, (frames[0],), str(temporary), input_names=["pixel_values"],
                      output_names=["logits", "pred_boxes"], opset_version=17, do_constant_folding=True,
                      dynamic_axes={"pixel_values": {0: "batch"}, "logits": {0: "batch"}, "pred_boxes": {0: "batch"}},
                      dynamo=False, export_params=True, verbose=False)
    exported = onnx.load(str(temporary))
    # Tracing records the decoder's top-k query gather as dynamic, so the outputs' query/class/box dims come out
    # symbolic. They are fixed by the model (300 queries, 80 classes, 4 box values): declare them, and the
    # batch-8 run below checks the real output shapes.
    for output, fixed in zip(exported.graph.output, ((300, 80), (300, 4))):
        for dim, value in zip(output.type.tensor_type.shape.dim[1:], fixed):
            dim.Clear()
            dim.dim_value = value
    onnx.save(exported, str(temporary))
    onnx.checker.check_model(exported, full_check=True)
    contract = {"inputs": {v.name: shape(v) for v in exported.graph.input},
                "outputs": {v.name: shape(v) for v in exported.graph.output}}
    expected = {"inputs": {"pixel_values": ["batch", 3, 640, 640]},
                "outputs": {"logits": ["batch", 300, 80], "pred_boxes": ["batch", 300, 4]}}
    if contract != expected:
        temporary.unlink()
        raise SystemExit(f"Unexpected dynamic contract: {contract}")

    static = onnxruntime.InferenceSession(str(STATIC_ONNX), providers=["CPUExecutionProvider"])
    dynamic = onnxruntime.InferenceSession(str(temporary), providers=["CPUExecutionProvider"])
    checks = {}
    singles = []
    for index, pixel_values in zip((141, 283), frames):
        x = pixel_values.numpy()
        reference = static.run(None, {"pixel_values": x})
        result = dynamic.run(None, {"pixel_values": x})
        singles.append(result)
        hf = np.load(HF_REFERENCE / f"hf_frame_{index:06d}.npz")
        checks[f"frame_{index}"] = {
            "batch1_vs_static_onnx_max_abs": {n: float(np.max(np.abs(a - b))) for n, a, b in zip(("logits", "pred_boxes"), result, reference)},
            "batch1_vs_hf_reference_max_abs": {n: float(np.max(np.abs(result[i] - hf[n]))) for i, n in enumerate(("logits", "pred_boxes"))},
        }
    batch = dynamic.run(None, {"pixel_values": np.concatenate([f.numpy() for f in frames])})
    checks["batch2_vs_batch1_max_abs"] = {n: float(max(np.max(np.abs(batch[i][k] - singles[k][i][0])) for k in range(2)))
                                          for i, n in enumerate(("logits", "pred_boxes"))}
    eight = dynamic.run(None, {"pixel_values": np.concatenate([frames[0].numpy()] * 8)})
    checks["batch8_shapes"] = [list(eight[0].shape), list(eight[1].shape)]
    def numbers(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from numbers(item)
        elif isinstance(value, float):
            yield value
    worst = max(numbers({k: v for k, v in checks.items() if k != "batch8_shapes"}))
    passed = worst <= MAX_ABS_DIFF and checks["batch8_shapes"] == [[8, 300, 80], [8, 300, 4]]
    REPORT.mkdir(parents=True, exist_ok=True)
    if not passed:
        temporary.unlink()
    else:
        os.replace(temporary, DYNAMIC_ONNX)
    result = {
        "schema_version": 1, "result": "PASS" if passed else "FAIL", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "checkpoint_sha256": EXPECTED_CHECKPOINT, "static_onnx_sha256": EXPECTED_STATIC_ONNX,
        "dynamic_onnx": DYNAMIC_ONNX.relative_to(ROOT).as_posix() if passed else None,
        "dynamic_onnx_sha256": sha256(DYNAMIC_ONNX) if passed else None, "contract": contract,
        "export": {"opset_version": 17, "do_constant_folding": True, "dynamo": False,
                   "dynamic_axes": {"pixel_values": {0: "batch"}, "logits": {0: "batch"}, "pred_boxes": {0: "batch"}}},
        "versions": {"torch": torch.__version__, "transformers": transformers.__version__, "onnx": onnx.__version__,
                     "onnxruntime": onnxruntime.__version__, "numpy": np.__version__},
        "max_abs_diff_allowed": MAX_ABS_DIFF, "worst_max_abs_diff": worst, "checks": checks,
        "method": "onnxruntime CPU FP32 on reports/model-evaluation/rt-detr/frames 141 and 283 (processor-resized to 640x640)",
    }
    (REPORT / "export.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("result", "worst_max_abs_diff", "dynamic_onnx_sha256")}))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    sys.exit(main())
