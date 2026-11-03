"""Check paced replay against real host (Windows) time, not producer FPS alone.

Every `*.release.jsonl` record carries `pts_ns` (source time), `emitted_unix_ns` (producer wall
clock, inside WSL) and `read_unix_ns` (backend host wall clock when the record was read). For a
correctly paced producer, host read time advances one second per source second. Per file this
reports:

* rate_host_per_source: host seconds per source second (least-squares slope; 1.000 is real time);
* drift_s: how far delivery runs ahead (+) of / behind (-) the source timeline anchored at the
  first frame, in host time (max ahead, max behind, final);
* rate_guest_wall_per_source: the same slope from emitted_unix_ns (WSL wall clock).

Capacity stages A-C have producer records only (`bench-NN.jsonl`). Inside WSL the guest wall
clock is a sawtooth around host time (runs 2-6 % fast, stepped back ~1.1-1.5 s every ~30 s by
time sync; measured 2026-10-28), so it is used only at the first frame after each sync step,
where it equals host time plus a constant; rate and lead are judged on those points. With fewer
than MIN_SYNC_POINTS steps (e.g. native Linux) the WSL wall clock is corrected linearly by the
step's measured offset change (summary.json `clock_offset`) instead.

PASS for a file: delivery never more than 1.0 s ahead of the anchored source timeline (200 ms
lead plus pipe/queue jitter) and, for files covering at least 120 s of source, |rate - 1| <= 0.002
(0.2 %, ~1.2 s per 10 min). The rate is not judged on shorter files: frames buffered in the
wsl.exe stdout pipe and later drained into the backend queue shift host read time by a few
hundred ms once (seen 2026-10-28: -0.36 s transit with +9 queued frames), which is ~1.5 % of a
24 s clip but ~0.06 % of a 10-min window. Under 20 s of source: TOO_SHORT, not judged.

Bounded memory: each line is scanned for the three integer fields only (no full JSON parse), and
only three float arrays per file are kept.

Run: .venv/Scripts/python.exe scripts/benchmark/check_host_pacing.py <dir-or-file>... [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from array import array
from pathlib import Path

RATE_TOLERANCE = 0.002
MAX_AHEAD_S = 1.0
MIN_SOURCE_S = 20.0
MIN_RATE_SOURCE_S = 120.0
SYNC_STEP_S = 0.5
MIN_SYNC_POINTS = 4
FIELDS = {name: re.compile(rf'"{name}":\s*(\d+)') for name in ("pts_ns", "emitted_unix_ns", "read_unix_ns")}


def slope(xs: array, ys: array) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx


def step_offset_change(path: Path) -> tuple[float, float] | None:
    """(change in wsl_minus_host, combined uncertainty), in seconds, from a capacity step's summary.json."""
    summary = path.parent / "summary.json"
    if not summary.is_file():
        return None
    offset = json.loads(summary.read_text(encoding="utf-8")).get("clock_offset") or {}
    before, after = offset.get("before") or {}, offset.get("after") or {}
    if before.get("wsl_minus_host_ns") is None or after.get("wsl_minus_host_ns") is None:
        return None
    return ((after["wsl_minus_host_ns"] - before["wsl_minus_host_ns"]) / 1e9,
            ((before.get("uncertainty_ns") or 0) + (after.get("uncertainty_ns") or 0)) / 1e9)


def sync_points(pts: array, wall: array) -> list[int]:
    """Indexes of the first frame after each guest time-sync step (wall - source time drops >= SYNC_STEP_S)."""
    return [i for i in range(1, len(pts)) if (wall[i] - pts[i]) - (wall[i - 1] - pts[i - 1]) <= -SYNC_STEP_S]


def read_series(path: Path) -> dict[str, array]:
    series = {name: array("q") for name in FIELDS}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            values = {name: pattern.search(line) for name, pattern in FIELDS.items()}
            if not values["pts_ns"]:
                continue
            for name, match in values.items():
                series[name].append(int(match.group(1)) if match else 0)
    return series


def check_file(path: Path) -> dict:
    series = read_series(path)
    key = "read_unix_ns" if any(series["read_unix_ns"]) else "emitted_unix_ns"
    basis = ("backend read time (host wall clock)" if key == "read_unix_ns" else
             "producer emit time (WSL wall clock), corrected by the step's measured offset change")
    index = [i for i in range(len(series["pts_ns"])) if series[key][i]]
    if len(index) < 2:
        return {"file": str(path), "frames": len(index), "verdict": "NO_DATA"}
    p0, h0 = series["pts_ns"][index[0]], series[key][index[0]]
    pts = array("d", ((series["pts_ns"][i] - p0) / 1e9 for i in index))
    host = array("d", ((series[key][i] - h0) / 1e9 for i in index))
    result: dict = {"file": str(path), "basis": basis, "frames": len(index)}
    if key == "emitted_unix_ns":
        result["rate_guest_wall_per_source"] = round(slope(pts, host), 5)
        syncs = sync_points(pts, host)
        if len(syncs) >= MIN_SYNC_POINTS:
            # The guest wall clock equals host time (+ a constant) just after each time-sync step: judge on those frames.
            pts = array("d", (pts[i] - pts[syncs[0]] for i in syncs))
            host = array("d", (host[i] - host[syncs[0]] for i in syncs))
            result["basis"] = "producer emit time (WSL wall clock) at the first frame after each guest time-sync step"
            result["sync_points"] = len(syncs)
        else:
            change = step_offset_change(path)
            if change is None:
                return {**result, "verdict": "NO_HOST_CLOCK"}
            scale = (host[-1] - change[0]) / host[-1]
            host = array("d", (h * scale for h in host))
            result["clock_correction"] = {"offset_change_s": round(change[0], 3), "offset_uncertainty_s": round(change[1], 3)}
    elif any(series["emitted_unix_ns"][i] for i in index):
        e0 = series["emitted_unix_ns"][index[0]]
        result["rate_guest_wall_per_source"] = round(slope(pts, array("d", ((series["emitted_unix_ns"][i] - e0) / 1e9 for i in index))), 5)
    drift = array("d", (p - h for p, h in zip(pts, host)))
    result.update({
        "source_s": round(pts[-1], 3), "host_s": round(host[-1], 3),
        "rate_host_per_source": round(slope(pts, host), 5),
        "drift_s": {"max_ahead": round(max(drift), 3), "max_behind": round(min(drift), 3), "final": round(drift[-1], 3)},
    })
    if pts[-1] < MIN_SOURCE_S:
        result["verdict"] = "TOO_SHORT"
    else:
        rate_judged = pts[-1] >= MIN_RATE_SOURCE_S
        result["rate_judged"] = rate_judged
        ok = max(drift) <= MAX_AHEAD_S and (not rate_judged or abs(result["rate_host_per_source"] - 1) <= RATE_TOLERANCE)
        result["verdict"] = "PASS" if ok else "FAIL"
    return result


def collect(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if not path.is_dir():
            files.append(path)
            continue
        for directory in sorted({p.parent for p in path.rglob("*.jsonl")}):
            release = sorted(directory.glob("*.release.jsonl"))
            files.extend(release or [p for p in sorted(directory.glob("*.jsonl"))
                                     if not p.name.startswith(("resources", "memory"))])
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    results = [check_file(f) for f in collect(args.paths)]
    judged = [r for r in results if r["verdict"] in ("PASS", "FAIL")]
    summary = {"tolerance": {"rate": RATE_TOLERANCE, "max_ahead_s": MAX_AHEAD_S, "min_source_s": MIN_SOURCE_S,
                             "min_rate_source_s": MIN_RATE_SOURCE_S},
               "files": results,
               "verdict": "NO_DATA" if not judged else ("PASS" if all(r["verdict"] == "PASS" for r in judged) else "FAIL")}
    text = json.dumps(summary, indent=1)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes((text + "\n").encode("utf-8"))
    print(text)
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
