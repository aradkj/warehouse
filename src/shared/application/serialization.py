"""Canonical JSON representation of application-layer values.

Used by :func:`shared.application.idempotency.fingerprint` so that two logically
identical commands always produce the same hash. Values are *type-tagged*: a
``Quantity(5)`` can never collide with the plain integer ``5``, and
``Money(1234, "USD")`` can never collide with the string ``"1234 USD"``.
"""

from __future__ import annotations

import dataclasses
import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from shared.application.errors import InvalidRequest


def canonical(value: Any) -> Any:
    """Return a deterministic, JSON-serialisable representation of ``value``."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, enum.Enum):
        return {"$enum": f"{type(value).__name__}:{value.value}"}
    if isinstance(value, uuid.UUID):
        return {"$uuid": str(value)}
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise InvalidRequest("timestamps in command payloads must be timezone-aware")
        return {"$ts": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "$type": type(value).__name__,
            "fields": {
                f.name: canonical(getattr(value, f.name)) for f in dataclasses.fields(value)
            },
        }
    if isinstance(value, dict):
        return {str(key): canonical(item) for key, item in sorted(value.items(), key=_key)}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [canonical(item) for item in value]
        if isinstance(value, (set, frozenset)):
            items = sorted(items, key=lambda item: repr(item))
        return items
    if isinstance(value, (bytes, bytearray)):
        return {"$bytes": value.hex()}
    # Unknown types are rendered through ``repr`` so that hashing stays total and
    # deterministic instead of raising deep inside a transaction.
    return {"$repr": repr(value)}


def _key(item: tuple[str, Any]) -> str:
    return item[0]


__all__ = ["canonical"]
