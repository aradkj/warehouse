"""Cache port: protocol and value types for caching and distributed coordination.

Phase 3 introduces cache-aside acceleration for read operations and single-flight
distributed locks for cache stampede protection.

Key architectural guarantees:
* PostgreSQL remains the authoritative system of record.
* The domain never depends on caching or Redis.
* Application use cases orchestrate through this port protocol.
* Inventory and order consistency must never depend solely on cached values.
* Redis failure degrades gracefully to cache misses and database fallbacks.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class NegativeCacheMarker:
    """Explicit representation of a cached negative result ('not found').

    Used by cache-aside reads to prevent cache penetration when querying
    non-existent resources in the database. Has a deliberately short TTL.
    """

    reason: str = "not_found"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class CacheEntry[T]:
    """Outcome of inspecting a cache key."""

    key: str
    value: T | None = None
    is_hit: bool = False
    is_negative: bool = False

    @property
    def is_miss(self) -> bool:
        return not self.is_hit

    @classmethod
    def hit(cls, key: str, value: T) -> CacheEntry[T]:
        return cls(key=key, value=value, is_hit=True, is_negative=False)

    @classmethod
    def negative(cls, key: str) -> CacheEntry[T]:
        return cls(key=key, value=None, is_hit=True, is_negative=True)

    @classmethod
    def miss(cls, key: str) -> CacheEntry[T]:
        return cls(key=key, value=None, is_hit=False, is_negative=False)


@runtime_checkable
class CachePort(Protocol):
    """Outbound port: cache-aside storage, TTL management, and stampede protection."""

    async def get(self, key: str) -> Any | None:
        """Fetch a value by key.

        Returns:
            The deserialized value on cache hit.
            :class:`NegativeCacheMarker` if a negative result was cached.
            ``None`` on cache miss, expired entry, corrupted payload, or cache outage.
        """
        ...

    async def get_entry(self, key: str) -> CacheEntry[Any]:
        """Fetch a typed entry with explicit hit, miss, and negative flags."""
        ...

    async def set(
        self,
        key: str,
        value: Any,
        ttl: int | timedelta,
    ) -> bool:
        """Store a value with an explicit TTL.

        Returns True if stored successfully, False on failure.
        """
        ...

    async def set_negative(
        self,
        key: str,
        ttl: int | timedelta = 30,
    ) -> bool:
        """Store a negative cache marker for non-existent resources with a short TTL."""
        ...

    async def delete(self, key: str) -> bool:
        """Evict a key from the cache."""
        ...

    async def exists(self, key: str) -> bool:
        """Check whether a key exists in the cache."""
        ...

    async def expire(self, key: str, ttl: int | timedelta) -> bool:
        """Update the time-to-live for an existing key."""
        ...

    async def increment(self, key: str, delta: int = 1) -> int:
        """Atomically increment a numerical key, creating it if it does not exist."""
        ...

    async def acquire_lock(
        self,
        key: str,
        ttl: int | timedelta = 5,
        owner: str | None = None,
    ) -> str | None:
        """Acquire a short-lived distributed lock for stampede protection.

        Returns:
            The unique owner token string if acquired, or ``None`` if held by another.
        """
        ...

    async def release_lock(self, key: str, token: str) -> bool:
        """Release a distributed lock only if the token matches the current owner."""
        ...

    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Awaitable[T]],
        ttl: int | timedelta,
        *,
        negative_ttl: int | timedelta | None = None,
    ) -> T:
        """Cache-aside single-flight retrieval with stampede protection.

        1. Checks cache; on hit, returns cached value.
        2. On miss, acquires single-flight lock before invoking ``factory()``.
        3. Double-checks cache; populates cache on missing and releases lock.
        4. If cache is unavailable, gracefully invokes ``factory()`` directly.
        """
        ...

    async def ping(self) -> bool:
        """Verify cache connectivity."""
        ...

    async def close(self) -> None:
        """Close connection pools cleanly upon application shutdown."""
        ...


__all__ = [
    "CacheEntry",
    "CachePort",
    "NegativeCacheMarker",
]
