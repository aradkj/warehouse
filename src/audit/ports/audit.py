"""Audit port interface."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from audit.domain.entry import AuditEntry


@runtime_checkable
class AuditPort(Protocol):
    """Outbound port for recording audit decisions and events."""

    async def record(self, entry: AuditEntry) -> None:
        """Record an audit trail entry."""
        ...


__all__ = ["AuditEntry", "AuditPort"]
