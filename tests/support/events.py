"""Event recording used by tests that assert on domain events.

Kept in ``tests.support`` rather than in the root ``conftest`` so that the root
conftest can stay free of anything but the standard library, pytest and the
framework-free domain package.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shared.domain.events import DomainEvent


@dataclass(slots=True)
class RecordingEventDispatcher:
    """Captures dispatched domain events.

    Phase 1 publishes nothing out of process, so this dispatcher records *what
    would have been published*; tests use it to prove aggregates really collect the
    events the phase promises, and that an idempotent replay does not emit them
    twice.
    """

    events: list[DomainEvent] = field(default_factory=list)

    async def dispatch(
        self, events: tuple[DomainEvent, ...], *args: object, **kwargs: object
    ) -> None:
        self.events.extend(events)

    def names(self) -> list[str]:
        return [event.event_name for event in self.events]

    def clear(self) -> None:
        self.events.clear()


__all__ = ["RecordingEventDispatcher"]
