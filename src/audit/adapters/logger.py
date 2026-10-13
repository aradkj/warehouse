"""Structured logger adapter for AuditPort."""

from __future__ import annotations

import logging
from typing import Any

from audit.domain.entry import AuditEntry
from audit.ports.audit import AuditPort

logger = logging.getLogger("arad.audit")

_REDACTED_FIELDS = {
    "password",
    "password_hash",
    "secret",
    "secret_hash",
    "client_secret",
    "client_secret_hash",
    "token",
    "refresh_token",
    "access_token",
    "invitation_token",
}


def _redact_dict(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if data is None:
        return None
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        k_lower = key.lower()
        if (
            k_lower in _REDACTED_FIELDS
            or any(term in k_lower for term in ("secret", "password", "token", "credential"))
        ):
            cleaned[key] = "[REDACTED]"
        elif isinstance(value, dict):
            cleaned[key] = _redact_dict(value)
        else:
            cleaned[key] = value
    return cleaned


class LoggingAuditAdapter(AuditPort):
    """Logs structured audit records to the dedicated audit log."""

    async def record(self, entry: AuditEntry) -> None:
        logger.info(
            "AUDIT_EVENT: action=%s actor=%s tenant=%s resource=%s:%s correlation=%s details=%s",
            entry.action,
            entry.actor_id,
            entry.tenant_id,
            entry.resource_type,
            entry.resource_id,
            entry.correlation_id,
            {
                "audit_id": entry.audit_id,
                "occurred_at": entry.occurred_at.isoformat(),
                "causation_id": entry.causation_id,
                "before": _redact_dict(entry.before),
                "after": _redact_dict(entry.after),
            },
        )


__all__ = ["LoggingAuditAdapter"]
