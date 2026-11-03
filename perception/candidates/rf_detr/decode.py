"""Deterministic person-only RF-DETR raw-output decoding, without NMS."""
from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np

# RF-DETR uses sparse COCO category ids 0..90; slot 0 is "N/A" and person is slot 1
# (config.json id2label at the pinned revision), unlike RT-DETR where person is slot 0.
PERSON_LOGIT_SLOT = 1
APP_CLASS_ID = 0
APP_LABEL = "person"
NUM_QUERIES = 300
NUM_CLASSES = 91


@dataclass(frozen=True)
class DecodeGeometry:
    source_width: int
    source_height: int
    network_width: int = 512
    network_height: int = 512

    def __post_init__(self) -> None:
        if min(self.source_width, self.source_height, self.network_width, self.network_height) <= 0:
            raise ValueError("Decode dimensions must be positive")


def sigmoid(values: np.ndarray) -> np.ndarray:
    """Numerically stable sigmoid for float32/float64 arrays."""
    values = np.asarray(values)
    if values.dtype not in (np.float32, np.float64):
        raise TypeError("Logits must be float32 or float64")
    output = np.empty_like(values)
    positive = values >= 0
    output[positive] = 1.0 / (1.0 + np.exp(-values[positive]))
    exp_values = np.exp(values[~positive])
    output[~positive] = exp_values / (1.0 + exp_values)
    return output


def _single_batch(name: str, values: np.ndarray, trailing: int) -> np.ndarray:
    array = np.asarray(values)
    if array.ndim == 3 and array.shape[0] == 1:
        array = array[0]
    if array.shape != (NUM_QUERIES, trailing):
        raise ValueError(f"{name} must have shape [1,{NUM_QUERIES},{trailing}] or [{NUM_QUERIES},{trailing}], got {array.shape}")
    if array.dtype not in (np.float32, np.float64):
        raise TypeError(f"{name} must be float32 or float64")
    if not np.isfinite(array).all():
        raise ValueError(f"{name} contains non-finite values")
    return array


def decode_person(
    logits: np.ndarray,
    pred_boxes: np.ndarray,
    geometry: DecodeGeometry,
    threshold: float = 0.25,
    top_k: int = NUM_QUERIES,
) -> list[dict]:
    """Decode COCO slot 1 into app class 0, preserving direct-resize geometry.

    A query is kept when sigmoid(person logit) is strictly greater than the
    threshold, matching the HF post_process_object_detection comparison. Order
    is descending score with ascending query index as the deterministic tie
    break. Boxes are converted normalized cxcywh -> network xyxy, then scaled by
    independent x/y factors to undo direct resize. No NMS is performed.
    """
    if not isinstance(threshold, (float, int)) or isinstance(threshold, bool) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("threshold must be finite and in [0,1]")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= NUM_QUERIES:
        raise ValueError("top_k must be an integer in [1,300]")
    logits_array = _single_batch("logits", logits, NUM_CLASSES)
    boxes_array = _single_batch("pred_boxes", pred_boxes, 4)
    scores = sigmoid(logits_array[:, PERSON_LOGIT_SLOT])
    candidates = [(int(index), float(scores[index])) for index in range(NUM_QUERIES) if scores[index] > threshold]
    candidates.sort(key=lambda item: (-item[1], item[0]))
    detections = []
    sx = geometry.source_width / geometry.network_width
    sy = geometry.source_height / geometry.network_height
    for query_index, score in candidates[:top_k]:
        cx, cy, width, height = (float(value) for value in boxes_array[query_index])
        network_xyxy = np.array([
            (cx - width / 2) * geometry.network_width,
            (cy - height / 2) * geometry.network_height,
            (cx + width / 2) * geometry.network_width,
            (cy + height / 2) * geometry.network_height,
        ], dtype=np.float64)
        source_xyxy = network_xyxy * np.array([sx, sy, sx, sy], dtype=np.float64)
        source_xyxy[[0, 2]] = np.clip(source_xyxy[[0, 2]], 0, geometry.source_width)
        source_xyxy[[1, 3]] = np.clip(source_xyxy[[1, 3]], 0, geometry.source_height)
        x1, y1, x2, y2 = (float(value) for value in source_xyxy)
        if x2 <= x1 or y2 <= y1:
            continue
        detections.append({
            "query_index": query_index,
            "class_id": APP_CLASS_ID,
            "coco_class_id": PERSON_LOGIT_SLOT,
            "label": APP_LABEL,
            "confidence": score,
            "bbox_xyxy_pixels": [x1, y1, x2, y2],
            "bbox_xywh_pixels": {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1},
        })
    return detections
