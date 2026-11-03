#!/usr/bin/env python3
"""Diagnose RF-DETR two-stage top-k proposal selection sensitivity (eager PyTorch only).

Runs the HF model twice per frame on inputs that differ only by float rounding
(processor tensor vs manual contract tensor), records the encoder top-k
proposal indices/scores via a torch.topk spy, and reports whether the selected
proposal set differs and how close the k-th/(k+1)-th proposal scores are.
"""
import json
import os
import sys
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
import numpy as np
import torch
from PIL import Image
from transformers import RfDetrForObjectDetection, RfDetrImageProcessor

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_onnx import UPSTREAM, REFERENCE_DIR, manual_rgb01, MEAN, STD, install_static_pos_encoding_patch  # noqa: E402

calls = []
_topk = torch.topk


def spy(input, k, *args, **kwargs):
    values, indices = _topk(input, k, *args, **kwargs)
    full = torch.sort(input.detach().flatten() if input.dim() == 1 else input.detach().reshape(input.shape[0], -1), descending=True).values
    calls.append({"shape": list(input.shape), "k": int(k), "indices": indices.detach().clone(),
                  "kth": float(full[..., k - 1].flatten()[0]), "next": float(full[..., k].flatten()[0]) if full.shape[-1] > k else None})
    return values, indices


def main():
    out = Path(sys.argv[1]).resolve()
    if out.exists():
        raise SystemExit(f"Refusing to overwrite {out}")
    install_static_pos_encoding_patch()
    processor = RfDetrImageProcessor.from_pretrained(str(UPSTREAM), local_files_only=True)
    model = RfDetrForObjectDetection.from_pretrained(str(UPSTREAM), local_files_only=True).eval()
    mean = torch.tensor(MEAN).view(1, 3, 1, 1); std = torch.tensor(STD).view(1, 3, 1, 1)
    torch.topk = spy
    frames = []
    try:
        for index in (141, 283):
            with Image.open(REFERENCE_DIR / f"frame_{index:06d}_bt709.png") as source:
                image = source.convert("RGB")
            a = processor(images=image, return_tensors="pt")["pixel_values"]
            b = (manual_rgb01(image) - mean) / std
            runs = []
            for name, x in (("processor", a), ("manual", b)):
                calls.clear()
                with torch.no_grad():
                    o = model(pixel_values=x, return_dict=True)
                runs.append((name, [dict(c) for c in calls], o))
            (na, ca, oa), (nb, cb, ob) = runs
            per_call = []
            for x, y in zip(ca, cb):
                sa, sb = set(x["indices"].flatten().tolist()), set(y["indices"].flatten().tolist())
                per_call.append({"shape": x["shape"], "k": x["k"], "selected_set_symmetric_difference": len(sa ^ sb),
                                 "order_equal": bool(torch.equal(x["indices"], y["indices"])),
                                 "processor_kth_minus_next_margin": None if x["next"] is None else x["kth"] - x["next"],
                                 "manual_kth_minus_next_margin": None if y["next"] is None else y["kth"] - y["next"]})
            frames.append({"frame_index": index, "input_max_abs": float((a - b).abs().max()),
                           "logits_max_abs": float((oa.logits - ob.logits).abs().max()),
                           "topk_calls": per_call})
    finally:
        torch.topk = _topk
    out.write_text(json.dumps({"schema_version": 1, "frames": frames}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(frames, indent=1))



def adjacent_margins(argv):
    """Second mode: report where processor vs manual top-k orders diverge and the local score gaps."""
    out = Path(argv[1]).resolve()
    if out.exists():
        raise SystemExit(f"Refusing to overwrite {out}")
    install_static_pos_encoding_patch()
    processor = RfDetrImageProcessor.from_pretrained(str(UPSTREAM), local_files_only=True)
    model = RfDetrForObjectDetection.from_pretrained(str(UPSTREAM), local_files_only=True).eval()
    mean = torch.tensor(MEAN).view(1, 3, 1, 1); std = torch.tensor(STD).view(1, 3, 1, 1)
    captured = []

    def capture(input, k, *args, **kwargs):
        captured.append(input.detach().reshape(-1).clone())
        return _topk(input, k, *args, **kwargs)

    torch.topk = capture
    result = []
    try:
        for index in (141, 283):
            with Image.open(REFERENCE_DIR / f"frame_{index:06d}_bt709.png") as source:
                image = source.convert("RGB")
            scores = []
            for x in (processor(images=image, return_tensors="pt")["pixel_values"], (manual_rgb01(image) - mean) / std):
                captured.clear()
                with torch.no_grad():
                    model(pixel_values=x, return_dict=True)
                scores.append(captured[0])
            a, b = scores
            order_a, order_b = torch.argsort(a, descending=True, stable=True)[:300], torch.argsort(b, descending=True, stable=True)[:300]
            diverge = [int(r) for r in torch.nonzero(order_a != order_b).flatten()]
            sorted_a = a[order_a]
            gaps = (sorted_a[:-1] - sorted_a[1:]).abs()
            result.append({"frame_index": index, "diverging_ranks": diverge,
                           "score_gaps_at_diverging_ranks": [float(gaps[r]) if r < len(gaps) else None for r in diverge],
                           "min_adjacent_gap_in_top300": float(gaps.min()), "max_proposal_score_abs_delta_between_inputs": float((a - b).abs().max())})
    finally:
        torch.topk = _topk
    out.write_text(json.dumps({"schema_version": 1, "frames": result}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "--margins":
        adjacent_margins(sys.argv)
    else:
        main()
