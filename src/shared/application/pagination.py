"""Cursor-based pagination primitives (application layer, framework independent).

Offset pagination is deliberately not used for tenant-facing list endpoints:
with concurrent inserts it duplicates and skips rows, and its cost grows with
the offset. Cursors are keyset positions over ``(sort_value, id)`` - the columns
that back the composite indexes documented in
``docs/architecture/data-model.md``.

The cursor is opaque to clients: base64url(JSON) with the sort key and id.
Encoding it in the application layer (not the API layer) means every inbound
adapter - HTTP today, a CLI or a worker tomorrow - produces the same cursors.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar

from shared.application.errors import InvalidRequest

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


@dataclass(frozen=True, slots=True)
class Page[T]:
    """One page of a keyset-paginated result."""

    items: tuple[T, ...]
    next_cursor: str | None

    def __len__(self) -> int:
        return len(self.items)


def build_page[T](
    rows: Sequence[T], *, limit: int, cursor_of: Callable[[T], Mapping[str, Any]]
) -> Page[T]:
    """Build a page from ``limit + 1`` fetched rows.

    Repositories fetch one row more than the page size; the extra row only serves
    to decide whether a next cursor exists (no ``COUNT(*)`` needed).
    """
    has_more = len(rows) > limit
    page_rows = list(rows[:limit])
    next_cursor = encode_cursor(dict(cursor_of(page_rows[-1]))) if has_more and page_rows else None
    return Page(items=tuple(page_rows), next_cursor=next_cursor)


def encode_cursor(values: dict[str, Any]) -> str:
    """Encode cursor values (strings/datetimes/UUIDs) into an opaque token."""
    payload = {key: _encode_value(value) for key, value in values.items()}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> dict[str, str]:
    """Decode an opaque token, raising ``InvalidRequest`` for malformed input."""
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + padding)
        payload = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise InvalidRequest(
            "the pagination cursor is malformed", details={"cursor": cursor}
        ) from exc
    if not isinstance(payload, dict) or not payload:
        raise InvalidRequest("the pagination cursor is malformed", details={"cursor": cursor})
    return {str(key): str(value) for key, value in payload.items()}


def cursor_datetime(values: Mapping[str, str], field: str) -> datetime:
    """Read a timestamp from a decoded cursor, rejecting garbage."""
    raw = values.get(field)
    if raw is None:
        raise InvalidRequest("the pagination cursor is missing data", details={"field": field})
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise InvalidRequest(
            "the pagination cursor is malformed", details={"field": field}
        ) from exc
    if parsed.tzinfo is None:
        raise InvalidRequest("the pagination cursor is malformed", details={"field": field})
    return parsed


def cursor_uuid(values: Mapping[str, str], field: str) -> uuid.UUID:
    """Read a UUID from a decoded cursor, rejecting garbage."""
    raw = values.get(field)
    if raw is None:
        raise InvalidRequest("the pagination cursor is missing data", details={"field": field})
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise InvalidRequest(
            "the pagination cursor is malformed", details={"field": field}
        ) from exc


def normalise_limit(limit: int | None) -> int:
    """Clamp a requested page size to the platform bounds."""
    if limit is None:
        return DEFAULT_PAGE_SIZE
    if limit < 1 or limit > MAX_PAGE_SIZE:
        raise InvalidRequest(
            f"limit must be between 1 and {MAX_PAGE_SIZE}", details={"limit": limit}
        )
    return limit


def _encode_value(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise InvalidRequest("cursor timestamps must be timezone-aware")
        return value.isoformat()
    return str(value)


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "Page",
    "build_page",
    "cursor_datetime",
    "cursor_uuid",
    "decode_cursor",
    "encode_cursor",
    "normalise_limit",
]
