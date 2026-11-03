"""Topology-constrained transition association with conservative UNKNOWN decisions."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

from .types import TrackKey

AssociationStatus = Literal["CANDIDATE", "CONFIRMED", "UNKNOWN", "EXPIRED", "REVOKED", "REJECTED"]


@dataclass(frozen=True, slots=True)
class TopologyEdge:
    portal_id: str
    source_floor_id: str
    source_zone_id: str
    destination_floor_id: str
    destination_zone_id: str
    min_travel_seconds: float
    max_travel_seconds: float
    direction: Literal["UP", "DOWN", "BOTH"] = "BOTH"

    def __post_init__(self) -> None:
        if self.min_travel_seconds < 0 or self.max_travel_seconds <= self.min_travel_seconds:
            raise ValueError("Invalid topology travel window")


@dataclass(frozen=True, slots=True)
class TransitionObservation:
    key: TrackKey
    floor_id: str
    zone_id: str
    timestamp: float
    direction: Literal["UP", "DOWN"]

    def __post_init__(self) -> None:
        if not math.isfinite(self.timestamp):
            raise ValueError("Transition timestamp must be finite")


@dataclass(frozen=True, slots=True)
class AssociationPolicy:
    acceptance_threshold: float = 0.75
    ambiguity_margin: float = 0.1
    require_score: bool = True
    max_observation_age_ms: float = 1500

    def __post_init__(self) -> None:
        if not 0 <= self.acceptance_threshold <= 1 or not 0 <= self.ambiguity_margin <= 1:
            raise ValueError("Association thresholds must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    track_key: str
    score: float | None
    status: AssociationStatus
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AssociationDecision:
    id: str
    status: AssociationStatus
    source_track_key: str
    selected_track_key: str | None
    portal_id: str | None
    temporary_global_track_id: str | None
    score: float | None
    margin: float | None
    reason: str
    candidates: tuple[CandidateEvaluation, ...]
    supersedes_id: str | None = None


class TopologyAssociation:
    """Scores are adapter evidence scores, never calibrated identity probabilities."""

    def __init__(self, session_id: str, policy: AssociationPolicy = AssociationPolicy()) -> None:
        self.session_id = session_id
        self.policy = policy
        self._used_sources: dict[TrackKey, AssociationDecision] = {}
        self._used_candidates: dict[TrackKey, TrackKey] = {}

    def associate(
        self,
        source: TransitionObservation,
        candidates: Sequence[TransitionObservation],
        edges: Sequence[TopologyEdge],
        scores: Mapping[TrackKey, float | None] | None = None,
        *,
        now: float | None = None,
    ) -> AssociationDecision:
        scores = scores or {}
        evaluation_time = max([source.timestamp, *(candidate.timestamp for candidate in candidates)]) if now is None else now
        previous = self._used_sources.get(source.key)
        if previous is not None:
            return AssociationDecision(
                self._decision_id(source, candidates), "UNKNOWN", source.key.as_string(), None, previous.portal_id,
                None, None, None, "Source track already has a confirmed mutual-exclusion association",
                tuple(CandidateEvaluation(c.key.as_string(), scores.get(c.key), "REJECTED", ("Source is already associated",)) for c in candidates),
            )
        evaluations: list[CandidateEvaluation] = []
        viable: list[tuple[TransitionObservation, TopologyEdge, float]] = []
        topology_rejected = False
        for candidate in candidates:
            reasons: list[str] = []
            edge = next((
                item for item in edges
                if item.source_floor_id == source.floor_id and item.source_zone_id == source.zone_id
                and item.destination_floor_id == candidate.floor_id and item.destination_zone_id == candidate.zone_id
                and item.direction in ("BOTH", source.direction)
            ), None)
            if edge is None:
                reasons.append("No legal directed portal connects the source and candidate zones")
            if candidate.key == source.key:
                reasons.append("Source and candidate track keys must differ")
            if candidate.direction != source.direction:
                reasons.append("Transition directions are inconsistent")
            if candidate.key in self._used_candidates:
                reasons.append("Candidate is already consumed by another confirmed association")
            elapsed = candidate.timestamp - source.timestamp
            if edge is not None and not edge.min_travel_seconds <= elapsed <= edge.max_travel_seconds:
                reasons.append("Elapsed time is outside the portal travel window")
                topology_rejected = True
            age_ms = (evaluation_time - candidate.timestamp) * 1000
            if age_ms > self.policy.max_observation_age_ms or age_ms < 0:
                reasons.append("Candidate observation is stale or has invalid temporal ordering")
            score = scores.get(candidate.key)
            if score is not None and not 0 <= score <= 1:
                reasons.append("Association evidence score is outside [0, 1]")
            if reasons:
                evaluations.append(CandidateEvaluation(candidate.key.as_string(), score, "REJECTED", tuple(reasons)))
            else:
                evaluations.append(CandidateEvaluation(candidate.key.as_string(), score, "CANDIDATE", ()))
                viable.append((candidate, edge, score))  # type: ignore[arg-type]

        if not viable:
            status: AssociationStatus = "EXPIRED" if topology_rejected else "UNKNOWN"
            reason = "No candidate passed topology, time, freshness, and mutual-exclusion gates"
            return self._decision(status, source, None, None, None, None, reason, evaluations)
        if any(score is None for _, _, score in viable):
            # Topology-only identity continuity is never invented, even if an adapter marks scores optional.
            return self._decision(
                "UNKNOWN", source, None, viable[0][1].portal_id, None, None,
                "Required association evidence is unavailable; continuity remains UNKNOWN", evaluations,
            )
        ranked = sorted(viable, key=lambda item: (-float(item[2]), item[0].key.as_string()))
        best, edge, best_score = ranked[0]
        assert best_score is not None
        second_score = ranked[1][2] if len(ranked) > 1 else None
        margin = best_score - second_score if second_score is not None else None
        if best_score < self.policy.acceptance_threshold:
            return self._decision(
                "UNKNOWN", source, None, edge.portal_id, best_score, margin,
                "Best evidence score is below the acceptance threshold", evaluations,
            )
        if margin is not None and margin < self.policy.ambiguity_margin:
            return self._decision(
                "UNKNOWN", source, None, edge.portal_id, best_score, margin,
                "Alternative candidates are within the ambiguity margin", evaluations,
            )
        temporary_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.session_id}:{source.key}:{best.key}"))
        decision = self._decision(
            "CONFIRMED", source, best, edge.portal_id, best_score, margin,
            "Topology, temporal, evidence-threshold, margin, and mutual-exclusion gates passed", evaluations,
            temporary_id,
        )
        self._used_sources[source.key] = decision
        self._used_candidates[best.key] = source.key
        return decision

    def revoke(self, decision: AssociationDecision, reason: str) -> AssociationDecision:
        if decision.status != "CONFIRMED" or not decision.selected_track_key:
            raise ValueError("Only a confirmed association can be revoked")
        if not reason.strip():
            raise ValueError("Revocation requires a reason")
        source_key = TrackKey(*decision.source_track_key.split(":", 2))
        candidate_key = TrackKey(*decision.selected_track_key.split(":", 2))
        self._used_sources.pop(source_key, None)
        self._used_candidates.pop(candidate_key, None)
        return AssociationDecision(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"revoke:{decision.id}:{reason}")),
            status="REVOKED",
            source_track_key=decision.source_track_key,
            selected_track_key=decision.selected_track_key,
            portal_id=decision.portal_id,
            temporary_global_track_id=None,
            score=decision.score,
            margin=decision.margin,
            reason=reason.strip(),
            candidates=decision.candidates,
            supersedes_id=decision.id,
        )

    def _decision(
        self, status: AssociationStatus, source: TransitionObservation,
        selected: TransitionObservation | None, portal_id: str | None, score: float | None,
        margin: float | None, reason: str, evaluations: list[CandidateEvaluation],
        temporary_id: str | None = None,
    ) -> AssociationDecision:
        selected_key = selected.key.as_string() if selected else None
        decision_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{self.session_id}:{source.key}:{source.timestamp:.6f}:{selected_key}:{status}",
        ))
        return AssociationDecision(
            decision_id, status, source.key.as_string(), selected_key, portal_id, temporary_id,
            score, margin, reason, tuple(evaluations),
        )

    @staticmethod
    def _decision_id(source: TransitionObservation, candidates: Sequence[TransitionObservation]) -> str:
        keys = ",".join(sorted(candidate.key.as_string() for candidate in candidates))
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"already-associated:{source.key}:{keys}"))
