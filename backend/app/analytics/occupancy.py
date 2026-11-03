"""Count-conserving building and floor occupancy ledger."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

IN_TRANSIT = "IN_TRANSIT"
UNALLOCATED = "UNALLOCATED"


@dataclass(frozen=True, slots=True)
class OccupancySnapshot:
    expected: int
    estimated: int
    visible_fused: int | None
    unresolved_not_visible: int | None
    discrepancy: int | None
    floor_balances: dict[str, int]
    in_transit: int
    unallocated: int
    allocation_discrepancy: int
    external_entries: int
    external_exits: int
    quality: Literal["OBSERVED", "ESTIMATED"]
    anomalies: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CorrectionRecord:
    previous_initial: int
    new_initial: int
    reason: str


@dataclass(slots=True)
class OccupancyLedger:
    initial_occupancy: int = 0
    boundary_coverage_complete: bool = False
    external_entries: int = 0
    external_exits: int = 0
    balances: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.initial_occupancy < 0:
            raise ValueError("Initial occupancy cannot be negative")
        if not self.balances:
            self.balances[UNALLOCATED] = self.initial_occupancy

    @property
    def expected(self) -> int:
        return self.initial_occupancy + self.external_entries - self.external_exits

    @staticmethod
    def _count(count: int) -> None:
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise ValueError("Ledger changes require a positive integer count")

    def external_entry(self, destination_floor: str | None = None, count: int = 1) -> None:
        self._count(count)
        self.external_entries += count
        allocation = destination_floor or UNALLOCATED
        self.balances[allocation] = self.balances.get(allocation, 0) + count

    def external_exit(self, source_floor: str | None = None, count: int = 1) -> None:
        self._count(count)
        self.external_exits += count
        allocation = source_floor or UNALLOCATED
        self.balances[allocation] = self.balances.get(allocation, 0) - count

    def internal_transfer(self, source_floor: str, destination_floor: str, count: int = 1) -> None:
        self._count(count)
        if source_floor == destination_floor:
            raise ValueError("An internal transfer requires distinct allocations")
        self.balances[source_floor] = self.balances.get(source_floor, 0) - count
        self.balances[destination_floor] = self.balances.get(destination_floor, 0) + count

    def begin_transition(self, source_floor: str, count: int = 1) -> None:
        self.internal_transfer(source_floor, IN_TRANSIT, count)

    def complete_transition(self, destination_floor: str, count: int = 1) -> None:
        self.internal_transfer(IN_TRANSIT, destination_floor, count)

    def reinitialize(self, new_initial: int, reason: str) -> CorrectionRecord:
        if new_initial < 0 or not reason.strip():
            raise ValueError("A non-negative initial value and audit reason are required")
        previous = self.initial_occupancy
        self.initial_occupancy = new_initial
        self.external_entries = 0
        self.external_exits = 0
        self.balances = {UNALLOCATED: new_initial}
        return CorrectionRecord(previous, new_initial, reason.strip())

    def snapshot(
        self,
        visible_fused: int | None,
        unresolved_not_visible: int | None,
    ) -> OccupancySnapshot:
        if visible_fused is not None and visible_fused < 0:
            raise ValueError("Visible occupancy cannot be negative")
        if unresolved_not_visible is not None and unresolved_not_visible < 0:
            raise ValueError("Unresolved occupancy cannot be negative")
        expected = self.expected
        floors = {key: value for key, value in self.balances.items() if key not in {IN_TRANSIT, UNALLOCATED}}
        in_transit = self.balances.get(IN_TRANSIT, 0)
        unallocated = self.balances.get(UNALLOCATED, 0)
        allocated_total = sum(floors.values()) + in_transit + unallocated
        allocation_discrepancy = expected - allocated_total
        discrepancy = (
            expected - visible_fused - unresolved_not_visible
            if visible_fused is not None and unresolved_not_visible is not None
            else None
        )
        anomalies: list[str] = []
        if expected < 0:
            anomalies.append("Expected building occupancy is negative")
        for scope, value in self.balances.items():
            if value < 0:
                anomalies.append(f"Allocation {scope} is negative")
        if allocation_discrepancy != 0:
            anomalies.append("Floor/transit allocations do not conserve expected occupancy")
        if discrepancy is not None and discrepancy != 0:
            anomalies.append("Visible/unresolved reconciliation differs from expected occupancy")
        quality: Literal["OBSERVED", "ESTIMATED"] = (
            "OBSERVED" if self.boundary_coverage_complete and not anomalies else "ESTIMATED"
        )
        return OccupancySnapshot(
            expected=expected,
            estimated=expected,
            visible_fused=visible_fused,
            unresolved_not_visible=unresolved_not_visible,
            discrepancy=discrepancy,
            floor_balances=floors,
            in_transit=in_transit,
            unallocated=unallocated,
            allocation_discrepancy=allocation_discrepancy,
            external_entries=self.external_entries,
            external_exits=self.external_exits,
            quality=quality,
            anomalies=tuple(anomalies),
        )
