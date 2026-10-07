"""Column types and small Core helpers shared by every adapter.

Keeping the physical representation in one module means a change (for example
switching ``TIMESTAMP WITH TIME ZONE`` handling) happens once.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, Uuid
from sqlalchemy.dialects.postgresql import JSONB

#: Native ``uuid`` column. Identifiers are produced by the domain, so there is no
#: client-side default that would silently replace them.
UuidCol = Uuid(as_uuid=True)

#: ``TIMESTAMPTZ``: all instants are timezone-aware UTC.
TimestampCol = DateTime(timezone=True)

VersionCol = Integer()

JsonbCol = JSONB()

NOT_NULL_ZERO = "0"

__all__ = [
    "JsonbCol",
    "TimestampCol",
    "UuidCol",
    "VersionCol",
    "utc_timestamp",
]


def utc_timestamp(value: datetime | None) -> Any:
    """Normalise a datetime for storage (defensive: domain always sends UTC)."""
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("naive datetimes cannot be persisted")
    return value


def as_uuid(value: Any) -> uuid.UUID:
    """Coerce a database value into a ``uuid.UUID``."""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))
