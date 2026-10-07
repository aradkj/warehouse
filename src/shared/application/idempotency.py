"""Tenant-scoped idempotency: port and value types.

Exact semantics (also documented in ``docs/decisions/0006-idempotency-semantics.md``)
------------------------------------------------------------------------------
A mutation request may carry an ``Idempotency-Key`` header. The pair

    (tenant_id, idempotency_key)

is registered in the ``idempotency_records`` table *inside the same database
transaction as the business operation*:

1. ``claim`` attempts ``INSERT ... ON CONFLICT DO NOTHING``.
2. If the row did not exist, the caller owns the operation and proceeds.
3. If a row already exists:
   * identical request fingerprint and a completed record -> **replay**: the
     stored resource reference is re-read and returned, so no second order is
     created and no second reservation is made;
   * identical fingerprint while still running -> ``IdempotencyOperationInProgress``
     (409, retry later with the same key);
   * different fingerprint -> ``DuplicateIdempotencyKey`` (409).
4. On success the use case calls ``complete`` with the resource reference and the
   status code; the same transaction commits both. The response body is *not*
   stored - a replay returns the current representation of the same resource,
   which keeps the record small and avoids serving stale snapshots.
5. An operation that ends without producing a durable resource calls ``abandon``,
   which deletes the claim: replaying it is harmless because nothing happened.
6. If the transaction rolls back, the claim disappears with it. Only *committed*
   operations are memorised, which means a failed attempt can be retried with the
   same key - the client is never permanently poisoned by a 5xx.

Replay is therefore a *storage-level* guarantee (unique index on
``(tenant_id, key)``) rather than an application-level convention.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from shared.application.errors import InvalidRequest
from shared.application.serialization import canonical


class IdempotencyState(StrEnum):
    """Lifecycle of an idempotency record."""

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class IdempotencyOutcome(StrEnum):
    """Result of attempting to claim an idempotency key."""

    STARTED = "started"
    REPLAY = "replay"


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """A stored idempotency claim plus the response it produced."""

    tenant_id: uuid.UUID
    idempotency_key: str
    operation: str
    request_hash: str
    state: IdempotencyState
    resource_type: str | None
    resource_id: uuid.UUID | None
    response_status: int | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    """Outcome of ``IdempotencyStore.claim``."""

    outcome: IdempotencyOutcome
    record: IdempotencyRecord


@runtime_checkable
class IdempotencyStore(Protocol):
    """Port: tenant-scoped idempotency registry."""

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
        """Claim a key or replay/conflict according to the semantics above."""
        ...

    async def abandon(self, *, tenant_id: uuid.UUID, key: str, now: datetime) -> None:
        """Drop a claim that produced no durable resource.

        Used by operations that legitimately end without creating anything (for
        example a ``PARTIAL`` reservation that could not hold a single unit). The
        operation had no side effect, so retrying the same key later simply
        re-executes it - which is why the claim must not survive.
        """
        ...

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
        """Record the successful result of an operation."""
        ...


def completed_resource_id(record: IdempotencyRecord) -> uuid.UUID:
    """Return the resource id stored on a completed record.

    Only ``complete`` can produce such a record, so ``resource_id`` is set. The
    check is kept as a single, shared guard instead of one defensive ``assert``
    per use case: a record that is somehow inconsistent is a platform error, not a
    client error, and must fail loudly (500) rather than serve a wrong resource.
    """
    resource_id = record.resource_id
    if resource_id is None:  # pragma: no cover - impossible for a completed record
        raise InvalidRequest(
            "the stored idempotency record has no resource to replay",
            details={"idempotency_key": record.idempotency_key, "state": record.state.value},
        )
    return resource_id


def fingerprint(payload: Any) -> str:
    """Return a stable SHA-256 fingerprint of a command payload.

    Uses the type-tagged canonical form from
    :mod:`shared.application.serialization`, so two logically identical requests
    always hash identically and two different requests never do.
    """
    encoded = json.dumps(
        canonical(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "IdempotencyClaim",
    "IdempotencyOutcome",
    "IdempotencyRecord",
    "IdempotencyState",
    "IdempotencyStore",
    "completed_resource_id",
    "fingerprint",
]
