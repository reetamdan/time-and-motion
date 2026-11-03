#!/usr/bin/env python3
"""Build a colour-correct (BT.709 limited-range) HF-processor reference and crosscheck runs.

The original reference PNGs were decoded by OpenCV with a BT.601 matrix although
the stream is tagged BT.709 limited range (see colour-attribution.json). This
script never touches the original evidence. It writes a new reference
directory with the corrected input tensors and ONNX person decodes, then
applies the *unchanged* strict criteria (one-to-one, IoU >= 0.5, score delta
<= 0.02) to pre- and post-tracker objects of each supplied diagnostic run.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image
import onnxruntime as ort

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from perception.candidates.rt_detr.decode import DecodeGeometry, decode_person  # noqa: E402
from compare_pretracker import strict_match, tensor_diff, xyxy  # noqa: E402

SOURCE = ROOT / "demo" / "cam01.mp4"
SOURCE_SHA = "4201f7162eb0b9bcd3910f87b1a67a5bedac89c2b0a0bb4222e67d12f816b503"
ONNX_PATH = ROOT / "models" / "rt-detr-person-candidate" / "rt_detr_r18vd_raw.onnx"
EXPECTED_ONNX = "8048e1534bd0864ae53003a3f273a6155f14ff9cb39454c5141baac3babc2e07"
FRAMES = (141, 283)
SCALE = "in_color_matrix=bt709:in_range=tv:flags=bilinear+accurate_rnd+full_chroma_int"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    args = parser.parse_args()
    out = args.out_dir.resolve()
    if out.exists():
        raise SystemExit(f"Refusing to reuse {out}")
    out.mkdir(parents=True)
    if sha256(SOURCE) != SOURCE_SHA or sha256(ONNX_PATH) != EXPECTED_ONNX:
        raise SystemExit("source or ONNX identity mismatch")
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    session = ort.InferenceSession(str(ONNX_PATH), providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    names = [o.name for o in session.get_outputs()]
    reference = {}
    for index in FRAMES:
        command = [ffmpeg, "-v", "error", "-i", str(SOURCE), "-vf", f"select=eq(n\\,{index}),scale=1920:1080:{SCALE},format=rgb24",
                   "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        rgb = np.frombuffer(subprocess.run(command, check=True, capture_output=True).stdout, np.uint8).reshape(1080, 1920, 3)
        png = out / f"frame_{index:06d}_bt709.png"
        Image.fromarray(rgb).save(png)
        # Same contract as RTDetrImageProcessor (verified in export run-001): PIL bilinear direct resize, /255.
        pixels = (np.asarray(Image.fromarray(rgb).resize((640, 640), Image.Resampling.BILINEAR), np.float32) / 255.0).transpose(2, 0, 1)
        tensor_path = out / f"pixel_values_{index:06d}_bt709.f32le"
        np.ascontiguousarray(pixels, dtype="<f4").tofile(tensor_path)
        logits, boxes = (np.asarray(v) for v in session.run(names, {input_name: pixels[None]}))
        decodes = decode_person(logits, boxes, DecodeGeometry(1920, 1080), 0.25, 300)
        decode_path = out / f"onnx_decode_frame_{index:06d}_bt709.json"
        decode_path.write_text(json.dumps({"frame_index": index, "threshold": 0.25, "detections": decodes}, indent=1) + "\n", encoding="utf-8")
        reference[index] = {"png": png.name, "png_sha256": sha256(png), "tensor": tensor_path.name, "tensor_sha256": sha256(tensor_path),
                            "decode": decode_path.name, "person_decodes": len(decodes), "pairs": [(d["bbox_xyxy_pixels"], float(d["confidence"])) for d in decodes],
                            "pixels": pixels}
    runs = []
    for run_dir in args.run_dir:
        run = run_dir.resolve()
        summary = json.loads((run / "run-summary.json").read_text(encoding="utf-8"))
        pre = {r["frame_number"]: r for r in map(json.loads, (run / "pre_tracker.jsonl").open(encoding="utf-8"))}
        post_file = run / "post_tracker.jsonl"
        post = {r["frame_number"]: r for r in map(json.loads, post_file.open(encoding="utf-8"))} if post_file.exists() else {}
        frames = []
        for index in FRAMES:
            ds_input = np.fromfile(run / f"ds_input_{index:06d}.f32le", dtype="<f4").reshape(3, 640, 640)
            pre_pairs = [(xyxy(o), float(o["confidence"])) for o in pre[index]["objects"]]
            row = {"frame_index": index, "input_vs_bt709_reference": tensor_diff(ds_input, reference[index]["pixels"]),
                   "pre_tracker_strict": {k: v for k, v in strict_match(pre_pairs, reference[index]["pairs"]).items()}}
            if post:
                post_pairs = [(xyxy(o), float(o["confidence"])) for o in post[index]["objects"]]
                row["post_nvdcf_strict"] = strict_match(post_pairs, reference[index]["pairs"])
            frames.append(row)
        def full(key):
            return all(f[key]["matches"] == f[key]["left_count"] == f[key]["right_count"] for f in frames if key in f)
        runs.append({"run_dir": str(run.relative_to(ROOT)), "infer_config": summary.get("infer_config"),
                     "pre_tracker_result": "PASS" if full("pre_tracker_strict") else "FAIL",
                     "post_nvdcf_result": ("PASS" if full("post_nvdcf_strict") else "FAIL") if post else None,
                     "frames": frames})
    result = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "reference": {"decoder": subprocess.run([ffmpeg, "-version"], capture_output=True, text=True).stdout.splitlines()[0],
                      "colour_conversion": SCALE, "resize": "PIL Image.resize BILINEAR (antialiased) to 640x640, /255, no mean/std",
                      "onnx_sha256": EXPECTED_ONNX, "onnxruntime": ort.__version__,
                      "frames": {str(k): {kk: vv for kk, vv in v.items() if kk not in ("pairs", "pixels")} for k, v in reference.items()}},
        "criteria": {"matching": "strict one-to-one", "bbox_iou_min": 0.5, "score_abs_delta_max": 0.02, "unchanged_from_original": True},
        "runs": runs,
        "note": "Original reference (OpenCV BT.601 decode) and its FAIL crosscheck remain unchanged in export/run-001 and deepstream/run-20261026T143055Z.",
    }
    (out / "corrected-reference-crosscheck.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for r in runs:
        print(r["run_dir"], "pre:", r["pre_tracker_result"], "post:", r["post_nvdcf_result"])
        for f in r["frames"]:
            p = f["pre_tracker_strict"]
            q = f.get("post_nvdcf_strict") or {}
            print(f"  frame {f['frame_index']} input mean_abs {f['input_vs_bt709_reference']['mean_abs']:.5f} "
                  f"pre {p['matches']}/{p['left_count']} (ref {p['right_count']}) max_dscore {p['max_score_abs_delta']} "
                  f"post {q.get('matches')}/{q.get('left_count')}")


if __name__ == "__main__":
    main()
