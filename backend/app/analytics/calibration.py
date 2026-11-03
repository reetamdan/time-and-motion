"""Camera calibration: image pixels -> floor-map coordinates, with explicit validity.

A calibration file (``configs/calibration/<ref>.json``) holds a planar homography from pipeline-frame
pixels (the frame size it was measured on) to one floor's map coordinates, its revision, and
held-out validation points. Pixel and floor coordinates are never mixed:

* a calibration is only applied to frames of exactly its ``image_size`` (a resolution, crop, or zoom
  change invalidates it at runtime);
* only the person's ground-contact point (bottom-centre of the box) is projected;
* a projected point outside the floor or behind the camera (w <= 0) is rejected, not clamped.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

Matrix = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]


class CalibrationError(ValueError):
    pass


def apply_homography(matrix: Matrix, x: float, y: float) -> tuple[float, float] | None:
    u = matrix[0][0] * x + matrix[0][1] * y + matrix[0][2]
    v = matrix[1][0] * x + matrix[1][1] * y + matrix[1][2]
    w = matrix[2][0] * x + matrix[2][1] * y + matrix[2][2]
    if not math.isfinite(w) or w <= 1e-12:
        return None
    return u / w, v / w


@dataclass(frozen=True, slots=True)
class Calibration:
    camera_id: str
    revision: str
    floor_id: str
    image_size: tuple[int, int]
    homography: Matrix
    validation_points: tuple[tuple[float, float, float, float], ...]  # (u, v, x, y)
    max_validation_error: float  # floor-map units
    measured_by: str | None = None

    def project(self, pixel_x: float, pixel_y: float) -> tuple[float, float] | None:
        return apply_homography(self.homography, pixel_x, pixel_y)

    def validation_errors(self) -> list[float]:
        errors = []
        for u, v, x, y in self.validation_points:
            point = self.project(u, v)
            errors.append(math.inf if point is None else math.hypot(point[0] - x, point[1] - y))
        return errors

    def validity(self) -> tuple[bool, str]:
        errors = self.validation_errors()
        if len(errors) < 2:
            return False, "calibration has fewer than 2 held-out validation points"
        worst = max(errors)
        if worst > self.max_validation_error:
            return False, f"validation error {worst:.2f} exceeds the allowed {self.max_validation_error:.2f} floor units"
        return True, f"{len(errors)} validation points, max error {worst:.2f} floor units"


def load_calibration(path: Path) -> Calibration:
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        matrix = tuple(tuple(float(value) for value in row) for row in data["homography_image_to_floor"])
        if len(matrix) != 3 or any(len(row) != 3 for row in matrix):
            raise CalibrationError("homography must be 3x3")
        if not all(math.isfinite(value) for row in matrix for value in row):
            raise CalibrationError("homography values must be finite")
        size = data["image_size"]
        calibration = Calibration(
            camera_id=str(data["camera_id"]), revision=str(data["revision"]), floor_id=str(data["floor_id"]),
            image_size=(int(size[0]), int(size[1])), homography=matrix,  # type: ignore[arg-type]
            validation_points=tuple(tuple(float(v) for v in point) for point in data.get("validation_points", [])),  # type: ignore[misc]
            max_validation_error=float(data["max_validation_error"]), measured_by=data.get("measured_by"),
        )
    except CalibrationError:
        raise
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        raise CalibrationError(f"cannot read calibration {path.name}: {exc}") from exc
    if min(calibration.image_size) <= 0:
        raise CalibrationError("image_size must be positive")
    if any(len(point) != 4 for point in calibration.validation_points):
        raise CalibrationError("validation points must be [u, v, x, y]")
    return calibration
