from backend.app.analytics.occupancy import IN_TRANSIT, UNALLOCATED, OccupancyLedger


def test_external_entries_exits_and_reconciliation_example() -> None:
    ledger = OccupancyLedger(initial_occupancy=120, boundary_coverage_complete=True)
    for _ in range(18):
        ledger.external_entry("floor-1")
    for _ in range(10):
        ledger.external_exit("floor-1")
    snapshot = ledger.snapshot(visible_fused=126, unresolved_not_visible=2)
    assert snapshot.expected == 128
    assert snapshot.external_entries == 18 and snapshot.external_exits == 10
    assert snapshot.discrepancy == 0
    assert snapshot.floor_balances["floor-1"] == 8
    assert snapshot.unallocated == 120


def test_internal_floor_transfer_conserves_building_totals() -> None:
    ledger = OccupancyLedger()
    ledger.external_entry("floor-1")
    ledger.begin_transition("floor-1")
    during = ledger.snapshot(0, 1)
    assert during.expected == 1 and during.in_transit == 1
    assert during.floor_balances.get("floor-1", 0) == 0
    assert during.external_entries == 1 and during.external_exits == 0
    ledger.complete_transition("floor-2")
    after = ledger.snapshot(1, 0)
    assert after.in_transit == 0 and after.floor_balances["floor-2"] == 1
    assert after.allocation_discrepancy == 0 and after.discrepancy == 0


def test_negative_and_inconsistent_ledgers_are_never_silently_clamped() -> None:
    ledger = OccupancyLedger()
    ledger.external_exit("floor-1")
    snapshot = ledger.snapshot(None, None)
    assert snapshot.expected == -1
    assert snapshot.floor_balances["floor-1"] == -1
    assert snapshot.discrepancy is None
    assert any("negative" in anomaly for anomaly in snapshot.anomalies)


def test_allocation_discrepancy_is_explicit() -> None:
    ledger = OccupancyLedger(initial_occupancy=10, balances={"floor-1": 9})
    snapshot = ledger.snapshot(9, 0)
    assert snapshot.allocation_discrepancy == 1
    assert snapshot.discrepancy == 1
    assert snapshot.quality == "ESTIMATED"


def test_reinitialization_is_audited_and_clears_counters() -> None:
    ledger = OccupancyLedger(initial_occupancy=2)
    ledger.external_entry("floor-1")
    correction = ledger.reinitialize(5, "Manual observed reconciliation")
    assert correction.previous_initial == 2 and correction.new_initial == 5
    snapshot = ledger.snapshot(None, None)
    assert snapshot.expected == 5 and snapshot.external_entries == 0
    assert snapshot.unallocated == 5
