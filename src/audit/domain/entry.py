"""Audit entry model - **placeholder for Phase 2/4**.

The specification has the Audit context record business actions, policy
decisions, actor context, correlation identifiers and before/after state. Phase 1
does not persist audit records: there is no outbox to feed them and no ABAC engine
to record decisions for.

What Phase 1 *does* provide is the raw material:

* correlation ids and actor references on every request context,
* immutable ``inventory_movements`` (the stock ledger *is* an audit trail),
* immutable ``order_lines`` pricing snapshots,
* version columns on every aggregate.

This module names the shape those records will take so that later phases extend
the model instead of inventing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """A single recorded business or authorization action (Phase 2+)."""

    audit_id: str
    tenant_id: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    correlation_id: str | None
    causation_id: str | None
    occurred_at: datetime
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


PLANNED_PHASE = 2

__all__ = ["PLANNED_PHASE", "AuditEntry"]
