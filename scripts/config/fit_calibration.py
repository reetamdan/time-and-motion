#!/usr/bin/env python3
"""Fit an image -> floor-map homography from measured point correspondences and validate it.

Input JSON (measured by a person on site / on the floor plan; nothing is estimated here):

  {"camera_id": "cam-01", "floor_id": "floor-1", "revision": "2026-11-01-a", "image_size": [1920, 1080],
   "measured_by": "name", "max_validation_error": 3.0,
   "fit_points":        [[u, v, x, y], ...],   # >= 4, spread over the floor area people walk on
   "validation_points": [[u, v, x, y], ...]}   # >= 2 held out, NOT used for fitting

(u, v) are pipeline-frame pixels on a frame of exactly image_size; (x, y) are floor-map units of
floors.yaml. Writes configs/calibration/<camera>-<revision>.json (refuses to overwrite) and prints
the cameras.yaml fields to set. The loader refuses a VALID status whose validation fails.

Run: .venv/Scripts/python.exe scripts/config/fit_calibration.py measurements.json [--config-dir configs]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.app.analytics.calibration import Calibration  # noqa: E402


def fit_homography(points: list[list[float]]) -> np.ndarray:
    """Normalized DLT (Hartley) least-squares homography from >= 4 correspondences."""
    data = np.asarray(points, dtype=float)
    if data.shape[0] < 4 or data.shape[1] != 4:
        raise ValueError("need at least 4 [u, v, x, y] fit points")

    def normalizer(xy: np.ndarray) -> np.ndarray:
        centre = xy.mean(axis=0)
        scale = np.sqrt(2) / max(np.mean(np.linalg.norm(xy - centre, axis=1)), 1e-12)
        return np.array([[scale, 0, -scale * centre[0]], [0, scale, -scale * centre[1]], [0, 0, 1]])

    t_src, t_dst = normalizer(data[:, :2]), normalizer(data[:, 2:])
    src = (t_src @ np.c_[data[:, :2], np.ones(len(data))].T).T
    dst = (t_dst @ np.c_[data[:, 2:], np.ones(len(data))].T).T
    rows = []
    for (u, v, _), (x, y, _) in zip(src, dst):
        rows.append([-u, -v, -1, 0, 0, 0, x * u, x * v, x])
        rows.append([0, 0, 0, -u, -v, -1, y * u, y * v, y])
    _, singular, vt = np.linalg.svd(np.asarray(rows))
    if singular[-2] < 1e-9:
        raise ValueError("fit points are degenerate (collinear or repeated)")
    h = vt[-1].reshape(3, 3)
    h = np.linalg.inv(t_dst) @ h @ t_src
    return h / h[2, 2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("measurements", type=Path)
    parser.add_argument("--config-dir", type=Path, default=ROOT / "configs")
    args = parser.parse_args()
    data = json.loads(args.measurements.read_text(encoding="utf-8"))
    homography = fit_homography(data["fit_points"])
    calibration = Calibration(
        camera_id=data["camera_id"], revision=data["revision"], floor_id=data["floor_id"],
        image_size=tuple(data["image_size"]), homography=tuple(tuple(float(v) for v in row) for row in homography),  # type: ignore[arg-type]
        validation_points=tuple(tuple(p) for p in data["validation_points"]),  # type: ignore[misc]
        max_validation_error=float(data["max_validation_error"]), measured_by=data.get("measured_by"))
    valid, reason = calibration.validity()
    fit_errors = Calibration(calibration.camera_id, calibration.revision, calibration.floor_id, calibration.image_size,
                             calibration.homography, tuple(tuple(p) for p in data["fit_points"]), 1e9).validation_errors()  # type: ignore[misc]
    out = args.config_dir / "calibration" / f"{data['camera_id']}-{data['revision']}.json"
    if out.exists():
        raise SystemExit(f"{out} exists; calibrations are immutable - use a new revision")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "camera_id": calibration.camera_id, "revision": calibration.revision, "floor_id": calibration.floor_id,
        "image_size": list(calibration.image_size), "homography_image_to_floor": homography.tolist(),
        "validation_points": data["validation_points"], "max_validation_error": calibration.max_validation_error,
        "measured_by": calibration.measured_by, "fit_points": data["fit_points"],
        "fit_residual_max": max(fit_errors), "validation_result": reason,
    }, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"written": str(out), "valid": valid, "validation": reason, "fit_residual_max": max(fit_errors)}, indent=1))
    print(f"\nSet in cameras.yaml for {calibration.camera_id}:\n  calibration_ref: {out.stem}\n"
          f"  calibration_revision: {calibration.revision}\n  calibration_status: {'VALID' if valid else 'INVALID'}")
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
