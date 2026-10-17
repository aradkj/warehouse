"""SQLAlchemy implementation of :class:`fulfillment.ports.repositories.FulfillmentRepository`."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fulfillment.adapters.persistence.mappers import (
    fulfillment_insert_values,
    fulfillment_task_insert_values,
    fulfillment_to_domain,
    fulfillment_update_values,
)
from fulfillment.adapters.persistence.tables import fulfillment_tasks, fulfillments
from fulfillment.domain.fulfillment import Fulfillment
from fulfillment.ports.repositories import FulfillmentRepository
from shared.domain.identifiers import FulfillmentId, OrderId, TenantId
from shared.infrastructure.db.execution import execute, execute_guarded_update


class SqlAlchemyFulfillmentRepository(FulfillmentRepository):
    """Fulfillment persistence adapter (fulfillment aggregate + tasks)."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, fulfillment: Fulfillment) -> None:
        await execute(
            self._session,
            insert(fulfillments).values(**fulfillment_insert_values(fulfillment)),
            duplicates={"uq_fulfillments_active_per_order": ("fulfillment", "order_id")},
        )
        for task in fulfillment.tasks:
            await execute(
                self._session,
                insert(fulfillment_tasks).values(
                    **fulfillment_task_insert_values(fulfillment.tenant_id, fulfillment.id, task)
                ),
            )

    async def get(
        self, *, tenant_id: TenantId, fulfillment_id: FulfillmentId
    ) -> Fulfillment | None:
        return await self._load(tenant_id=tenant_id, fulfillment_id=fulfillment_id, lock=False)

    async def get_for_update(
        self, *, tenant_id: TenantId, fulfillment_id: FulfillmentId
    ) -> Fulfillment | None:
        return await self._load(tenant_id=tenant_id, fulfillment_id=fulfillment_id, lock=True)

    async def save(self, fulfillment: Fulfillment, *, expected_version: int) -> None:
        statement = (
            update(fulfillments)
            .where(
                fulfillments.c.id == fulfillment.id,
                fulfillments.c.tenant_id == fulfillment.tenant_id,
                fulfillments.c.version == expected_version,
            )
            .values(**fulfillment_update_values(fulfillment), version=fulfillment.version)
        )
        await execute_guarded_update(
            self._session,
            statement,
            resource_type="fulfillment",
            resource_id=fulfillment.id,
            expected_version=expected_version,
        )
        for task in fulfillment.tasks:
            await execute(
                self._session,
                update(fulfillment_tasks)
                .where(
                    fulfillment_tasks.c.id == task.task_id,
                    fulfillment_tasks.c.fulfillment_id == fulfillment.id,
                    fulfillment_tasks.c.tenant_id == fulfillment.tenant_id,
                )
                .values(is_completed=task.is_completed),
            )

    async def list_by_order(self, *, tenant_id: TenantId, order_id: OrderId) -> list[Fulfillment]:
        stmt: Select[Any] = (
            select(fulfillments)
            .where(
                fulfillments.c.tenant_id == tenant_id,
                fulfillments.c.order_id == order_id,
            )
            .order_by(fulfillments.c.created_at.desc())
        )
        rows = (await self._session.execute(stmt)).mappings().all()
        if not rows:
            return []

        fulfillment_ids = [r["id"] for r in rows]
        task_stmt: Select[Any] = select(fulfillment_tasks).where(
            fulfillment_tasks.c.tenant_id == tenant_id,
            fulfillment_tasks.c.fulfillment_id.in_(fulfillment_ids),
        )
        task_rows = (await self._session.execute(task_stmt)).mappings().all()
        tasks_by_f_id: dict[Any, list[Any]] = {f_id: [] for f_id in fulfillment_ids}
        for tr in task_rows:
            tasks_by_f_id[tr["fulfillment_id"]].append(tr)

        return [fulfillment_to_domain(row, tasks_by_f_id.get(row["id"], [])) for row in rows]

    async def _load(
        self, *, tenant_id: TenantId, fulfillment_id: FulfillmentId, lock: bool
    ) -> Fulfillment | None:
        stmt: Select[Any] = select(fulfillments).where(
            fulfillments.c.id == fulfillment_id,
            fulfillments.c.tenant_id == tenant_id,
        )
        if lock:
            stmt = stmt.with_for_update()

        row = (await self._session.execute(stmt)).mappings().one_or_none()
        if row is None:
            return None

        task_stmt: Select[Any] = select(fulfillment_tasks).where(
            fulfillment_tasks.c.fulfillment_id == fulfillment_id,
            fulfillment_tasks.c.tenant_id == tenant_id,
        )
        task_rows = (await self._session.execute(task_stmt)).mappings().all()
        return fulfillment_to_domain(row, task_rows)


__all__ = ["SqlAlchemyFulfillmentRepository"]
