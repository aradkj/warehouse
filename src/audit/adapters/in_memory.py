"""In-memory AuditPort adapter for testing."""

from __future__ import annotations

from audit.domain.entry import AuditEntry
from audit.ports.audit import AuditPort


class InMemoryAuditAdapter(AuditPort):
    """In-memory collector for audit events."""

    def __init__(self, entries: list[AuditEntry] | None = None) -> None:
        self.entries: list[AuditEntry] = entries if entries is not None else []

    async def record(self, entry: AuditEntry) -> None:
        self.entries.append(entry)

    def clear(self) -> None:
        self.entries.clear()


__all__ = ["InMemoryAuditAdapter"]
