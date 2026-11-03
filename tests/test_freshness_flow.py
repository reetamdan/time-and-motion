from backend.app.analytics.flow import FlowEvent, RollingFlow
from backend.app.analytics.freshness import FreshnessPolicy, resolve_fresh_value

from .support import key, observation


def test_stale_value_is_retained_for_diagnostics_but_null_for_contract() -> None:
    value = resolve_fresh_value(7, 10, 12, FreshnessPolicy(max_age_ms=1000))
    assert value.value == 7 and value.quality == "STALE" and value.stale
    assert value.contract_value is None


def test_stale_track_is_not_fresh() -> None:
    sample = observation(key(), 1, 0)
    assert FreshnessPolicy(1000).evaluate(sample, 2.1).fresh is False
    assert FreshnessPolicy(1000).evaluate(sample, 1.5).fresh is True


def test_rolling_flow_is_directional_windowed_and_idempotent() -> None:
    flow = RollingFlow(window_seconds=60)
    assert flow.add(FlowEvent("a", 0, "UP"))
    assert not flow.add(FlowEvent("a", 0, "UP"))
    flow.add(FlowEvent("b", 30, "UP", observed=False))
    flow.add(FlowEvent("c", 30, "DOWN"))
    up = flow.rate("UP", 30)
    assert up.rate_per_minute == 2 and up.observed_count == 1 and up.inferred_count == 1
    assert flow.rate("DOWN", 30).rate_per_minute == 1
    aged = flow.rate("UP", 61)
    assert aged.rate_per_minute == 1 and aged.observed_count == 0 and aged.inferred_count == 1
