"""Bounded presentation of persisted per-camera count history (source-time buckets).

Downsampling merges consecutive buckets of one source segment into bins; it never merges across a
segment boundary and never fills missing buckets. A bin reports how many buckets it covers
(``bucket_span``) and how many actually had frames (``buckets_present``), so clients can show
partial bins and draw gaps where bucket indices jump by more than one span.
"""

from __future__ import annotations

import math
from typing import Any


def downsample(samples: list[dict[str, Any]], max_points: int | None) -> tuple[list[dict[str, Any]], int]:
    """Return (points, factor). factor 1 = samples unchanged (each gets bucket_span 1)."""
    if not samples:
        return [], 1
    if max_points is None or len(samples) <= max_points:
        return [{**sample, "bucket_span": 1, "buckets_present": 1} for sample in samples], 1
    factor = math.ceil(len(samples) / max_points)
    # Bins are aligned to bucket_index so the same bucket always lands in the same bin across polls.
    bins: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for sample in samples:
        bins.setdefault((sample["source_segment"], sample["bucket_index"] // factor), []).append(sample)
    points = []
    for (segment, index), members in bins.items():
        frames = sum(member["frames"] for member in members)
        mean = sum(member["visible_mean"] * member["frames"] for member in members) / frames if frames else None
        first, last = members[0], members[-1]
        points.append({
            **first,
            "bucket_index": index * factor,
            "source_time_s": first["source_time_s"],
            "frame_number": last["frame_number"],
            "visible_count": last["visible_count"],
            "visible_mean": mean,
            "visible_max": max(member["visible_max"] for member in members),
            "frames": frames,
            "bucket_span": factor,
            "buckets_present": len(members),
        })
    return points, factor
