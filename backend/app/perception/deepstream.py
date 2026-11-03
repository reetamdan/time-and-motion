"""Pinned DeepStream runtime availability and real Service Maker observation sessions."""

from __future__ import annotations

import asyncio
import threading

from ..configuration import AppConfig, CameraConfig
from .artifacts import HashMemo, profile_blockers, runtime_artifact_checks
from .base import AdapterAvailability, ObservationSink, PerceptionAdapter, PerceptionStartError, ResourceIdentity
from .capability import CapabilityCache
from .service_maker import ServiceMakerSessions, workspace_path, wsl_path


class DeepStreamAdapter(PerceptionAdapter):
    """Run the configured detector profile in the pinned container; never synthetic output.

    Availability = configuration-level profile checks + expected-vs-current artifact identities
    (memoized hashes) + the cached container capability probe (see capability.py).
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.memo = HashMemo()
        self._cached: AdapterAvailability | None = None
        self._lock = threading.Lock()
        root = config.project_root
        self.capability = CapabilityCache(
            config.runtime.capability, config.models.pipeline, config.runtime.telemetry.wsl_distro,
            lambda: wsl_path(root / "scripts" / "deepstream.sh"), lambda path: workspace_path(path, root), self.memo,
        )
        self.sessions = ServiceMakerSessions(config, on_launch_failure=self._launch_failed)

    def _launch_failed(self, reason: str) -> None:
        # An observed container start failure means the cached capability may be wrong.
        self.capability.invalidate(f"observed start failure: {reason[:200]}")

    def artifact_checks(self) -> list[dict]:
        return [check.as_dict() for check in runtime_artifact_checks(self.config, self.memo)]

    def availability(self) -> AdapterAvailability:
        with self._lock:  # concurrent callers share one evaluation
            blockers = profile_blockers(self.config)
            checks = runtime_artifact_checks(self.config, self.memo)
            blockers.extend(f"{c.name} identity {c.status} (expected {c.expected_sha256 or 'none recorded'}, "
                            f"current {c.current_sha256 or 'unavailable'})" for c in checks if not c.ok)
            capability = None
            if not blockers:
                # Never run the probe with unverified pipeline files.
                capability = self.capability.get()
                blockers.extend(capability.blockers)
            available = not blockers
            selected = self.config.models.selected
            label = f"{selected.id} ({selected.acceptance})" if selected else "no detector"
            detail = (
                f"Pinned DeepStream runtime + {label} + NvDCF; real boxes and IDs. "
                + ("Not an evaluated PRD baseline." if not selected or selected.acceptance != "accepted" else "")
                if available else "Perception is unavailable; source previews and deterministic replay APIs remain independent"
            )
            report = AdapterAvailability(
                name="deepstream-service-maker",
                available=available,
                detail=detail.strip(),
                blockers=tuple(blockers),
                runtime_detected=bool(capability and capability.available),
                diagnostics={
                    "artifacts": [c.as_dict() for c in checks],
                    "capability": None if capability is None else {
                        "source": capability.source, "available": capability.available,
                        "validated_at_utc": capability.validated_at_utc, "probe_seconds": capability.probe_seconds,
                        "fields": capability.fields},
                    "selected_model": selected.id if selected else None,
                    "acceptance": selected.acceptance if selected else None,
                },
            )
            self._cached = report
            return report

    def cached_availability(self) -> AdapterAvailability | None:
        # Snapshots and other hot paths only read the last result; they never probe.
        return self._cached

    def invalidate_capability(self, reason: str) -> None:
        self.capability.invalidate(reason)

    async def start_camera(
        self, camera: CameraConfig, camera_epoch: str, sink: ObservationSink, replay_mode: str = "paced"
    ) -> None:
        report = await asyncio.to_thread(self.availability)
        if not report.available:
            raise PerceptionStartError("; ".join(report.blockers) or report.detail, resource_allocated=False)
        await self.sessions.start_camera(camera, camera_epoch, sink, replay_mode)

    async def stop_camera(self, camera_id: str, camera_epoch: str) -> None:
        await self.sessions.stop_camera(camera_id, camera_epoch)

    def resource_identity(self, camera_id: str, camera_epoch: str) -> ResourceIdentity | None:
        return self.sessions.resource_identity(camera_id, camera_epoch)

    async def adopt_resource(self, camera_id: str, camera_epoch: str, identity: ResourceIdentity | None) -> None:
        self.sessions.adopt(camera_id, camera_epoch, identity)

    async def shutdown(self) -> None:
        await self.sessions.shutdown()
