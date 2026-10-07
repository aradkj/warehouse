"""Aggregate / entity building blocks.

Two concerns are centralised here so that no aggregate can forget them:

1. **Event collection.** Aggregates append domain events while they mutate;
   the application layer pulls them after the transaction commits.
2. **Optimistic concurrency.** Every aggregate carries a ``version`` that is
   incremented on each state-changing operation. The persistence adapter writes
   ``UPDATE ... WHERE id = :id AND version = :expected`` and raises
   :class:`~shared.application.errors.ConcurrencyConflict` when zero rows are
   affected, which turns a lost update into a visible error instead of silent
   data corruption.

The rule "a mutation that matters records an event, and recording an event
bumps the version" keeps the two in lock-step and makes the version meaningful
to business readers rather than a technical counter.
"""

from __future__ import annotations

from abc import ABC

from shared.domain.events import DomainEvent
from shared.domain.identifiers import TenantId


class Entity(ABC):  # noqa: B024 - marker base class: subclasses inherit identity, not behaviour
    """Base class for objects with identity inside an aggregate."""

    __slots__ = ()


class AggregateRoot(Entity, ABC):
    """Base class for aggregate roots.

    Subclasses must:

    * call ``super().__init__(tenant_id=..., version=...)`` in their own
      constructor,
    * mutate state only through intention-revealing methods that call
      :meth:`_record_event`.
    """

    __slots__ = ("_events", "_tenant_id", "_version")

    def __init__(self, *, tenant_id: TenantId, version: int = 0) -> None:
        if version < 0:
            raise ValueError("aggregate version cannot be negative")
        self._tenant_id: TenantId = tenant_id
        self._version = version
        self._events: list[DomainEvent] = []

    # -- identity ---------------------------------------------------------
    @property
    def tenant_id(self) -> TenantId:
        """The tenant that owns this aggregate (set once, never changed)."""
        return self._tenant_id

    # -- optimistic concurrency ------------------------------------------
    @property
    def version(self) -> int:
        """Number of recorded state transitions (see module docstring)."""
        return self._version

    # -- domain events ----------------------------------------------------
    @property
    def events(self) -> tuple[DomainEvent, ...]:
        """Events recorded so far, without clearing them."""
        return tuple(self._events)

    def pull_events(self) -> tuple[DomainEvent, ...]:
        """Return and clear the recorded events.

        Called by the application layer exactly once per unit of work, after the
        aggregates have been persisted and before the transaction commits
        (Phase 2 will hand these to the transactional outbox).
        """
        events = tuple(self._events)
        self._events.clear()
        return events

    # -- internals --------------------------------------------------------
    def _record_event(self, event: DomainEvent) -> None:
        """Record a domain event and advance the aggregate version.

        This is the *only* way to change observable state, which guarantees that
        ``version`` always moves together with business state.
        """
        self._events.append(event)
        self._version += 1

    def _assert_same_tenant(self, other_tenant_id: TenantId, *, what: str) -> None:
        """Guard against cross-tenant references (invariant: always invalid)."""
        if other_tenant_id != self._tenant_id:
            from shared.domain.errors import CrossTenantAccess

            raise CrossTenantAccess(
                f"{what} belongs to a different tenant than this aggregate",
                details={
                    "aggregate_tenant_id": str(self._tenant_id),
                    "referenced_tenant_id": str(other_tenant_id),
                },
            )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<{type(self).__name__} version={self._version}>"

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return _identity(self) == _identity(other) and isinstance(other, AggregateRoot)

    def __hash__(self) -> int:
        return hash((type(self).__name__, _identity(self)))


def _identity(aggregate: object) -> object:
    return getattr(aggregate, "id", None)


class ValueObject(ABC):  # noqa: B024 - marker base class, deliberately without abstract methods
    """Marker base class for immutable, identity-less concepts.

    Value objects are compared by value; concrete ones are frozen dataclasses
    (see :mod:`shared.domain.value_objects`).
    """

    __slots__ = ()


def ensure_expected_version(aggregate: AggregateRoot, expected_version: int | None) -> None:
    """Raise when a caller supplied an optimistic-concurrency expectation that
    does not match the loaded aggregate.

    Used by application services that accept an ``If-Match`` style version from
    the API.
    """
    from shared.application.errors import ConcurrencyConflict

    if expected_version is not None and expected_version != aggregate.version:
        raise ConcurrencyConflict(
            "the resource was modified by another request",
            details={"expected_version": expected_version, "actual_version": aggregate.version},
        )


__all__ = [
    "AggregateRoot",
    "Entity",
    "ValueObject",
    "ensure_expected_version",
]
