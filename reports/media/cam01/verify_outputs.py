from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import cv2

OUTPUT_DIR = Path(__file__).resolve().parent
ROOT = OUTPUT_DIR.parents[2]
sys.path.insert(0, str(ROOT))

from backend.app.configuration import load_configuration


EXPECTED_HASH = "4201f7162eb0b9bcd3910f87b1a67a5bedac89c2b0a0bb4222e67d12f816b503"
EXPECTED_SIZE = 17_687_764


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> None:
    source = ROOT / "demo" / "cam01.mp4"
    assert source.stat().st_size == EXPECTED_SIZE
    assert digest(source) == EXPECTED_HASH

    probe = json.loads((OUTPUT_DIR / "media_probe.json").read_text(encoding="utf-8"))
    provenance = json.loads((OUTPUT_DIR / "provenance.json").read_text(encoding="utf-8"))
    technical = provenance["technical_probe"]
    reported = probe["container_reported"]
    decoded = probe["sequential_decode"]
    assert provenance["source"]["sha256"] == EXPECTED_HASH
    assert provenance["source"]["size_bytes"] == EXPECTED_SIZE
    assert technical["codec_fourcc"] == reported["codec_fourcc"] == "h264"
    assert technical["width"] == reported["width"] == 1920
    assert technical["height"] == reported["height"] == 1080
    assert technical["fps"] == reported["fps"]
    assert technical["container_reported_frame_count"] == reported["frame_count"] == 710
    assert technical["sequential_decoded_frame_count"] == decoded["decoded_frame_count"] == 710
    assert technical["duration_seconds"] == decoded["decoded_duration_seconds_at_reported_fps"]
    assert technical["last_frame_timestamp_ms"] == decoded["last_timestamp_ms"]
    assert decoded["clean_eof"] and not decoded["decode_errors"]

    images = [probe["contact_sheet"], *probe["evidence_images"]]
    for record in images:
        path = ROOT / record["path"]
        assert path.stat().st_size == record["size_bytes"] < 3_000_000
        image = cv2.imread(str(path))
        assert image is not None
        assert [image.shape[1], image.shape[0]] == [record["width"], record["height"]]

    config = load_configuration(ROOT / "configs", ROOT)
    assert [camera.id for camera in config.cameras] == ["cam-01", "cam-02", "cam-03", "cam-04"]
    assert [camera.id for camera in config.cameras if camera.enabled] == ["cam-01"]
    assert all(camera.calibration_status == "MISSING" for camera in config.cameras)
    cam01 = config.cameras[0]
    assert cam01.name == "Outdoor Pedestrian Area"
    assert cam01.source == "./demo/cam01.mp4"
    assert cam01.source_path is not None and cam01.source_path == source
    assert cam01.source_path.exists()
    assert [camera.source for camera in config.cameras[1:]] == [
        "./data/demo/cam02.mp4",
        "./data/demo/cam03.mp4",
        "./data/demo/cam04.mp4",
    ]

    report = (OUTPUT_DIR / "report.md").read_text(encoding="utf-8")
    assert EXPECTED_HASH in report
    assert "53 passed, 6 failed" in report
    assert "Known-positive person-detection smoke test: suitable" in report

    print(
        json.dumps(
            {
                "source_sha256_verified": EXPECTED_HASH,
                "source_size_verified": EXPECTED_SIZE,
                "decoded_frames": decoded["decoded_frame_count"],
                "decode_errors": decoded["decode_errors"],
                "evidence_images_verified": len(images),
                "largest_image_bytes": max(record["size_bytes"] for record in images),
                "camera_slots": len(config.cameras),
                "enabled_camera_ids": [camera.id for camera in config.cameras if camera.enabled],
                "calibration_statuses": [camera.calibration_status for camera in config.cameras],
                "report_test_summary": "53 passed, 6 failed, 1 deprecation warning",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
