"""Audit port interface."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from audit.domain.entry import AuditEntry


@runtime_checkable
class AuditPort(Protocol):
    """Outbound port for recording audit decisions and events."""

    async def record(self, entry: AuditEntry) -> None:
        """Record an audit trail entry."""
        ...

    async def list_for_tenant(
        self, tenant_id: Any, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Fetch audit records for a tenant ordered by occurrence descending."""
        ...


__all__ = ["AuditEntry", "AuditPort"]
