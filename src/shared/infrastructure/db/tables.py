"""Platform-wide tables (not owned by a single bounded context).

``idempotency_records`` is the only Phase 1 table that lives here: every
mutation endpoint of every context may use it, and its semantics are defined in
:mod:`shared.application.idempotency` (ADR-0006).
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from identity.adapters.persistence.tables import tenants
from shared.infrastructure.db.base import metadata
from shared.infrastructure.db.types import TimestampCol, UuidCol

IDEMPOTENCY_KEY_MAX_LENGTH = 255

idempotency_records = Table(
    "idempotency_records",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column(
        "tenant_id",
        UuidCol,
        ForeignKey(tenants.c.id, ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("idempotency_key", String(IDEMPOTENCY_KEY_MAX_LENGTH), nullable=False),
    #: Operation name, e.g. ``ordering.create_order``. Part of the stored audit
    #: trail; never reused for a different operation.
    Column("operation", String(120), nullable=False),
    #: SHA-256 of the canonical command payload.
    Column("request_hash", String(64), nullable=False),
    Column("state", String(16), nullable=False),
    #: Polymorphic reference to the created/changed resource. Deliberately not a
    #: foreign key: the target table depends on the operation.
    Column("resource_type", String(64), nullable=True),
    Column("resource_id", UuidCol, nullable=True),
    Column("response_status", Integer, nullable=True),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("updated_at", TimestampCol, nullable=False, server_default=func.now()),
    #: Retention marker. A Phase 2 job deletes expired *completed* records; the
    #: index below exists for that sweep, not for request-path queries.
    Column("expires_at", TimestampCol, nullable=False),
    # The unique constraint - not application code - is what makes duplicate
    # submissions impossible under concurrency.
    UniqueConstraint(
        "tenant_id",
        "idempotency_key",
        name="uq_idempotency_records_tenant_id_idempotency_key",
    ),
    CheckConstraint("state IN ('in_progress', 'completed')", name="state_valid"),
    CheckConstraint(
        "state <> 'completed' OR (resource_type IS NOT NULL AND resource_id IS NOT NULL "
        "AND response_status IS NOT NULL)",
        name="completed_has_result",
    ),
    CheckConstraint(
        f"length(idempotency_key) BETWEEN 1 AND {IDEMPOTENCY_KEY_MAX_LENGTH}",
        name="key_length",
    ),
    CheckConstraint("length(request_hash) = 64", name="request_hash_sha256"),
    Index("ix_idempotency_records_expires_at", "expires_at"),
)

outbox_events = Table(
    "outbox_events",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column(
        "tenant_id",
        UuidCol,
        ForeignKey(tenants.c.id, ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("event_type", String(120), nullable=False),
    Column("schema_version", Integer, nullable=False, server_default="1"),
    Column("aggregate_type", String(64), nullable=False),
    Column("aggregate_id", UuidCol, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("headers", JSONB, nullable=False, server_default="{}"),
    Column("correlation_id", String(128), nullable=False),
    Column("causation_id", String(128), nullable=True),
    Column("occurred_at", TimestampCol, nullable=False),
    Column("status", String(16), nullable=False, server_default="PENDING"),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    Column("available_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("claimed_at", TimestampCol, nullable=True),
    Column("claimed_by", String(128), nullable=True),
    Column("published_at", TimestampCol, nullable=True),
    Column("last_error", Text, nullable=True),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("updated_at", TimestampCol, nullable=False, server_default=func.now()),
    CheckConstraint(
        "status IN ('PENDING', 'CLAIMED', 'PUBLISHED', 'FAILED')",
        name="ck_outbox_events_status_valid",
    ),
    Index("ix_outbox_events_status_available_at", "status", "available_at"),
    Index("ix_outbox_events_tenant_id", "tenant_id"),
)

inbox_messages = Table(
    "inbox_messages",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column("message_id", String(128), nullable=False),
    Column("consumer_name", String(128), nullable=False),
    Column(
        "tenant_id",
        UuidCol,
        ForeignKey(tenants.c.id, ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("event_type", String(120), nullable=False),
    Column("status", String(16), nullable=False, server_default="RECEIVED"),
    Column("attempt_count", Integer, nullable=False, server_default="1"),
    Column("locked_by", String(128), nullable=True),
    Column("locked_at", TimestampCol, nullable=True),
    Column("lease_until", TimestampCol, nullable=True),
    Column("failed_at", TimestampCol, nullable=True),
    Column("last_error", Text, nullable=True),
    Column("received_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("processed_at", TimestampCol, nullable=True),
    UniqueConstraint("message_id", "consumer_name", name="uq_inbox_messages_message_consumer"),
    CheckConstraint(
        "status IN ('RECEIVED', 'PROCESSING', 'PROCESSED', 'FAILED')",
        name="ck_inbox_messages_status_valid",
    ),
    Index("ix_inbox_messages_status_lease_until", "status", "lease_until"),
    Index("ix_inbox_messages_tenant_id", "tenant_id"),
)

__all__ = [
    "IDEMPOTENCY_KEY_MAX_LENGTH",
    "idempotency_records",
    "inbox_messages",
    "outbox_events",
]
