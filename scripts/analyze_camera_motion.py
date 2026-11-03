#!/usr/bin/env python3
"""Measure global (camera) image motion in a clip with phase correlation on downscaled frames.

Used to decide whether a fixed image-space line is meaningful: with a moving camera, a
person can cross a fixed pixel line because the view moved, not because they did.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("demo/cam01.mp4"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scale", type=float, default=0.25)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit(f"Refusing to overwrite {args.out}")
    capture = cv2.VideoCapture(str(args.source))
    previous = None
    window = None
    per_frame = []
    index = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        gray = cv2.cvtColor(cv2.resize(frame, None, fx=args.scale, fy=args.scale, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.float32)
        if window is None:
            window = cv2.createHanningWindow(gray.shape[::-1], cv2.CV_32F)
        if previous is not None:
            (dx, dy), response = cv2.phaseCorrelate(previous, gray, window)
            per_frame.append({"frame": index, "dx_px_full_res": dx / args.scale, "dy_px_full_res": dy / args.scale, "response": response})
        previous = gray
        index += 1
    capture.release()
    dx = np.array([p["dx_px_full_res"] for p in per_frame])
    dy = np.array([p["dy_px_full_res"] for p in per_frame])
    response = np.array([p["response"] for p in per_frame])
    confident = response >= 0.1
    result = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": str(args.source), "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
        "frames_decoded": index, "method": f"cv2.phaseCorrelate on grayscale frames downscaled x{args.scale} with a Hanning window; shifts reported at full resolution",
        "opencv_version": cv2.__version__,
        "confident_pairs": int(confident.sum()), "pairs": len(per_frame),
        "cumulative_shift_px": {"dx": float(dx[confident].sum()), "dy": float(dy[confident].sum())},
        "per_frame_shift_abs_median_px": {"dx": float(np.median(np.abs(dx[confident]))), "dy": float(np.median(np.abs(dy[confident])))},
        "per_frame_shift_abs_p90_px": {"dx": float(np.quantile(np.abs(dx[confident]), 0.9)), "dy": float(np.quantile(np.abs(dy[confident]), 0.9))},
        "frames_with_shift_over_2px": int((np.hypot(dx, dy)[confident] > 2).sum()),
        "per_frame": per_frame,
        "caveat": "Phase correlation on a dense moving crowd mixes camera motion with crowd motion; a consistent large cumulative shift and low-texture background shift indicate camera motion. Visual inspection of sampled frames is recorded separately.",
    }
    args.out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "per_frame"}, indent=1))


if __name__ == "__main__":
    main()
