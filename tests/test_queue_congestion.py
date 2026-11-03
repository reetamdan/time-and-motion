from backend.app.analytics.congestion import CongestionMonitor, CongestionRule
from backend.app.analytics.queueing import QueuePolicy, QueueSample, QueueTracker

from .support import key


def test_queue_join_leave_dwell_and_stationary_nonmember() -> None:
    tracker = QueueTracker(QueuePolicy(join_dwell_seconds=2, leave_dwell_seconds=1, min_join_movement=2))
    stationary = key(local="stationary")
    moving = key(local="moving")
    assert tracker.update(QueueSample(stationary, 0, True, position=(0, 0))) is None
    assert tracker.update(QueueSample(stationary, 3, True, position=(0, 0))) is None
    assert tracker.update(QueueSample(moving, 0, True, position=(0, 0))) is None
    assert tracker.update(QueueSample(moving, 1, True, position=(3, 0))) is None
    joined = tracker.update(QueueSample(moving, 2, True, position=(3, 0)))
    assert joined is not None and joined.transition == "JOINED"
    assert tracker.snapshot(2).queue_size == 1
    assert tracker.update(QueueSample(moving, 2.5, False)) is None
    left = tracker.update(QueueSample(moving, 3.5, False))
    assert left is not None and left.transition == "LEFT" and left.wait_seconds is None


def test_queue_service_wait_requires_observed_service_completion() -> None:
    tracker = QueueTracker(QueuePolicy(join_dwell_seconds=0, require_movement_to_join=False))
    track = key()
    tracker.update(QueueSample(track, 10, True))
    transition = tracker.update(QueueSample(track, 18, True, service_completed=True))
    assert transition.wait_seconds == 8
    assert transition.wait_basis == "OBSERVED_SERVICE_COMPLETION"


def test_queue_occlusion_and_expiration_do_not_become_zero() -> None:
    tracker = QueueTracker(QueuePolicy(join_dwell_seconds=0, max_occlusion_seconds=2,
                                       require_movement_to_join=False))
    track = key()
    tracker.update(QueueSample(track, 0, True))
    occluded = tracker.snapshot(1)
    assert occluded.queue_size == 1 and occluded.quality == "ESTIMATED"
    expired = tracker.snapshot(3)
    assert expired.queue_size is None and expired.quality == "UNAVAILABLE"
    assert expired.unresolved_members == 1
    assert tracker.snapshot(4).queue_size is None


def test_congestion_persistence_hysteresis_and_recovery() -> None:
    monitor = CongestionMonitor(CongestionRule(
        busy=10, congested=20, severe=30, persist_seconds=2, recover_seconds=2,
        recovery_hysteresis=0.2,
    ))
    assert monitor.update(21, 0) is None
    assert monitor.update(21, 1) is None
    transition = monitor.update(21, 2)
    assert transition is not None and transition.current == "CONGESTED"
    assert monitor.update(18, 3) is None  # above 20 * (1 - .2), so no flapping
    assert monitor.update(15, 4) is None
    recovered = monitor.update(15, 6)
    assert recovered is not None and recovered.current == "BUSY"
    assert monitor.update(5, 7) is None
    assert monitor.update(5, 9).current == "NORMAL"


def test_density_requires_valid_metric_area() -> None:
    monitor = CongestionMonitor(CongestionRule(busy=1, congested=2, severe=3, metric="density", persist_seconds=0))
    assert monitor.update_occupancy(10, 0, area_m2=None).current == "UNAVAILABLE"
    transition = monitor.update_occupancy(10, 1, area_m2=5)
    assert transition is not None and transition.value == 2
    assert transition.previous == "UNAVAILABLE" and transition.current == "CONGESTED"
