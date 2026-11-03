from __future__ import annotations

from backend.app.analytics.crossing import Boundary, CrossingPolicy, DirectionalCrossing, IdempotencyCache

from .support import key, observation


def detector(**overrides) -> DirectionalCrossing:
    policy = CrossingPolicy(dead_band=1, debounce_seconds=1, min_displacement=1, **overrides)
    return DirectionalCrossing(Boundary("entry", (0, 0), (100, 0), "IN", "cam-01", policy))


def test_directional_crossing_jitter_debounce_and_idempotency() -> None:
    track = key()
    crossing = detector()
    assert crossing.update(observation(track, 0, -5)) is None
    assert crossing.update(observation(track, 0.2, 0.2)) is None
    assert crossing.update(observation(track, 0.4, -0.4)) is None
    event = crossing.update(observation(track, 1, 5))
    assert event is not None and event.direction == "IN"
    assert crossing.update(observation(track, 1, 5)) is None
    assert crossing.update(observation(track, 1.5, -5)) is None  # reverse transition is debounced
    reverse = crossing.update(observation(track, 2.5, -5))
    assert reverse is not None and reverse.direction == "OUT"

    replay = DirectionalCrossing(crossing.boundary, IdempotencyCache())
    replay_track = key(epoch="replay")
    replay.update(observation(replay_track, 0, -5))
    replay_event = replay.update(observation(replay_track, 1, 5))
    assert replay_event is not None
    replay_again = DirectionalCrossing(crossing.boundary, replay._idempotency)
    replay_again.update(observation(replay_track, 0, -5))
    assert replay_again.update(observation(replay_track, 1, 5)) is None


def test_crossing_requires_confirmation_beyond_dead_band() -> None:
    track = key()
    crossing = detector(confirmation_seconds=0.5)
    crossing.update(observation(track, 0, -4))
    assert crossing.update(observation(track, 1, 4)) is None
    assert crossing.update(observation(track, 1.2, 0.2)) is None
    assert crossing.update(observation(track, 1.6, 4)) is not None


def test_stale_track_cannot_complete_old_crossing() -> None:
    track = key()
    crossing = detector(stale_after_ms=1000)
    crossing.update(observation(track, 0, -4))
    assert crossing.update(observation(track, 5, 4), now=5) is None
    # The gap establishes a new side. A subsequent real reverse transition is OUT, never a phantom IN.
    event = crossing.update(observation(track, 5.2, -4))
    assert event is not None and event.direction == "OUT"


def test_stale_observation_does_not_mutate_boundary_state() -> None:
    track = key()
    crossing = detector(stale_after_ms=1000)
    crossing.update(observation(track, 0, -4))
    assert crossing.update(observation(track, 0.5, 4), now=2) is None
    event = crossing.update(observation(track, 0.8, 4), now=0.8)
    assert event is not None


def test_epoch_prevents_numeric_tracker_id_collision() -> None:
    first = key(epoch="epoch-a", local="7")
    second = key(epoch="epoch-b", local="7")
    assert first != second and first.as_string() != second.as_string()
    crossing = detector()
    crossing.update(observation(first, 0, -4))
    assert crossing.update(observation(first, 1, 4)) is not None
    # A reused numeric ID in a new epoch does not inherit the old side or emit an entry by itself.
    assert crossing.update(observation(second, 1.1, 4)) is None


def test_minimum_track_persistence_suppresses_unconfirmed_crossing() -> None:
    track = key()
    crossing = detector(min_track_observations=3, min_track_seconds=1)
    crossing.update(observation(track, 0, -4))
    assert crossing.update(observation(track, 0.5, 4)) is None
    later_reverse = crossing.update(observation(track, 1.5, -4))
    assert later_reverse is not None and later_reverse.direction == "OUT"
