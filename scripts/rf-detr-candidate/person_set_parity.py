#!/usr/bin/env python3
"""Order-independent strict person-set comparison between two decode files.

Uses the unchanged strict crosscheck rule (one-to-one, IoU >= 0.5, |score delta| <= 0.02).
Reported alongside, never instead of, raw-slot and query-order parity.
Usage: person_set_parity.py OUT.json LABEL=left.json:right.json [...]
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "rt-detr-candidate"))
from compare_pretracker import strict_match  # noqa: E402


def pairs(path):
    return [(d["bbox_xyxy_pixels"], float(d["confidence"])) for d in json.loads(Path(path).read_text(encoding="utf-8"))["detections"]]


def main():
    out = Path(sys.argv[1])
    if out.exists():
        raise SystemExit(f"Refusing to overwrite {out}")
    rows = {}
    for spec in sys.argv[2:]:
        label, files = spec.split("=", 1)
        left, right = files.rsplit("::", 1)
        m = strict_match(pairs(left), pairs(right))
        m.pop("unmatched_left"); m.pop("unmatched_right")
        m["result"] = "PASS" if m["matches"] == m["left_count"] == m["right_count"] else "FAIL"
        rows[label] = {"left": left, "right": right, **m}
    out.write_text(json.dumps({"schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                               "criteria": "strict one-to-one, IoU>=0.5, score delta<=0.02", "comparisons": rows}, indent=2) + "\n", encoding="utf-8")
    for k, v in rows.items():
        print(k, v["result"], f"{v['matches']}/{v['left_count']} vs {v['right_count']}", "max_dscore", v["max_score_abs_delta"], "max_box_px", v["max_bbox_abs_delta_px"])


if __name__ == "__main__":
    main()
