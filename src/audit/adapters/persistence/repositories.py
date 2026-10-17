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
        tenant_uuid = uuid.UUID(str(entry.tenant_id)) if entry.tenant_id is not None else None

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
        from datetime import UTC, datetime

        from shared.infrastructure.db.session import SqlAlchemyTransactionScope
        from shared.infrastructure.db.tables import outbox_events

        outbox_stmt = None
        if tenant_uuid is not None:
            outbox_id = uuid.uuid4()
            corr_id = entry.correlation_id or str(uuid.uuid4())
            caus_id = entry.causation_id or str(audit_uuid)
            payload = {
                "audit_id": str(audit_uuid),
                "tenant_id": str(tenant_uuid),
                "actor_id": str(entry.actor_id),
                "action": str(entry.action),
                "resource_type": str(entry.resource_type),
                "resource_id": str(entry.resource_id),
                "correlation_id": corr_id,
                "causation_id": caus_id,
                "occurred_at": entry.occurred_at.isoformat(),
                "policy_decision": combined_details.get("policy_decision", "ALLOW"),
                "decision_reason": combined_details.get("decision_reason", "authorized"),
                "details": combined_details,
            }
            headers = {
                "correlation_id": corr_id,
                "causation_id": caus_id,
                "event_name": "AuditLogged",
            }
            outbox_stmt = sa.insert(outbox_events).values(
                id=outbox_id,
                tenant_id=tenant_uuid,
                event_type="AuditLogged",
                schema_version=1,
                aggregate_type="AuditEntry",
                aggregate_id=audit_uuid,
                payload=payload,
                headers=headers,
                correlation_id=corr_id,
                causation_id=caus_id,
                occurred_at=entry.occurred_at,
                status="PENDING",
                attempt_count=0,
                available_at=datetime.now(UTC),
            )

        if isinstance(self._target, SqlAlchemyTransactionScope):
            if self._target._session is not None:
                await self._target._session.execute(stmt)
                if outbox_stmt is not None:
                    await self._target._session.execute(outbox_stmt)
            else:
                async with self._target:
                    await self._target.session.execute(stmt)
                    if outbox_stmt is not None:
                        await self._target.session.execute(outbox_stmt)
                    await self._target.commit()
        else:
            await self._target.execute(stmt)
            if outbox_stmt is not None:
                await self._target.execute(outbox_stmt)

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
        from shared.infrastructure.db.session import SqlAlchemyTransactionScope

        if isinstance(self._target, SqlAlchemyTransactionScope):
            if self._target._session is not None:
                result = await self._target._session.execute(stmt)
                return [dict(row._mapping) for row in result.fetchall()]
            async with self._target:
                result = await self._target.session.execute(stmt)
                return [dict(row._mapping) for row in result.fetchall()]
        result = await self._target.execute(stmt)
        return [dict(row._mapping) for row in result.fetchall()]

    async def get_by_id(self, audit_id: uuid.UUID) -> dict[str, Any] | None:
        """Fetch a single audit log by primary key."""
        stmt = sa.select(audit_logs).where(audit_logs.c.id == audit_id)
        from shared.infrastructure.db.session import SqlAlchemyTransactionScope

        if isinstance(self._target, SqlAlchemyTransactionScope):
            if self._target._session is not None:
                result = await self._target._session.execute(stmt)
                row = result.fetchone()
                return dict(row._mapping) if row else None
            async with self._target:
                result = await self._target.session.execute(stmt)
                row = result.fetchone()
                return dict(row._mapping) if row else None
        result = await self._target.execute(stmt)
        row = result.fetchone()
        return dict(row._mapping) if row else None


__all__ = ["SqlAlchemyAuditAdapter"]
