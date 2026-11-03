from backend.app.analytics.association import (
    AssociationPolicy, TopologyAssociation, TopologyEdge, TransitionObservation,
)

from .support import key

EDGE = TopologyEdge("stair-a", "floor-1", "stair-f1", "floor-2", "landing-f2", 2, 20, "UP")


def transition(local: str, timestamp: float, floor: str = "floor-2", zone: str = "landing-f2"):
    return TransitionObservation(key(camera="cam-04" if floor == "floor-2" else "cam-03", local=local),
                                 floor, zone, timestamp, "UP")


def source() -> TransitionObservation:
    return TransitionObservation(key(camera="cam-03", local="source"), "floor-1", "stair-f1", 0, "UP")


def test_clear_topology_and_evidence_confirm_once() -> None:
    engine = TopologyAssociation("run-1")
    candidate = transition("candidate", 8)
    decision = engine.associate(source(), [candidate], [EDGE], {candidate.key: 0.91})
    assert decision.status == "CONFIRMED"
    assert decision.selected_track_key == candidate.key.as_string()
    assert decision.temporary_global_track_id
    second_source = TransitionObservation(key(camera="cam-03", local="other"), "floor-1", "stair-f1", 1, "UP")
    second = engine.associate(second_source, [candidate], [EDGE], {candidate.key: 0.95})
    assert second.status == "UNKNOWN"
    assert any("already consumed" in reason for item in second.candidates for reason in item.reasons)


def test_ambiguous_candidates_remain_unknown() -> None:
    engine = TopologyAssociation("run-1", AssociationPolicy(acceptance_threshold=0.75, ambiguity_margin=0.12))
    first, second = transition("a", 8), transition("b", 8.1)
    decision = engine.associate(source(), [first, second], [EDGE], {first.key: 0.9, second.key: 0.85})
    assert decision.status == "UNKNOWN" and decision.selected_track_key is None
    assert "ambiguity margin" in decision.reason
    assert len(decision.candidates) == 2


def test_topology_time_and_staleness_are_hard_gates() -> None:
    engine = TopologyAssociation("run-1")
    wrong_floor = transition("wrong", 8, floor="floor-3")
    decision = engine.associate(source(), [wrong_floor], [EDGE], {wrong_floor.key: 0.99})
    assert decision.status == "UNKNOWN"
    assert any("No legal directed portal" in reason for item in decision.candidates for reason in item.reasons)

    too_fast = transition("fast", 1)
    expired = engine.associate(source(), [too_fast], [EDGE], {too_fast.key: 0.99})
    assert expired.status == "EXPIRED"

    timely = transition("timely", 8)
    stale = engine.associate(source(), [timely], [EDGE], {timely.key: 0.99}, now=10)
    assert stale.status == "UNKNOWN"
    assert any("stale" in reason for item in stale.candidates for reason in item.reasons)


def test_missing_or_low_evidence_is_not_an_identity_probability() -> None:
    engine = TopologyAssociation("run-1")
    candidate = transition("candidate", 8)
    missing = engine.associate(source(), [candidate], [EDGE], {})
    assert missing.status == "UNKNOWN" and missing.score is None
    low = engine.associate(source(), [candidate], [EDGE], {candidate.key: 0.2})
    assert low.status == "UNKNOWN" and low.score == 0.2


def test_revocation_is_a_new_audited_decision() -> None:
    engine = TopologyAssociation("run-1")
    candidate = transition("candidate", 8)
    confirmed = engine.associate(source(), [candidate], [EDGE], {candidate.key: 0.95})
    revoked = engine.revoke(confirmed, "Contradictory later geometry")
    assert revoked.status == "REVOKED" and revoked.supersedes_id == confirmed.id
    assert revoked.temporary_global_track_id is None
