"""Physical schema for the fulfillment context."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    func,
    text,
)

from catalog.adapters.persistence.tables import skus
from identity.adapters.persistence.tables import tenants
from inventory.adapters.persistence.tables import warehouses
from ordering.adapters.persistence.tables import orders
from shared.infrastructure.db.base import metadata
from shared.infrastructure.db.types import TimestampCol, UuidCol

_FULFILLMENT_STATUS_LIST = (
    "'PENDING', 'ALLOCATED', 'IN_PROGRESS', 'SHIPPED', 'FAILED', 'COMPENSATED'"
)

fulfillments = Table(
    "fulfillments",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column(
        "tenant_id",
        UuidCol,
        ForeignKey(tenants.c.id, ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "order_id",
        UuidCol,
        ForeignKey(orders.c.id, ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "warehouse_id",
        UuidCol,
        ForeignKey(warehouses.c.id, ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("status", String(32), nullable=False),
    Column("tracking_number", String(128), nullable=True),
    Column("carrier", String(64), nullable=True),
    Column("failure_reason", Text, nullable=True),
    Column("retry_count", Integer, nullable=False, server_default="0"),
    Column("max_retries", Integer, nullable=False, server_default="3"),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("updated_at", TimestampCol, nullable=False, server_default=func.now()),
    CheckConstraint("version >= 1", name="ck_fulfillments_version_positive"),
    CheckConstraint("retry_count >= 0", name="ck_fulfillments_retry_count_non_negative"),
    CheckConstraint("max_retries >= 0", name="ck_fulfillments_max_retries_non_negative"),
    CheckConstraint(
        f"status IN ({_FULFILLMENT_STATUS_LIST})",
        name="ck_fulfillments_status_valid",
    ),
)

Index(
    "ix_fulfillments_tenant_id_order_id",
    fulfillments.c.tenant_id,
    fulfillments.c.order_id,
)
Index(
    "ix_fulfillments_tenant_id_created_at_id",
    fulfillments.c.tenant_id,
    fulfillments.c.created_at.desc(),
    fulfillments.c.id.desc(),
)
# Partial unique index: at most one active fulfillment per order
Index(
    "uq_fulfillments_active_per_order",
    fulfillments.c.tenant_id,
    fulfillments.c.order_id,
    unique=True,
    postgresql_where=text("status IN ('PENDING', 'ALLOCATED', 'IN_PROGRESS')"),
)

fulfillment_tasks = Table(
    "fulfillment_tasks",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column(
        "tenant_id",
        UuidCol,
        ForeignKey(tenants.c.id, ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "fulfillment_id",
        UuidCol,
        ForeignKey(fulfillments.c.id, ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "warehouse_id",
        UuidCol,
        ForeignKey(warehouses.c.id, ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "sku_id",
        UuidCol,
        ForeignKey(skus.c.id, ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("quantity", Integer, nullable=False),
    Column("is_completed", Boolean, nullable=False, server_default=text("false")),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    CheckConstraint("quantity > 0", name="ck_fulfillment_tasks_quantity_positive"),
)

Index(
    "ix_fulfillment_tasks_tenant_id_fulfillment_id",
    fulfillment_tasks.c.tenant_id,
    fulfillment_tasks.c.fulfillment_id,
)

__all__ = [
    "fulfillment_tasks",
    "fulfillments",
]
