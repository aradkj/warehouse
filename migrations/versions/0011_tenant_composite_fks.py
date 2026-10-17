"""Enforce tenant-aware composite foreign keys for fulfillments and tasks.

Revision ID: 0011_tenant_composite_fks
Revises: 0010_durable_fulfillment

Enforces cross-tenant isolation at the database constraint layer:
1. Adds UNIQUE (tenant_id, id) constraints to orders, warehouses, skus, and fulfillments.
2. Replaces single-column foreign keys with composite (tenant_id, reference_id) foreign keys
   on fulfillments (order_id, warehouse_id) and fulfillment_tasks (fulfillment_id, warehouse_id, sku_id).
This guarantees that PostgreSQL rejects any record referencing an entity owned by another tenant,
even if application or RLS checks were somehow bypassed.
"""

from __future__ import annotations

from alembic import op

revision: str = "0011_tenant_composite_fks"
down_revision: str | None = "0010_durable_fulfillment"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 1. Add UNIQUE constraints on referenced tables for composite foreign keys
    op.create_unique_constraint("uq_orders_tenant_id_id", "orders", ["tenant_id", "id"])
    op.create_unique_constraint("uq_warehouses_tenant_id_id", "warehouses", ["tenant_id", "id"])
    op.create_unique_constraint("uq_skus_tenant_id_id", "skus", ["tenant_id", "id"])
    op.create_unique_constraint("uq_fulfillments_tenant_id_id", "fulfillments", ["tenant_id", "id"])

    # 2. Upgrade fulfillments foreign keys to composite tenant-aware constraints
    op.drop_constraint("fk_fulfillments_order_id_orders", "fulfillments", type_="foreignkey")
    op.drop_constraint(
        "fk_fulfillments_warehouse_id_warehouses", "fulfillments", type_="foreignkey"
    )

    op.create_foreign_key(
        "fk_fulfillments_tenant_id_order_id_orders",
        "fulfillments",
        "orders",
        ["tenant_id", "order_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_fulfillments_tenant_id_warehouse_id_warehouses",
        "fulfillments",
        "warehouses",
        ["tenant_id", "warehouse_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )

    # 3. Upgrade fulfillment_tasks foreign keys to composite tenant-aware constraints
    op.drop_constraint(
        "fk_fulfillment_tasks_fulfillment_id_fulfillments", "fulfillment_tasks", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_fulfillment_tasks_warehouse_id_warehouses", "fulfillment_tasks", type_="foreignkey"
    )
    op.drop_constraint("fk_fulfillment_tasks_sku_id_skus", "fulfillment_tasks", type_="foreignkey")

    op.create_foreign_key(
        "fk_fulfillment_tasks_tenant_id_fulfillment_id_fulfillments",
        "fulfillment_tasks",
        "fulfillments",
        ["tenant_id", "fulfillment_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_fulfillment_tasks_tenant_id_warehouse_id_warehouses",
        "fulfillment_tasks",
        "warehouses",
        ["tenant_id", "warehouse_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_fulfillment_tasks_tenant_id_sku_id_skus",
        "fulfillment_tasks",
        "skus",
        ["tenant_id", "sku_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    # 1. Revert fulfillment_tasks to single-column foreign keys
    op.drop_constraint(
        "fk_fulfillment_tasks_tenant_id_sku_id_skus", "fulfillment_tasks", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_fulfillment_tasks_tenant_id_warehouse_id_warehouses",
        "fulfillment_tasks",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_fulfillment_tasks_tenant_id_fulfillment_id_fulfillments",
        "fulfillment_tasks",
        type_="foreignkey",
    )

    op.create_foreign_key(
        "fk_fulfillment_tasks_sku_id_skus",
        "fulfillment_tasks",
        "skus",
        ["sku_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_fulfillment_tasks_warehouse_id_warehouses",
        "fulfillment_tasks",
        "warehouses",
        ["warehouse_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_fulfillment_tasks_fulfillment_id_fulfillments",
        "fulfillment_tasks",
        "fulfillments",
        ["fulfillment_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 2. Revert fulfillments to single-column foreign keys
    op.drop_constraint(
        "fk_fulfillments_tenant_id_warehouse_id_warehouses", "fulfillments", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_fulfillments_tenant_id_order_id_orders", "fulfillments", type_="foreignkey"
    )

    op.create_foreign_key(
        "fk_fulfillments_warehouse_id_warehouses",
        "fulfillments",
        "warehouses",
        ["warehouse_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_fulfillments_order_id_orders",
        "fulfillments",
        "orders",
        ["order_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # 3. Drop UNIQUE constraints
    op.drop_constraint("uq_fulfillments_tenant_id_id", "fulfillments", type_="unique")
    op.drop_constraint("uq_skus_tenant_id_id", "skus", type_="unique")
    op.drop_constraint("uq_warehouses_tenant_id_id", "warehouses", type_="unique")
    op.drop_constraint("uq_orders_tenant_id_id", "orders", type_="unique")
