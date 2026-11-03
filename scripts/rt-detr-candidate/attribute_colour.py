#!/usr/bin/env python3
"""Attribute residual DeepStream-vs-HF input differences to YUV->RGB colour conversion.

Decodes the exact frames with ffmpeg under explicit colour matrices, applies the
HF resize (PIL bilinear with antialias), and compares each against (a) the
DeepStream nvinfer input tensor of a diagnostic run and (b) the HF reference
tensor that was built from the OpenCV-decoded PNG.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "demo" / "cam01.mp4"
EXPORT_RUN = ROOT / "reports" / "model-evaluation" / "rt-detr" / "export" / "run-001"
VARIANTS = {
    "bt709_tv": "in_color_matrix=bt709:in_range=tv",
    "bt601_tv": "in_color_matrix=bt601:in_range=tv",
    "bt709_pc": "in_color_matrix=bt709:in_range=pc",
}


def decode(index: int, scale_args: str, flags: str) -> np.ndarray:
    command = [shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-i", str(SOURCE), "-vf",
               f"select=eq(n\\,{index}),scale=1920:1080:{scale_args}:flags={flags},format=rgb24",
               "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    data = subprocess.run(command, check=True, capture_output=True).stdout
    if len(data) != 1920 * 1080 * 3:
        raise RuntimeError(f"unexpected decode size {len(data)}")
    return np.frombuffer(data, np.uint8).reshape(1080, 1920, 3)


def hf_resize(rgb: np.ndarray) -> np.ndarray:
    img = Image.fromarray(rgb).resize((640, 640), Image.Resampling.BILINEAR)
    return (np.asarray(img, np.float32) / 255.0).transpose(2, 0, 1)


def diff(a: np.ndarray, b: np.ndarray) -> dict:
    d = a.astype(np.float64) - b.astype(np.float64)
    return {"mean_abs": float(np.abs(d).mean()), "max_abs": float(np.abs(d).max()),
            "per_channel_mean_signed": [float(d[c].mean()) for c in range(3)]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    destination = run / "colour-attribution.json"
    if destination.exists():
        raise SystemExit(f"Refusing to overwrite {destination}")
    frames = []
    for index in (141, 283):
        ds = np.fromfile(run / f"ds_input_{index:06d}.f32le", dtype="<f4").reshape(3, 640, 640)
        with np.load(EXPORT_RUN / f"hf_frame_{index:06d}.npz") as data:
            hf = np.asarray(data["pixel_values"], np.float32)[0]
        rows = {}
        for name, scale_args in VARIANTS.items():
            for flags in ("bilinear+accurate_rnd+full_chroma_int", "bicubic"):
                tensor = hf_resize(decode(index, scale_args, flags))
                rows[f"{name}/{flags}"] = {"vs_deepstream_input": diff(ds, tensor), "vs_hf_reference_input": diff(hf, tensor)}
        frames.append({"frame_index": index, "variants": rows,
                       "closest_to_deepstream": min(rows, key=lambda k: rows[k]["vs_deepstream_input"]["mean_abs"]),
                       "closest_to_hf_reference": min(rows, key=lambda k: rows[k]["vs_hf_reference_input"]["mean_abs"])})
    version = subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-version"], capture_output=True, text=True).stdout.splitlines()[0]
    result = {"schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(), "ffmpeg": version,
              "source_colour_metadata": "h264 yuv420p, color_range=tv, color_space/transfer/primaries=bt709 (ffprobe)",
              "frames": frames, "scope": "Attribution of preprocessing differences only; not a detector-quality measure."}
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for frame in frames:
        print(frame["frame_index"], "closest DS:", frame["closest_to_deepstream"], "closest HF:", frame["closest_to_hf_reference"])
        for key, value in frame["variants"].items():
            print(f"  {key:50s} DS {value['vs_deepstream_input']['mean_abs']:.5f} {np.round(value['vs_deepstream_input']['per_channel_mean_signed'], 4)}"
                  f"  HF {value['vs_hf_reference_input']['mean_abs']:.5f} {np.round(value['vs_hf_reference_input']['per_channel_mean_signed'], 4)}")


if __name__ == "__main__":
    main()
