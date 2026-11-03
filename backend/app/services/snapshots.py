"""Consistent API snapshots assembled from configuration and real runtime/persistence state."""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

from ..analytics.timebase import ANALYTICS_FPS_BASIS, SOURCE_TIME_BASIS
from ..configuration import AppConfig, CameraConfig
from ..db.repository import Repository
from ..schemas import (
    Alert, AssociationRecord, Camera, Congestion, Floor, FloorCamera, FloorPortal, Health, KPIs,
    Notice, Quality, Snapshot, SystemMetrics, Zone,
)
from ..timeutils import iso_now
from .runtime import CameraRuntimeState, CameraRuntimeSupervisor
from .sequence import SequenceState
from .telemetry import TelemetryService

LOGGER = logging.getLogger("cctv.snapshots")


class SnapshotService:
    def __init__(
        self, config: AppConfig, repository: Repository, supervisor: CameraRuntimeSupervisor,
        telemetry: TelemetryService, sequence: SequenceState,
    ) -> None:
        self.config = config
        self.repository = repository
        self.supervisor = supervisor
        self.telemetry = telemetry
        self.sequence = sequence

    async def metrics_collected(self, metrics: dict[str, object]) -> None:
        sequence = self.sequence.bump()
        run_id, status, _ = self.supervisor.current_run()
        try:
            self.repository.record_metrics(
                metrics, run_id if status in {"STARTING", "RUNNING", "DEGRADED", "STOP_FAILED"} else None, sequence
            )
        except sqlite3.Error:
            LOGGER.exception("Could not persist system metrics; readiness will report persistence degradation")

    def metrics(self) -> dict[str, object]:
        return self.telemetry.current()

    def _camera(self, config: CameraConfig, state: CameraRuntimeState) -> Camera:
        now = time.monotonic()
        age_ms = (now - state.last_good_monotonic) * 1000 if state.last_good_monotonic is not None else None
        stale = age_ms is not None and age_ms > self.config.analytics.health.stale_after_ms
        health = Health(state.health)
        visible_count = state.visible_count
        source_fps = state.source_fps
        analytics_fps = state.analytics_fps
        reason = state.unavailable_reason
        if stale and state.perception_started:
            visible_count = None
            source_fps = None
            analytics_fps = None
            health = Health.DEGRADED
            reason = "No fresh perception metadata; last values are not current"
        frame_age = state.frame_age_ms + (age_ms or 0) if state.frame_age_ms is not None else None
        return Camera(
            id=config.id,
            name=config.name,
            floor_id=config.floor_id,
            enabled=config.enabled,
            health=health,
            calibration_status=config.calibration_status,
            calibration_revision=config.calibration_revision,
            source_type=config.source_type,
            source_label=config.source_label,
            epoch=state.epoch,
            visible_count=visible_count,
            source_fps=source_fps,
            analytics_fps=analytics_fps,
            frame_age_ms=frame_age,
            frame_age_basis=state.frame_age_basis,
            dropped_frames=state.dropped_frames,
            replay_mode=state.replay_mode,
            analytics_fps_basis=ANALYTICS_FPS_BASIS if state.replay_mode else None,
            # Stale source position is diagnostic context, not a current value: withheld like the count.
            source_position_s=None if stale and state.perception_started else state.source_position_s,
            source_segment=state.source_segment,
            source_time_basis=SOURCE_TIME_BASIS if state.source_position_s is not None else None,
            source_discontinuities=state.source_discontinuities,
            source_loop=config.loop and config.source_type == "file",
            source_loop_index=state.loop_index,
            source_file_position_s=None if stale and state.perception_started else state.file_position_s,
            backend_queue_ms=None if stale and state.perception_started else state.backend_queue_ms,
            persisted_count_samples=state.persisted_count_samples,
            camera_line_crossings=state.camera_line_crossings,
            stream_outcome=state.stream_outcome,
            stream_outcome_detail=state.stream_outcome_detail,
            frames_at_detection_limit=state.frames_at_detection_limit,
            detection_limit=state.detection_limit,
            reconnect_attempt=state.reconnect_attempt,
            next_reconnect_at=state.next_reconnect_at,
            evidence_available=config.source_type == "file",
            evidence_unavailable_reason=None if config.source_type == "file" else (
                "Live sources have no local recording buffer in this version; evidence clips need the source file"),
            last_good_age_ms=age_ms,
            unavailable_reason=reason,
            preview_url=f"/api/v1/cameras/{config.id}/preview" if config.source_type == "file" else None,
        )

    def _crossings_measurable(self, states) -> bool:
        # Runtime validity (frame geometry checked), not just the configured calibration status.
        capabilities = self.supervisor.capabilities()["cameras"]
        return any(
            boundary.enabled and boundary.coordinate_space == "floor_map"
            and capabilities.get(boundary.camera_id, {}).get("facility_crossings", {}).get("available")
            and states.get(boundary.camera_id) is not None and states[boundary.camera_id].perception_started
            for boundary in self.config.analytics.boundaries
        )

    @staticmethod
    def _quality(value: object) -> Quality:
        try:
            return Quality(str(value))
        except ValueError:
            return Quality.UNAVAILABLE

    def snapshot(self) -> dict[str, Any]:
        run_id, run_status, perception_active = self.supervisor.current_run()
        active = run_status in {"STARTING", "RUNNING", "DEGRADED", "STOP_FAILED"}
        states = self.supervisor.states()
        cameras = [self._camera(camera, states[camera.id]) for camera in self.config.cameras]
        cameras_by_id = {camera.id: camera for camera in cameras}
        occupancy = self.repository.latest_analytics(run_id, "occupancy") if run_id and active else []
        queues = self.repository.latest_analytics(run_id, "queue") if run_id and active else []
        flows = self.repository.latest_analytics(run_id, "flow") if run_id and active else []
        congestion = self.repository.latest_analytics(run_id, "congestion") if run_id and active else []
        occupancy_by_scope = {(row["scope_type"], row["scope_id"]): row for row in occupancy}
        queue_by_id = {row["queue_id"]: row for row in queues}
        congestion_by_id = {row["zone_id"]: row for row in congestion}
        configured_portal_ids = {portal.id for portal in self.config.portals}
        up_flow_rates = [
            row["rate_per_minute"] for row in flows
            if row.get("portal_id") in configured_portal_ids
            and row.get("direction") == "UP" and row.get("rate_per_minute") is not None
        ]
        down_flow_rates = [
            row["rate_per_minute"] for row in flows
            if row.get("portal_id") in configured_portal_ids
            and row.get("direction") == "DOWN" and row.get("rate_per_minute") is not None
        ]

        zone_models: dict[str, Zone] = {}
        for zone in self.config.zones:
            occ = occupancy_by_scope.get(("zone", zone.id))
            queue = queue_by_id.get(zone.id)
            level = congestion_by_id.get(zone.id, {}).get("level", "UNAVAILABLE")
            try:
                congestion_level = Congestion(level)
            except ValueError:
                congestion_level = Congestion.UNAVAILABLE
            zone_models[zone.id] = Zone(
                id=zone.id,
                floor_id=zone.floor_id,
                name=zone.name,
                kind=zone.kind,
                polygon=zone.polygon,
                capacity=zone.capacity,
                occupancy=(occ.get("visible") if occ.get("visible") is not None else occ.get("estimated")) if occ else None,
                congestion=congestion_level,
                queue_size=queue.get("queue_size") if queue and zone.kind == "queue" else None,
            )

        floors: list[Floor] = []
        for floor in self.config.floors:
            occ = occupancy_by_scope.get(("floor", floor.id))
            floor_cameras = [
                FloorCamera(
                    id=camera.id,
                    name=camera.name,
                    position=camera.position,
                    coverage_polygon=camera.coverage_polygon,
                    health=cameras_by_id[camera.id].health,
                    calibration_status=camera.calibration_status,
                )
                for camera in self.config.cameras if camera.floor_id == floor.id
            ]
            portal_models = [
                FloorPortal(id=portal.id, name=portal.name, position=endpoint.position, direction=portal.direction)
                for portal in self.config.portals
                for endpoint in (portal.source, portal.destination)
                if endpoint.floor_id == floor.id
            ]
            floors.append(Floor(
                id=floor.id,
                name=floor.name,
                level=floor.level,
                width=floor.width,
                height=floor.height,
                visible_occupancy=occ.get("visible") if occ else None,
                estimated_occupancy=occ.get("estimated") if occ else None,
                quality=self._quality(occ.get("quality")) if occ else Quality.UNAVAILABLE,
                zones=[zone_models[zone.id] for zone in self.config.zones if zone.floor_id == floor.id],
                cameras=floor_cameras,
                walls=[[wall.start, wall.end] for wall in floor.walls],
                portals=portal_models,
            ))

        building = occupancy_by_scope.get(("building", "facility"))
        entries = self.repository.count_crossings(run_id, "IN") if run_id else 0
        exits = self.repository.count_crossings(run_id, "OUT") if run_id else 0
        unresolved = self.repository.count_unresolved_associations(run_id) if run_id else None
        queue_values = [row.get("queue_size") for row in queues]
        queue_total = sum(value for value in queue_values if value is not None) if queue_values and all(
            value is not None for value in queue_values
        ) else None
        waits = [row.get("max_wait_seconds") for row in queues if row.get("max_wait_seconds") is not None]
        kpis = KPIs(
            building_expected=building.get("expected") if building else None,
            building_visible=building.get("visible") if building else None,
            building_estimated=building.get("estimated") if building else None,
            unresolved_transitions=unresolved,
            discrepancy=building.get("discrepancy") if building else None,
            quality=self._quality(building.get("quality")) if building else Quality.UNAVAILABLE,
            # Building entries/exits are measured only through an active calibrated floor-map boundary;
            # otherwise "no crossings" is unknown, not zero.
            entries=entries if self._crossings_measurable(states) or entries > 0 else None,
            exits=exits if self._crossings_measurable(states) or exits > 0 else None,
            queue_total=queue_total,
            max_wait_seconds=max(waits) if waits else None,
            stair_up_per_min=sum(up_flow_rates) if up_flow_rates else None,
            stair_down_per_min=sum(down_flow_rates) if down_flow_rates else None,
            congested_zones=sum(
                row.get("level") in {"CONGESTED", "SEVERELY CONGESTED"} for row in congestion
            ),
            healthy_cameras=sum(camera.health == Health.HEALTHY for camera in cameras),
            enabled_cameras=sum(camera.enabled for camera in self.config.cameras),
        )
        alerts = self.repository.list_alerts(run_id=run_id, limit=100) if run_id else []
        associations = self.repository.list_associations(run_id=run_id, limit=100) if run_id else []
        notices: list[Notice] = []
        if any(floor.fixture_status == "ILLUSTRATIVE_UNCALIBRATED" for floor in self.config.floors):
            notices.append(Notice(
                severity="WARNING",
                title="Illustrative, uncalibrated floor fixtures",
                message="Both floor layouts are ILLUSTRATIVE and UNCALIBRATED; geometry-dependent counts are unavailable.",
            ))
        # Never probe from a snapshot (called per live update); use the last real probe result.
        availability = self.supervisor.cached_availability()
        if self.config.models.selection_mode == "provisional_smoke":
            notices.append(Notice(
                severity="INFO",
                title="Provisional perception smoke profile",
                message="TrafficCamNet/NvDCF supplies real person boxes, IDs and visible counts; the evaluated PRD detector baseline remains unselected.",
            ))
        if availability is None:
            notices.append(Notice(severity="INFO", title="Perception availability not yet probed",
                                  message="The perception runtime is probed when a run starts or the environment is inspected."))
        elif not availability.available:
            notices.append(Notice(severity="WARNING", title="Perception runtime unavailable", message=availability.detail))
            notices.append(Notice(
                severity="INFO", title="SOURCE PREVIEW · ANALYTICS UNAVAILABLE",
                message="Existing clips are served without detection overlays or inferred crowd counts.",
            ))
        missing = [camera.id for camera in self.config.cameras if not states[camera.id].source_available]
        if missing:
            notices.append(Notice(
                severity="WARNING", title="Configured sources unavailable",
                message=f"Unavailable camera sources: {', '.join(missing)}. Other cameras remain isolated.",
            ))
        snapshot = Snapshot(
            run_id=run_id,
            run_status=run_status,
            generated_at=iso_now(),
            sequence=self.sequence.current,
            watermark=self.sequence.watermark,
            cameras=cameras,
            floors=floors,
            alerts=[Alert.model_validate(alert) for alert in alerts],
            kpis=kpis,
            associations=[AssociationRecord.model_validate(record) for record in associations],
            system_metrics=SystemMetrics.model_validate(self.metrics()),
            notices=notices,
        )
        return snapshot.model_dump(mode="json")

    def versions(self) -> dict[str, Any]:
        selected = next((model for model in self.config.models.models if model.id == self.config.models.selected_model), None)
        return {
            "schema_version": "1.0",
            "api_version": "v1",
            "database_schema_version": self.repository.schema_version,
            "configuration_revision": self.config.configuration_revision,
            "configurations": {
                name: {"schema_version": 1, "sha256": checksum}
                for name, checksum in self.config.file_revisions.items()
            },
            "selected_model": self.config.models.selected_model,
            "model_acceptance": selected.acceptance if selected else None,
            "model_revision": selected.revision if selected else None,
            "model_selection_mode": self.config.models.selection_mode,
            "default_replay_mode": self.config.runtime.replay.mode,
            "evaluated_baseline_selected": self.config.models.selection_mode == "evaluated_baseline" and selected is not None,
            "model_checksum": selected.sha256 if selected else None,
            "engine_checksum": selected.engine_sha256 if selected else None,
            "tracker_revision": self.config.models.tracker.revision,
            "calibration_revisions": {
                camera.id: camera.calibration_revision for camera in self.config.cameras
            },
        }
