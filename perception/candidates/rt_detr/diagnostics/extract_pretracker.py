#!/usr/bin/env python3
"""Diagnostic-only RT-DETR capture of pre-tracker nvinfer output.

This is not the production extractor (perception/deepstream/service_maker/
extract_metadata.py is pinned by hash and unchanged). It runs the same
batch_capture -> nvinfer flow and records, for every frame:

* pre-tracker detector objects (probe directly on the nvinfer output, before
  any tracker), with unmodified rect_params and detector confidence;
* optionally post-NvDCF objects from a second probe after the tracker;
* for selected frames, the raw nvinfer output tensors attached through
  output-tensor-meta=1, saved as .npy.

The nvinfer input tensor is captured by nvinfer itself (dump-input-tensor=1 in
the diagnostic config), which appends one CHW float32 tensor per batch to
ip_tensor_dump.bin in the process working directory.
"""

from __future__ import annotations

import argparse
import ctypes
import glob
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import traceback
from typing import Any

DEPS_DIR = Path("/workspace/perception/deepstream/service_maker/.deps")
sys.path.insert(0, str(DEPS_DIR))

import numpy as np  # noqa: E402
import pyservicemaker  # noqa: E402
from pyservicemaker import BatchMetadataOperator, EOSMessage, Flow, Pipeline, Probe, RenderMode  # noqa: E402

UNTRACKED_OBJECT_ID = 0xFFFFFFFFFFFFFFFF
GST_CLOCK_TIME_NONE = 0xFFFFFFFFFFFFFFFF


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def finite(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"non-finite value {result!r}")
    return result


def object_record(obj: Any) -> dict[str, Any]:
    rect = obj.rect_params
    raw_id = int(obj.object_id)
    return {
        "class_id": int(obj.class_id),
        "label": str(obj.label or ""),
        "confidence": finite(obj.confidence),
        "tracker_confidence": finite(obj.tracker_confidence),
        "track_id": None if raw_id == UNTRACKED_OBJECT_ID else str(raw_id),
        "bbox": {"x": finite(rect.left), "y": finite(rect.top),
                 "width": finite(rect.width), "height": finite(rect.height)},
    }


class _DLDevice(ctypes.Structure):
    _fields_ = [("device_type", ctypes.c_int32), ("device_id", ctypes.c_int32)]


class _DLDataType(ctypes.Structure):
    _fields_ = [("code", ctypes.c_uint8), ("bits", ctypes.c_uint8), ("lanes", ctypes.c_uint16)]


class _DLTensor(ctypes.Structure):
    _fields_ = [("data", ctypes.c_void_p), ("device", _DLDevice), ("ndim", ctypes.c_int32),
                ("dtype", _DLDataType), ("shape", ctypes.POINTER(ctypes.c_int64)),
                ("strides", ctypes.POINTER(ctypes.c_int64)), ("byte_offset", ctypes.c_uint64)]


_CUDART = None


def _cudart() -> Any:
    global _CUDART
    if _CUDART is None:
        for name in ("libcudart.so", "libcudart.so.13", "libcudart.so.12"):
            try:
                _CUDART = ctypes.CDLL(name)
                break
            except OSError:
                continue
        if _CUDART is None:
            matches = sorted(glob.glob("/usr/local/cuda*/lib64/libcudart.so*"))
            if not matches:
                raise RuntimeError("libcudart not found")
            _CUDART = ctypes.CDLL(matches[0])
    return _CUDART


def tensor_to_numpy(tensor: Any) -> np.ndarray:
    """Copy a DLPack tensor to host. CUDA tensors are copied with cudaMemcpy."""
    device = tuple(tensor.__dlpack_device__())
    if device[0] == 1:  # kDLCPU
        return np.array(np.from_dlpack(tensor), copy=True)
    if device[0] != 2:  # kDLCUDA
        raise RuntimeError(f"unsupported dlpack device {device}")
    capsule = tensor.__dlpack__()
    get_pointer = ctypes.pythonapi.PyCapsule_GetPointer
    get_pointer.restype = ctypes.c_void_p
    get_pointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
    # DLManagedTensor starts with its DLTensor; the capsule is not consumed, so the producer keeps ownership.
    dl = _DLTensor.from_address(get_pointer(capsule, b"dltensor"))
    if (dl.dtype.code, dl.dtype.bits, dl.dtype.lanes) != (2, 32, 1):
        raise RuntimeError(f"expected float32, got dlpack dtype {(dl.dtype.code, dl.dtype.bits, dl.dtype.lanes)}")
    shape = tuple(dl.shape[i] for i in range(dl.ndim))
    if dl.strides:  # DLPack strides are in elements; NULL means compact row-major
        contiguous = [1]
        for size in reversed(shape[1:]):
            contiguous.insert(0, contiguous[0] * size)
        if tuple(dl.strides[i] for i in range(dl.ndim)) != tuple(contiguous):
            raise RuntimeError("non-contiguous CUDA tensor")
    host = np.empty(shape, dtype=np.float32)
    cudart = _cudart()
    cudart.cudaMemcpy.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    status = cudart.cudaMemcpy(host.ctypes.data, ctypes.c_void_p(dl.data + dl.byte_offset), host.nbytes, 2)
    if status != 0:
        raise RuntimeError(f"cudaMemcpy failed with status {status}")
    return host


class StageWriter(BatchMetadataOperator):
    def __init__(self, stage: str, output: Path, tensor_dir: Path | None, tensor_frames: set[int]):
        super().__init__()
        self.stage = stage
        self.output_path = output
        self.tensor_dir = tensor_dir
        self.tensor_frames = tensor_frames
        self.handle = output.open("w", encoding="utf-8", newline="\n")
        self.frames = 0
        self.tensor_records: list[dict[str, Any]] = []
        self.tensor_errors: list[str] = []

    def close(self) -> None:
        self.handle.close()

    def _capture_tensors(self, frame_meta: Any, frame_number: int) -> None:
        items = list(frame_meta.tensor_items)
        if not items:
            self.tensor_errors.append(f"frame {frame_number}: no tensor_items")
            return
        for item_index, user_meta in enumerate(items):
            try:
                item = user_meta.as_tensor_output()
                layers = item.get_layers()
            except Exception as exc:  # an exception escaping the probe aborts the pipeline
                self.tensor_errors.append(f"frame {frame_number} item {item_index}: {type(exc).__name__}: {exc}")
                continue
            for name, tensor in layers.items():
                record = {"frame_number": frame_number, "item": item_index, "layer": name,
                          "unique_id": int(item.unique_id), "shape": list(tensor.shape),
                          "dtype": str(tensor.dtype), "dlpack_device": list(tensor.__dlpack_device__())}
                try:
                    array = tensor_to_numpy(tensor)
                    path = self.tensor_dir / f"{name.replace('/', '-').replace(':', '_')}_{frame_number:06d}.npy"
                    if path.exists():
                        raise RuntimeError(f"refusing to overwrite {path}")
                    np.save(path, array)
                    record.update({"path": path.name, "numpy_shape": list(array.shape),
                                   "numpy_dtype": str(array.dtype), "sha256": sha256_file(str(path))})
                except Exception as exc:  # recorded, never synthesized
                    record["error"] = f"{type(exc).__name__}: {exc}"
                    self.tensor_errors.append(f"frame {frame_number} {name}: {record['error']}")
                self.tensor_records.append(record)

    def handle_metadata(self, batch_meta: Any) -> None:
        for frame_meta in batch_meta.frame_items:
            frame_number = int(frame_meta.frame_number)
            record: dict[str, Any] = {
                "stage": self.stage, "frame_number": frame_number,
                "frame_width": int(frame_meta.pipeline_width), "frame_height": int(frame_meta.pipeline_height),
                "source_width": int(frame_meta.source_width), "source_height": int(frame_meta.source_height),
                "objects": [object_record(obj) for obj in frame_meta.object_items],
            }
            pts = int(frame_meta.buffer_pts)
            if pts != GST_CLOCK_TIME_NONE:
                record["pts_ns"] = pts
            if self.tensor_dir is not None and frame_number in self.tensor_frames:
                self._capture_tensors(frame_meta, frame_number)
            self.handle.write(json.dumps(record, allow_nan=False, separators=(",", ":")) + "\n")
            self.handle.flush()
            self.frames += 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="/workspace/demo/cam01.mp4")
    parser.add_argument("--infer-config", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--tensor-frames", default="141,283")
    parser.add_argument("--tracker-config", default="")
    parser.add_argument("--tracker-lib", default="/opt/nvidia/deepstream/deepstream/lib/libnvds_nvmultiobjecttracker.so")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tensor_dir = out / "tensors"
    tensor_dir.mkdir(exist_ok=True)
    tensor_frames = {int(v) for v in args.tensor_frames.split(",") if v.strip()}
    summary: dict[str, Any] = {
        "schema_version": 1, "started_at_utc": utc_now(), "source": args.source,
        "source_sha256": sha256_file(args.source), "infer_config": args.infer_config,
        "infer_config_sha256": sha256_file(args.infer_config),
        "tracker_config": args.tracker_config or None, "pyservicemaker_version": pyservicemaker.__version__,
        "numpy_version": np.__version__, "cwd": str(Path.cwd()), "pipeline_returned": False,
        "clean_eos": False, "error": None,
    }
    pre = StageWriter("pre_tracker", out / "pre_tracker.jsonl", tensor_dir, tensor_frames)
    post = StageWriter("post_tracker", out / "post_tracker.jsonl", None, set()) if args.tracker_config else None
    eos: list[Any] = []
    started = time.monotonic()
    try:
        pipeline = Pipeline("rt-detr-pretracker-diagnostic")
        flow = Flow(pipeline).batch_capture(
            [args.source], width=args.width, height=args.height, batch_size=1,
            batched_push_timeout=40000, buffer_pool_size=4, live_source=False,
            file_loop=False, enable_padding=False, gpu_id=0,
        ).infer(args.infer_config).attach(what=Probe("pre-tracker", pre))
        if post is not None:
            flow = flow.track(tracker_width=960, tracker_height=544, ll_config_file=args.tracker_config,
                              ll_lib_file=args.tracker_lib, gpu_id=0).attach(what=Probe("post-tracker", post))
        flow.render(RenderMode.DISCARD, enable_osd=False, sync=False)(
            lambda message: eos.append(message) if isinstance(message, EOSMessage) else None)
        summary["pipeline_returned"] = True
    except Exception as exc:
        summary["error"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
    finally:
        pre.close()
        if post is not None:
            post.close()
        summary.update({
            "ended_at_utc": utc_now(), "elapsed_seconds": time.monotonic() - started,
            "pre_tracker_frames": pre.frames, "post_tracker_frames": post.frames if post else None,
            "eos_message_count": len(eos),
            "clean_eos": bool(eos) and summary["pipeline_returned"] and summary["error"] is None,
            "tensor_records": pre.tensor_records, "tensor_errors": pre.tensor_errors,
        })
        (out / "run-summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "tensor_records"}, indent=2), file=sys.stderr)
    sys.exit(0 if summary["clean_eos"] else 1)


if __name__ == "__main__":
    main()
