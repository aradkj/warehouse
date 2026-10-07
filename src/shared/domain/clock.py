"""Time as an injected dependency.

Business rules depend on time (reservation expiry, order timestamps, movement
ordering). Reading ``datetime.now()`` inline would make the domain untestable and
non-deterministic, so the domain depends on the :class:`Clock` port instead.

All timestamps in the platform are timezone-aware UTC. Naive datetimes are
rejected at the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Port: supplies the current instant."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC instant."""
        ...


class SystemClock:
    """Production clock: reads the operating-system clock."""

    __slots__ = ()

    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(slots=True)
class FixedClock:
    """Deterministic clock for tests and replay scenarios."""

    current: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))

    def __post_init__(self) -> None:
        self.set(self.current)

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> datetime:
        """Move the clock forward and return the new instant."""
        self.current = self.current + timedelta(seconds=seconds)
        return self.current

    def set(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self.current = moment.astimezone(UTC)


def utcnow() -> datetime:
    """Escape hatch used only by infrastructure code (never by the domain)."""
    return datetime.now(UTC)


__all__ = ["Clock", "FixedClock", "SystemClock", "utcnow"]
