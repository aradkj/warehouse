"""Direct database assertions.

Some guarantees can only be *proved* by looking at PostgreSQL itself - that the
stock row really holds the counters we think it does, that a replayed request did
not insert a second order, that an idempotency record is committed. These helpers
keep those checks short and explicit; they are read-only on purpose, because
writing through SQL would bypass the very repositories under test.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def scalar(engine: AsyncEngine, sql: str, **params: Any) -> Any:
    """Run a single-value query."""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
        result = await connection.execute(text(sql), params)
        return result.scalar()


async def rows(engine: AsyncEngine, sql: str, **params: Any) -> list[dict[str, Any]]:
    """Run a query and return the rows as dictionaries."""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
        result = await connection.execute(text(sql), params)
        return [dict(row) for row in result.mappings().all()]


async def count(engine: AsyncEngine, table: str, **filters: Any) -> int:
    """``SELECT count(*)`` with simple equality filters."""
    where = ""
    if filters:
        clauses = " AND ".join(f"{column} = :{column}" for column in filters)
        where = f" WHERE {clauses}"
    value = await scalar(engine, f"SELECT count(*) FROM {table}{where}", **filters)
    return int(value or 0)


async def table_row(engine: AsyncEngine, table: str, **filters: Any) -> dict[str, Any] | None:
    """First matching row of a table, or ``None``."""
    clauses = " AND ".join(f"{column} = :{column}" for column in filters)
    where = f" WHERE {clauses}" if clauses else ""
    found = await rows(engine, f"SELECT * FROM {table}{where}", **filters)
    return found[0] if found else None


__all__ = ["count", "rows", "scalar", "table_row"]
