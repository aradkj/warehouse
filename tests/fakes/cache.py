"""Fake cache implementation for application-layer tests."""

from __future__ import annotations

from shared.infrastructure.cache.memory import InMemoryCache

FakeCache = InMemoryCache

__all__ = [
    "FakeCache",
    "InMemoryCache",
]
