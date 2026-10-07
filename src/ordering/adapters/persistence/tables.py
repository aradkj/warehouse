"""Physical schema for the ordering context.

Index decisions (see ``docs/architecture/data-model.md``):

* ``ix_orders_tenant_id_created_at_id`` / ``ix_orders_tenant_id_status_created_at_id``
  - the list endpoint's keyset pagination, mirroring the catalog decision.
* ``ix_order_lines_tenant_id_order_id`` - loading an order's lines.
* ``ix_order_lines_tenant_id_sku_id`` - "which orders contain this SKU", needed by
  reconciliation in Phase 3 and by support tooling.

``total_minor`` is deliberately **not** stored: the total is derived from the
line-level price snapshots, so a stored total could only ever disagree with the
lines it summarises.
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
    UniqueConstraint,
    func,
)

from catalog.adapters.persistence.tables import products, skus
from identity.adapters.persistence.tables import tenants
from ordering.domain.value_objects import MAX_ORDER_LINES, OrderStatus
from shared.infrastructure.db.base import metadata
from shared.infrastructure.db.types import TimestampCol, UuidCol

_ORDER_STATUS_LIST = ", ".join(f"'{status.value}'" for status in OrderStatus)

orders = Table(
    "orders",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column("tenant_id", UuidCol, ForeignKey(tenants.c.id, ondelete="RESTRICT"), nullable=False),
    #: Opaque reference from the sales channel (their order number, a customer id
    #: in their system, ...). Kept as text: it is not ours to interpret.
    Column("customer_reference", String(128), nullable=True),
    Column("currency", String(3), nullable=False),
    Column("status", String(24), nullable=False),
    Column("confirmed_at", TimestampCol, nullable=True),
    Column("cancelled_at", TimestampCol, nullable=True),
    Column("cancellation_reason", String(256), nullable=True),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("updated_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("version", Integer, nullable=False),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
    CheckConstraint(f"status IN ({_ORDER_STATUS_LIST})", name="status_valid"),
    CheckConstraint(
        "status <> 'CONFIRMED' OR confirmed_at IS NOT NULL", name="confirmed_has_timestamp"
    ),
    CheckConstraint(
        "status <> 'CANCELLED' OR (cancelled_at IS NOT NULL AND cancellation_reason IS NOT NULL)",
        name="cancelled_has_reason",
    ),
)

Index(
    "ix_orders_tenant_id_created_at_id",
    orders.c.tenant_id,
    orders.c.created_at.desc(),
    orders.c.id.desc(),
)
Index(
    "ix_orders_tenant_id_status_created_at_id",
    orders.c.tenant_id,
    orders.c.status,
    orders.c.created_at.desc(),
    orders.c.id.desc(),
)

order_lines = Table(
    "order_lines",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column("tenant_id", UuidCol, ForeignKey(tenants.c.id, ondelete="RESTRICT"), nullable=False),
    Column("order_id", UuidCol, ForeignKey(orders.c.id, ondelete="CASCADE"), nullable=False),
    Column("line_number", Integer, nullable=False),
    Column("sku_id", UuidCol, ForeignKey(skus.c.id, ondelete="RESTRICT"), nullable=False),
    Column("product_id", UuidCol, ForeignKey(products.c.id, ondelete="RESTRICT"), nullable=False),
    # --- pricing and identity snapshot (immutable once written) --------------
    Column("sku_code", String(64), nullable=False),
    Column("product_name", String(200), nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("unit_price_minor", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    UniqueConstraint("order_id", "line_number", name="uq_order_lines_order_id_line_number"),
    CheckConstraint("line_number >= 1", name="line_number_positive"),
    CheckConstraint("quantity > 0", name="quantity_positive"),
    CheckConstraint("unit_price_minor >= 0", name="unit_price_non_negative"),
    CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
)

Index("ix_order_lines_tenant_id_order_id", order_lines.c.tenant_id, order_lines.c.order_id)
Index("ix_order_lines_tenant_id_sku_id", order_lines.c.tenant_id, order_lines.c.sku_id)

__all__ = ["MAX_ORDER_LINES", "order_lines", "orders"]
