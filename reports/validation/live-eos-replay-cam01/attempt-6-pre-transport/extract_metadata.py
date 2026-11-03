#!/usr/bin/env python3
"""Extract post-NvDCF metadata through the supported pyservicemaker Flow API.

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

SCHEMA_VERSION = "1.0"
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


class JsonlMetadataWriter(BatchMetadataOperator):
    def __init__(self, output_path: Path, camera_id: str, camera_epoch: str):
        super().__init__()
        self._output_path = output_path
        self._camera_id = camera_id
        self._camera_epoch = camera_epoch
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

    def handle_metadata(self, batch_meta: Any) -> None:
        if self._output is None:
            raise RuntimeError("JSONL output is not open")
        for frame_meta in batch_meta.frame_items:
            record = frame_record(frame_meta, self._camera_id, self._camera_epoch)
            self._output.write(
                json.dumps(record, allow_nan=False, separators=(",", ":")) + "\n"
            )
            # Preserve a usable prefix and exact coverage if a later stage fails.
            self._output.flush()
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
    args = parser.parse_args()
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
        "source": args.source,
        "source_sha256": sha256_file(args.source),
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
    }
    writer = JsonlMetadataWriter(output_path, args.camera_id, args.camera_epoch)
    eos_messages: list[EOSMessage] = []

    def on_message(message: Any) -> None:
        if isinstance(message, EOSMessage):
            eos_messages.append(message)

    try:
        writer.open()
        pipeline = Pipeline("service-maker-cam01-metadata")
        flow = (
            Flow(pipeline)
            .batch_capture(
                [args.source],
                width=args.width,
                height=args.height,
                batch_size=1,
                batched_push_timeout=40000,
                buffer_pool_size=4,
                live_source=False,
                file_loop=False,
                enable_padding=False,
                gpu_id=args.gpu_id,
            )
            .infer(args.infer_config)
            .track(
                tracker_width=args.tracker_width,
                tracker_height=args.tracker_height,
                ll_config_file=args.tracker_config,
                ll_lib_file=args.tracker_lib,
                gpu_id=args.gpu_id,
            )
        )
        flow.attach(what=Probe("jsonl-metadata", writer)).render(
            RenderMode.DISCARD, enable_osd=False, sync=False
        )(on_message)
        summary["pipeline_returned"] = True
    except Exception as exc:
        summary["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
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
        flush=True,
    )


if __name__ == "__main__":
    main()
