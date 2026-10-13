"""Physical schema for durable audit logging."""

from __future__ import annotations

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    String,
    Table,
    func,
)

from shared.infrastructure.db.base import metadata
from shared.infrastructure.db.types import JsonbCol, TimestampCol, UuidCol

audit_logs = Table(
    "audit_logs",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column(
        "tenant_id",
        UuidCol,
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_audit_logs_tenant_id"),
        nullable=True,
        index=True,
    ),
    Column("actor_id", String(128), nullable=False, index=True),
    Column("action", String(64), nullable=False, index=True),
    Column("resource_type", String(64), nullable=False, index=True),
    Column("resource_id", String(128), nullable=False),
    Column("correlation_id", String(128), nullable=True),
    Column("causation_id", String(128), nullable=True),
    Column("occurred_at", TimestampCol, nullable=False, server_default=func.now(), index=True),
    Column("details", JsonbCol, nullable=True),
)

__all__ = ["audit_logs"]
