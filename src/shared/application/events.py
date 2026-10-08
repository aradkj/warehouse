"""Outbound event dispatcher port (Phase 1 placeholder).

Phase 1 collects domain events but publishes nothing: durable delivery
(RabbitMQ + the transactional outbox) is Phase 2. This module exists so that the
*seam* is already defined and named, and so that the architecture test can
assert that only infrastructure may implement it.

Application services accept an :class:`EventDispatcher` (or ``None`` in tests);
they never import a broker client.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from shared.domain.events import DomainEvent


@runtime_checkable
class EventDispatcher(Protocol):
    """Port: hand committed domain events to the outside world.

    Phase 2 will implement this as a transactional outbox writer; Phase 3+ may
    add in-process projections. Implementations must not be called before the
    surrounding transaction commits.
    """

    async def dispatch(self, events: tuple[DomainEvent, ...], *, context: Any = None) -> None:
        """Publish the given events (at-least-once in later phases)."""
        ...


class NullEventDispatcher:
    """Phase 1 default: records nothing, publishes nothing.

    Exists so that wiring code has a non-optional collaborator and so that
    "no publication" is an explicit decision rather than a missing feature.
    """

    __slots__ = ()

    async def dispatch(self, events: tuple[DomainEvent, ...], *, context: Any = None) -> None:
        return None


__all__ = ["EventDispatcher", "NullEventDispatcher"]
