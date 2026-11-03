#!/usr/bin/env python3
"""TEST-ONLY backend: the real FastAPI app + dashboard with a fault-injection perception adapter.

Never a perception result. The adapter launches no container and produces no detections; its
"resource" is an in-memory record whose first release attempt fails, so the dashboard's
STOP_FAILED presentation and cleanup-retry path can be exercised in a real browser.

Usage: fault_injection_backend.py --port 8767 --database <path>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

from backend.app.main import create_app  # noqa: E402
from backend.app.perception.base import (  # noqa: E402
    AdapterAvailability, PerceptionAdapter, PerceptionCleanupError, ResourceIdentity,
)


class FaultInjectionAdapter(PerceptionAdapter):
    """TEST-ONLY: release of the (simulated) resource fails `failures` times, then succeeds."""

    def __init__(self, failures: int = 1) -> None:
        self.failures = failures
        self.resources: set[str] = set()

    def availability(self) -> AdapterAvailability:
        return AdapterAvailability("TEST-ONLY-fault-injection", True,
                                   "TEST-ONLY fault-injection adapter: no video, no detections, simulated resource",
                                   runtime_detected=False)

    def cached_availability(self) -> AdapterAvailability:
        return self.availability()

    def resource_identity(self, camera_id: str, camera_epoch: str) -> ResourceIdentity:
        return ResourceIdentity("simulated", f"test-only-{camera_id}-{camera_epoch}", "in-memory")

    async def start_camera(self, camera, camera_epoch, sink, replay_mode="paced") -> None:
        self.resources.add(self.resource_identity(camera.id, camera_epoch).name)

    async def stop_camera(self, camera_id: str, camera_epoch: str) -> None:
        name = self.resource_identity(camera_id, camera_epoch).name
        if name not in self.resources:
            return
        if self.failures > 0:
            self.failures -= 1
            raise PerceptionCleanupError(f"TEST-ONLY injected fault: {name} not confirmed released")
        self.resources.discard(name)

    async def shutdown(self) -> None:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    if args.database.exists():
        raise SystemExit(f"Refusing to reuse {args.database}")
    app = create_app(database_path=args.database, perception_adapter=FaultInjectionAdapter())
    print("TEST-ONLY fault-injection backend: results are not perception evidence", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
