#!/usr/bin/env python3
"""Extract post-NvDCF metadata through the supported pyservicemaker Flow API (v2).

v2 is the versioned successor of extract_metadata.py (v1, sha256 6093b5d5..., kept unchanged as
the frozen historical baseline). Differences, and nothing else:

* ``--pace source`` (paced replay only): before a frame is written, the producer waits until
  ``wall_start + (pts - pts_start) - pace_lead`` so it never runs more than ``--pace-lead-ms``
  ahead of the source timeline. Blocking in the metadata probe stalls the streaming thread, so
  decode/inference upstream wait too (backpressure at the source, not a growing pipe buffer).
  ``--pace none`` (default) behaves exactly like v1: as fast as the pipeline runs.
* Schema 1.1 = schema 1.0 + ``emitted_unix_ns``: the producer's wall clock when the record was
  written to stdout, so end-to-end lead and transit can be measured on the host.
* run-summary.json records the pacing mode, lead, total producer wait, and frames emitted late.
* ``--stage`` selects the PRD §19.1 workload stage for benchmarks: ``decode`` (A: decode + mux
  only), ``detect`` (B: + nvinfer, no tracker; objects carry no track_id), ``track`` (C, default:
  + NvDCF, the application pipeline). Only ``track`` is used by the application.
* Live ``rtsp://``/``rtsps://`` sources: batch_capture runs with live_source=True, the summary
  records the URL without credentials/query and no file hash, error text is scrubbed of the URL,
  and ``--pace source`` is refused (a live source is already real time).
* ``--loop`` (file sources only): DeepStream ``file_loop`` replays the file continuously, for
  demo replay that never reaches end of stream. Measured on DeepStream 9.2: buffer PTS and
  frame_number keep increasing across each wrap (one ~0.1 s PTS step at the join), so the backend
  sees one continuous source segment; frame ``n`` is file frame ``n mod frames_in_file``. Without
  ``--loop`` (default) the behaviour is unchanged.
* ``--output-max-mib N`` (0 = unlimited, the default): once the JSONL file reaches N MiB it is
  renamed to ``<output>.prev`` (replacing an older one) and a new file is started, so a long or
  looping run keeps at most about 2 N MiB on disk. The live stdout transport is unaffected; the
  summary records the rotations.

Output is JSON Lines: exactly one object per decoded frame, including frames
whose ``objects`` list is empty. There are no per-frame KITTI files and no
past-frame records are synthesized.

Schema 1.0 contract:
* ``bbox`` and ``tracker_bbox`` are ``{x, y, width, height}`` (xywh), never
  xyxy. Values are unclipped pixels in the post-streammux pipeline frame.
  ``source_width``/``source_height`` describe the original source and
  ``frame_width``/``frame_height`` describe that pipeline coordinate space.
* ``confidence`` is detector confidence. ``tracker_confidence`` is preserved
  independently and is not assumed to be a normalized probability.
* ``track_id`` is a decimal string, or null only for DeepStream's UNTRACKED
  uint64 sentinel. IDs are local to ``camera_id`` + ``camera_epoch``.
* When present, ``pts_ns`` is the real FrameMetadata.buffer_pts value and
  ``timestamp`` is that source-timeline PTS in seconds. It is not wall-clock
  time, host monotonic time, or an NTP timestamp.
* The JSONL file contains frames only. Clean EOS and pipeline completion are
  recorded separately in run-summary.json; EOF alone does not prove completeness.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import traceback
from typing import Any
from urllib.parse import urlsplit

# PyYAML is installed into the mounted project, not into the base image.
DEPS_DIR = Path(__file__).resolve().parent / ".deps"
sys.path.insert(0, str(DEPS_DIR))

import yaml  # noqa: E402
import pyservicemaker  # noqa: E402
from pyservicemaker import (  # noqa: E402
    BatchMetadataOperator,
    EOSMessage,
    Flow,
    Pipeline,
    Probe,
    RenderMode,
)

SCHEMA_VERSION = "1.1"
FRAME_PREFIX = "AEGIS_FRAME_V1:"
UNTRACKED_OBJECT_ID = 0xFFFFFFFFFFFFFFFF
GST_CLOCK_TIME_NONE = 0xFFFFFFFFFFFFFFFF

DEFAULT_SOURCE = "/workspace/demo/cam01.mp4"
DEFAULT_INFER_CONFIG = "/workspace/perception/deepstream/cam01_trafficcamnet_smoke_infer.txt"
DEFAULT_TRACKER_CONFIG = "/workspace/perception/deepstream/reference/config_tracker_NvDCF_perf.yml"
DEFAULT_TRACKER_LIB = "/opt/nvidia/deepstream/deepstream/lib/libnvds_nvmultiobjecttracker.so"
DEFAULT_OUTPUT = "/workspace/reports/validation/service-maker-cam01/frames.jsonl"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_live(source: str) -> bool:
    return source.lower().startswith(("rtsp://", "rtsps://"))


def redact_source(source: str) -> str:
    """rtsp://user:pass@host:port/path?query -> rtsp://host:port/path (never logs credentials or query)."""
    if not is_live(source):
        return source
    parsed = urlsplit(source)
    host = parsed.hostname or "source"
    return f"{parsed.scheme}://{host}{f':{parsed.port}' if parsed.port else ''}{parsed.path}"


def scrub(text: str, source: str) -> str:
    """Remove a live URL (and its userinfo) from free text such as GStreamer error messages."""
    if not is_live(source):
        return text
    text = text.replace(source, redact_source(source))
    userinfo = urlsplit(source).netloc.rpartition("@")[0]
    return text.replace(userinfo, "<redacted>") if userinfo else text


def finite_float(value: Any, field: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"Non-finite {field}: {result!r}")
    return result


def bbox_xywh(left: Any, top: Any, width: Any, height: Any) -> dict[str, float]:
    """Return an unmodified xywh box; clipping would hide tracker behavior."""
    return {
        "x": finite_float(left, "bbox.x"),
        "y": finite_float(top, "bbox.y"),
        "width": finite_float(width, "bbox.width"),
        "height": finite_float(height, "bbox.height"),
    }


def object_record(object_meta: Any) -> dict[str, Any]:
    rect = object_meta.rect_params
    raw_track_id = int(object_meta.object_id)
    record: dict[str, Any] = {
        "class_id": int(object_meta.class_id),
        "label": str(object_meta.label or ""),
        "confidence": finite_float(object_meta.confidence, "confidence"),
        "tracker_confidence": finite_float(
            object_meta.tracker_confidence, "tracker_confidence"
        ),
        "track_id": None if raw_track_id == UNTRACKED_OBJECT_ID else str(raw_track_id),
        "bbox": bbox_xywh(rect.left, rect.top, rect.width, rect.height),
    }
    if raw_track_id != UNTRACKED_OBJECT_ID:
        tracker_box = object_meta.nv_bbox_info
        record["tracker_bbox"] = bbox_xywh(
            tracker_box["left"],
            tracker_box["top"],
            tracker_box["width"],
            tracker_box["height"],
        )
    return record


def frame_record(frame_meta: Any, camera_id: str, camera_epoch: str) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "camera_id": camera_id,
        "camera_epoch": camera_epoch,
        "frame_number": int(frame_meta.frame_number),
        "source_id": int(frame_meta.source_id),
        "pad_index": int(frame_meta.pad_index),
        "source_width": int(frame_meta.source_width),
        "source_height": int(frame_meta.source_height),
        "frame_width": int(frame_meta.pipeline_width),
        "frame_height": int(frame_meta.pipeline_height),
        "bbox_format": "xywh",
        "bbox_coordinate_space": "pipeline_frame_pixels",
        "objects": [object_record(obj) for obj in frame_meta.object_items],
    }
    pts_ns = int(frame_meta.buffer_pts)
    if pts_ns != GST_CLOCK_TIME_NONE:
        record["pts_ns"] = pts_ns
        record["timestamp"] = pts_ns / 1_000_000_000.0
    return record


class GuestHostClock:
    """Host time as seen from inside a WSL guest, for pacing.

    Measured 2026-10-28 against the Windows host: the guest's CLOCK_MONOTONIC runs fast (2-6 %,
    varying with load), and the guest wall clock runs with it and is stepped back to host time
    every ~30-32 s by time sync (~1.1-1.5 s per step). Neither is host time at every instant: the
    wall clock is a sawtooth around it, right only just after each sync step.

    This clock detects sync steps as jumps of (wall - monotonic) of at least ``SYNC_STEP_S``, keeps
    the wall time just after the last ``WINDOW`` steps, and returns the last synced wall time plus
    monotonic time since it, divided by the monotonic rate measured between the oldest and newest
    kept steps. Until two steps have been seen (or with no steps at all, e.g. native Linux with a
    slewed clock) it returns the wall clock unchanged. A jump of ``RESET_STEP_S`` or more is a real
    clock reset, not a periodic sync: the kept steps are dropped.
    """

    SYNC_STEP_S = 0.2
    RESET_STEP_S = 5.0
    WINDOW = 5
    RATE_BOUNDS = (0.8, 1.2)

    def __init__(self, wall=time.time, mono=time.monotonic):
        self._wall, self._mono = wall, mono
        self._last_offset: float | None = None
        self._syncs: list[tuple[float, float]] = []  # (monotonic, wall) just after each sync step
        self.sync_steps = 0

    def rate(self) -> float | None:
        """Monotonic seconds per host second, or None while unknown."""
        if len(self._syncs) < 2:
            return None
        (m0, w0), (m1, w1) = self._syncs[0], self._syncs[-1]
        if w1 <= w0:
            return None
        rate = (m1 - m0) / (w1 - w0)
        return rate if self.RATE_BOUNDS[0] < rate < self.RATE_BOUNDS[1] else None

    def __call__(self) -> float:
        mono, wall = self._mono(), self._wall()
        offset = wall - mono
        if self._last_offset is not None:
            jump = abs(offset - self._last_offset)
            if jump >= self.RESET_STEP_S:
                self._syncs.clear()
            elif jump >= self.SYNC_STEP_S:
                self._syncs.append((mono, wall))
                del self._syncs[:-self.WINDOW]
                self.sync_steps += 1
        self._last_offset = offset
        rate = self.rate()
        if rate is None:
            return wall
        m1, w1 = self._syncs[-1]
        return w1 + (mono - m1) / rate


class SourcePacer:
    """Holds the producer to the source timeline: at most ``lead_s`` ahead of host time.

    The default clock is ``GuestHostClock`` (see there for the WSL clock measurements). Pacing on
    the raw guest monotonic clock let producers run ~3.5 % fast (extractor e846ce3e); pacing on
    the raw guest wall clock and re-anchoring on its sync steps let them run ~1-4 % fast
    (8a2340e2, 8dd882fd). Waits are re-evaluated against fresh clock reads in short slices
    (``MAX_SLEEP_S``). A clock step back of up to ``MAX_CLOCK_STEP_S`` is waited out, never
    re-anchored, because that is exactly how far the producer is ahead of host time. A longer
    implied wait (a real clock reset, or a forward PTS jump) re-anchors once it persists over
    ``STEP_CONFIRM_READS`` reads ``STEP_CONFIRM_INTERVAL_S`` apart, instead of stalling.
    """

    MAX_CLOCK_STEP_S = 3.0
    MAX_SLEEP_S = 0.05
    DUE_TOLERANCE_S = 0.001
    STEP_CONFIRM_READS = 3
    STEP_CONFIRM_INTERVAL_S = 0.1

    def __init__(self, mode: str, lead_s: float, clock=None, sleep=time.sleep):
        self.mode = mode
        self.lead_s = lead_s
        self.clock = clock if clock is not None else GuestHostClock()
        self._sleep = sleep
        self._anchor: tuple[int, float] | None = None  # (pts_ns, clock seconds)
        self.total_wait_s = 0.0
        self.frames_late = 0
        self.reanchors = 0
        self.implausible_reads = 0  # clock reads implying a wait > lead + MAX_CLOCK_STEP_S

    def wait(self, pts_ns: int | None) -> None:
        if self.mode != "source" or pts_ns is None:
            return
        now = self.clock()
        if self._anchor is None or pts_ns < self._anchor[0]:
            self._anchor = (pts_ns, now)  # first frame, or a backwards jump: re-anchor
            return
        due = self._anchor[1] + (pts_ns - self._anchor[0]) / 1e9 - self.lead_s
        implausible = 0
        while True:
            delay = due - now
            if delay <= self.DUE_TOLERANCE_S:  # a guest sleep covers ~1/rate of a host-time wait: stop at 1 ms, never spin
                if delay < -1.0:
                    self.frames_late += 1  # the pipeline itself is slower than real time here
                return
            if delay > self.lead_s + self.MAX_CLOCK_STEP_S:
                implausible += 1
                self.implausible_reads += 1
                if implausible >= self.STEP_CONFIRM_READS:
                    self._anchor = (pts_ns, now)  # persisted: a real clock reset, or a PTS jump forward
                    self.reanchors += 1
                    return
                step = self.STEP_CONFIRM_INTERVAL_S
            else:
                step = min(delay, self.MAX_SLEEP_S)
            self._sleep(step)
            self.total_wait_s += step
            now = self.clock()


class JsonlMetadataWriter(BatchMetadataOperator):
    def __init__(self, output_path: Path, camera_id: str, camera_epoch: str, pacer: SourcePacer,
                 max_bytes: int = 0):
        super().__init__()
        self._output_path = output_path
        self._max_bytes = max_bytes
        self._written_bytes = 0
        self.rotations = 0
        self._camera_id = camera_id
        self._camera_epoch = camera_epoch
        self._pacer = pacer
        self._output = None
        self.frames_written = 0
        self.objects_written = 0
        self.first_frame_number: int | None = None
        self.last_frame_number: int | None = None

    def open(self) -> None:
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        # Truncate on every run so a partial rerun cannot be mistaken for stale success.
        self._output = self._output_path.open("w", encoding="utf-8", newline="\n")

    def close(self) -> None:
        if self._output is not None:
            self._output.close()
            self._output = None

    def _rotate(self) -> None:
        """Keep the newest frames bounded on disk: current file -> <output>.prev, then start a new file."""
        self.close()
        self._output_path.replace(self._output_path.with_name(self._output_path.name + ".prev"))
        self._output = self._output_path.open("w", encoding="utf-8", newline="\n")
        self._written_bytes = 0
        self.rotations += 1

    def handle_metadata(self, batch_meta: Any) -> None:
        if self._output is None:
            raise RuntimeError("JSONL output is not open")
        for frame_meta in batch_meta.frame_items:
            record = frame_record(frame_meta, self._camera_id, self._camera_epoch)
            self._pacer.wait(record.get("pts_ns"))
            record["emitted_unix_ns"] = time.time_ns()
            payload = json.dumps(record, allow_nan=False, separators=(",", ":"))
            if self._max_bytes and self._written_bytes >= self._max_bytes:
                self._rotate()
            self._output.write(payload + "\n")
            self._written_bytes += len(payload) + 1
            # Preserve a usable prefix and exact coverage if a later stage fails.
            self._output.flush()
            # The same real frame is also the live transport; never synthesize past frames.
            sys.stdout.write(FRAME_PREFIX + payload + "\n")
            sys.stdout.flush()
            frame_number = record["frame_number"]
            if self.first_frame_number is None:
                self.first_frame_number = frame_number
            self.last_frame_number = frame_number
            self.frames_written += 1
            self.objects_written += len(record["objects"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--infer-config", default=DEFAULT_INFER_CONFIG)
    parser.add_argument("--tracker-config", default=DEFAULT_TRACKER_CONFIG)
    parser.add_argument("--tracker-lib", default=DEFAULT_TRACKER_LIB)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--run-summary",
        help="Defaults to run-summary.json beside --output",
    )
    parser.add_argument("--camera-id", default="cam-01")
    parser.add_argument("--camera-epoch", required=True)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--tracker-width", type=int, default=960)
    parser.add_argument("--tracker-height", type=int, default=544)
    parser.add_argument("--gpu-id", type=int, default=0)
    parser.add_argument("--pace", choices=("none", "source"), default="none",
                        help="source: hold the producer to the source timeline (paced replay)")
    parser.add_argument("--pace-lead-ms", type=float, default=200.0,
                        help="maximum producer lead over the source timeline when --pace source")
    parser.add_argument("--stage", choices=("decode", "detect", "track"), default="track",
                        help="benchmark workload stage (PRD 19.1 A/B/C); the application always uses track")
    parser.add_argument("--output-max-mib", type=float, default=0.0,
                        help="rotate the JSONL output to <output>.prev at this size (0 = unlimited)")
    parser.add_argument("--loop", action="store_true",
                        help="replay a file source continuously (DeepStream file_loop); never ends on its own")
    args = parser.parse_args()
    if args.pace_lead_ms < 0:
        parser.error("--pace-lead-ms must be >= 0")
    if args.output_max_mib < 0:
        parser.error("--output-max-mib must be >= 0")
    if is_live(args.source) and args.pace != "none":
        parser.error("--pace source applies to file replay only; a live source is already real time")
    if is_live(args.source) and args.loop:
        parser.error("--loop applies to file replay only")
    if not args.camera_id.strip() or not args.camera_epoch.strip():
        parser.error("camera-id and camera-epoch must be non-empty")
    if min(args.width, args.height, args.tracker_width, args.tracker_height) <= 0:
        parser.error("frame and tracker dimensions must be positive")
    return args


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_path = Path(args.output)
    summary_path = (
        Path(args.run_summary)
        if args.run_summary
        else output_path.with_name("run-summary.json")
    )
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    started_monotonic = time.monotonic()
    summary: dict[str, Any] = {
        "schema_version": 1,
        "started_at_utc": utc_now(),
        # Live URLs may carry credentials: only scheme/host/port/path are recorded; files are hashed.
        "source": redact_source(args.source),
        "source_sha256": None if is_live(args.source) else sha256_file(args.source),
        "live_source": is_live(args.source),
        "camera_id": args.camera_id,
        "camera_epoch": args.camera_epoch,
        "output": str(output_path),
        "infer_config": args.infer_config,
        "tracker_config": args.tracker_config,
        "tracker_lib": args.tracker_lib,
        "render_mode": "DISCARD",
        "pyservicemaker_version": pyservicemaker.__version__,
        "pyyaml_version": yaml.__version__,
        "pipeline_returned": False,
        "clean_eos": False,
        "eos_message_count": 0,
        "error": None,
        "extractor_version": 2,
        "schema_version": SCHEMA_VERSION,
        "pace": args.pace,
        "pace_lead_ms": args.pace_lead_ms if args.pace == "source" else None,
        "stage": args.stage,
        "loop": bool(args.loop),
        "output_max_mib": args.output_max_mib or None,
    }
    pacer = SourcePacer(args.pace, args.pace_lead_ms / 1000.0)
    writer = JsonlMetadataWriter(output_path, args.camera_id, args.camera_epoch, pacer,
                                 int(args.output_max_mib * 1024 * 1024))
    eos_messages: list[EOSMessage] = []

    def on_message(message: Any) -> None:
        if isinstance(message, EOSMessage):
            eos_messages.append(message)

    try:
        writer.open()
        pipeline = Pipeline("service-maker-cam01-metadata")
        flow = Flow(pipeline).batch_capture(
            [args.source],
            width=args.width,
            height=args.height,
            batch_size=1,
            batched_push_timeout=40000,
            buffer_pool_size=4,
            live_source=is_live(args.source),
            file_loop=bool(args.loop),
            enable_padding=False,
            gpu_id=args.gpu_id,
        )
        if args.stage in ("detect", "track"):
            flow = flow.infer(args.infer_config)
        if args.stage == "track":
            flow = flow.track(
                tracker_width=args.tracker_width,
                tracker_height=args.tracker_height,
                ll_config_file=args.tracker_config,
                ll_lib_file=args.tracker_lib,
                gpu_id=args.gpu_id,
            )
        flow.attach(what=Probe("jsonl-metadata", writer)).render(
            RenderMode.DISCARD, enable_osd=False, sync=False
        )(on_message)
        summary["pipeline_returned"] = True
    except Exception as exc:
        summary["error"] = {
            "type": type(exc).__name__,
            "message": scrub(str(exc), args.source),
            "traceback": scrub(traceback.format_exc(), args.source),
        }
        if is_live(args.source):  # the interpreter's own traceback would print the raw URL
            print(summary["error"]["traceback"], file=sys.stderr, flush=True)
            raise SystemExit(1) from None
        raise
    finally:
        writer.close()
        summary.update(
            {
                "ended_at_utc": utc_now(),
                "elapsed_seconds": time.monotonic() - started_monotonic,
                "frames_written": writer.frames_written,
                "objects_written": writer.objects_written,
                "first_frame_number": writer.first_frame_number,
                "last_frame_number": writer.last_frame_number,
                "eos_message_count": len(eos_messages),
                "pace_total_wait_seconds": round(pacer.total_wait_s, 3),
                "pace_frames_late_over_1s": pacer.frames_late,
                "pace_clock": "host estimate (guest wall at sync steps + monotonic rate)" if args.pace == "source" else None,
                "pace_clock_sync_steps": getattr(pacer.clock, "sync_steps", None) if args.pace == "source" else None,
                "pace_clock_rate": pacer.clock.rate() if args.pace == "source" else None,
                "pace_reanchors": pacer.reanchors,
                "output_rotations": writer.rotations,
                "pace_implausible_clock_reads": pacer.implausible_reads,
                "clean_eos": len(eos_messages) > 0
                and summary["pipeline_returned"]
                and summary["error"] is None,
            }
        )
        summary_path.write_text(
            json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
    return summary


def main() -> None:
    summary = run(parse_args())
    print(
        "Service Maker extraction complete: "
        f"frames={summary['frames_written']} objects={summary['objects_written']} "
        f"clean_eos={summary['clean_eos']} output={summary['output']}",
        file=sys.stderr,
        flush=True,
    )


if __name__ == "__main__":
    main()
