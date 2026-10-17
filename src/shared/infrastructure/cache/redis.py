"""Asynchronous Redis cache adapter with connection pooling and stampede protection.

Architecture & guarantees:
* Implements :class:`~shared.ports.cache.CachePort`.
* Employs ``redis.asyncio`` with a shared, reusable connection pool.
* Strict graceful degradation: Redis connection failures, timeouts, and network
  glitches are logged and downgraded to cache misses / no-ops so business workflows
  proceed uninterrupted against the PostgreSQL system of record.
* Protects against cache stampedes using short-lived distributed locks with
  unique owner tokens and atomic release semantics.
* Supports explicit negative caching with distinct TTLs.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, TypeVar, cast

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from bootstrap.config import Settings
from shared.infrastructure.cache.keys import default_key_builder
from shared.infrastructure.cache.serialization import CacheSerializer
from shared.ports.cache import CacheEntry, CachePort, NegativeCacheMarker

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Lua script to atomically release a lock only if the caller holds the matching token.
_LUA_RELEASE_LOCK = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

#: Lua script to atomically validate whether the caller holds the matching token.
_LUA_VALIDATE_LOCK = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return 1
else
    return 0
end
"""


def _ttl_seconds(ttl: int | timedelta) -> int:
    """Normalize timedelta or integer to integer seconds >= 1."""
    secs = int(ttl.total_seconds()) if isinstance(ttl, timedelta) else int(ttl)
    return max(1, secs)


class RedisCacheAdapter(CachePort):
    """Production Redis cache implementation."""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        *,
        default_ttl_seconds: int = 300,
        negative_ttl_seconds: int = 30,
        lock_timeout_seconds: float = 5.0,
    ) -> None:
        self._redis = redis_client
        self._default_ttl = default_ttl_seconds
        self._negative_ttl = negative_ttl_seconds
        self._lock_timeout = lock_timeout_seconds
        self._release_script = self._redis.register_script(_LUA_RELEASE_LOCK)
        self._validate_script = self._redis.register_script(_LUA_VALIDATE_LOCK)

    @classmethod
    def from_settings(cls, settings: Settings) -> RedisCacheAdapter:
        """Factory initializing a pooled async Redis client from application settings."""
        pool = aioredis.ConnectionPool.from_url(
            settings.redis_url,
            max_connections=settings.redis_max_connections,
            socket_timeout=settings.redis_socket_timeout_seconds,
            socket_connect_timeout=settings.redis_connect_timeout_seconds,
            decode_responses=False,
        )
        client = aioredis.Redis(connection_pool=pool)
        return cls(
            client,
            default_ttl_seconds=settings.redis_default_ttl_seconds,
            negative_ttl_seconds=settings.redis_negative_ttl_seconds,
            lock_timeout_seconds=settings.redis_lock_timeout_seconds,
        )

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        max_connections: int = 50,
        socket_timeout: float = 2.0,
        socket_connect_timeout: float = 2.0,
        default_ttl_seconds: int = 300,
        negative_ttl_seconds: int = 30,
        lock_timeout_seconds: float = 5.0,
    ) -> RedisCacheAdapter:
        """Factory initializing client from an explicit connection URL."""
        pool = aioredis.ConnectionPool.from_url(
            url,
            max_connections=max_connections,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
            decode_responses=False,
        )
        client = aioredis.Redis(connection_pool=pool)
        return cls(
            client,
            default_ttl_seconds=default_ttl_seconds,
            negative_ttl_seconds=negative_ttl_seconds,
            lock_timeout_seconds=lock_timeout_seconds,
        )

    async def get(self, key: str) -> Any | None:
        """Fetch a value from Redis; degrades to None on miss, corruption, or outage."""
        entry = await self.get_entry(key)
        return entry.value

    async def get_entry(self, key: str) -> CacheEntry[Any]:
        """Fetch a typed entry with explicit hit, miss, and negative markers."""
        try:
            raw = await self._redis.get(key)
        except (RedisError, TimeoutError, OSError) as exc:
            logger.warning("Redis GET failed for key '%s'; treating as cache miss: %s", key, exc)
            return CacheEntry.miss(key)

        if raw is None:
            return CacheEntry.miss(key)

        deserialized = CacheSerializer.deserialize(raw)
        if deserialized is None:
            # Corrupted payload: evict stale key in background
            with contextlib.suppress(Exception):
                await self._redis.delete(key)
            return CacheEntry.miss(key)

        if isinstance(deserialized, NegativeCacheMarker):
            return CacheEntry.negative(key)

        return CacheEntry.hit(key, deserialized)

    async def set(
        self,
        key: str,
        value: Any,
        ttl: int | timedelta | None = None,
    ) -> bool:
        """Store a value in Redis with TTL. Returns False without raising if Redis fails."""
        secs = _ttl_seconds(ttl if ttl is not None else self._default_ttl)
        try:
            serialized = CacheSerializer.serialize(value)
            await self._redis.set(key, serialized, ex=secs)
            return True
        except (RedisError, TimeoutError, OSError) as exc:
            logger.warning("Redis SET failed for key '%s'; continuing without cache: %s", key, exc)
            return False

    async def set_negative(
        self,
        key: str,
        ttl: int | timedelta | None = None,
    ) -> bool:
        """Store a negative cache entry with short TTL."""
        marker = NegativeCacheMarker()
        secs = _ttl_seconds(ttl if ttl is not None else self._negative_ttl)
        return await self.set(key, marker, ttl=secs)

    async def delete(self, key: str) -> bool:
        """Evict a key from Redis."""
        try:
            res = await self._redis.delete(key)
            return bool(res > 0)
        except (RedisError, TimeoutError, OSError) as exc:
            logger.warning("Redis DELETE failed for key '%s': %s", key, exc)
            return False

    async def exists(self, key: str) -> bool:
        """Check key existence."""
        try:
            res = await self._redis.exists(key)
            return bool(res > 0)
        except (RedisError, TimeoutError, OSError) as exc:
            logger.warning("Redis EXISTS failed for key '%s': %s", key, exc)
            return False

    async def expire(self, key: str, ttl: int | timedelta) -> bool:
        """Update TTL for an existing key."""
        secs = _ttl_seconds(ttl)
        try:
            res = await self._redis.expire(key, secs)
            return bool(res)
        except (RedisError, TimeoutError, OSError) as exc:
            logger.warning("Redis EXPIRE failed for key '%s': %s", key, exc)
            return False

    async def increment(self, key: str, delta: int = 1) -> int:
        """Atomically increment a numerical key, creating it if it does not exist."""
        try:
            res = await self._redis.incrby(key, delta)
            return int(res)
        except (RedisError, TimeoutError, OSError) as exc:
            logger.warning("Redis INCRBY failed for key '%s': %s", key, exc)
            return 0

    async def acquire_lock(
        self,
        key: str,
        ttl: int | timedelta | None = None,
        owner: str | None = None,
    ) -> str | None:
        """Acquire a distributed lock with NX PX."""
        lock_key = default_key_builder.lock(key)
        token = owner or str(uuid.uuid4())
        secs = _ttl_seconds(ttl if ttl is not None else int(self._lock_timeout))
        try:
            acquired = await self._redis.set(lock_key, token, ex=secs, nx=True)
            if acquired:
                return token
            return None
        except (RedisError, TimeoutError, OSError) as exc:
            logger.warning("Redis lock acquisition failed for key '%s': %s", key, exc)
            return None

    async def release_lock(self, key: str, token: str) -> bool:
        """Release distributed lock safely via Lua script."""
        lock_key = default_key_builder.lock(key)
        try:
            result = await self._release_script(keys=[lock_key], args=[token])
            return bool(result == 1)
        except (RedisError, TimeoutError, OSError) as exc:
            logger.warning("Redis lock release failed for key '%s': %s", key, exc)
            return False

    async def validate_lock(self, key: str, token: str) -> bool:
        """Validate whether distributed lock is still held by caller."""
        lock_key = default_key_builder.lock(key)
        try:
            result = await self._validate_script(keys=[lock_key], args=[token])
            return bool(result == 1)
        except (RedisError, TimeoutError, OSError) as exc:
            logger.warning("Redis lock validation failed for key '%s': %s", key, exc)
            return False

    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Awaitable[T]],
        ttl: int | timedelta,
        *,
        negative_ttl: int | timedelta | None = None,
    ) -> T:
        """Cache-aside execution with stampede protection.

        If Redis is down or unreachable, seamlessly falls back to executing ``factory()``.
        """
        # 1. Inspect cache
        entry = await self.get_entry(key)
        if entry.is_hit:
            if entry.is_negative:
                # Cached negative result
                return cast(T, None)
            return cast(T, entry.value)

        # 2. Cache miss: attempt single-flight lock to prevent stampede
        token = await self.acquire_lock(key, ttl=int(self._lock_timeout))
        if token is not None:
            try:
                # Double-check cache in case another worker just populated it
                entry_check = await self.get_entry(key)
                if entry_check.is_hit:
                    if entry_check.is_negative:
                        return cast(T, None)
                    return cast(T, entry_check.value)

                # Authoritative query to database/factory
                result = await factory()

                if result is not None:
                    await self.set(key, result, ttl=ttl)
                elif negative_ttl is not None:
                    await self.set_negative(key, ttl=negative_ttl)

                return result
            finally:
                await self.release_lock(key, token)

        # 3. Lock not acquired: another process is populating cache. Wait and poll briefly.
        max_wait_seconds = 1.5
        poll_interval = 0.005
        elapsed = 0.0

        while elapsed < max_wait_seconds:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            entry_check = await self.get_entry(key)
            if entry_check.is_hit:
                if entry_check.is_negative:
                    return cast(T, None)
                return cast(T, entry_check.value)

        # 4. Fallback if lock was never released or cache was never populated
        logger.info("Stampede wait timeout for key '%s'; falling back to direct computation", key)
        return await factory()

    async def ping(self) -> bool:
        """Probe Redis connectivity."""
        try:
            return bool(await self._redis.ping())
        except Exception:
            return False

    async def close(self) -> None:
        """Close connection pool cleanly."""
        try:
            await self._redis.aclose()
            pool = getattr(self._redis, "connection_pool", None)
            if pool is not None and hasattr(pool, "aclose"):
                await pool.aclose()
        except Exception as exc:
            logger.warning("Error closing Redis connection pool: %s", exc)


__all__ = [
    "RedisCacheAdapter",
]
