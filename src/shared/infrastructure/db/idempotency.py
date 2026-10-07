"""PostgreSQL implementation of the idempotency registry.

The whole guarantee rests on one database-level primitive:

    INSERT INTO idempotency_records (...) VALUES (...) ON CONFLICT DO NOTHING

The unique index on ``(tenant_id, idempotency_key)`` decides the winner. A
second, concurrent request with the same key blocks on the index entry until the
first transaction commits and then observes the committed record: it replays
instead of executing the operation a second time. No advisory locks, no Redis,
no application-level coordination.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from shared.application.errors import (
    DuplicateIdempotencyKey,
    IdempotencyOperationInProgress,
)
from shared.application.idempotency import (
    IdempotencyClaim,
    IdempotencyOutcome,
    IdempotencyRecord,
    IdempotencyState,
    IdempotencyStore,
)
from shared.infrastructure.db.execution import execute
from shared.infrastructure.db.tables import idempotency_records


class SqlAlchemyIdempotencyStore(IdempotencyStore):
    """Idempotency registry bound to one session/transaction."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(
        self,
        *,
        tenant_id: uuid.UUID,
        key: str,
        operation: str,
        request_hash: str,
        now: datetime,
        expires_at: datetime,
    ) -> IdempotencyClaim:
        insert_statement = (
            pg_insert(idempotency_records)
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                idempotency_key=key,
                operation=operation,
                request_hash=request_hash,
                state=IdempotencyState.IN_PROGRESS.value,
                created_at=now,
                updated_at=now,
                expires_at=expires_at,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "idempotency_key"])
            .returning(*idempotency_records.c)
        )
        result = await execute(self._session, insert_statement)
        row = result.mappings().first()
        if row is not None:
            return IdempotencyClaim(outcome=IdempotencyOutcome.STARTED, record=_to_record(row))

        # The key already exists: decide between replay and conflict.
        existing = await self._load(tenant_id=tenant_id, key=key)
        if existing is None:  # pragma: no cover - the conflicting row was removed
            # Extremely narrow window: the competing transaction aborted after we
            # were blocked. Retrying the insert is the correct response.
            result = await execute(self._session, insert_statement)
            row = result.mappings().first()
            if row is not None:
                return IdempotencyClaim(outcome=IdempotencyOutcome.STARTED, record=_to_record(row))
            existing = await self._load(tenant_id=tenant_id, key=key)
            if existing is None:  # pragma: no cover - defensive
                raise IdempotencyOperationInProgress(
                    "another request with this idempotency key is in progress",
                    details={"idempotency_key": key},
                )

        if existing.request_hash != request_hash:
            raise DuplicateIdempotencyKey(
                "this idempotency key was already used for a different request",
                details={
                    "idempotency_key": key,
                    "operation": existing.operation,
                    "requested_operation": operation,
                },
            )
        if existing.state is IdempotencyState.IN_PROGRESS:
            raise IdempotencyOperationInProgress(
                "another request with this idempotency key is still in progress",
                details={"idempotency_key": key, "operation": existing.operation},
            )
        return IdempotencyClaim(outcome=IdempotencyOutcome.REPLAY, record=existing)

    async def abandon(self, *, tenant_id: uuid.UUID, key: str, now: datetime) -> None:
        statement = delete(idempotency_records).where(
            idempotency_records.c.tenant_id == tenant_id,
            idempotency_records.c.idempotency_key == key,
        )
        await execute(self._session, statement)

    async def complete(
        self,
        *,
        tenant_id: uuid.UUID,
        key: str,
        resource_type: str,
        resource_id: uuid.UUID,
        response_status: int,
        now: datetime,
    ) -> None:
        statement = (
            update(idempotency_records)
            .where(
                idempotency_records.c.tenant_id == tenant_id,
                idempotency_records.c.idempotency_key == key,
            )
            .values(
                state=IdempotencyState.COMPLETED.value,
                resource_type=resource_type,
                resource_id=resource_id,
                response_status=response_status,
                updated_at=now,
            )
        )
        result = await execute(self._session, statement)
        if result.rowcount != 1:  # pragma: no cover - the claim is always ours
            raise IdempotencyOperationInProgress(
                "the idempotency claim could not be completed",
                details={"idempotency_key": key},
            )

    # -- internals ---------------------------------------------------------
    async def _load(self, *, tenant_id: uuid.UUID, key: str) -> IdempotencyRecord | None:
        statement = select(idempotency_records).where(
            idempotency_records.c.tenant_id == tenant_id,
            idempotency_records.c.idempotency_key == key,
        )
        result = await self._session.execute(statement)
        row = result.mappings().first()
        return _to_record(row) if row is not None else None


def _to_record(row: RowMapping) -> IdempotencyRecord:
    return IdempotencyRecord(
        id=row["id"],
        tenant_id=row["tenant_id"],
        idempotency_key=row["idempotency_key"],
        operation=row["operation"],
        request_hash=row["request_hash"],
        state=IdempotencyState(row["state"]),
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        response_status=row["response_status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
    )


__all__ = ["SqlAlchemyIdempotencyStore"]
