#!/usr/bin/env python3
"""Like-for-like RF-DETR parity: eager wrapper vs ONNX Runtime on byte-identical rgb01 inputs.

The export run's HF-processor comparison is retained as-is. This check removes the
1.2e-6 processor-vs-manual rounding difference, which can reorder near-tied
two-stage top-k proposals (see topk-selection-diagnosis.json), by feeding the
exact saved rgb01 tensor to both implementations.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
import numpy as np
import onnxruntime as ort
import torch
from transformers import RfDetrForObjectDetection

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_onnx import UPSTREAM, ONNX_PATH, NormalizedRawWrapper, install_static_pos_encoding_patch, sha256, RAW_TOL, THRESHOLD  # noqa: E402
from perception.candidates.rf_detr.decode import DecodeGeometry, decode_person  # noqa: E402


def main():
    run = Path(sys.argv[1]).resolve()
    out = run / "parity-identical-input.json"
    if out.exists():
        raise SystemExit(f"Refusing to overwrite {out}")
    install_static_pos_encoding_patch()
    wrapper = NormalizedRawWrapper(RfDetrForObjectDetection.from_pretrained(str(UPSTREAM), local_files_only=True).eval())
    session = ort.InferenceSession(str(ONNX_PATH), providers=["CPUExecutionProvider"])
    frames = []
    for index in (141, 283):
        path = run / f"rgb01_{index:06d}.f32le"
        x = np.fromfile(path, dtype="<f4").reshape(1, 3, 512, 512)
        with torch.no_grad():
            tl, tb = (t.numpy() for t in wrapper(torch.from_numpy(x.copy())))
        ol, ob = session.run(["logits", "pred_boxes"], {"input": x})
        g = DecodeGeometry(1920, 1080)
        td, od = decode_person(tl, tb, g, THRESHOLD), decode_person(ol, ob, g, THRESHOLD)
        hf = json.loads((run / f"hf_decode_frame_{index:06d}.json").read_text(encoding="utf-8"))["detections"]
        rec = {"frame_index": index, "input": path.name, "input_sha256": sha256(path),
               "logits_max_abs": float(np.abs(tl - ol).max()), "pred_boxes_max_abs": float(np.abs(tb - ob).max()),
               "eager_person_decodes": len(td), "onnx_person_decodes": len(od),
               "person_query_order_equal": [d["query_index"] for d in td] == [d["query_index"] for d in od],
               "person_score_max_abs": max((abs(a["confidence"] - b["confidence"]) for a, b in zip(td, od)), default=0.0),
               "hf_processor_path_person_query_order_equal": [d["query_index"] for d in hf] == [d["query_index"] for d in od],
               "hf_processor_path_person_score_max_abs": max((abs(a["confidence"] - b["confidence"]) for a, b in zip(hf, od)), default=0.0)}
        rec["result"] = "PASS" if rec["logits_max_abs"] <= RAW_TOL and rec["pred_boxes_max_abs"] <= 1e-4 and rec["person_query_order_equal"] else "FAIL"
        frames.append(rec)
    result = {"schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(), "onnx_sha256": sha256(ONNX_PATH),
              "result": "PASS" if all(f["result"] == "PASS" for f in frames) else "FAIL", "frames": frames,
              "tolerances": {"logits": RAW_TOL, "pred_boxes": 1e-4}}
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
