"""Migration tests: the schema must be reproducible from an empty database.

The migration is the only thing that creates the schema in a fresh environment, so
"it works on my machine" is not good enough: these tests build the whole schema on a
scratch database, prove the models and the migration agree (``compare_metadata``
reports no drift), check that the constraints that protect the domain invariants are
really in PostgreSQL, and prove the migration can be rolled all the way back and
reapplied.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from tests.conftest import test_database_url as resolve_database_url

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRATCH_DATABASE = "arad_migration_test"


def _admin_url(database: str) -> str:
    return resolve_database_url().rsplit("/", 1)[0] + f"/{database}"


def _alembic_config() -> Any:
    from alembic.config import Config

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return config


def _run_alembic(action: str, url: str, target: str | None = None) -> None:
    """Run an alembic command against ``url``, in a thread.

    ``migrations/env.py`` drives an async engine and calls ``asyncio.run`` internally,
    which cannot be done from inside a running event loop - hence the thread.
    """
    from alembic import command

    config = _alembic_config()
    previous = os.environ.get("ARAD_DATABASE_URL")
    os.environ["ARAD_DATABASE_URL"] = url
    try:
        if action == "upgrade":
            command.upgrade(config, target or "head")
        elif action == "downgrade":
            command.downgrade(config, target or "base")
        else:  # pragma: no cover - guard against typos
            raise AssertionError(action)
    finally:
        if previous is None:
            os.environ.pop("ARAD_DATABASE_URL", None)
        else:
            os.environ["ARAD_DATABASE_URL"] = previous


@pytest.fixture
async def scratch_database() -> Any:
    """An empty database, dropped again afterwards."""
    admin = create_async_engine(_admin_url("postgres").replace("/postgres", "/arad_test"))
    scratch_url = _admin_url(SCRATCH_DATABASE)
    # CREATE/DROP DATABASE cannot run inside a transaction block.
    admin_connection = await admin.connect()
    autocommit = await admin_connection.execution_options(isolation_level="AUTOCOMMIT")
    try:
        await autocommit.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH_DATABASE}"'))
        await autocommit.execute(text(f'CREATE DATABASE "{SCRATCH_DATABASE}"'))
    finally:
        await admin_connection.close()

    engine = create_async_engine(scratch_url)
    try:
        yield engine
    finally:
        await engine.dispose()
        connection = await admin.connect()
        autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
        try:
            await autocommit.execute(
                text(f'DROP DATABASE IF EXISTS "{SCRATCH_DATABASE}" WITH (FORCE)')
            )
        finally:
            await connection.close()
        await admin.dispose()


async def table_names(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        return {row[0] for row in result}


EXPECTED_TABLES = {
    "alembic_version",
    "tenants",
    "products",
    "skus",
    "warehouses",
    "inventory_items",
    "inventory_movements",
    "reservations",
    "reservation_allocations",
    "orders",
    "order_lines",
    "idempotency_records",
    "outbox_events",
    "inbox_messages",
    "users",
    "tenant_memberships",
    "user_sessions",
    "service_accounts",
    "roles",
    "role_permissions",
    "membership_roles",
    "service_account_scopes",
    "access_policies",
    "audit_logs",
}


class TestMigrations:
    async def test_the_migration_builds_the_complete_schema_from_empty(
        self, scratch_database: AsyncEngine
    ) -> None:
        scratch_url = str(scratch_database.url.render_as_string(hide_password=False))
        assert await table_names(scratch_database) == set(), "the scratch database starts empty"

        await asyncio.to_thread(_run_alembic, "upgrade", scratch_url)

        assert await table_names(scratch_database) == EXPECTED_TABLES

        async with scratch_database.connect() as connection:
            version = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar()
        assert version == "0009_harden_rls_and_roles"

    async def test_the_models_and_the_migration_agree(self, scratch_database: AsyncEngine) -> None:
        """Autogenerate must find no difference between metadata and the migrated schema."""
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        import shared.infrastructure.db.registry  # noqa: F401
        from shared.infrastructure.db.base import metadata

        scratch_url = str(scratch_database.url.render_as_string(hide_password=False))
        await asyncio.to_thread(_run_alembic, "upgrade", scratch_url)

        async with scratch_database.connect() as connection:
            diffs = await connection.run_sync(
                lambda sync_connection: compare_metadata(
                    MigrationContext.configure(sync_connection), metadata
                )
            )

        assert diffs == [], f"schema drift detected: {diffs}"

    async def test_the_migration_round_trips(self, scratch_database: AsyncEngine) -> None:
        """Reversible migrations (0001-0008) can round-trip to base; 0009 safely prohibits downgrade."""
        scratch_url = str(scratch_database.url.render_as_string(hide_password=False))

        # 1. Migrations up to 0008 can round-trip to base
        await asyncio.to_thread(_run_alembic, "upgrade", scratch_url, "0008_postgresql_rls")
        await asyncio.to_thread(_run_alembic, "downgrade", scratch_url, "base")
        remaining = await table_names(scratch_database)
        assert remaining == {"alembic_version"}, f"downgrade left tables behind: {remaining}"

        # 2. Upgrade to head (0009_harden_rls_and_roles)
        await asyncio.to_thread(_run_alembic, "upgrade", scratch_url, "head")
        assert await table_names(scratch_database) == EXPECTED_TABLES

        # 3. Attempting downgrade on 0009 is strictly refused with a security exception
        with pytest.raises(
            RuntimeError,
            match="Downgrading migration 0009_harden_rls_and_roles is strictly prohibited",
        ):
            await asyncio.to_thread(_run_alembic, "downgrade", scratch_url, "-1")

        # 4. Schema, version, and policies remain consistent and secure after rejected downgrade
        async with scratch_database.connect() as connection:
            version = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar()
            assert version == "0009_harden_rls_and_roles"

            # Verify no policies retain bypass_rls
            result = await connection.execute(
                text("SELECT policyname FROM pg_policies WHERE qual ILIKE '%bypass_rls%'")
            )
            assert result.all() == [], "Policies must not contain bypass_rls after rejected downgrade"

        assert await table_names(scratch_database) == EXPECTED_TABLES

    async def test_the_test_database_is_at_head(self, database: AsyncEngine) -> None:
        """The suite runs against a migrated database, never a hand-made one."""
        from alembic.script import ScriptDirectory

        head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
        async with database.connect() as connection:
            version = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar()
        assert version == head


class TestSchemaGuarantees:
    """The database is the last line of defence for the domain invariants."""

    @pytest.fixture(autouse=True)
    def _require_migrated_schema(self, database: AsyncEngine) -> Iterator[None]:
        yield

    async def constraints(self, engine: AsyncEngine, kind: str) -> dict[str, str]:
        """Constraints defined on the application's own tables.

        The namespace filter matters: without it this query also returns the catalog's
        own constraints, and a test that "finds" ``cardinal_number_domain_check``
        proves nothing about the schema.
        """
        # ``contype`` is PostgreSQL's internal ``"char"`` type, which asyncpg will not
        # accept a text parameter for; the value is validated against an allow-list
        # instead of being interpolated blindly.
        if kind not in {"c", "f", "p", "u"}:
            raise ValueError(f"unsupported constraint type: {kind!r}")
        statement = text(
            "SELECT con.conname, pg_get_constraintdef(con.oid)"
            " FROM pg_constraint con"
            " JOIN pg_class rel ON rel.oid = con.conrelid"
            " JOIN pg_namespace ns ON ns.oid = rel.relnamespace"
            f" WHERE con.contype = '{kind}' AND ns.nspname = 'public'"
        )
        async with engine.connect() as connection:
            result = await connection.execute(statement)
            return {row[0]: row[1] for row in result}

    async def indexes(self, engine: AsyncEngine) -> dict[str, str]:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'")
            )
            return {row[0]: row[1] for row in result}

    async def test_foreign_keys_link_the_tenants_and_aggregates(
        self, database: AsyncEngine
    ) -> None:
        checks = await self.constraints(database, "f")
        for expected in (
            "fk_products_tenant_id_tenants",
            "fk_skus_product_id_products",
            "fk_skus_tenant_id_tenants",
            "fk_warehouses_tenant_id_tenants",
            "fk_inventory_items_tenant_id_tenants",
            "fk_inventory_items_sku_id_skus",
            "fk_inventory_items_warehouse_id_warehouses",
            "fk_inventory_movements_inventory_item_id_inventory_items",
            "fk_reservations_tenant_id_tenants",
            "fk_reservations_order_id_orders",
            "fk_reservation_allocations_reservation_id_reservations",
            "fk_orders_tenant_id_tenants",
            "fk_order_lines_order_id_orders",
            "fk_idempotency_records_tenant_id_tenants",
        ):
            assert expected in checks, f"missing foreign key {expected}"

    async def test_check_constraints_protect_the_inventory_invariant(
        self, database: AsyncEngine
    ) -> None:
        checks = await self.constraints(database, "c")
        available = checks["ck_inventory_items_available_non_negative"]
        assert "reserved" in available and "on_hand" in available, available
        assert "ck_inventory_items_on_hand_non_negative" in checks
        assert "ck_inventory_items_reserved_non_negative" in checks
        # Registration rows are created at version 0, so the guard is ">= 0".
        assert "ck_inventory_items_version_non_negative" in checks

    async def test_check_constraints_protect_orders_and_reservations(
        self, database: AsyncEngine
    ) -> None:
        checks = await self.constraints(database, "c")
        for expected in (
            "ck_order_lines_quantity_positive",
            "ck_order_lines_line_number_positive",
            "ck_order_lines_unit_price_non_negative",
            "ck_order_lines_currency_format",
            "ck_orders_status_valid",
            "ck_orders_version_positive",
            "ck_orders_confirmed_has_timestamp",
            "ck_orders_cancelled_has_reason",
            "ck_reservations_status_valid",
            "ck_reservations_version_positive",
            "ck_reservations_expiry_after_creation",
            "ck_reservations_confirmed_has_timestamp",
            "ck_reservations_released_has_timestamp",
            "ck_reservation_allocations_quantity_positive",
            "ck_inventory_movements_quantity_delta_non_zero",
            "ck_inventory_movements_on_hand_after_non_negative",
            "ck_inventory_movements_reserved_after_non_negative",
            "ck_idempotency_records_key_length",
            "ck_idempotency_records_state_valid",
            "ck_idempotency_records_completed_has_result",
            "ck_skus_unit_price_non_negative",
        ):
            assert expected in checks, f"missing check constraint {expected}"

    async def test_uniqueness_is_enforced_by_the_database(self, database: AsyncEngine) -> None:
        checks = await self.constraints(database, "u")
        for expected in (
            "uq_tenants_slug",
            "uq_products_tenant_id_slug",
            "uq_skus_tenant_id_code",
            "uq_warehouses_tenant_id_code",
            "uq_inventory_items_tenant_id_warehouse_id_sku_id",
            "uq_order_lines_order_id_line_number",
        ):
            assert expected in checks, f"missing unique constraint {expected}"

    async def test_idempotency_is_unique_per_tenant_and_key(self, database: AsyncEngine) -> None:
        checks = await self.constraints(database, "u")
        definition = checks["uq_idempotency_records_tenant_id_idempotency_key"]
        assert "tenant_id" in definition and "idempotency_key" in definition

    async def test_an_order_can_hold_at_most_one_active_reservation(
        self, database: AsyncEngine
    ) -> None:
        """A partial unique index, because "at most one ACTIVE per order" is partial."""
        found = await self.indexes(database)
        definition = found["uq_reservations_active_per_order"]
        assert "UNIQUE" in definition.upper()
        assert "order_id" in definition
        assert "ACTIVE" in definition, definition

    async def test_every_tenant_owned_table_carries_a_tenant_id(
        self, database: AsyncEngine
    ) -> None:
        """Phase 1's isolation foundation: ownership is a column, not a convention."""
        async with database.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT table_name FROM information_schema.columns"
                    " WHERE column_name = 'tenant_id' AND table_schema = 'public'"
                )
            )
            with_tenant = {row[0] for row in result}

        assert {
            "products",
            "skus",
            "warehouses",
            "inventory_items",
            "inventory_movements",
            "reservations",
            "orders",
            "idempotency_records",
        } <= with_tenant

    async def test_timestamps_and_version_columns_exist(self, database: AsyncEngine) -> None:
        """Optimistic concurrency and auditability depend on these columns."""
        async with database.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT table_name, column_name, is_nullable, data_type"
                    " FROM information_schema.columns"
                    " WHERE table_schema = 'public'"
                    " AND column_name IN ('created_at', 'updated_at', 'version')"
                )
            )
            columns = {(row[0], row[1]): (row[2], row[3]) for row in result}

        for table in (
            "products",
            "skus",
            "warehouses",
            "inventory_items",
            "reservations",
            "orders",
        ):
            assert columns[(table, "created_at")][0] == "NO"
            assert columns[(table, "updated_at")][0] == "NO"
            assert columns[(table, "version")][1] == "integer"

    async def test_timestamps_are_timezone_aware(self, database: AsyncEngine) -> None:
        """Naive timestamps are a bug waiting to happen: every column is ``timestamptz``."""
        async with database.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT table_name FROM information_schema.columns"
                    " WHERE table_schema = 'public'"
                    " AND column_name IN ('created_at', 'updated_at', 'expires_at', 'occurred_at')"
                    " AND data_type <> 'timestamp with time zone'"
                )
            )
            offenders = {row[0] for row in result}
        assert not offenders, f"naive timestamp columns: {sorted(offenders)}"

    async def test_money_is_stored_as_integer_minor_units(self, database: AsyncEngine) -> None:
        """No floating point ever touches money, and no amount is stored as text."""
        async with database.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT table_name, column_name, data_type FROM information_schema.columns"
                    " WHERE table_schema = 'public' AND column_name LIKE '%minor%'"
                )
            )
            rows = {(row[0], row[1]): row[2] for row in result}
            approximate = await connection.execute(
                text(
                    "SELECT table_name, column_name, data_type FROM information_schema.columns"
                    " WHERE table_schema = 'public'"
                    " AND data_type IN ('real', 'double precision', 'numeric')"
                )
            )
            floating = {(row[0], row[1]): row[2] for row in approximate}

        assert rows[("skus", "unit_price_minor")] == "integer"
        assert rows[("order_lines", "unit_price_minor")] == "integer"
        assert floating == {}, f"catch-weight money must use integer minor units: {floating}"

    async def test_availability_queries_have_a_supporting_index(
        self, database: AsyncEngine
    ) -> None:
        """The availability endpoint filters by SKU and warehouse: both are indexed."""
        found = await self.indexes(database)
        assert any(
            "inventory_items" in name and "sku_id" in definition
            for name, definition in found.items()
        ), "no index supports the availability query"
        assert any(
            "inventory_items" in name and "warehouse_id" in definition
            for name, definition in found.items()
        ), "no index supports the per-warehouse query"

    async def test_index_plan_for_the_availability_query(self, database: AsyncEngine) -> None:
        """``EXPLAIN`` the hot query and require an index scan, not a sequential scan.

        With an empty table PostgreSQL prefers a sequential scan, so the plan is
        inspected with ``enable_seqscan`` off: that proves an index *can* serve the
        query without depending on table statistics.
        """
        async with database.connect() as connection:
            await connection.execute(text("SET LOCAL enable_seqscan = off"))
            plan = (
                await connection.execute(
                    text(
                        "EXPLAIN SELECT on_hand, reserved FROM inventory_items"
                        " WHERE tenant_id = '00000000-0000-4000-8000-000000000000'"
                        " AND sku_id = '00000000-0000-4000-8000-000000000000'"
                    )
                )
            ).scalars()
            rendered = " ".join(str(line) for line in plan)
        assert "Seq Scan" not in rendered
        assert "inventory_items" in rendered
