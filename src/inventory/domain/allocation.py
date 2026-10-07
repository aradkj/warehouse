"""Allocation policy: deciding *where* stock comes from.

This is a pure domain service - no repositories, no locks, no I/O. The
application layer loads the candidates (under a row lock) and passes them in, so
the decision procedure itself is trivially unit-testable and deterministic.

Two properties matter and both are guaranteed here:

**Determinism.** Candidates are considered in a fixed order
``(warehouse_id, inventory_item_id)``. Two runs with the same inputs produce
byte-identical plans, which is what makes concurrency tests reproducible, and
what keeps the *lock order* in the adapter (ordered by inventory item id)
consistent with the allocation order.

**No overselling.** The planner never allocates more than a candidate's current
availability, and each candidate can be consumed by several requirements (that
is how a single warehouse serves multiple lines of one order).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

from inventory.domain.value_objects import (
    AllocationCandidate,
    AllocationPlan,
    ReservationAllocation,
    StockRequirement,
    StockShortfall,
)
from shared.domain.value_objects import Quantity


class AllocationStrategy(StrEnum):
    """How to behave when the full requirement cannot be satisfied.

    ``PARTIAL``       reserve whatever is available (an order may end up in a
                      configurable back-order state),
    ``ALL_OR_NOTHING`` reserve nothing at all - the caller decides what a
                      partially servable order means.
    """

    PARTIAL = "PARTIAL"
    ALL_OR_NOTHING = "ALL_OR_NOTHING"

    @property
    def requires_complete(self) -> bool:
        """True when a partial hold must be treated as a failure."""
        return self is AllocationStrategy.ALL_OR_NOTHING


class InventoryAllocationPolicy:
    """Greedy, deterministic, multi-warehouse allocation."""

    __slots__ = ("_strategy",)

    def __init__(self, strategy: AllocationStrategy = AllocationStrategy.PARTIAL) -> None:
        self._strategy = strategy

    @property
    def strategy(self) -> AllocationStrategy:
        return self._strategy

    def plan(
        self,
        *,
        requirements: Sequence[StockRequirement],
        candidates: Sequence[AllocationCandidate],
        now: datetime,
    ) -> AllocationPlan:
        """Build an allocation plan for ``requirements`` against ``candidates``."""
        remaining: dict[object, int] = {}
        ordered_candidates = sorted(
            candidates,
            key=lambda candidate: (
                str(candidate.warehouse_id),
                str(candidate.inventory_item_id),
            ),
        )
        for candidate in ordered_candidates:
            remaining[candidate.inventory_item_id] = candidate.available.value

        allocations: list[ReservationAllocation] = []
        shortfalls: list[StockShortfall] = []

        for requirement in requirements:
            if not requirement.quantity.is_positive():  # pragma: no cover - validated upstream
                shortfalls.append(
                    StockShortfall(
                        sku_id=requirement.sku_id,
                        required=requirement.quantity,
                        allocated=Quantity.zero(),
                    )
                )
                continue
            needed = requirement.quantity.value
            for candidate in ordered_candidates:
                if needed == 0:
                    break
                if candidate.sku_id != requirement.sku_id:
                    continue
                free = remaining.get(candidate.inventory_item_id, 0)
                if free <= 0:
                    continue
                take = min(free, needed)
                remaining[candidate.inventory_item_id] = free - take
                needed -= take
                allocations.append(
                    ReservationAllocation.create(
                        inventory_item_id=candidate.inventory_item_id,
                        warehouse_id=candidate.warehouse_id,
                        sku_id=candidate.sku_id,
                        quantity=Quantity(take),
                        order_line_id=requirement.order_line_id,
                        now=now,
                    )
                )
            if needed > 0:
                shortfalls.append(
                    StockShortfall(
                        sku_id=requirement.sku_id,
                        required=requirement.quantity,
                        allocated=Quantity(requirement.quantity.value - needed),
                    )
                )

        if shortfalls and self._strategy.requires_complete:
            # Nothing is held under ALL_OR_NOTHING, so the report says so: a
            # shortfall describes what the *plan* holds, not what happened to be
            # on the shelf. Callers that want the latter ask the availability
            # endpoint, which is the source of truth for stock levels.
            return AllocationPlan(
                allocations=(),
                shortfalls=tuple(
                    StockShortfall(
                        sku_id=shortfall.sku_id,
                        required=shortfall.required,
                        allocated=Quantity.zero(),
                    )
                    for shortfall in shortfalls
                ),
            )
        return AllocationPlan(allocations=tuple(allocations), shortfalls=tuple(shortfalls))


__all__ = ["AllocationStrategy", "InventoryAllocationPolicy"]
