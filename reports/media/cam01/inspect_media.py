from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np


OUTPUT_DIR = Path(__file__).resolve().parent
ROOT = OUTPUT_DIR.parents[2]
SOURCE = ROOT / "demo" / "cam01.mp4"
MAX_IMAGE_BYTES = 3_000_000
SAMPLE_LIMIT = 6


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fourcc_string(value: float) -> str:
    integer = int(value)
    return "".join(chr((integer >> (8 * index)) & 0xFF) for index in range(4))


def encode_under_limit(image: np.ndarray) -> tuple[bytes, int, float, int, int]:
    working = image
    resize_scale = 1.0
    for attempt in range(12):
        for quality in (92, 85, 75, 65, 55, 45):
            ok, encoded = cv2.imencode(
                ".jpg", working, [int(cv2.IMWRITE_JPEG_QUALITY), quality]
            )
            if not ok:
                raise RuntimeError("OpenCV could not encode JPEG evidence")
            payload = encoded.tobytes()
            if len(payload) < MAX_IMAGE_BYTES:
                height, width = working.shape[:2]
                return payload, quality, resize_scale, width, height
        resize_scale *= 0.85
        working = cv2.resize(
            image,
            None,
            fx=resize_scale,
            fy=resize_scale,
            interpolation=cv2.INTER_AREA,
        )
    raise RuntimeError(f"Could not encode JPEG below {MAX_IMAGE_BYTES} bytes")


def write_jpeg(path: Path, image: np.ndarray) -> dict:
    payload, quality, resize_scale, width, height = encode_under_limit(image)
    path.write_bytes(payload)
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "size_bytes": len(payload),
        "width": width,
        "height": height,
        "jpeg_quality": quality,
        "resize_scale_from_source": round(resize_scale, 6),
    }


def main() -> None:
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)

    source_stat = SOURCE.stat()
    capture = cv2.VideoCapture(str(SOURCE))
    if not capture.isOpened():
        raise RuntimeError("OpenCV could not open the supplied video")

    backend = capture.getBackendName()
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    reported_frames = int(round(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
    width = int(round(capture.get(cv2.CAP_PROP_FRAME_WIDTH)))
    height = int(round(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    fourcc_value = float(capture.get(cv2.CAP_PROP_FOURCC))
    fourcc = fourcc_string(fourcc_value)
    codec_names = {
        "avc1": "H.264 / AVC",
        "h264": "H.264 / AVC",
        "H264": "H.264 / AVC",
        "hev1": "H.265 / HEVC",
        "hvc1": "H.265 / HEVC",
        "mp4v": "MPEG-4 Part 2",
        "MJPG": "Motion JPEG",
    }

    decoded_frames = 0
    decode_errors: list[dict] = []
    timestamps_ms: list[float] = []
    first_frame_shape: list[int] | None = None
    last_frame_shape: list[int] | None = None

    while True:
        ok, frame = capture.read()
        if not ok:
            if reported_frames and decoded_frames < reported_frames:
                decode_errors.append(
                    {
                        "frame_index": decoded_frames,
                        "error": "Sequential decoding stopped before the container-reported frame count",
                    }
                )
            break
        if frame is None or frame.size == 0:
            decode_errors.append(
                {"frame_index": decoded_frames, "error": "Empty decoded frame"}
            )
            continue
        shape = [int(frame.shape[1]), int(frame.shape[0])]
        if first_frame_shape is None:
            first_frame_shape = shape
        last_frame_shape = shape
        timestamps_ms.append(float(capture.get(cv2.CAP_PROP_POS_MSEC)))
        decoded_frames += 1

    capture.release()

    if decoded_frames == 0:
        raise RuntimeError("No frames could be decoded")
    if fps <= 0:
        raise RuntimeError(f"Invalid reported FPS: {fps}")

    sample_indices = sorted(
        set(
            int(index)
            for index in np.linspace(
                0, decoded_frames - 1, num=min(SAMPLE_LIMIT, decoded_frames)
            )
        )
    )
    sample_set = set(sample_indices)
    selected: dict[int, tuple[np.ndarray, float]] = {}

    capture = cv2.VideoCapture(str(SOURCE))
    if not capture.isOpened():
        raise RuntimeError("OpenCV could not reopen the supplied video")
    frame_index = 0
    while frame_index <= max(sample_indices):
        ok, frame = capture.read()
        if not ok or frame is None or frame.size == 0:
            decode_errors.append(
                {
                    "frame_index": frame_index,
                    "error": "Frame could not be decoded during evidence extraction",
                }
            )
            break
        if frame_index in sample_set:
            selected[frame_index] = (
                frame.copy(),
                float(capture.get(cv2.CAP_PROP_POS_MSEC)),
            )
        frame_index += 1
    capture.release()

    missing_samples = sample_set.difference(selected)
    if missing_samples:
        raise RuntimeError(f"Missing selected frames: {sorted(missing_samples)}")

    evidence: list[dict] = []
    for index in sample_indices:
        frame, timestamp_ms = selected[index]
        filename = f"frame_{index:06d}_t{round(timestamp_ms):06d}ms.jpg"
        record = write_jpeg(OUTPUT_DIR / filename, frame)
        record.update(
            {
                "frame_index": index,
                "timestamp_ms": round(timestamp_ms, 3),
                "source_width": int(frame.shape[1]),
                "source_height": int(frame.shape[0]),
            }
        )
        evidence.append(record)

    columns = 3
    cell_width = 600
    cell_image_height = 400
    label_height = 38
    rows = (len(evidence) + columns - 1) // columns
    sheet = np.full(
        (rows * (cell_image_height + label_height), columns * cell_width, 3),
        24,
        dtype=np.uint8,
    )

    for position, record in enumerate(evidence):
        index = record["frame_index"]
        frame = selected[index][0]
        scale = min(cell_width / frame.shape[1], cell_image_height / frame.shape[0])
        thumb = cv2.resize(
            frame,
            (max(1, round(frame.shape[1] * scale)), max(1, round(frame.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )
        row, column = divmod(position, columns)
        x0 = column * cell_width
        y0 = row * (cell_image_height + label_height)
        x_offset = (cell_width - thumb.shape[1]) // 2
        y_offset = (cell_image_height - thumb.shape[0]) // 2
        sheet[
            y0 + y_offset : y0 + y_offset + thumb.shape[0],
            x0 + x_offset : x0 + x_offset + thumb.shape[1],
        ] = thumb
        label = f"frame {index} | {record['timestamp_ms'] / 1000:.3f} s"
        cv2.putText(
            sheet,
            label,
            (x0 + 12, y0 + cell_image_height + 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            (235, 235, 235),
            1,
            cv2.LINE_AA,
        )

    contact_sheet = write_jpeg(OUTPUT_DIR / "contact_sheet.jpg", sheet)
    contact_sheet["sampled_frame_indices"] = sample_indices

    clean_eof = not any(
        error["error"] == "Sequential decoding stopped before the container-reported frame count"
        for error in decode_errors
    )
    timestamp_deltas = np.diff(np.asarray(timestamps_ms, dtype=np.float64))
    result = {
        "schema_version": 1,
        "source": {
            "path": SOURCE.relative_to(ROOT).as_posix(),
            "filename": SOURCE.name,
            "size_bytes": source_stat.st_size,
            "sha256": sha256(SOURCE),
        },
        "tooling": {
            "python_package": "opencv-python-headless",
            "opencv_version": cv2.__version__,
            "decode_backend": backend,
        },
        "container_reported": {
            "codec_fourcc": fourcc,
            "codec_name": codec_names.get(fourcc, "Unmapped FourCC; see codec_fourcc"),
            "codec_fourcc_integer": int(fourcc_value),
            "width": width,
            "height": height,
            "fps": fps,
            "frame_count": reported_frames,
            "duration_seconds_from_reported_count_and_fps": reported_frames / fps,
        },
        "sequential_decode": {
            "decoded_frame_count": decoded_frames,
            "decoded_duration_seconds_at_reported_fps": decoded_frames / fps,
            "clean_eof": clean_eof,
            "frame_count_matches_container_report": decoded_frames == reported_frames,
            "decode_errors": decode_errors,
            "first_decoded_frame_shape": first_frame_shape,
            "last_decoded_frame_shape": last_frame_shape,
            "first_timestamp_ms": timestamps_ms[0] if timestamps_ms else None,
            "last_timestamp_ms": timestamps_ms[-1] if timestamps_ms else None,
            "timestamps_strictly_increasing": bool(np.all(timestamp_deltas > 0)),
            "minimum_timestamp_delta_ms": float(timestamp_deltas.min())
            if timestamp_deltas.size
            else None,
            "maximum_timestamp_delta_ms": float(timestamp_deltas.max())
            if timestamp_deltas.size
            else None,
        },
        "evidence_images": evidence,
        "contact_sheet": contact_sheet,
        "image_size_limit_bytes_exclusive": MAX_IMAGE_BYTES,
        "method_notes": [
            "Metadata is read through the OpenCV VideoCapture backend; codec identity is the reported FourCC.",
            "The complete clip was decoded sequentially once for validation and again only as needed to extract selected frames.",
            "A false read at the container-reported final frame is treated as normal EOF; early termination or empty frames are recorded as decode errors.",
            "JPEG quality or dimensions were reduced only when necessary to keep each evidence image below the byte limit.",
        ],
    }
    (OUTPUT_DIR / "media_probe.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
