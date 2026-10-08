"""In-memory cache implementation of CachePort.

Used by pure unit tests and application-layer test suites running without Redis.
Supports TTL expiration, negative caching, lock semantics, and simulated failure.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar, cast

from shared.infrastructure.cache.keys import default_key_builder
from shared.ports.cache import CacheEntry, CachePort, NegativeCacheMarker

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _ttl_seconds(ttl: int | timedelta) -> int:
    if isinstance(ttl, timedelta):
        return max(1, int(ttl.total_seconds()))
    return max(1, int(ttl))


class InMemoryCache(CachePort):
    """In-memory dictionary-backed cache implementation."""

    def __init__(
        self,
        *,
        default_ttl_seconds: int = 300,
        negative_ttl_seconds: int = 30,
        lock_timeout_seconds: float = 5.0,
    ) -> None:
        self._store: dict[str, tuple[Any, datetime | None]] = {}
        self._locks: dict[str, tuple[str, datetime]] = {}
        self._default_ttl = default_ttl_seconds
        self._negative_ttl = negative_ttl_seconds
        self._lock_timeout = lock_timeout_seconds
        self.simulate_outage: bool = False

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _is_expired(self, expires_at: datetime | None) -> bool:
        if expires_at is None:
            return False
        return self._now() >= expires_at

    async def get(self, key: str) -> Any | None:
        entry = await self.get_entry(key)
        return entry.value

    async def get_entry(self, key: str) -> CacheEntry[Any]:
        if self.simulate_outage:
            return CacheEntry.miss(key)

        if key not in self._store:
            return CacheEntry.miss(key)

        val, expires_at = self._store[key]
        if self._is_expired(expires_at):
            del self._store[key]
            return CacheEntry.miss(key)

        if isinstance(val, NegativeCacheMarker):
            return CacheEntry.negative(key)

        return CacheEntry.hit(key, val)

    async def set(
        self,
        key: str,
        value: Any,
        ttl: int | timedelta | None = None,
    ) -> bool:
        if self.simulate_outage:
            return False

        secs = _ttl_seconds(ttl if ttl is not None else self._default_ttl)
        expires_at = self._now() + timedelta(seconds=secs)
        self._store[key] = (value, expires_at)
        return True

    async def set_negative(
        self,
        key: str,
        ttl: int | timedelta | None = None,
    ) -> bool:
        marker = NegativeCacheMarker()
        secs = _ttl_seconds(ttl if ttl is not None else self._negative_ttl)
        return await self.set(key, marker, ttl=secs)

    async def delete(self, key: str) -> bool:
        if self.simulate_outage:
            return False
        if key in self._store:
            del self._store[key]
            return True
        return False

    async def exists(self, key: str) -> bool:
        if self.simulate_outage:
            return False
        entry = await self.get_entry(key)
        return entry.is_hit

    async def expire(self, key: str, ttl: int | timedelta) -> bool:
        if self.simulate_outage:
            return False
        if key not in self._store:
            return False
        val, _ = self._store[key]
        secs = _ttl_seconds(ttl)
        self._store[key] = (val, self._now() + timedelta(seconds=secs))
        return True

    async def acquire_lock(
        self,
        key: str,
        ttl: int | timedelta | None = None,
        owner: str | None = None,
    ) -> str | None:
        if self.simulate_outage:
            return None

        lock_key = default_key_builder.lock(key)
        now = self._now()

        if lock_key in self._locks:
            _, expires_at = self._locks[lock_key]
            if now < expires_at:
                return None  # Lock still active

        token = owner or str(uuid.uuid4())
        secs = _ttl_seconds(ttl if ttl is not None else int(self._lock_timeout))
        self._locks[lock_key] = (token, now + timedelta(seconds=secs))
        return token

    async def release_lock(self, key: str, token: str) -> bool:
        if self.simulate_outage:
            return False

        lock_key = default_key_builder.lock(key)
        if lock_key not in self._locks:
            return False

        current_token, _ = self._locks[lock_key]
        if current_token == token:
            del self._locks[lock_key]
            return True
        return False

    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Awaitable[T]],
        ttl: int | timedelta,
        *,
        negative_ttl: int | timedelta | None = None,
    ) -> T:
        if self.simulate_outage:
            return await factory()

        entry = await self.get_entry(key)
        if entry.is_hit:
            if entry.is_negative:
                return cast(T, None)
            return cast(T, entry.value)

        token = await self.acquire_lock(key, ttl=int(self._lock_timeout))
        if token is not None:
            try:
                entry_check = await self.get_entry(key)
                if entry_check.is_hit:
                    if entry_check.is_negative:
                        return cast(T, None)
                    return cast(T, entry_check.value)

                result = await factory()
                if result is not None:
                    await self.set(key, result, ttl=ttl)
                elif negative_ttl is not None:
                    await self.set_negative(key, ttl=negative_ttl)
                return result
            finally:
                await self.release_lock(key, token)

        # In-memory single-flight wait
        for _ in range(10):
            await asyncio.sleep(0.02)
            entry_check = await self.get_entry(key)
            if entry_check.is_hit:
                if entry_check.is_negative:
                    return cast(T, None)
                return cast(T, entry_check.value)

        return await factory()

    async def ping(self) -> bool:
        return not self.simulate_outage

    async def close(self) -> None:
        self._store.clear()
        self._locks.clear()


__all__ = [
    "InMemoryCache",
]
