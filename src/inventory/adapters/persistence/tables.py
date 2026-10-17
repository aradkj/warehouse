"""Physical schema for the inventory context.

This is where the platform's headline invariant is defended in depth:

* ``inventory_items.on_hand >= 0``
* ``inventory_items.reserved >= 0``
* ``inventory_items.reserved <= inventory_items.on_hand``  (i.e. ``available >= 0``)

The aggregates enforce the same rules first; the constraints make it impossible
for a future adapter, a manual ``psql`` session or a migration script to break
them silently.

Index decisions (measured, see ``docs/architecture/data-model.md``):

* ``uq_inventory_items_tenant_id_warehouse_id_sku_id`` - one stock row per
  SKU/warehouse pair. Also the arbiter for the upsert on first receipt.
* ``ix_inventory_items_tenant_id_sku_id`` - the reservation hot path
  (``WHERE tenant_id = :t AND sku_id = ANY(:skus) ... FOR UPDATE``).
* ``ix_inventory_items_tenant_id_created_at_id`` - availability pagination.
* ``ix_inventory_movements_tenant_id_inventory_item_id_occurred_at`` - "show me
  the ledger of this stock row, newest first".
* ``uq_reservations_active_per_order`` - a *partial* unique index that makes it
  impossible for one order to hold two active reservations at the same time.
  This is a business invariant expressed by the database: the second concurrent
  reservation for the same order fails with a unique violation instead of
  silently double-holding stock.
* ``ix_reservations_status_expires_at`` - the Phase 2 expiry sweeper scans
  ``WHERE status = 'ACTIVE' AND expires_at <= now()``.
"""

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
    UniqueConstraint,
    func,
    text,
)

from catalog.adapters.persistence.tables import skus
from identity.adapters.persistence.tables import tenants
from inventory.domain.value_objects import MovementType, ReservationStatus
from ordering.adapters.persistence.tables import orders
from shared.infrastructure.db.base import metadata
from shared.infrastructure.db.types import TimestampCol, UuidCol

_MOVEMENT_TYPE_LIST = ", ".join(f"'{value.value}'" for value in MovementType)
_RESERVATION_STATUS_LIST = ", ".join(f"'{value.value}'" for value in ReservationStatus)

warehouses = Table(
    "warehouses",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column("tenant_id", UuidCol, ForeignKey(tenants.c.id, ondelete="RESTRICT"), nullable=False),
    Column("code", String(32), nullable=False),
    Column("name", String(160), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("updated_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("version", Integer, nullable=False),
    UniqueConstraint("tenant_id", "code", name="uq_warehouses_tenant_id_code"),
    UniqueConstraint("tenant_id", "id", name="uq_warehouses_tenant_id_id"),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint("code ~ '^[A-Z0-9][A-Z0-9._-]{1,31}$'", name="code_format"),
    CheckConstraint("length(btrim(name)) BETWEEN 1 AND 160", name="name_length"),
)

Index(
    "ix_warehouses_tenant_id_created_at_id",
    warehouses.c.tenant_id,
    warehouses.c.created_at.desc(),
    warehouses.c.id.desc(),
)

inventory_items = Table(
    "inventory_items",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column("tenant_id", UuidCol, ForeignKey(tenants.c.id, ondelete="RESTRICT"), nullable=False),
    Column(
        "warehouse_id",
        UuidCol,
        ForeignKey(warehouses.c.id, ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("sku_id", UuidCol, ForeignKey(skus.c.id, ondelete="RESTRICT"), nullable=False),
    #: Physical units in the warehouse.
    Column("on_hand", Integer, nullable=False, server_default=text("0")),
    #: Units promised to active reservations.
    Column("reserved", Integer, nullable=False, server_default=text("0")),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("updated_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("version", Integer, nullable=False),
    UniqueConstraint(
        "tenant_id",
        "warehouse_id",
        "sku_id",
        name="uq_inventory_items_tenant_id_warehouse_id_sku_id",
    ),
    # Deliberately ``>= 0``: a stock row is *registered* (with no movement) the
    # first time a SKU is received into a warehouse, and registering is not a
    # business event, so the aggregate is legitimately persisted at version 0.
    # Every other aggregate is only written after its creation event (version 1+).
    CheckConstraint("version >= 0", name="version_non_negative"),
    CheckConstraint("on_hand >= 0", name="on_hand_non_negative"),
    CheckConstraint("reserved >= 0", name="reserved_non_negative"),
    # available = on_hand - reserved must never be negative.
    CheckConstraint("reserved <= on_hand", name="available_non_negative"),
)

Index("ix_inventory_items_tenant_id_sku_id", inventory_items.c.tenant_id, inventory_items.c.sku_id)
Index(
    "ix_inventory_items_tenant_id_warehouse_id",
    inventory_items.c.tenant_id,
    inventory_items.c.warehouse_id,
)
Index(
    "ix_inventory_items_tenant_id_created_at_id",
    inventory_items.c.tenant_id,
    inventory_items.c.created_at.desc(),
    inventory_items.c.id.desc(),
)

inventory_movements = Table(
    "inventory_movements",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column("tenant_id", UuidCol, ForeignKey(tenants.c.id, ondelete="RESTRICT"), nullable=False),
    Column(
        "inventory_item_id",
        UuidCol,
        ForeignKey(inventory_items.c.id, ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "warehouse_id", UuidCol, ForeignKey(warehouses.c.id, ondelete="RESTRICT"), nullable=False
    ),
    Column("sku_id", UuidCol, ForeignKey(skus.c.id, ondelete="RESTRICT"), nullable=False),
    Column("movement_type", String(32), nullable=False),
    Column("reason", String(32), nullable=True),
    #: Signed change: on_hand delta for receipts/adjustments, reserved delta for holds.
    Column("quantity_delta", Integer, nullable=False),
    #: Snapshot of the counters after the movement - makes the ledger replayable
    #: without recomputation and lets reconciliation detect gaps.
    Column("on_hand_after", Integer, nullable=False),
    Column("reserved_after", Integer, nullable=False),
    Column("reference_type", String(32), nullable=True),
    Column("reference_id", UuidCol, nullable=True),
    Column("note", String(256), nullable=True),
    Column("occurred_at", TimestampCol, nullable=False),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    CheckConstraint(f"movement_type IN ({_MOVEMENT_TYPE_LIST})", name="movement_type_valid"),
    CheckConstraint("quantity_delta <> 0", name="quantity_delta_non_zero"),
    CheckConstraint("on_hand_after >= 0", name="on_hand_after_non_negative"),
    CheckConstraint("reserved_after >= 0", name="reserved_after_non_negative"),
)

Index(
    "ix_inventory_movements_tenant_id_inventory_item_id_occurred_at",
    inventory_movements.c.tenant_id,
    inventory_movements.c.inventory_item_id,
    inventory_movements.c.occurred_at.desc(),
)
Index(
    "ix_inventory_movements_tenant_id_reference_type_reference_id",
    inventory_movements.c.tenant_id,
    inventory_movements.c.reference_type,
    inventory_movements.c.reference_id,
)

reservations = Table(
    "reservations",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column("tenant_id", UuidCol, ForeignKey(tenants.c.id, ondelete="RESTRICT"), nullable=False),
    # Cross-context reference: kept as a real foreign key inside the monolith so
    # referential integrity is enforced by PostgreSQL. When Ordering is extracted,
    # this becomes a plain identifier (see ADR-0009).
    Column("order_id", UuidCol, ForeignKey(orders.c.id, ondelete="RESTRICT"), nullable=True),
    Column("status", String(16), nullable=False),
    Column("expires_at", TimestampCol, nullable=False),
    Column("confirmed_at", TimestampCol, nullable=True),
    Column("released_at", TimestampCol, nullable=True),
    Column("release_reason", String(128), nullable=True),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("updated_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("version", Integer, nullable=False),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint(f"status IN ({_RESERVATION_STATUS_LIST})", name="status_valid"),
    CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
    CheckConstraint(
        "status <> 'CONFIRMED' OR confirmed_at IS NOT NULL", name="confirmed_has_timestamp"
    ),
    CheckConstraint(
        "status <> 'RELEASED' OR released_at IS NOT NULL", name="released_has_timestamp"
    ),
)

Index("ix_reservations_tenant_id_order_id", reservations.c.tenant_id, reservations.c.order_id)
Index("ix_reservations_status_expires_at", reservations.c.status, reservations.c.expires_at)
# At most one ACTIVE reservation per order - enforced by the database, not by a
# read-then-write in application code.
Index(
    "uq_reservations_active_per_order",
    reservations.c.tenant_id,
    reservations.c.order_id,
    unique=True,
    postgresql_where=text("status = 'ACTIVE' AND order_id IS NOT NULL"),
)

reservation_allocations = Table(
    "reservation_allocations",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column("tenant_id", UuidCol, ForeignKey(tenants.c.id, ondelete="RESTRICT"), nullable=False),
    Column(
        "reservation_id",
        UuidCol,
        ForeignKey(reservations.c.id, ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "inventory_item_id",
        UuidCol,
        ForeignKey(inventory_items.c.id, ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "warehouse_id", UuidCol, ForeignKey(warehouses.c.id, ondelete="RESTRICT"), nullable=False
    ),
    Column("sku_id", UuidCol, ForeignKey(skus.c.id, ondelete="RESTRICT"), nullable=False),
    #: Set when the hold belongs to one line of an order; NULL for standalone holds.
    Column("order_line_id", UuidCol, nullable=True),
    Column("quantity", Integer, nullable=False),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    CheckConstraint("quantity > 0", name="quantity_positive"),
)

Index(
    "ix_reservation_allocations_tenant_id_reservation_id",
    reservation_allocations.c.tenant_id,
    reservation_allocations.c.reservation_id,
)
Index(
    "ix_reservation_allocations_tenant_id_inventory_item_id",
    reservation_allocations.c.tenant_id,
    reservation_allocations.c.inventory_item_id,
)

__all__ = [
    "inventory_items",
    "inventory_movements",
    "reservation_allocations",
    "reservations",
    "warehouses",
]
