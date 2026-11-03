#!/usr/bin/env python3
"""Extract lossless known-positive real frames using the existing OpenCV install."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import cv2

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "model-evaluation" / "rt-detr" / "frames"
SOURCE = ROOT / "demo" / "cam01.mp4"
EXPECTED_SHA256 = "4201f7162eb0b9bcd3910f87b1a67a5bedac89c2b0a0bb4222e67d12f816b503"
FRAMES = (141, 283)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if digest(SOURCE) != EXPECTED_SHA256:
        raise SystemExit("Authorized demo source hash mismatch")
    OUT.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(SOURCE))
    records = []
    try:
        for index in FRAMES:
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if not ok or frame is None or frame.shape != (1080, 1920, 3):
                raise RuntimeError(f"Could not decode exact frame {index}")
            path = OUT / f"frame_{index:06d}.png"
            if path.exists():
                raise SystemExit(f"Refusing to overwrite evidence: {path}")
            if not cv2.imwrite(str(path), frame, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
                raise RuntimeError(f"PNG write failed: {path}")
            records.append({"frame_index": index, "path": str(path.relative_to(ROOT)), "sha256": digest(path), "decoded_bgr_shape": list(frame.shape), "pixel_sha256_bgr": hashlib.sha256(frame.tobytes()).hexdigest(), "known_positive_basis": "Visible people in authorized clip and existing independent TrafficCamNet smoke metadata; not ground-truth labels"})
    finally:
        capture.release()
    result = {"schema_version": 1, "source": "demo/cam01.mp4", "source_sha256": EXPECTED_SHA256, "opencv_version": cv2.__version__, "frames": records, "quality_scope": "Known-positive integration inputs only; no labels and no precision/recall claim"}
    (OUT / "frames.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
