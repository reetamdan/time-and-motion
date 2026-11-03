#!/usr/bin/env python3
"""Hardware integration check: real Service Maker frames -> backend ObservationSink."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.configuration import load_configuration  # noqa: E402
from backend.app.perception.base import ObservationBatch  # noqa: E402
from backend.app.perception.deepstream import DeepStreamAdapter  # noqa: E402


class Sink:
    def __init__(self, camera_id: str, epoch: str) -> None:
        self.camera_id = camera_id
        self.epoch = epoch
        self.done = asyncio.Event()
        self.frames = 0
        self.person_observations = 0
        self.track_ids: set[str] = set()
        self.multi_frame_ids: dict[str, set[int]] = {}
        self.errors: list[str] = []
        self.last_batch: ObservationBatch | None = None
        self.max_visible = 0

    async def emit(self, batch: ObservationBatch) -> None:
        frame = int(batch.metadata["frame_number"])
        if batch.camera_id != self.camera_id or batch.camera_epoch != self.epoch:
            self.errors.append("camera/epoch mismatch")
        if not math.isfinite(batch.received_at):
            self.errors.append(f"non-finite receipt timestamp at frame {frame}")
        self.frames += 1
        self.person_observations += len(batch.observations)
        self.max_visible = max(self.max_visible, len(batch.observations))
        for observation in batch.observations:
            if observation.key.camera_id != self.camera_id or observation.key.camera_epoch != self.epoch:
                self.errors.append(f"track scope mismatch at frame {frame}")
            if not math.isfinite(observation.timestamp):
                self.errors.append(f"non-finite observation timestamp at frame {frame}")
            if observation.position is not None or observation.world_position is not None:
                self.errors.append(f"uncalibrated geometry was asserted at frame {frame}")
            track = observation.key.local_track_id
            self.track_ids.add(track)
            self.multi_frame_ids.setdefault(track, set()).add(frame)
        self.last_batch = batch
        if frame == 709:
            self.done.set()


async def main() -> None:
    config = load_configuration(ROOT / "configs", ROOT)
    camera = config.camera_map["cam-01"]
    epoch = f"backend-proof-{int(time.time())}"
    adapter = DeepStreamAdapter(config)
    availability = adapter.availability()
    sink = Sink(camera.id, epoch)
    started = time.monotonic()
    if not availability.available:
        raise RuntimeError("Perception availability failed: " + "; ".join(availability.blockers))
    await adapter.start_camera(camera, epoch, sink)
    try:
        await asyncio.wait_for(sink.done.wait(), timeout=60)
        # Let the consumer process normal process EOF before stopping the session.
        await asyncio.sleep(0.2)
    finally:
        await adapter.stop_camera(camera.id, epoch)
        await adapter.shutdown()
    checks = {
        "adapter_available": availability.available and availability.runtime_detected,
        "all_frames_consumed": sink.frames == 710,
        "real_person_observations": sink.person_observations > 0,
        "real_track_ids": len(sink.track_ids) > 0,
        "persistent_track_ids": any(len(frames) > 1 for frames in sink.multi_frame_ids.values()),
        "no_uncalibrated_geometry": not any("geometry" in error for error in sink.errors),
        "contract_errors_absent": not sink.errors,
        "source_pts_preserved": bool(sink.last_batch and isinstance(sink.last_batch.metadata.get("source_pts_ns"), int)),
        "monotonic_freshness_basis": bool(sink.last_batch and sink.last_batch.received_at > 0),
    }
    report = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "result": "PASS" if all(checks.values()) else "FAIL",
        "checks": {name: "PASS" if passed else "FAIL" for name, passed in checks.items()},
        "adapter": availability.__dict__ if hasattr(availability, "__dict__") else {
            "name": availability.name,
            "available": availability.available,
            "runtime_detected": availability.runtime_detected,
            "detail": availability.detail,
            "blockers": list(availability.blockers),
        },
        "metrics": {
            "frames": sink.frames,
            "person_observations": sink.person_observations,
            "unique_person_ids": len(sink.track_ids),
            "multi_frame_person_ids": sum(len(frames) > 1 for frames in sink.multi_frame_ids.values()),
            "max_visible_persons_per_frame": sink.max_visible,
            "elapsed_seconds": time.monotonic() - started,
            "errors": sink.errors,
        },
        "limitations": [
            "This proves real backend observation delivery, not detector accuracy or facility calibration.",
            "Geometry positions are intentionally null; facility crossings and calibrated occupancy are not asserted.",
            "TrafficCamNet remains a provisional smoke profile, not the evaluated PRD baseline.",
        ],
    }
    destination = ROOT / "reports" / "validation" / "backend-service-maker-cam01" / "result.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report["result"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
