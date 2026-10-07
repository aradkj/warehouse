"""SQLAlchemy implementation of :class:`ordering.ports.repositories.OrderRepository`."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, insert, select, tuple_, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from ordering.adapters.persistence.mappers import (
    order_insert_values,
    order_line_insert_values,
    order_to_domain,
    order_update_values,
)
from ordering.adapters.persistence.tables import order_lines, orders
from ordering.domain.order import Order
from ordering.domain.value_objects import OrderStatus
from shared.application.pagination import (
    Page,
    build_page,
    cursor_datetime,
    cursor_uuid,
    decode_cursor,
)
from shared.domain.identifiers import OrderId, TenantId
from shared.infrastructure.db.execution import execute, execute_guarded_update


class SqlAlchemyOrderRepository:
    """Order persistence adapter (order aggregate + its immutable lines)."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, order: Order) -> None:
        await execute(self._session, insert(orders).values(**order_insert_values(order)))
        for line in order.lines:
            await execute(
                self._session,
                insert(order_lines).values(
                    **order_line_insert_values(order.tenant_id, order.id, line)
                ),
            )

    async def get(self, *, tenant_id: TenantId, order_id: OrderId) -> Order | None:
        return await self._load(tenant_id=tenant_id, order_id=order_id, lock=False)

    async def get_for_update(self, *, tenant_id: TenantId, order_id: OrderId) -> Order | None:
        """Lock the order row: every state transition starts here.

        Locking the aggregate row (not just the lines) means two concurrent
        confirm/cancel requests are serialised by PostgreSQL, so the transition
        table can never be evaluated twice on the same starting state.
        """
        return await self._load(tenant_id=tenant_id, order_id=order_id, lock=True)

    async def save(self, order: Order, *, expected_version: int) -> None:
        statement = (
            update(orders)
            .where(
                orders.c.id == order.id,
                orders.c.tenant_id == order.tenant_id,
                orders.c.version == expected_version,
            )
            .values(**order_update_values(order), version=order.version)
        )
        await execute_guarded_update(
            self._session,
            statement,
            resource_type="order",
            resource_id=order.id,
            expected_version=expected_version,
        )

    async def page(
        self,
        *,
        tenant_id: TenantId,
        limit: int,
        cursor: str | None = None,
        status: OrderStatus | None = None,
    ) -> Page[Order]:
        statement: Select[Any] = select(orders).where(orders.c.tenant_id == tenant_id)
        if status is not None:
            statement = statement.where(orders.c.status == status.value)
        if cursor is not None:
            values = decode_cursor(cursor)
            statement = statement.where(
                tuple_(orders.c.created_at, orders.c.id)
                < tuple_(cursor_datetime(values, "created_at"), cursor_uuid(values, "id"))
            )
        statement = statement.order_by(orders.c.created_at.desc(), orders.c.id.desc())
        statement = statement.limit(limit + 1)
        rows = (await self._session.execute(statement)).mappings().all()
        # One extra query for the whole page instead of one per order: lines are
        # part of the aggregate and an order without its lines is meaningless.
        lines_by_order = await self._lines_for_orders(
            tenant_id=tenant_id, order_ids=tuple(OrderId(row["id"]) for row in rows)
        )
        return build_page(
            [order_to_domain(row, lines_by_order.get(OrderId(row["id"]), [])) for row in rows],
            limit=limit,
            cursor_of=lambda order: {"created_at": order.created_at, "id": order.id},
        )

    # -- internals ---------------------------------------------------------
    async def _lines_for_orders(
        self, *, tenant_id: TenantId, order_ids: tuple[OrderId, ...]
    ) -> dict[OrderId, list[RowMapping]]:
        """Batch-load the order lines of a whole page (avoids an N+1 query)."""
        if not order_ids:
            return {}
        statement = (
            select(order_lines)
            .where(
                order_lines.c.tenant_id == tenant_id,
                order_lines.c.order_id.in_(order_ids),
            )
            .order_by(order_lines.c.order_id.asc(), order_lines.c.line_number.asc())
        )
        grouped: dict[OrderId, list[RowMapping]] = {}
        for row in (await self._session.execute(statement)).mappings().all():
            grouped.setdefault(OrderId(row["order_id"]), []).append(row)
        return grouped

    async def _load(self, *, tenant_id: TenantId, order_id: OrderId, lock: bool) -> Order | None:
        statement = select(orders).where(orders.c.tenant_id == tenant_id, orders.c.id == order_id)
        if lock:
            statement = statement.with_for_update()
        row = (await self._session.execute(statement)).mappings().first()
        if row is None:
            return None
        line_rows: list[RowMapping] = list(
            (
                await self._session.execute(
                    select(order_lines)
                    .where(
                        order_lines.c.tenant_id == tenant_id,
                        order_lines.c.order_id == order_id,
                    )
                    .order_by(order_lines.c.line_number.asc())
                )
            )
            .mappings()
            .all()
        )
        return order_to_domain(row, line_rows)


__all__ = ["SqlAlchemyOrderRepository"]
