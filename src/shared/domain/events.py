"""Domain events.

A domain event is an immutable statement of something that happened inside an
aggregate, named in the past tense. In Phase 1 events are:

* collected by aggregates while they mutate,
* returned to the application service through
  :meth:`AggregateRoot.pull_events`,
* logged/asserted in tests, and
* **not** published anywhere (broker integration, the transactional outbox and
  the relay are explicitly out of scope until Phase 2).

The base class deliberately freezes the *envelope* now so Phase 2 can persist it
without touching a single domain class: ``event_id``, ``event_name``,
``schema_version``, ``occurred_at`` and ``tenant_id`` are part of the contract.
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

from shared.domain.identifiers import TenantId

#: Payload values must be JSON-serialisable without custom encoders.
JSONValue = str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]


@dataclass(frozen=True, kw_only=True, slots=True)
class DomainEvent:
    """Base class for every domain event.

    ``kw_only=True`` keeps field ordering irrelevant for subclasses, so events
    stay readable no matter how many attributes a subclass adds.
    """

    #: Envelope version; bumped only on a breaking payload change.
    schema_version: ClassVar[int] = 1

    event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def event_name(self) -> str:
        """Stable event name used as the message routing key in later phases."""
        return type(self).__name__

    def payload(self) -> dict[str, JSONValue]:
        """Return the event body (envelope attributes excluded).

        UUIDs and datetimes are rendered as strings so that the payload can be
        stored in the Phase 2 ``outbox_events`` JSONB column verbatim.
        """
        body: dict[str, JSONValue] = {}
        for f in dataclasses.fields(self):
            if f.name in _ENVELOPE_FIELDS:
                continue
            body[f.name] = _encode(getattr(self, f.name))
        return body

    def envelope(self) -> dict[str, JSONValue]:
        """Return the envelope (event id, name, version, time, tenant)."""
        envelope: dict[str, JSONValue] = {
            "event_id": str(self.event_id),
            "event_name": self.event_name,
            "schema_version": self.schema_version,
            "occurred_at": self.occurred_at.isoformat(),
        }
        tenant_id = getattr(self, "tenant_id", None)
        if tenant_id is not None:
            envelope["tenant_id"] = str(tenant_id)
        return envelope


@dataclass(frozen=True, kw_only=True, slots=True)
class TenantDomainEvent(DomainEvent):
    """Base class for events that belong to a tenant.

    Every tenant-owned aggregate emits events derived from this class, which is
    what makes tenant-scoped publication (and later, tenant-partitioned audit
    tables) possible without inspecting each event type.
    """

    tenant_id: TenantId


_ENVELOPE_FIELDS = frozenset({"event_id", "occurred_at", "schema_version"})


def _encode(value: Any) -> JSONValue:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"domain event payload is not JSON-serialisable: {type(value)!r}")


__all__ = ["DomainEvent", "JSONValue", "TenantDomainEvent"]
