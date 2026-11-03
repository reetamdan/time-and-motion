#!/usr/bin/env python3
"""Like-for-like RF-DETR comparison of a pre-tracker DeepStream diagnostic run.

Stages (reported separately, same structure as the RT-DETR diagnostic):
  A. nvinfer input tensor vs the HF-contract rgb01 tensor (BT.709 decode, bilinear antialias=False).
  B. DeepStream raw output tensors vs ONNX Runtime on the *same* DeepStream input.
     Raw-slot and person-decode parity are reported separately because raw
     slots are order-sensitive to near-tied two-stage proposals.
  C. Pre-tracker objects vs Python decode of DeepStream's own raw tensors.
  D. Pre-tracker objects vs the HF person decode (unchanged strict criteria).
  E. Post-NvDCF objects vs pre-tracker objects.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import numpy as np
import onnxruntime as ort

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "rt-detr-candidate"))
from perception.candidates.rf_detr.decode import DecodeGeometry, decode_person  # noqa: E402
from compare_pretracker import strict_match, tensor_diff, xyxy, sha256  # noqa: E402  (RT-DETR helpers)

EXPORT = ROOT / "reports" / "model-evaluation" / "rf-detr" / "export" / "run-002"
ONNX_PATH = ROOT / "models" / "rf-detr-person-candidate" / "rf_detr_small_rgb01_norm_in_graph.onnx"
FRAMES = (141, 283)
LOGIT_TOL, BOX_TOL, PERSON_SCORE_TOL = 1e-3, 1e-4, 5e-3


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--onnx", type=Path, default=ONNX_PATH)
    parser.add_argument("--input-shape", default="3,512,512", help="DS network input CHW, e.g. 3,1080,1920 for variant 2")
    parser.add_argument("--hf-input", default=str(EXPORT / "rgb01_{index:06d}.f32le"),
                        help="HF-contract tensor with the same shape as the DS input")
    args = parser.parse_args()
    shape = tuple(int(v) for v in args.input_shape.split(","))
    run = args.run_dir.resolve()
    destination = run / "pretracker-comparison.json"
    if destination.exists():
        raise SystemExit(f"Refusing to overwrite {destination}")
    session = ort.InferenceSession(str(args.onnx), providers=["CPUExecutionProvider"])
    pre = {r["frame_number"]: r for r in map(json.loads, (run / "pre_tracker.jsonl").open(encoding="utf-8"))}
    post_path = run / "post_tracker.jsonl"
    post = {r["frame_number"]: r for r in map(json.loads, post_path.open(encoding="utf-8"))} if post_path.exists() else {}
    summary = json.loads((run / "run-summary.json").read_text(encoding="utf-8"))
    geometry = DecodeGeometry(1920, 1080)
    frames = []
    for index in FRAMES:
        ds_input = np.fromfile(run / f"ds_input_{index:06d}.f32le", dtype="<f4").reshape(shape)
        hf_input = np.fromfile(args.hf_input.format(index=index), dtype="<f4").reshape(shape)
        ds_logits = np.load(run / "tensors" / f"logits_{index:06d}.npy").reshape(300, 91)
        ds_boxes = np.load(run / "tensors" / f"pred_boxes_{index:06d}.npy").reshape(300, 4)
        stage_a = tensor_diff(ds_input, hf_input)

        ol, ob = session.run(["logits", "pred_boxes"], {"input": ds_input[None]})
        ol, ob = ol.reshape(300, 91), ob.reshape(300, 4)
        ds_dec, onnx_dec = decode_person(ds_logits, ds_boxes, geometry), decode_person(ol, ob, geometry)
        stage_b = {"logits": tensor_diff(ds_logits, ol), "pred_boxes": tensor_diff(ds_boxes, ob),
                   "slots_logits_delta_gt_1e-3": int((np.abs(ds_logits - ol).max(-1) > LOGIT_TOL).sum()),
                   "person_decodes_ds": len(ds_dec), "person_decodes_onnx": len(onnx_dec),
                   "person_query_order_equal": [d["query_index"] for d in ds_dec] == [d["query_index"] for d in onnx_dec],
                   "person_score_max_abs": max((abs(a["confidence"] - b["confidence"]) for a, b in zip(ds_dec, onnx_dec)), default=0.0)}
        sets = strict_match([(d["bbox_xyxy_pixels"], d["confidence"]) for d in ds_dec], [(d["bbox_xyxy_pixels"], d["confidence"]) for d in onnx_dec])
        stage_b["person_set_strict"] = {k: sets[k] for k in ("matches", "left_count", "right_count", "max_score_abs_delta", "max_bbox_abs_delta_px")}
        stage_b["person_set_result"] = "PASS" if sets["matches"] == sets["left_count"] == sets["right_count"] else "FAIL"
        stage_b["raw_slot_result"] = "PASS" if stage_b["logits"]["max_abs"] <= LOGIT_TOL and stage_b["pred_boxes"]["max_abs"] <= BOX_TOL else "FAIL"
        stage_b["person_decode_result"] = "PASS" if (stage_b["person_query_order_equal"] and len(ds_dec) == len(onnx_dec)
                                                     and stage_b["person_score_max_abs"] <= PERSON_SCORE_TOL) else "FAIL"

        pre_objects = pre[index]["objects"]
        pre_pairs = [(xyxy(o), float(o["confidence"])) for o in pre_objects]
        raw_pairs = [(d["bbox_xyxy_pixels"], float(d["confidence"])) for d in ds_dec]
        stage_c = strict_match(pre_pairs, raw_pairs, score_max=1e-5, iou_min=0.99)
        stage_c["all_class_id_zero_person"] = all(o["class_id"] == 0 and o["label"] == "person" for o in pre_objects)
        stage_c["result"] = "PASS" if (stage_c["matches"] == len(pre_pairs) == len(raw_pairs) and stage_c["all_class_id_zero_person"]
                                       and (stage_c["max_bbox_abs_delta_px"] or 0) <= 0.05) else "FAIL"

        hf = json.loads((EXPORT / f"hf_decode_frame_{index:06d}.json").read_text(encoding="utf-8"))["detections"]
        hf_pairs = [(d["bbox_xyxy_pixels"], float(d["confidence"])) for d in hf]
        stage_d = strict_match(pre_pairs, hf_pairs)
        stage_d["result"] = "PASS" if stage_d["matches"] == len(pre_pairs) == len(hf_pairs) else "FAIL"
        unmatched = [pre_pairs[k][1] for k in stage_d["unmatched_left"]]
        stage_d["unmatched_pre_tracker_confidences"] = sorted(round(v, 4) for v in unmatched)

        stage_e = None
        if post:
            post_pairs = [(xyxy(o), float(o["confidence"])) for o in post[index]["objects"]]
            stage_e = strict_match(post_pairs, pre_pairs, score_max=1e-5, iou_min=0.5)
            stage_e["post_vs_hf_strict_matches"] = strict_match(post_pairs, hf_pairs)["matches"]
        frames.append({"frame_index": index, "A_input_ds_vs_hf_contract": stage_a, "B_engine_in_ds_vs_onnx_same_input": stage_b,
                       "C_parser_scaling_vs_python_decode_of_ds_tensors": stage_c, "D_pre_tracker_vs_hf_strict": stage_d,
                       "E_post_nvdcf_vs_pre_tracker": stage_e})

    def every(stage, key="result"):
        return "PASS" if all(f[stage][key] == "PASS" for f in frames) else "FAIL"

    result = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(), "run_dir": str(run.relative_to(ROOT)),
        "infer_config": summary.get("infer_config"), "infer_config_sha256": summary.get("infer_config_sha256"),
        "onnx": str(args.onnx), "onnx_sha256": sha256(args.onnx), "onnxruntime_version": ort.__version__,
        "stage_results": {
            "B_engine_inside_deepstream_raw_slots": every("B_engine_in_ds_vs_onnx_same_input", "raw_slot_result"),
            "B_engine_inside_deepstream_person_decodes": every("B_engine_in_ds_vs_onnx_same_input", "person_decode_result"),
            "B_engine_inside_deepstream_person_set_strict": every("B_engine_in_ds_vs_onnx_same_input", "person_set_result"),
            "C_native_parser_and_scaling": every("C_parser_scaling_vs_python_decode_of_ds_tensors"),
            "D_end_to_end_pre_tracker_vs_hf_strict": every("D_pre_tracker_vs_hf_strict"),
        },
        "frames": frames,
        "tolerances": {"raw_logits": LOGIT_TOL, "raw_boxes": BOX_TOL, "person_score": PERSON_SCORE_TOL,
                       "parser_score": 1e-5, "parser_iou_min": 0.99, "parser_bbox_px": 0.05,
                       "strict_crosscheck": {"score_abs_delta_max": 0.02, "bbox_iou_min": 0.5}},
        "scope": "Two real frames; implementation consistency only. Not labeled accuracy.",
    }
    destination.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result["stage_results"]))
    for f in frames:
        b, c, d, e = (f["B_engine_in_ds_vs_onnx_same_input"], f["C_parser_scaling_vs_python_decode_of_ds_tensors"],
                      f["D_pre_tracker_vs_hf_strict"], f["E_post_nvdcf_vs_pre_tracker"])
        print(f"frame {f['frame_index']}: A mean_abs {f['A_input_ds_vs_hf_contract']['mean_abs']:.5f} bias {np.round(f['A_input_ds_vs_hf_contract']['per_channel_mean_signed_a_minus_b'], 4)} | "
              f"B logits {b['logits']['max_abs']:.2e} boxes {b['pred_boxes']['max_abs']:.2e} person {b['person_decodes_ds']}/{b['person_decodes_onnx']} order {b['person_query_order_equal']} set {b['person_set_result']} | "
              f"C {c['matches']}/{c['left_count']}/{c['right_count']} | D {d['matches']}/{d['left_count']} (hf {d['right_count']}) unmatched {d['unmatched_pre_tracker_confidences']} | "
              f"E {None if e is None else (e['matches'], e['left_count'], e['right_count'], e['max_bbox_abs_delta_px'])}")


if __name__ == "__main__":
    main()
