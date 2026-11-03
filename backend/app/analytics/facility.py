"""Calibration-gated facility analytics for one run: floor positions, zones, fusion, queues,
congestion, floor-map crossings, stair transitions, flow, and the occupancy ledger.

Everything here consumes only observations whose camera has a runtime-valid calibration
(``CameraGeometry``); uncalibrated cameras keep their camera-local counts and contribute nothing
here. Each capability reports exactly why it is unavailable instead of producing a zero.

Two clocks, never mixed:

* Source/event time (per-camera source seconds + the camera's ``timestamp_offset_ms``) drives
  dwell, persistence, crossing timing, and transition travel windows. Cross-camera fusion
  additionally requires the cameras to share a ``replay_group`` so their source timelines are
  comparable, and their latest source times must agree within the stale limit.
* Receive time (backend monotonic clock) decides whether a camera's cached contribution is still
  current. A contribution older than ``stale_after_ms`` (or from a camera whose stream ended, or
  that has not produced a frame in this epoch) is not used: the affected zone/floor/building
  result becomes UNAVAILABLE, never the stale count and never 0. ``evaluate`` is called on a
  timer as well as per frame, so a camera that stops sending frames still expires.

Floor/building aggregation counts each person once: zone counts may overlap legitimately (a queue
inside a dining area), so the floor figure is the de-duplicated union of zone memberships (by
camera-local track, then fused across overlapping cameras), never a sum of zone counts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import time
from typing import Any, Callable

from ..configuration import AppConfig, FloorConfig, PortalConfig, ZoneConfig
from .association import AssociationPolicy, TopologyAssociation, TopologyEdge, TransitionObservation
from .calibration import Calibration
from .congestion import CongestionMonitor, CongestionRule
from .crossing import Boundary, CrossingPolicy, DirectionalCrossing, IdempotencyCache
from .flow import FlowEvent, RollingFlow
from .fusion import FusionObservation, FusionPolicy, OverlapFusion
from .occupancy import OccupancyLedger
from .queueing import QueuePolicy, QueueSample, QueueSnapshot, QueueTracker
from .types import Observation, TrackKey

FACILITY_EMIT_SECONDS = 1.0
FLOW_WINDOW_SECONDS = 60.0
# A track last seen inside a portal endpoint zone that has not been observed for this long (source
# time of its camera) is treated as having left through the portal.
TRANSITION_DEPARTURE_SECONDS = 1.0
FLOOR_SCOPE = "people visible inside configured zones on this floor (not whole-floor occupancy)"


def point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


@dataclass(slots=True)
class CameraGeometry:
    """Runtime validity of one camera's calibration and image-space lines for the current epoch."""

    calibration: Calibration | None
    calibration_valid: bool
    calibration_reason: str
    frame_size: tuple[int, int] | None = None
    invalid_lines: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FloorObservation:
    key: TrackKey
    floor_id: str
    position: tuple[float, float]  # floor-map units
    time: float  # source seconds + camera offset


@dataclass(slots=True)
class CameraContribution:
    """The latest calibrated frame of one camera epoch, with both of its clocks."""

    epoch: str | None
    segment: int | None
    observations: list[FloorObservation]
    source_time: float  # source seconds + camera offset
    received_at: float  # backend monotonic seconds
    ended: str | None = None  # stream outcome once the source ended


@dataclass(slots=True)
class ZoneResult:
    zone_id: str
    visible: int | None
    quality: str
    reason: str | None
    cameras: tuple[str, ...] = ()
    members: tuple[FloorObservation, ...] = ()


@dataclass(slots=True)
class FloorResult:
    floor_id: str
    visible: int | None
    quality: str
    reason: str | None
    zones_unavailable: list[str] = field(default_factory=list)
    multi_zone_members: int = 0
    cameras: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"visible_in_zones": self.visible, "quality": self.quality, "reason": self.reason,
                "zones_unavailable": self.zones_unavailable, "multi_zone_members": self.multi_zone_members,
                "cameras": list(self.cameras), "scope": FLOOR_SCOPE}


@dataclass(slots=True)
class _PortalPresence:
    portal: PortalConfig
    endpoint: str  # "source" | "destination"
    zone_id: str
    first_seen: float
    last_seen: float


class FacilityAnalytics:
    def __init__(self, config: AppConfig, emit_alert: Callable[..., Any] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config
        self.emit_alert = emit_alert
        self.clock = clock
        self.stale_after_s = config.analytics.health.stale_after_ms / 1000
        self.geometry: dict[str, CameraGeometry] = {}
        self.contributions: dict[str, CameraContribution] = {}
        self._previous_position: dict[TrackKey, tuple[float, float, float]] = {}
        association = config.analytics.association
        self._fusion = OverlapFusion("facility", FusionPolicy(
            max_time_delta_ms=association.max_time_delta_ms, max_world_distance_m=association.max_world_distance_m))
        rule = config.analytics.congestion
        self._congestion = {zone.id: CongestionMonitor(CongestionRule(
            busy=rule.busy_threshold, congested=rule.congested_threshold, severe=rule.severe_threshold,
            metric=rule.metric, persist_seconds=rule.persist_seconds, recover_seconds=rule.recover_seconds,
            recovery_hysteresis=rule.recovery_hysteresis, revision=rule.revision)) for zone in config.zones}
        queue = config.analytics.queue
        self._queue_policy = QueuePolicy(
            join_dwell_seconds=queue.join_dwell_seconds, leave_dwell_seconds=queue.leave_dwell_seconds,
            max_occlusion_seconds=queue.max_occlusion_seconds, min_join_movement=queue.min_join_movement)
        self._queues = {zone.id: QueueTracker(self._queue_policy) for zone in config.zones if zone.kind == "queue"}
        occupancy = config.analytics.occupancy
        self.ledger = OccupancyLedger(occupancy.initial_occupancy, occupancy.boundary_coverage_complete)
        crossing = config.analytics.crossing
        self._crossing_policy = CrossingPolicy(
            dead_band=crossing.dead_band, debounce_seconds=crossing.debounce_seconds,
            confirmation_seconds=crossing.confirmation_seconds, min_track_seconds=crossing.min_track_seconds,
            min_track_observations=crossing.min_track_observations, min_displacement=crossing.min_displacement,
            stale_after_ms=crossing.stale_after_ms)
        self._idempotency = IdempotencyCache()
        self._boundaries: dict[str, tuple[str, DirectionalCrossing]] = {}
        for boundary in config.analytics.boundaries:
            if boundary.enabled and boundary.coordinate_space == "floor_map":
                self._boundaries[boundary.id] = (boundary.camera_id, self._new_detector(boundary))
        self.crossings: list[dict[str, Any]] = []
        # Stair/portal transitions: departures wait for an arrival on the other floor; association
        # stays UNKNOWN unless real re-identification evidence is supplied (none is, today).
        self._association = TopologyAssociation("facility", AssociationPolicy(
            acceptance_threshold=association.acceptance_threshold, ambiguity_margin=association.ambiguity_margin,
            max_observation_age_ms=max(config.analytics.health.stale_after_ms, 1500)))
        self._edges = [edge for portal in config.portals for edge in _portal_edges(portal)]
        self._presence: dict[TrackKey, _PortalPresence] = {}
        self._departures: list[TransitionObservation] = []
        self._flows = {portal.id: RollingFlow(FLOW_WINDOW_SECONDS) for portal in config.portals}
        self.decisions: list[dict[str, Any]] = []
        self._last_emit: dict[str, float] = {}

    def _new_detector(self, boundary) -> DirectionalCrossing:
        return DirectionalCrossing(Boundary(boundary.id, tuple(boundary.start), tuple(boundary.end),  # type: ignore[arg-type]
                                            boundary.positive_direction, boundary.owner, self._crossing_policy),
                                   self._idempotency)

    # ------------------------------------------------------------------ camera lifecycle

    def reset_camera(self, camera_id: str, epoch: str | None = None) -> CameraGeometry:
        """New epoch: start from the configured calibration; frame size is checked on its first frame.

        Every time-dependent state derived from the camera's previous epoch is dropped.
        """
        camera = self.config.camera_map[camera_id]
        calibration = self.config.calibrations.get(camera_id)
        if camera.calibration_status != "VALID" or calibration is None:
            geometry = CameraGeometry(None, False, f"calibration {camera.calibration_status}")
        else:
            geometry = CameraGeometry(calibration, True, f"calibration {calibration.revision} (frame size not yet seen)")
        self.geometry[camera_id] = geometry
        self.contributions.pop(camera_id, None)
        self._forget_camera_state(camera_id)
        return geometry

    def end_camera(self, camera_id: str, outcome: str) -> None:
        """The camera's source ended (EOF, abnormal termination, consumer failure): its cached
        contribution is no longer current, whatever its age."""
        contribution = self.contributions.get(camera_id)
        if contribution is not None:
            contribution.ended = outcome
        self._forget_camera_state(camera_id)

    def remove_camera(self, camera_id: str) -> None:
        self.contributions.pop(camera_id, None)
        self._forget_camera_state(camera_id)

    def _forget_camera_state(self, camera_id: str) -> None:
        """Drop per-track time-dependent state of one camera (epoch end, segment discontinuity)."""
        for key in [key for key in self._previous_position if key.camera_id == camera_id]:
            del self._previous_position[key]
        for key in [key for key in self._presence if key.camera_id == camera_id]:
            del self._presence[key]
        self._departures = [item for item in self._departures if item.key.camera_id != camera_id]
        for zone_id in list(self._queues):
            if camera_id in self.config.zone_map[zone_id].authoritative_camera_ids:
                self._queues[zone_id] = QueueTracker(self._queue_policy)  # member dwell timers restart
        for boundary in self.config.analytics.boundaries:
            if boundary.id in self._boundaries and boundary.camera_id == camera_id:
                self._boundaries[boundary.id] = (camera_id, self._new_detector(boundary))
        for zone in self.config.zones:
            if camera_id in zone.authoritative_camera_ids:
                monitor = self._congestion[zone.id]
                monitor._clear_candidate()  # persistence must be re-earned on the new timeline

    # ------------------------------------------------------------------ geometry gating

    def observe_frame_size(self, camera_id: str, width: int | None, height: int | None) -> list[str]:
        """Invalidate calibration / image lines measured on a different frame geometry. Returns problems."""
        geometry = self.geometry.get(camera_id) or self.reset_camera(camera_id)
        if geometry.frame_size is not None or not width or not height:
            return []
        geometry.frame_size = (width, height)
        problems = []
        if geometry.calibration is not None and geometry.calibration_valid:
            if geometry.calibration.image_size != geometry.frame_size:
                geometry.calibration_valid = False
                geometry.calibration_reason = (
                    f"calibration {geometry.calibration.revision} was measured on {geometry.calibration.image_size[0]}x"
                    f"{geometry.calibration.image_size[1]} but frames are {width}x{height} (resolution/crop/zoom changed)")
                problems.append(geometry.calibration_reason)
                self.contributions.pop(camera_id, None)
            else:
                geometry.calibration_reason = f"calibration {geometry.calibration.revision} matches the {width}x{height} frames"
        for boundary in self.config.analytics.boundaries:
            if boundary.camera_id == camera_id and boundary.coordinate_space == "image_pixels" and boundary.enabled:
                if boundary.frame_size and tuple(boundary.frame_size) != geometry.frame_size:
                    reason = (f"line {boundary.id} was drawn on {boundary.frame_size[0]}x{boundary.frame_size[1]} "
                              f"but frames are {width}x{height}")
                    geometry.invalid_lines[boundary.id] = reason
                    problems.append(reason)
        return problems

    def valid_image_lines(self, camera_id: str) -> set[str] | None:
        geometry = self.geometry.get(camera_id)
        if geometry is None or geometry.frame_size is None:
            return None  # not yet known: callers keep lines active only after the check
        return {b.id for b in self.config.analytics.boundaries if b.camera_id == camera_id
                and b.coordinate_space == "image_pixels" and b.id not in geometry.invalid_lines}

    # ------------------------------------------------------------------ per frame

    def project(self, camera_id: str, observations: tuple[Observation, ...], source_time: float) -> list[FloorObservation]:
        geometry = self.geometry.get(camera_id)
        if geometry is None or not geometry.calibration_valid or geometry.calibration is None:
            return []
        floor = self.config.floor_map[geometry.calibration.floor_id]
        offset = self.config.camera_map[camera_id].timestamp_offset_ms / 1000
        projected = []
        for observation in observations:
            foot = observation.metadata.get("image_bottom_center_pixels")
            if not isinstance(foot, (list, tuple)) or len(foot) != 2:
                continue
            point = geometry.calibration.project(float(foot[0]), float(foot[1]))
            if point is None or not (0 <= point[0] <= floor.width and 0 <= point[1] <= floor.height):
                continue  # behind the camera or off the floor plan: rejected, never clamped
            projected.append(FloorObservation(observation.key, floor.id, point, source_time + offset))
        return projected

    def update(self, camera_id: str, source_time: float, observations: tuple[Observation, ...], *,
               received_at: float | None = None, epoch: str | None = None,
               segment: int | None = None) -> list[dict[str, Any]]:
        """Feed one frame; returns crossing events on floor-map boundaries (facility entries/exits)."""
        geometry = self.geometry.get(camera_id)
        if geometry is None or not geometry.calibration_valid:
            return []
        previous = self.contributions.get(camera_id)
        if previous is not None and (previous.epoch != epoch or previous.segment != segment):
            # Source discontinuity (new segment) or epoch change: earlier timers are not comparable.
            self._forget_camera_state(camera_id)
        floor_observations = self.project(camera_id, observations, source_time)
        offset = self.config.camera_map[camera_id].timestamp_offset_ms / 1000
        self.contributions[camera_id] = CameraContribution(
            epoch, segment, floor_observations, source_time + offset,
            self.clock() if received_at is None else received_at)
        events = []
        for boundary_id, (boundary_camera, detector) in self._boundaries.items():
            if boundary_camera != camera_id:
                continue
            for item in floor_observations:
                event = detector.update(Observation(key=item.key, timestamp=item.time, position=item.position), now=item.time)
                if event is None:
                    continue
                if event.direction == "IN":
                    self.ledger.external_entry(item.floor_id)
                else:
                    self.ledger.external_exit(item.floor_id)
                record = {"boundary_id": boundary_id, "direction": event.direction, "track_key": str(event.track_key),
                          "source_time_s": event.occurred_at, "event_id": event.event_id, "floor_id": item.floor_id}
                self.crossings.append(record)
                events.append(record)
        for zone_id, tracker in self._queues.items():
            zone = self.config.zone_map[zone_id]
            if camera_id not in zone.authoritative_camera_ids:
                continue
            for item in floor_observations:
                last = self._previous_position.get(item.key)
                moving = last is not None and math.hypot(item.position[0] - last[0], item.position[1] - last[1]) >= \
                    self.config.analytics.queue.min_join_movement
                tracker.update(QueueSample(item.key, item.time, point_in_polygon(*item.position, zone.polygon), True, moving,
                                           item.position))
        for item in floor_observations:
            last = self._previous_position.get(item.key)
            if last is None or item.time - last[2] >= 1.0:
                self._previous_position[item.key] = (*item.position, item.time)
        self._update_transitions(camera_id, floor_observations, source_time + offset)
        return events

    # ------------------------------------------------------------------ stair / portal transitions

    def _update_transitions(self, camera_id: str, observations: list[FloorObservation], now: float) -> None:
        """Track presence inside portal endpoint zones; a track that vanishes there is a departure,
        a track that first appears there is an arrival. Arrivals are associated with departures on
        the other floor through the topology; continuity stays UNKNOWN without re-ID evidence."""
        if not self.config.portals:
            return
        seen = set()
        for item in observations:
            for portal in self.config.portals:
                for name in ("source", "destination"):
                    end = getattr(portal, name)
                    if end.camera_id != camera_id or end.floor_id != item.floor_id:
                        continue
                    zone = self.config.zone_map.get(end.zone_id)
                    if zone is None or not point_in_polygon(*item.position, zone.polygon):
                        continue
                    seen.add(item.key)
                    presence = self._presence.get(item.key)
                    if presence is None:
                        self._presence[item.key] = _PortalPresence(portal, name, end.zone_id, item.time, item.time)
                        self._arrival(portal, name, item)
                    else:
                        presence.last_seen = item.time
        for key, presence in list(self._presence.items()):
            if key.camera_id != camera_id or key in seen:
                continue
            if now - presence.last_seen >= TRANSITION_DEPARTURE_SECONDS:
                end = getattr(presence.portal, presence.endpoint)
                other = presence.portal.destination if presence.endpoint == "source" else presence.portal.source
                direction = _direction(self.config, end.floor_id, other.floor_id)
                if direction is not None:
                    self._departures.append(TransitionObservation(key, end.floor_id, presence.zone_id,
                                                                  presence.last_seen, direction))
                del self._presence[key]

    def _arrival(self, portal: PortalConfig, endpoint: str, item: FloorObservation) -> None:
        other = portal.source if endpoint == "destination" else portal.destination
        end = getattr(portal, endpoint)
        direction = _direction(self.config, other.floor_id, end.floor_id)
        if direction is None:
            return
        arrival = TransitionObservation(item.key, end.floor_id, end.zone_id, item.time, direction)
        horizon = max((p.max_travel_seconds for p in self.config.portals), default=0.0)
        self._departures = [d for d in self._departures if item.time - d.timestamp <= horizon]
        groups = {self.config.camera_map[c].replay_group for c in (portal.source.camera_id, portal.destination.camera_id)
                  if c in self.config.camera_map}
        if len(groups) != 1 or None in groups:
            return  # source times of the two endpoint cameras are not comparable: no flow, no association
        # Departures from the other end of this portal, still inside its travel window.
        sources = [d for d in self._departures if d.zone_id == other.zone_id and d.direction == direction
                   and 0 <= item.time - d.timestamp <= portal.max_travel_seconds
                   and d.key.camera_id != item.key.camera_id]
        self._flows[portal.id].add(FlowEvent(f"{portal.id}:{item.key.as_string()}:{item.time:.3f}", item.time, direction,
                                             observed=False))
        if not sources:
            return
        for source in sources:
            # Source-time comparison across cameras requires a shared replay group (checked by caller config).
            decision = self._association.associate(source, [arrival], self._edges, scores=None, now=item.time)
            self.decisions.append({
                "id": decision.id, "portal_id": decision.portal_id or portal.id, "status": decision.status,
                "source_track_key": decision.source_track_key, "candidate_track_key": arrival.key.as_string(),
                "temporary_global_track_id": decision.temporary_global_track_id, "score": decision.score,
                "margin": decision.margin, "decision_reason": decision.reason,
                "candidates": [{"track_key": c.track_key, "score": c.score, "status": c.status, "reasons": list(c.reasons)}
                               for c in decision.candidates]})

    def flow_rates(self, now: float) -> list[dict[str, Any]]:
        rows = []
        for portal in self.config.portals:
            for direction in ("UP", "DOWN"):
                if portal.direction not in ("BOTH", direction):
                    continue
                rate = self._flows[portal.id].rate(direction, now)  # type: ignore[arg-type]
                rows.append({"portal_id": portal.id, "direction": direction, "rate_per_minute": rate.rate_per_minute,
                             "window_seconds": rate.window_seconds, "observed_count": rate.observed_count,
                             "inferred_count": rate.inferred_count})
        return rows

    def take_decisions(self) -> list[dict[str, Any]]:
        decisions, self.decisions = self.decisions, []
        return decisions

    # ------------------------------------------------------------------ freshness

    def camera_status(self, camera_id: str, now: float | None = None) -> str | None:
        """None when the camera's contribution is current; otherwise exactly why it is not."""
        geometry = self.geometry.get(camera_id)
        if geometry is None:
            return f"{camera_id} not running"
        if not geometry.calibration_valid:
            return f"{camera_id} has no runtime-valid calibration ({geometry.calibration_reason})"
        contribution = self.contributions.get(camera_id)
        if contribution is None:
            return f"{camera_id} has produced no calibrated frame in this epoch"
        if contribution.ended is not None:
            return f"{camera_id} stream ended ({contribution.ended})"
        age = (self.clock() if now is None else now) - contribution.received_at
        if age > self.stale_after_s:
            return (f"{camera_id} is stale: last frame received {age:.1f} s ago "
                    f"(limit {self.stale_after_s:.1f} s)")
        return None

    def freshness(self, now: float | None = None) -> dict[str, str | None]:
        return {camera_id: self.camera_status(camera_id, now) for camera_id in self.geometry}

    # ------------------------------------------------------------------ aggregation

    def zone_result(self, zone: ZoneConfig, now: float | None = None) -> ZoneResult:
        if not zone.authoritative_camera_ids:
            return ZoneResult(zone.id, None, "UNAVAILABLE", "no authoritative camera configured")
        cameras = tuple(zone.authoritative_camera_ids)
        problems = [p for p in (self.camera_status(c, now) for c in cameras) if p is not None]
        if problems:
            return ZoneResult(zone.id, None, "UNAVAILABLE", "; ".join(problems), cameras)
        if len(cameras) > 1:
            structural = _overlap_structure(self.config, zone)
            if structural:
                return ZoneResult(zone.id, None, "UNAVAILABLE", structural, cameras)
            times = [self.contributions[c].source_time for c in cameras]
            if max(times) - min(times) > self.stale_after_s:
                return ZoneResult(zone.id, None, "UNAVAILABLE",
                                  f"overlapping cameras' source times differ by {max(times) - min(times):.1f} s "
                                  f"(limit {self.stale_after_s:.1f} s)", cameras)
        inside = tuple(item for camera in cameras for item in self.contributions[camera].observations
                       if item.floor_id == zone.floor_id and point_in_polygon(*item.position, zone.polygon))
        if len(cameras) == 1:
            return ZoneResult(zone.id, len({item.key for item in inside}), "OBSERVED", None, cameras, inside)
        result = self._fuse(inside, self.config.floor_map[zone.floor_id], {item.key: zone.overlap_group for item in inside})
        return ZoneResult(zone.id, result.occupancy_contribution, result.quality,
                          None if result.occupancy_contribution is not None else "fusion ambiguous", cameras, inside)

    def _fuse(self, items: tuple[FloorObservation, ...] | list[FloorObservation], floor: FloorConfig,
              groups: dict[TrackKey, str | None]):
        scale = floor.meters_per_unit or 1.0
        return self._fusion.fuse([
            FusionObservation(item.key, item.floor_id, item.time, (item.position[0] * scale, item.position[1] * scale),
                              groups.get(item.key)) for item in items])

    def floor_result(self, floor: FloorConfig, zones: dict[str, ZoneResult]) -> FloorResult:
        """De-duplicated people inside the floor's zones: one person in two overlapping zones counts once."""
        floor_zones = [zone for zone in self.config.zones if zone.floor_id == floor.id]
        if not floor_zones:
            return FloorResult(floor.id, None, "UNAVAILABLE", "no zones configured on this floor")
        unavailable = [zone.id for zone in floor_zones if zones[zone.id].visible is None]
        cameras = tuple(sorted({c for zone in floor_zones for c in zone.authoritative_camera_ids}))
        if unavailable:
            reasons = "; ".join(f"{z}: {zones[z].reason}" for z in unavailable)
            return FloorResult(floor.id, None, "UNAVAILABLE", f"zone coverage incomplete ({reasons})", unavailable,
                               cameras=cameras)
        members: dict[TrackKey, FloorObservation] = {}
        memberships: dict[TrackKey, int] = {}
        overlap: dict[TrackKey, str | None] = {}
        for zone in floor_zones:
            for item in zones[zone.id].members:
                members.setdefault(item.key, item)
                memberships[item.key] = memberships.get(item.key, 0) + 1
                if zone.overlap_group and len(zone.authoritative_camera_ids) > 1:
                    overlap[item.key] = zone.overlap_group
        multi = sum(count > 1 for count in memberships.values())
        qualities = {zones[zone.id].quality for zone in floor_zones}
        if len({key.camera_id for key in members}) <= 1:
            visible = len(members)
            quality = "ESTIMATED" if "ESTIMATED" in qualities else "OBSERVED"
        else:
            # Tracks from different cameras merge only through a validated overlap group (zone_result
            # already required a shared time base and metric scale for those); others stay distinct.
            fused = self._fuse(list(members.values()), floor, overlap)
            if fused.occupancy_contribution is None:
                return FloorResult(floor.id, None, "UNAVAILABLE", "cross-camera fusion unavailable", [], multi, cameras)
            visible = fused.occupancy_contribution
            quality = "ESTIMATED" if "ESTIMATED" in qualities or fused.quality == "ESTIMATED" else "OBSERVED"
        return FloorResult(floor.id, visible, quality, None, [], multi, cameras)

    def evaluate(self, now: float | None = None, source_now: float | None = None) -> dict[str, Any]:
        """Zone/floor/building figures + congestion/queue transitions.

        now: receive-domain monotonic time (freshness). source_now: latest source time seen, used
        only as the timestamp of transitions for zones with no current camera time.
        """
        now = self.clock() if now is None else now
        zones = {zone.id: self.zone_result(zone, now) for zone in self.config.zones}
        transitions = []
        for zone in self.config.zones:
            result = zones[zone.id]
            zone_time = self._zone_source_time(zone, source_now)
            if zone_time is None:
                continue
            monitor = self._congestion[zone.id]
            transition = (monitor.mark_unavailable(zone_time) if result.visible is None else
                          monitor.update_occupancy(result.visible, zone_time, zone.area_m2, zone.capacity))
            if transition is not None:
                transitions.append((zone, transition))
        queues = {}
        for zone_id, tracker in self._queues.items():
            zone_time = self._zone_source_time(self.config.zone_map[zone_id], source_now)
            if zones[zone_id].visible is None or zone_time is None:
                queues[zone_id] = QueueSnapshot(None, tracker.joins, tracker.leaves, tracker.expirations, 0, 0, None,
                                                "UNAVAILABLE")
            else:
                queues[zone_id] = tracker.snapshot(zone_time)
        floors = {floor.id: self.floor_result(floor, zones) for floor in self.config.floors}
        visible = [f.visible for f in floors.values()]
        building_visible = sum(visible) if visible and all(v is not None for v in visible) else None  # type: ignore[arg-type]
        # Unresolved (not currently visible) people cannot be measured without full coverage; never assumed 0.
        boundary_cameras = {camera for camera, _ in self._boundaries.values()}
        measurable = bool(boundary_cameras) and all(self.camera_status(camera, now) is None for camera in boundary_cameras)
        ledger = self.ledger.snapshot(building_visible, None) if measurable else None
        flow_time = max((c.source_time for c in self.contributions.values()), default=source_now)
        return {"zones": zones, "floors": floors, "queues": queues, "congestion_transitions": transitions,
                "building_visible": building_visible, "ledger": ledger,
                "flows": self.flow_rates(flow_time) if flow_time is not None and self._portals_measurable(now) else [],
                "freshness": self.freshness(now)}

    def _zone_source_time(self, zone: ZoneConfig, fallback: float | None) -> float | None:
        times = [self.contributions[c].source_time for c in zone.authoritative_camera_ids if c in self.contributions]
        return max(times) if times else fallback

    def _portals_measurable(self, now: float) -> bool:
        cameras = {end.camera_id for portal in self.config.portals for end in (portal.source, portal.destination)}
        return bool(cameras) and None not in cameras and all(self.camera_status(c, now) is None for c in cameras)  # type: ignore[arg-type]

    def due(self, now: float, camera_id: str = "") -> bool:
        """At most one evaluation per FACILITY_EMIT_SECONDS of one camera's own source time (or on a backwards jump).

        Keyed per camera: comparing source times of different cameras would look like time going
        backwards and re-arm evaluation on almost every frame.
        """
        last = self._last_emit.get(camera_id)
        if last is None or now - last >= FACILITY_EMIT_SECONDS or now < last:
            self._last_emit[camera_id] = now
            return True
        return False


def _direction(config: AppConfig, from_floor: str, to_floor: str):
    a, b = config.floor_map[from_floor].level, config.floor_map[to_floor].level
    return "UP" if b > a else "DOWN" if b < a else None


def _portal_edges(portal: PortalConfig) -> list[TopologyEdge]:
    edges = []
    if portal.direction in ("UP", "BOTH"):
        edges.append(TopologyEdge(portal.id, portal.source.floor_id, portal.source.zone_id, portal.destination.floor_id,
                                  portal.destination.zone_id, portal.min_travel_seconds, portal.max_travel_seconds, "UP"))
    if portal.direction in ("DOWN", "BOTH"):
        edges.append(TopologyEdge(portal.id, portal.destination.floor_id, portal.destination.zone_id, portal.source.floor_id,
                                  portal.source.zone_id, portal.min_travel_seconds, portal.max_travel_seconds, "DOWN"))
    return edges


def _overlap_structure(config: AppConfig, zone: ZoneConfig) -> str | None:
    groups = {config.camera_map[c].replay_group for c in zone.authoritative_camera_ids}
    if len(groups) != 1 or None in groups:
        return "overlapping cameras do not share a replay_group time base"
    floor = config.floor_map[zone.floor_id]
    if floor.meters_per_unit is None:
        return f"floor {floor.id} has no meters_per_unit scale for metric fusion"
    return None


def _zone_structure(config: AppConfig, zone: ZoneConfig, geometry: dict[str, CameraGeometry], running: set[str]) -> str | None:
    """Configuration/runtime-geometry reasons a zone cannot be measured (freshness excluded)."""
    if not zone.authoritative_camera_ids:
        return "no authoritative camera configured"
    missing = [c for c in zone.authoritative_camera_ids if c not in running]
    uncalibrated = [c for c in zone.authoritative_camera_ids
                    if c in running and not (geometry.get(c) and geometry[c].calibration_valid)]
    if missing or uncalibrated:
        return "; ".join(([f"not running: {', '.join(missing)}"] if missing else []) +
                         ([f"no runtime-valid calibration: {', '.join(uncalibrated)}"] if uncalibrated else []))
    if len(zone.authoritative_camera_ids) > 1:
        return _overlap_structure(config, zone)
    return None


def capability_matrix(config: AppConfig, geometry: dict[str, CameraGeometry], running: set[str],
                      freshness: dict[str, str | None] | None = None) -> dict[str, Any]:
    """Exactly which analytics are available, and why not, for the current configuration and run.

    ``available`` is structural (configuration + runtime geometry). ``current`` additionally needs
    every contributing camera to be fresh right now; ``freshness`` carries the per-camera reason.
    """
    freshness = freshness or {}
    cameras = {}
    for camera in config.cameras:
        state = geometry.get(camera.id)
        calibrated = bool(state and state.calibration_valid)
        lines = [b for b in config.analytics.boundaries if b.camera_id == camera.id and b.enabled]
        image_lines = [b for b in lines if b.coordinate_space == "image_pixels"]
        floor_lines = [b for b in lines if b.coordinate_space == "floor_map"]
        reason_calibration = (state.calibration_reason if state else f"calibration {camera.calibration_status}")
        cameras[camera.id] = {
            "enabled": camera.enabled, "running": camera.id in running,
            "fresh": camera.id in running and calibrated and freshness.get(camera.id, "unknown") is None,
            "freshness_reason": freshness.get(camera.id) if camera.id in freshness else None,
            "camera_local_counts": {"available": camera.id in running,
                                    "reason": None if camera.id in running else "camera not running"},
            "image_line_crossings": {
                "available": camera.id in running and any(b.id not in (state.invalid_lines if state else {}) for b in image_lines),
                "reason": "no image_pixels line configured" if not image_lines else (
                    "; ".join(state.invalid_lines.values()) if state and state.invalid_lines else None)},
            "floor_positions": {"available": calibrated, "reason": None if calibrated else reason_calibration},
            "facility_crossings": {"available": calibrated and bool(floor_lines),
                                   "reason": None if calibrated and floor_lines else (
                                       "no floor_map boundary configured" if not floor_lines else reason_calibration)},
        }
    zones = {}
    for zone in config.zones:
        reason = _zone_structure(config, zone, geometry, running)
        stale = [freshness[c] for c in zone.authoritative_camera_ids if freshness.get(c)] if reason is None else []
        entry = {"available": reason is None, "reason": reason, "current": reason is None and not stale,
                 "current_reason": "; ".join(stale) or None}  # type: ignore[arg-type]
        zones[zone.id] = {"kind": zone.kind, "occupancy": dict(entry), "congestion": dict(entry),
                          **({"queue": dict(entry)} if zone.kind == "queue" else {})}
    floors = {}
    for floor in config.floors:
        floor_zones = [z for z in config.zones if z.floor_id == floor.id]
        blocked = [z.id for z in floor_zones if not zones[z.id]["occupancy"]["available"]]
        floors[floor.id] = {"visible_in_zones": {
            "available": bool(floor_zones) and not blocked,
            "reason": "no zones configured" if not floor_zones else (
                f"zones unavailable: {', '.join(blocked)}" if blocked else None),
            "aggregation": "de-duplicated union of zone memberships (overlapping zones count a person once)",
            "fixture_status": floor.fixture_status}}
    coverage = config.analytics.occupancy.boundary_coverage_complete
    portal_ok = all(cameras[c]["facility_crossings"]["available"] for c in
                    {b.camera_id for b in config.analytics.boundaries if b.coordinate_space == "floor_map" and b.enabled})
    return {
        "cameras": cameras, "zones": zones, "floors": floors,
        "building_estimate": {"available": coverage and portal_ok and bool(config.analytics.boundaries),
                              "reason": None if coverage and portal_ok else (
                                  "boundary_coverage_complete is false (not every entrance is covered)" if not coverage
                                  else "a facility boundary camera lacks runtime-valid calibration")},
        "stair_association": _portal_capability(config, geometry, running),
    }


def _portal_capability(config: AppConfig, geometry: dict[str, CameraGeometry], running: set[str]) -> dict[str, Any]:
    if not config.portals:
        return {"available": False, "reason": "no portals configured"}
    problems = []
    for portal in config.portals:
        for end in (portal.source, portal.destination):
            camera = end.camera_id
            if camera is None:
                problems.append(f"{portal.id}: endpoint {end.zone_id} has no camera")
            elif camera not in running:
                problems.append(f"{portal.id}: {camera} not running")
            elif not (geometry.get(camera) and geometry[camera].calibration_valid):
                problems.append(f"{portal.id}: {camera} has no runtime-valid calibration")
        cameras = [end.camera_id for end in (portal.source, portal.destination) if end.camera_id]
        groups = {config.camera_map[c].replay_group for c in cameras if c in config.camera_map}
        if len(cameras) == 2 and (len(groups) != 1 or None in groups):
            problems.append(f"{portal.id}: endpoint cameras do not share a replay_group time base")
    return {"available": not problems, "reason": "; ".join(problems) or None,
            "identity_continuity": "UNKNOWN unless a re-identification evidence score is supplied (none configured)",
            "flow_basis": "inferred: arrivals first seen inside a portal endpoint zone"}
