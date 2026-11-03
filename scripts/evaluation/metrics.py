"""Detection and tracking metrics against human-reviewed ground truth (pure Python, no SciPy).

Boxes are [x, y, width, height] in source-frame pixels. Ground-truth boxes flagged ``ignore`` (too
small, unresolvable crowd, or inside an ignore region) neither count as misses nor make a matching
prediction a false positive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

Box = tuple[float, float, float, float]


def iou(a: Box, b: Box) -> float:
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    width = min(ax2, bx2) - max(a[0], b[0])
    height = min(ay2, by2) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    intersection = width * height
    union = a[2] * a[3] + b[2] * b[3] - intersection
    return intersection / union if union > 0 else 0.0


def point_in_polygon(x: float, y: float, polygon: list[list[float]]) -> bool:
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def in_ignore_region(box: Box, regions: list[list[list[float]]]) -> bool:
    """A box is ignored when its bottom-centre (foot point) lies inside an ignore polygon."""
    x, y = box[0] + box[2] / 2, box[1] + box[3]
    return any(point_in_polygon(x, y, region) for region in regions)


@dataclass(slots=True)
class FrameMatch:
    true_positives: int
    false_positives: int
    false_negatives: int
    pairs: list[tuple[int, int]]  # (gt index, prediction index)
    counted_predictions: int
    counted_ground_truth: int


def match_frame(gt: list[dict[str, Any]], predictions: list[dict[str, Any]], iou_threshold: float,
                regions: list[list[list[float]]] | None = None) -> FrameMatch:
    """COCO-style greedy matching: predictions by descending score, each to its best unmatched GT."""
    regions = regions or []
    order = sorted(range(len(predictions)), key=lambda i: -(predictions[i].get("score") or 0.0))
    matched_gt: set[int] = set()
    pairs: list[tuple[int, int]] = []
    false_positives = 0
    counted_predictions = 0
    for index in order:
        box = tuple(predictions[index]["bbox"])
        best, best_iou, best_ignored = None, iou_threshold, False
        for gi, item in enumerate(gt):
            if gi in matched_gt and not item.get("ignore"):
                continue
            overlap = iou(box, tuple(item["bbox"]))  # type: ignore[arg-type]
            # Prefer a real (non-ignored) match over an ignored one at equal quality.
            if overlap >= best_iou and (best is None or (best_ignored and not item.get("ignore")) or overlap > best_iou):
                best, best_iou, best_ignored = gi, overlap, bool(item.get("ignore"))
        if best is not None and not best_ignored:
            matched_gt.add(best)
            pairs.append((best, index))
            counted_predictions += 1
        elif best is not None or in_ignore_region(box, regions):  # type: ignore[arg-type]
            continue  # matched an ignored GT or lies in an ignore region: neither TP nor FP
        else:
            false_positives += 1
            counted_predictions += 1
    counted_gt = sum(1 for item in gt if not item.get("ignore"))
    return FrameMatch(len(pairs), false_positives, counted_gt - len(pairs), pairs, counted_predictions, counted_gt)


def detection_summary(frames: Iterable[tuple[list[dict], list[dict], list]], iou_threshold: float) -> dict[str, Any]:
    """Pooled detection metrics over reviewed frames (a reviewed frame with no people is a negative frame).

    Zero-denominator rules: precision is undefined (None) only when nothing was predicted; recall
    only when the reviewed frames contain no countable person; F1 = 2TP / (2TP + FP + FN), which is
    0 whenever there are errors but no true positive, and undefined only when TP = FP = FN = 0.
    """
    tp = fp = fn = 0
    count_errors: list[int] = []
    per_frame = []
    empty_frames = empty_frames_with_fp = empty_frame_fp = 0
    for gt, predictions, regions in frames:
        result = match_frame(gt, predictions, iou_threshold, regions)
        tp, fp, fn = tp + result.true_positives, fp + result.false_positives, fn + result.false_negatives
        error = result.counted_predictions - result.counted_ground_truth
        count_errors.append(error)
        if result.counted_ground_truth == 0:
            empty_frames += 1
            empty_frame_fp += result.false_positives
            empty_frames_with_fp += result.false_positives > 0
        per_frame.append({"tp": result.true_positives, "fp": result.false_positives, "fn": result.false_negatives,
                          "gt": result.counted_ground_truth, "predicted": result.counted_predictions, "count_error": error})
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    undefined = {name: reason for name, value, reason in (
        ("precision", precision, "no predictions on the scored frames"),
        ("recall", recall, "no countable ground-truth person on the scored frames"),
        ("f1", f1, "TP = FP = FN = 0 (nothing to score)")) if value is None}
    return {
        "iou_threshold": iou_threshold, "frames": len(per_frame), "true_positives": tp, "false_positives": fp,
        "false_negatives": fn, "precision": precision, "recall": recall, "f1": f1, "undefined": undefined,
        "reviewed_empty_frames": empty_frames, "false_positives_on_empty_frames": empty_frame_fp,
        "empty_frames_with_false_positives": empty_frames_with_fp,
        "count_mae": sum(abs(e) for e in count_errors) / len(count_errors) if count_errors else None,
        "count_mean_signed_error": sum(count_errors) / len(count_errors) if count_errors else None,
        "per_frame": per_frame,
    }


def tracking_identity_problems(sequence: list[tuple[list[dict], list[dict], list]]) -> list[str]:
    """Why a sequence cannot be scored for tracking (empty list when it can)."""
    problems = []
    missing_gt = sum(1 for gt, _, _ in sequence for item in gt if not item.get("ignore") and item.get("track_id") is None)
    if missing_gt:
        problems.append(f"{missing_gt} reviewed ground-truth box(es) have no track_id")
    predictions = [p for _, frame_predictions, _ in sequence for p in frame_predictions]
    if predictions and all(p.get("track_id") is None for p in predictions):
        problems.append("predictions carry no tracker ids (detector-only output)")
    for index, (gt, _, _) in enumerate(sequence):
        ids = [item["track_id"] for item in gt if item.get("track_id") is not None]
        if len(ids) != len(set(ids)):
            problems.append(f"frame {index} of the sequence repeats a ground-truth track_id")
    return problems


def hungarian(cost: list[list[float]]) -> list[int]:
    """Minimum-cost assignment for an n x m matrix (n <= m); returns the column for each row."""
    n, m = len(cost), len(cost[0]) if cost else 0
    if n == 0:
        return []
    if n > m:
        raise ValueError("hungarian() needs rows <= columns")
    inf = float("inf")
    u, v, p, way = [0.0] * (n + 1), [0.0] * (m + 1), [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv, used = [inf] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], inf, 0
            for j in range(1, m + 1):
                if not used[j]:
                    current = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if current < minv[j]:
                        minv[j], way[j] = current, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    assignment = [-1] * n
    for j in range(1, m + 1):
        if p[j]:
            assignment[p[j] - 1] = j - 1
    return assignment


def tracking_summary(sequence: list[tuple[list[dict], list[dict], list]], iou_threshold: float) -> dict[str, Any]:
    """CLEAR-MOT (MOTA, ID switches) and IDF1 over one annotated consecutive-frame sequence.

    GT items carry ``track_id``; predictions carry ``track_id`` from the tracker. Callers must check
    ``tracking_identity_problems`` first: this function raises rather than score invalid identities.
    """
    problems = tracking_identity_problems(sequence)
    if problems:
        raise ValueError("; ".join(problems))
    tp = fp = fn = switches = 0
    last_match: dict[Any, Any] = {}
    gt_frames: dict[Any, int] = {}
    pred_frames: dict[Any, int] = {}
    overlap: dict[tuple[Any, Any], int] = {}
    for gt, predictions, regions in sequence:
        result = match_frame(gt, predictions, iou_threshold, regions)
        tp, fp, fn = tp + result.true_positives, fp + result.false_positives, fn + result.false_negatives
        for item in gt:
            if not item.get("ignore"):
                gt_frames[item["track_id"]] = gt_frames.get(item["track_id"], 0) + 1
        for gi, pi in result.pairs:
            gid, pid = gt[gi]["track_id"], predictions[pi].get("track_id")
            if gid in last_match and last_match[gid] != pid:
                switches += 1
            last_match[gid] = pid
            overlap[(gid, pid)] = overlap.get((gid, pid), 0) + 1
        for pi, prediction in enumerate(predictions):
            if any(pi == p for _, p in result.pairs) or prediction.get("track_id") is None:
                if prediction.get("track_id") is not None:
                    pred_frames[prediction["track_id"]] = pred_frames.get(prediction["track_id"], 0) + 1
                continue
            box = tuple(prediction["bbox"])
            if not in_ignore_region(box, regions) and not any(  # type: ignore[arg-type]
                    item.get("ignore") and iou(box, tuple(item["bbox"])) >= iou_threshold for item in gt):  # type: ignore[arg-type]
                pred_frames[prediction["track_id"]] = pred_frames.get(prediction["track_id"], 0) + 1
    total_gt = sum(gt_frames.values())
    mota = 1 - (fn + fp + switches) / total_gt if total_gt else None
    # IDF1: optimal one-to-one track assignment maximizing identity-consistent matches.
    gids, pids = sorted(gt_frames, key=str), sorted(pred_frames, key=str)
    idtp = 0
    if gids and pids:
        size = max(len(gids), len(pids))
        cost = [[-(overlap.get((g, p), 0)) if r < len(gids) and c < len(pids) else 0
                 for c, p in enumerate(pids + [None] * (size - len(pids)))]
                for r, g in enumerate(gids + [None] * (size - len(gids)))]
        assignment = hungarian(cost)
        idtp = -sum(cost[r][c] for r, c in enumerate(assignment))
    total_pred = sum(pred_frames.values())
    idf1 = 2 * idtp / (total_gt + total_pred) if total_gt + total_pred else None
    return {"iou_threshold": iou_threshold, "frames": len(sequence), "gt_tracks": len(gids), "predicted_tracks": len(pids),
            "true_positives": tp, "false_positives": fp, "false_negatives": fn, "id_switches": switches,
            "mota": mota, "idf1": idf1, "idtp": idtp}
