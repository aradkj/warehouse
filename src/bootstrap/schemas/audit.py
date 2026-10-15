"""Audit request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class AuditEntryResponse(BaseModel):
    id: UUID
    tenant_id: UUID | None
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    correlation_id: str | None = None
    causation_id: str | None = None
    occurred_at: datetime
    details: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, data: dict[str, Any]) -> AuditEntryResponse:
        return cls(
            id=data["id"],
            tenant_id=data.get("tenant_id"),
            actor_id=str(data["actor_id"]),
            action=str(data["action"]),
            resource_type=str(data["resource_type"]),
            resource_id=str(data["resource_id"]),
            correlation_id=data.get("correlation_id"),
            causation_id=data.get("causation_id"),
            occurred_at=data["occurred_at"],
            details=data.get("details"),
        )


class AuditPageResponse(BaseModel):
    items: list[AuditEntryResponse]
    total: int


__all__ = [
    "AuditEntryResponse",
    "AuditPageResponse",
]
