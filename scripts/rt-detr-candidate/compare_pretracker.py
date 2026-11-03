#!/usr/bin/env python3
"""Like-for-like RT-DETR comparison of a pre-tracker DeepStream diagnostic run.

Stages, each reported separately:
  A. nvinfer input tensor vs the HF processor tensor (preprocessing/geometry).
  B. DeepStream raw output tensors vs ONNX Runtime on the *same* DeepStream input
     (engine-inside-DeepStream parity).
  C. Pre-tracker DeepStream objects vs the Python decode of DeepStream's own raw
     tensors (native parser + nvinfer coordinate scaling parity).
  D. Pre-tracker objects vs the original ONNX-on-HF-input decodes, using the
     unchanged strict criteria (end-to-end detector agreement).
  E. Post-NvDCF objects vs pre-tracker objects (tracker effect only).
  F. CPU emulations of candidate resize filters on the PNG reference frame, to
     attribute stage A differences to resize vs decode/colour conversion.

Run with the isolated export venv (onnxruntime, numpy, Pillow).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image
import onnxruntime as ort

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from perception.candidates.rt_detr.decode import DecodeGeometry, decode_person  # noqa: E402

EXPORT_RUN = ROOT / "reports" / "model-evaluation" / "rt-detr" / "export" / "run-001"
FRAMES_DIR = ROOT / "reports" / "model-evaluation" / "rt-detr" / "frames"
ONNX_PATH = ROOT / "models" / "rt-detr-person-candidate" / "rt_detr_r18vd_raw.onnx"
EXPECTED_ONNX = "8048e1534bd0864ae53003a3f273a6155f14ff9cb39454c5141baac3babc2e07"
SCORE_DELTA_MAX = 0.02
IOU_MIN = 0.5
FRAMES = (141, 283)
# Engine parity tolerances are the ones already used for the serialized TRT analysis.
LOGIT_TOL = 1e-3
BOX_TOL = 1e-4


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def iou(a, b) -> float:
    ix1, iy1, ix2, iy2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def xyxy(obj: dict) -> list[float]:
    box = obj["bbox"]
    return [box["x"], box["y"], box["x"] + box["width"], box["y"] + box["height"]]


def strict_match(left: list[tuple[list[float], float]], right: list[tuple[list[float], float]],
                 score_max: float | None = SCORE_DELTA_MAX, iou_min: float = IOU_MIN) -> dict:
    """Greedy one-to-one match, highest IoU first, exactly as the original crosscheck."""
    pairs = []
    for li, (lbox, lscore) in enumerate(left):
        for ri, (rbox, rscore) in enumerate(right):
            delta = abs(lscore - rscore)
            overlap = iou(lbox, rbox)
            if overlap >= iou_min and (score_max is None or delta <= score_max):
                pairs.append((overlap, delta, li, ri))
    pairs.sort(key=lambda p: (-p[0], p[1], p[2], p[3]))
    used_l, used_r, matches = set(), set(), []
    for overlap, delta, li, ri in pairs:
        if li in used_l or ri in used_r:
            continue
        used_l.add(li)
        used_r.add(ri)
        coord = max(abs(a - b) for a, b in zip(left[li][0], right[ri][0]))
        matches.append({"left": li, "right": ri, "iou": overlap, "score_abs_delta": delta, "bbox_max_abs_delta_px": coord})
    return {
        "left_count": len(left), "right_count": len(right), "matches": len(matches),
        "unmatched_left": sorted(set(range(len(left))) - used_l),
        "unmatched_right": sorted(set(range(len(right))) - used_r),
        "min_iou": min((m["iou"] for m in matches), default=None),
        "max_score_abs_delta": max((m["score_abs_delta"] for m in matches), default=None),
        "max_bbox_abs_delta_px": max((m["bbox_max_abs_delta_px"] for m in matches), default=None),
        "median_bbox_abs_delta_px": float(np.median([m["bbox_max_abs_delta_px"] for m in matches])) if matches else None,
    }


def tensor_diff(a: np.ndarray, b: np.ndarray) -> dict:
    d = np.abs(a.astype(np.float64) - b.astype(np.float64))
    out = {"max_abs": float(d.max()), "mean_abs": float(d.mean()), "p99_abs": float(np.quantile(d, 0.99))}
    if a.ndim == 3 and a.shape[0] == 3:
        out["per_channel_mean_signed_a_minus_b"] = [float((a[c].astype(np.float64) - b[c]).mean()) for c in range(3)]
        mse = float((d ** 2).mean())
        out["psnr_db_unit_range"] = float(10 * np.log10(1.0 / mse)) if mse > 0 else None
    return out


def resize_nearest(img: np.ndarray, h: int, w: int, mode: str) -> np.ndarray:
    sh, sw = img.shape[:2]
    if mode == "half_pixel":
        ys = np.floor((np.arange(h) + 0.5) * sh / h).astype(int)
        xs = np.floor((np.arange(w) + 0.5) * sw / w).astype(int)
    else:  # top-left aligned
        ys = np.floor(np.arange(h) * sh / h).astype(int)
        xs = np.floor(np.arange(w) * sw / w).astype(int)
    return img[np.clip(ys, 0, sh - 1)][:, np.clip(xs, 0, sw - 1)]


def resize_bilinear_no_aa(img: np.ndarray, h: int, w: int) -> np.ndarray:
    sh, sw = img.shape[:2]
    y = np.clip((np.arange(h) + 0.5) * sh / h - 0.5, 0, sh - 1)
    x = np.clip((np.arange(w) + 0.5) * sw / w - 0.5, 0, sw - 1)
    y0, x0 = np.floor(y).astype(int), np.floor(x).astype(int)
    y1, x1 = np.minimum(y0 + 1, sh - 1), np.minimum(x0 + 1, sw - 1)
    wy, wx = (y - y0)[:, None, None], (x - x0)[None, :, None]
    f = img.astype(np.float64)
    top = f[y0][:, x0] * (1 - wx) + f[y0][:, x1] * wx
    bottom = f[y1][:, x0] * (1 - wx) + f[y1][:, x1] * wx
    return top * (1 - wy) + bottom * wy


def to_chw_unit(img: np.ndarray) -> np.ndarray:
    return (np.asarray(img, dtype=np.float64) / 255.0).transpose(2, 0, 1).astype(np.float32)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    destination = run / "pretracker-comparison.json"
    if destination.exists():
        raise SystemExit(f"Refusing to overwrite {destination}")
    if sha256(ONNX_PATH) != EXPECTED_ONNX:
        raise SystemExit("ONNX identity mismatch")
    session = ort.InferenceSession(str(ONNX_PATH), providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    output_names = [o.name for o in session.get_outputs()]
    pre_rows = {r["frame_number"]: r for r in map(json.loads, (run / "pre_tracker.jsonl").open(encoding="utf-8"))}
    post_path = run / "post_tracker.jsonl"
    post_rows = {r["frame_number"]: r for r in map(json.loads, post_path.open(encoding="utf-8"))} if post_path.exists() else {}
    summary = json.loads((run / "run-summary.json").read_text(encoding="utf-8"))
    geometry = DecodeGeometry(1920, 1080)
    frames = []
    for index in FRAMES:
        ds_input = np.fromfile(run / f"ds_input_{index:06d}.f32le", dtype="<f4").reshape(3, 640, 640)
        with np.load(EXPORT_RUN / f"hf_frame_{index:06d}.npz") as data:
            hf_input = np.asarray(data["pixel_values"], dtype=np.float32)[0]
        ds_logits = np.load(run / "tensors" / f"logits_{index:06d}.npy").reshape(300, 80)
        ds_boxes = np.load(run / "tensors" / f"pred_boxes_{index:06d}.npy").reshape(300, 4)

        # A. preprocessing / geometry
        stage_a = tensor_diff(ds_input, hf_input)
        stage_a["ds_input_range"] = [float(ds_input.min()), float(ds_input.max())]

        # B. engine inside DeepStream vs ONNX Runtime on the identical input
        onnx_out = dict(zip(output_names, session.run(output_names, {input_name: ds_input[None]})))
        onnx_logits = np.asarray(onnx_out["logits"]).reshape(300, 80)
        onnx_boxes = np.asarray(onnx_out["pred_boxes"]).reshape(300, 4)
        stage_b = {"logits": tensor_diff(ds_logits, onnx_logits), "pred_boxes": tensor_diff(ds_boxes, onnx_boxes)}
        ds_raw_dec = decode_person(ds_logits, ds_boxes, geometry, 0.25, 300)
        onnx_ds_dec = decode_person(onnx_logits, onnx_boxes, geometry, 0.25, 300)
        stage_b["person_decodes_ds_tensors"] = len(ds_raw_dec)
        stage_b["person_decodes_onnx_on_ds_input"] = len(onnx_ds_dec)
        stage_b["person_query_order_equal"] = [d["query_index"] for d in ds_raw_dec] == [d["query_index"] for d in onnx_ds_dec]
        stage_b["result"] = "PASS" if (stage_b["logits"]["max_abs"] <= LOGIT_TOL and stage_b["pred_boxes"]["max_abs"] <= BOX_TOL
                                       and stage_b["person_query_order_equal"]) else "FAIL"

        # C. native parser + nvinfer scaling vs Python decode of the same DS tensors
        pre_objects = pre_rows[index]["objects"]
        pre_pairs = [(xyxy(o), float(o["confidence"])) for o in pre_objects]
        raw_pairs = [(d["bbox_xyxy_pixels"], float(d["confidence"])) for d in ds_raw_dec]
        stage_c = strict_match(pre_pairs, raw_pairs, score_max=1e-5, iou_min=0.99)
        stage_c["pre_tracker_track_ids_all_untracked"] = all(o["track_id"] is None for o in pre_objects)
        stage_c["result"] = "PASS" if (stage_c["matches"] == len(pre_pairs) == len(raw_pairs)
                                       and (stage_c["max_bbox_abs_delta_px"] or 0) <= 0.05) else "FAIL"

        # D. end-to-end detector agreement against the original HF-input ONNX reference
        reference = json.loads((EXPORT_RUN / f"onnx_decode_frame_{index:06d}.json").read_text(encoding="utf-8"))["detections"]
        ref_pairs = [(d["bbox_xyxy_pixels"], float(d["confidence"])) for d in reference]
        stage_d = strict_match(pre_pairs, ref_pairs)
        stage_d["result"] = "PASS" if stage_d["matches"] == len(pre_pairs) == len(ref_pairs) else "FAIL"
        stage_d_iou_only = strict_match(pre_pairs, ref_pairs, score_max=None)
        stage_d["iou_only_matches_for_context"] = stage_d_iou_only["matches"]
        stage_d["iou_only_max_score_abs_delta"] = stage_d_iou_only["max_score_abs_delta"]

        # E. tracker effect
        stage_e = None
        if post_rows:
            post_objects = post_rows[index]["objects"]
            post_pairs = [(xyxy(o), float(o["confidence"])) for o in post_objects]
            stage_e = strict_match(post_pairs, pre_pairs, score_max=1e-5, iou_min=0.5)
            stage_e["post_count"] = len(post_pairs)
            stage_e["pre_count"] = len(pre_pairs)
            stage_e["post_vs_original_onnx_reference_matches"] = strict_match(post_pairs, ref_pairs)["matches"]

        # F. attribution via CPU emulation from the lossless PNG (OpenCV/FFmpeg decode)
        with Image.open(FRAMES_DIR / f"frame_{index:06d}.png") as source:
            rgb = np.asarray(source.convert("RGB"))
        emulations = {
            "pil_bilinear_antialias_(hf)": to_chw_unit(np.asarray(Image.fromarray(rgb).resize((640, 640), Image.Resampling.BILINEAR))),
            "nearest_half_pixel": to_chw_unit(resize_nearest(rgb, 640, 640, "half_pixel")),
            "nearest_top_left": to_chw_unit(resize_nearest(rgb, 640, 640, "top_left")),
            "bilinear_no_antialias": to_chw_unit(np.clip(np.rint(resize_bilinear_no_aa(rgb, 640, 640)), 0, 255)),
        }
        stage_f = {name: tensor_diff(ds_input, value) for name, value in emulations.items()}
        stage_f["closest_by_mean_abs"] = min(emulations, key=lambda name: stage_f[name]["mean_abs"])

        frames.append({"frame_index": index, "A_input_ds_vs_hf": stage_a, "B_engine_in_ds_vs_onnx_same_input": stage_b,
                       "C_parser_scaling_vs_python_decode_of_ds_tensors": stage_c,
                       "D_pre_tracker_vs_original_onnx_reference_strict": stage_d,
                       "E_post_nvdcf_vs_pre_tracker": stage_e, "F_resize_emulation_vs_ds_input": stage_f})

    def all_pass(key):
        return all(f[key]["result"] == "PASS" for f in frames)

    result = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run.relative_to(ROOT)), "infer_config": summary.get("infer_config"),
        "infer_config_sha256": summary.get("infer_config_sha256"), "onnx_sha256": EXPECTED_ONNX,
        "onnxruntime_version": ort.__version__,
        "stage_results": {
            "B_engine_inside_deepstream": "PASS" if all_pass("B_engine_in_ds_vs_onnx_same_input") else "FAIL",
            "C_native_parser_and_scaling": "PASS" if all_pass("C_parser_scaling_vs_python_decode_of_ds_tensors") else "FAIL",
            "D_end_to_end_pre_tracker_vs_hf_input_onnx": "PASS" if all_pass("D_pre_tracker_vs_original_onnx_reference_strict") else "FAIL",
        },
        "frames": frames,
        "tolerances": {"engine_logits_max_abs": LOGIT_TOL, "engine_pred_boxes_max_abs": BOX_TOL,
                       "parser_score_abs": 1e-5, "parser_iou_min": 0.99, "parser_bbox_px": 0.05,
                       "strict_crosscheck": {"score_abs_delta_max": SCORE_DELTA_MAX, "bbox_iou_min": IOU_MIN}},
        "scope": "Two real frames; implementation consistency only. Not labeled accuracy.",
    }
    destination.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    brief = {"stage_results": result["stage_results"], "frames": []}
    for f in frames:
        brief["frames"].append({
            "frame": f["frame_index"],
            "A_mean_abs": f["A_input_ds_vs_hf"]["mean_abs"], "A_max_abs": f["A_input_ds_vs_hf"]["max_abs"],
            "A_channel_bias": f["A_input_ds_vs_hf"].get("per_channel_mean_signed_a_minus_b"),
            "B_logits_max": f["B_engine_in_ds_vs_onnx_same_input"]["logits"]["max_abs"],
            "B_boxes_max": f["B_engine_in_ds_vs_onnx_same_input"]["pred_boxes"]["max_abs"],
            "C": (f["C_parser_scaling_vs_python_decode_of_ds_tensors"]["matches"], f["C_parser_scaling_vs_python_decode_of_ds_tensors"]["left_count"], f["C_parser_scaling_vs_python_decode_of_ds_tensors"]["right_count"], f["C_parser_scaling_vs_python_decode_of_ds_tensors"]["max_bbox_abs_delta_px"]),
            "D": (f["D_pre_tracker_vs_original_onnx_reference_strict"]["matches"], f["D_pre_tracker_vs_original_onnx_reference_strict"]["left_count"], f["D_pre_tracker_vs_original_onnx_reference_strict"]["right_count"], f["D_pre_tracker_vs_original_onnx_reference_strict"]["iou_only_matches_for_context"]),
            "E": None if f["E_post_nvdcf_vs_pre_tracker"] is None else (f["E_post_nvdcf_vs_pre_tracker"]["matches"], f["E_post_nvdcf_vs_pre_tracker"]["post_count"], f["E_post_nvdcf_vs_pre_tracker"]["pre_count"], f["E_post_nvdcf_vs_pre_tracker"]["max_bbox_abs_delta_px"], f["E_post_nvdcf_vs_pre_tracker"]["post_vs_original_onnx_reference_matches"]),
            "F": {k: round(v["mean_abs"], 5) for k, v in f["F_resize_emulation_vs_ds_input"].items() if isinstance(v, dict)},
        })
    print(json.dumps(brief, indent=1))


if __name__ == "__main__":
    main()
