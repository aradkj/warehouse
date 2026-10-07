"""Physical schema for the identity context.

See ``docs/architecture/data-model.md`` for the ER overview and the rationale of
each constraint. Tables are SQLAlchemy Core objects: adapters write explicit SQL
so that locking, upserts and optimistic-concurrency guards stay visible.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, Integer, String, Table, UniqueConstraint, func

from identity.domain.tenant import TenantStatus
from shared.infrastructure.db.base import metadata
from shared.infrastructure.db.types import TimestampCol, UuidCol

_STATUS_LIST = ", ".join(f"'{status.value}'" for status in TenantStatus)

tenants = Table(
    "tenants",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    # The slug is the tenant's public, human-chosen identifier; it is immutable.
    Column("slug", String(64), nullable=False),
    Column("name", String(160), nullable=False),
    Column("status", String(16), nullable=False, server_default=TenantStatus.ACTIVE.value),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("updated_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("version", Integer, nullable=False),
    # Tenant uniqueness is a business rule (the slug identifies the tenant in
    # every external integration), so the database enforces it.
    UniqueConstraint("slug", name="uq_tenants_slug"),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$'", name="slug_format"),
    CheckConstraint("length(btrim(name)) > 0", name="name_not_empty"),
    CheckConstraint(f"status IN ({_STATUS_LIST})", name="status_valid"),
)

__all__ = ["tenants"]
