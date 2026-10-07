"""Measure the hot queries with ``EXPLAIN (ANALYZE, BUFFERS)``.

Index decisions in Phase 1 are *measured*, not guessed (see
``docs/architecture/data-model.md``). This script seeds a throwaway database with a
realistic volume, then reports the plan and the timing of the queries the API
actually runs, so the documented numbers can be reproduced on any machine:

    python scripts/measure_indexes.py --rows 50000

It is a developer tool, not part of the application: it talks to PostgreSQL
directly, creates its own scratch database and drops it again unless ``--keep`` is
given. Nothing here is imported by ``src/`` or by the test suite.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

SCRATCH_DATABASE = "arad_index_measurements"

#: Tables whose seeded volume is reported after seeding.
SEEDED_TABLES = (
    "inventory_items",
    "inventory_movements",
    "orders",
    "order_lines",
    "reservations",
    "idempotency_records",
)

QUERIES: tuple[tuple[str, str], ...] = (
    (
        "availability for one SKU (GET /v1/inventory/availability?sku_id=...)",
        "SELECT id, warehouse_id, sku_id, on_hand, reserved FROM inventory_items"
        " WHERE tenant_id = :tenant AND sku_id = :sku"
        " ORDER BY created_at DESC, id DESC LIMIT 50",
    ),
    (
        "availability for one warehouse",
        "SELECT id, sku_id, on_hand, reserved FROM inventory_items"
        " WHERE tenant_id = :tenant AND warehouse_id = :warehouse"
        " ORDER BY created_at DESC, id DESC LIMIT 50",
    ),
    (
        "newest-first order page (GET /v1/orders)",
        "SELECT id, status, customer_reference FROM orders"
        " WHERE tenant_id = :tenant ORDER BY created_at DESC, id DESC LIMIT 50",
    ),
    (
        "order page filtered by status",
        "SELECT id, customer_reference FROM orders"
        " WHERE tenant_id = :tenant AND status = 'CONFIRMED'"
        " ORDER BY created_at DESC, id DESC LIMIT 50",
    ),
    (
        "stock movements of one item (audit read)",
        "SELECT id, movement_type, quantity_delta FROM inventory_movements"
        " WHERE tenant_id = :tenant AND inventory_item_id = :item"
        " ORDER BY occurred_at DESC LIMIT 100",
    ),
    (
        "reservation lookup for an order (order confirmation path)",
        "SELECT id, status, expires_at FROM reservations"
        " WHERE tenant_id = :tenant AND order_id = :order",
    ),
    (
        "idempotency claim lookup (every mutation)",
        "SELECT id, state, resource_id FROM idempotency_records"
        " WHERE tenant_id = :tenant AND idempotency_key = :key",
    ),
)

SEED_TENANTS = """
INSERT INTO tenants (id, slug, name, status, created_at, updated_at, version)
VALUES ('11111111-1111-4111-8111-111111111111', 'user', 'Seed', 'ACTIVE', now(), now(), 1),
       ('22222222-2222-4222-8222-222222222222', 'other', 'Other', 'ACTIVE', now(), now(), 1)
"""

SEED_PRODUCT = """
INSERT INTO products (id, tenant_id, slug, name, status, attributes, created_at,
                      updated_at, version)
VALUES ('33333333-3333-4333-8333-333333333333', '11111111-1111-4111-8111-111111111111',
        'seed-product', 'Seed', 'ACTIVE', '{}'::jsonb, now(), now(), 1)
"""

SEED_SKUS = """
INSERT INTO skus (id, tenant_id, product_id, code, name, status, unit_price_minor,
                  currency, attributes, created_at, updated_at, version)
SELECT gen_random_uuid(), '11111111-1111-4111-8111-111111111111'::uuid,
       '33333333-3333-4333-8333-333333333333'::uuid, 'SEED-' || g, 'Seed SKU ' || g,
       'ACTIVE', 1000, 'GBP', '{}'::jsonb, now() - (g || ' seconds')::interval, now(), 1
FROM generate_series(1, {rows}) AS g
"""

SEED_WAREHOUSE = """
INSERT INTO warehouses (id, tenant_id, code, name, is_active, created_at, updated_at,
                        version)
VALUES ('55555555-5555-4555-8555-555555555555', '11111111-1111-4111-8111-111111111111',
        'SEED-W', 'Seed', true, now(), now(), 1)
"""

#: One stock row per SKU in one warehouse: ``inventory_items`` is unique on
#: ``(tenant_id, warehouse_id, sku_id)``, so volume comes from many SKUs, exactly as
#: it does in production. ``{rows}`` is substituted from a validated integer.
SEED_ITEMS = """
INSERT INTO inventory_items (id, tenant_id, warehouse_id, sku_id, on_hand, reserved,
                             created_at, updated_at, version)
SELECT gen_random_uuid(),
       CASE WHEN g % 8 = 0 THEN '22222222-2222-4222-8222-222222222222'::uuid
            ELSE '11111111-1111-4111-8111-111111111111'::uuid END,
       '55555555-5555-4555-8555-555555555555'::uuid,
       s.id, (g % 50) + 1, 0, now() - (g || ' seconds')::interval, now(), 1
FROM generate_series(1, {rows}) AS g
JOIN skus s ON s.code = 'SEED-' || g
"""

SEED_MOVEMENTS = """
INSERT INTO inventory_movements (id, tenant_id, inventory_item_id, warehouse_id, sku_id,
                                 movement_type, reason, quantity_delta, on_hand_after,
                                 reserved_after, reference_type, reference_id, note,
                                 occurred_at, created_at)
SELECT gen_random_uuid(), i.tenant_id, i.id, i.warehouse_id, i.sku_id, 'RECEIPT',
       'GOODS_RECEIPT', 1, i.on_hand, 0, NULL, NULL, NULL, i.created_at, i.created_at
FROM inventory_items i
WHERE i.tenant_id = '11111111-1111-4111-8111-111111111111'::uuid
"""

SEED_ORDERS = """
INSERT INTO orders (id, tenant_id, customer_reference, currency, status, confirmed_at,
                    created_at, updated_at, version)
SELECT gen_random_uuid(),
       CASE WHEN g % 8 = 0 THEN '22222222-2222-4222-8222-222222222222'::uuid
            ELSE '11111111-1111-4111-8111-111111111111'::uuid END,
       'PO-' || g, 'GBP',
       CASE WHEN g % 3 = 0 THEN 'CONFIRMED' ELSE 'PENDING' END,
       CASE WHEN g % 3 = 0 THEN now() ELSE NULL END,
       now() - (g || ' seconds')::interval, now(), 1
FROM generate_series(1, {rows}) AS g
"""

SEED_ORDER_LINES = """
INSERT INTO order_lines (id, tenant_id, order_id, line_number, sku_id, product_id,
                         sku_code, product_name, quantity, unit_price_minor, currency,
                         created_at)
SELECT gen_random_uuid(), o.tenant_id, o.id, 1,
       (SELECT id FROM skus ORDER BY code LIMIT 1),
       '33333333-3333-4333-8333-333333333333'::uuid,
       'SEED-1', 'Seed', 1, 2500, 'GBP', o.created_at
FROM orders o WHERE o.tenant_id = '11111111-1111-4111-8111-111111111111'::uuid
"""

SEED_RESERVATIONS = """
INSERT INTO reservations (id, tenant_id, order_id, status, expires_at, created_at,
                          updated_at, version)
SELECT gen_random_uuid(), o.tenant_id, o.id, 'ACTIVE', now() + interval '1 day',
       o.created_at, o.updated_at, 1
FROM orders o WHERE o.tenant_id = '11111111-1111-4111-8111-111111111111'::uuid
"""

SEED_IDEMPOTENCY = """
INSERT INTO idempotency_records (id, tenant_id, idempotency_key, operation, request_hash,
                                 state, resource_type, resource_id, response_status,
                                 created_at, updated_at, expires_at)
SELECT gen_random_uuid(), '11111111-1111-4111-8111-111111111111'::uuid, 'key-' || g,
       'ordering.create_order', repeat('a', 64), 'completed', 'order',
       gen_random_uuid(), 201, now(), now(), now() + interval '7 days'
FROM generate_series(1, {rows}) AS g
"""

#: Seed statements in dependency order; ``{rows}`` is substituted once per statement.
SEED_SCRIPTS: tuple[str, ...] = (
    SEED_TENANTS,
    SEED_PRODUCT,
    SEED_SKUS,
    SEED_WAREHOUSE,
    SEED_ITEMS,
    SEED_MOVEMENTS,
    SEED_ORDERS,
    SEED_ORDER_LINES,
    SEED_RESERVATIONS,
    SEED_IDEMPOTENCY,
)


def admin_url(database: str) -> str:
    base = os.environ.get("ARAD_DATABASE_URL", "postgresql+asyncpg://arad:arad@127.0.0.1:5432/arad")
    return base.rsplit("/", 1)[0] + f"/{database}"


async def recreate_scratch_database() -> None:
    engine = create_async_engine(admin_url("postgres"))
    connection = await engine.connect()
    autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
    try:
        await autocommit.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH_DATABASE}" WITH (FORCE)'))
        await autocommit.execute(text(f'CREATE DATABASE "{SCRATCH_DATABASE}"'))
    finally:
        await connection.close()
        await engine.dispose()


async def drop_scratch_database() -> None:
    engine = create_async_engine(admin_url("postgres"))
    connection = await engine.connect()
    autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
    try:
        await autocommit.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH_DATABASE}" WITH (FORCE)'))
    finally:
        await connection.close()
        await engine.dispose()


async def migrate() -> None:
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    previous = os.environ.get("ARAD_DATABASE_URL")
    os.environ["ARAD_DATABASE_URL"] = admin_url(SCRATCH_DATABASE)
    try:
        await asyncio.to_thread(command.upgrade, config, "head")
    finally:
        if previous is None:
            os.environ.pop("ARAD_DATABASE_URL", None)
        else:
            os.environ["ARAD_DATABASE_URL"] = previous


async def seed(engine: AsyncEngine, rows: int) -> dict[str, Any]:
    """Fill the scratch database with ``rows`` rows per owning table."""
    if rows < 1:
        raise SystemExit("--rows must be a positive integer")
    async with engine.begin() as connection:
        for script in SEED_SCRIPTS:
            # ``str.replace`` rather than ``str.format``: the seed SQL contains
            # literal JSON braces, which ``format`` would try to interpret.
            await connection.execute(text(script.replace("{rows}", str(rows))))
        await connection.execute(text("ANALYZE"))
    async with engine.connect() as connection:
        counts: dict[str, int] = {}
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT relname, n_live_tup FROM pg_stat_user_tables WHERE relname = ANY(:names)"),
            {"names": list(SEEDED_TABLES)},
        )
        counts = {row[0]: row[1] for row in result}
    return counts


async def explain(engine: AsyncEngine) -> None:
    tenant = "11111111-1111-4111-8111-111111111111"
    params = {
        "tenant": tenant,
        "sku": None,
        "warehouse": "55555555-5555-4555-8555-555555555555",
        "order": None,
        "item": None,
        "key": "key-42",
    }
    async with engine.connect() as connection:
        params["sku"] = (
            await connection.execute(text("SELECT id FROM skus ORDER BY code LIMIT 1"), {})
        ).scalar()
        params["order"] = (
            await connection.execute(
                text("SELECT id FROM orders WHERE tenant_id = :t LIMIT 1"), {"t": tenant}
            )
        ).scalar()
        params["item"] = (
            await connection.execute(
                text("SELECT id FROM inventory_items WHERE sku_id = :s LIMIT 1"),
                {"s": params["sku"]},
            )
        ).scalar()

        for label, sql in QUERIES:
            result = await connection.execute(text("EXPLAIN (ANALYZE, BUFFERS) " + sql), params)
            plan = [row[0] for row in result]
            execution = next((line for line in plan if "Execution Time" in line), "n/a")
            first = plan[0].strip()
            detailed = "Seq Scan" in "\n".join(plan)
            print(f"\n=== {label}")
            print(f"    {execution.strip()}")
            print(f"    top node : {first}")
            print(f"    full plan ({'SEQ SCAN PRESENT' if detailed else 'index access'}):")
            for line in plan[:6]:
                print(f"      {line}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=50_000, help="rows per seeded table")
    parser.add_argument("--keep", action="store_true", help="keep the scratch database")
    arguments = parser.parse_args()

    await recreate_scratch_database()
    await migrate()
    engine = create_async_engine(admin_url(SCRATCH_DATABASE))
    try:
        counts = await seed(engine, arguments.rows)
        print("seeded rows:")
        for table, value in counts.items():
            print(f"  {table:24s} {value}")
        await explain(engine)
    finally:
        await engine.dispose()
        if arguments.keep:
            print(f"\nkept database {SCRATCH_DATABASE}")
        else:
            await drop_scratch_database()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
