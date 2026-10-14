"""Durable SQLAlchemy implementation of AuditPort."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from audit.adapters.logger import _redact_dict
from audit.adapters.persistence.tables import audit_logs
from audit.domain.entry import AuditEntry
from audit.ports.audit import AuditPort


class SqlAlchemyAuditAdapter(AuditPort):
    """Transactionally consistent durable audit persistence."""

    def __init__(self, session: Any) -> None:
        self._target = session

    @property
    def _session(self) -> AsyncSession:
        from shared.infrastructure.db.session import SqlAlchemyTransactionScope

        if isinstance(self._target, SqlAlchemyTransactionScope):
            return self._target.session
        if callable(self._target):
            return self._target()
        return self._target


    async def record(self, entry: AuditEntry) -> None:
        """Insert an audit record into the audit_logs table."""
        # Redact any credential or secret details before persistence
        redacted_before = _redact_dict(entry.before) if entry.before else None
        redacted_after = _redact_dict(entry.after) if entry.after else None
        redacted_details = _redact_dict(entry.details) if entry.details else None

        combined_details: dict[str, Any] = {}
        if redacted_before is not None:
            combined_details["before"] = redacted_before
        if redacted_after is not None:
            combined_details["after"] = redacted_after
        if redacted_details is not None:
            combined_details.update(redacted_details)

        audit_uuid = uuid.UUID(str(entry.audit_id))
        tenant_uuid = (
            uuid.UUID(str(entry.tenant_id))
            if entry.tenant_id is not None
            else None
        )

        stmt = sa.insert(audit_logs).values(
            id=audit_uuid,
            tenant_id=tenant_uuid,
            actor_id=str(entry.actor_id),
            action=str(entry.action),
            resource_type=str(entry.resource_type),
            resource_id=str(entry.resource_id),
            correlation_id=entry.correlation_id,
            causation_id=entry.causation_id,
            occurred_at=entry.occurred_at,
            details=combined_details or None,
        )
        from shared.infrastructure.db.session import SqlAlchemyTransactionScope

        if isinstance(self._target, SqlAlchemyTransactionScope):
            if self._target._session is not None:
                await self._target._session.execute(stmt)
            else:
                async with self._target:
                    await self._target.session.execute(stmt)
                    await self._target.commit()
        else:
            await self._target.execute(stmt)

    async def list_for_tenant(
        self, tenant_id: uuid.UUID, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Fetch audit records for a tenant ordered by occurrence descending."""
        stmt = (
            sa.select(audit_logs)
            .where(audit_logs.c.tenant_id == tenant_id)
            .order_by(audit_logs.c.occurred_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [dict(row._mapping) for row in result.fetchall()]

    async def get_by_id(self, audit_id: uuid.UUID) -> dict[str, Any] | None:
        """Fetch a single audit log by primary key."""
        stmt = sa.select(audit_logs).where(audit_logs.c.id == audit_id)
        result = await self._session.execute(stmt)
        row = result.fetchone()
        return dict(row._mapping) if row else None


__all__ = ["SqlAlchemyAuditAdapter"]
