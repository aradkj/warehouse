"""Unit tests for CachePort contracts, TTL, negative caching, and stampede protection."""

from __future__ import annotations

import pytest

from shared.infrastructure.cache.memory import InMemoryCache
from shared.ports.cache import CachePort

pytestmark = pytest.mark.unit


async def test_cache_set_and_get() -> None:
    cache: CachePort = InMemoryCache()
    key = "v1:tenant-1:product:123"

    assert await cache.get(key) is None

    success = await cache.set(key, {"name": "Widget"}, ttl=60)
    assert success is True

    val = await cache.get(key)
    assert val == {"name": "Widget"}


async def test_cache_entry_status_flags() -> None:
    cache = InMemoryCache()
    key = "v1:tenant-1:order:456"

    # Miss
    entry_miss = await cache.get_entry(key)
    assert entry_miss.is_hit is False
    assert entry_miss.is_miss is True
    assert entry_miss.is_negative is False

    # Positive hit
    await cache.set(key, {"total": 100}, ttl=60)
    entry_hit = await cache.get_entry(key)
    assert entry_hit.is_hit is True
    assert entry_hit.is_miss is False
    assert entry_hit.is_negative is False
    assert entry_hit.value == {"total": 100}

    # Negative hit
    await cache.set_negative(key, ttl=30)
    entry_neg = await cache.get_entry(key)
    assert entry_neg.is_hit is True
    assert entry_neg.is_miss is False
    assert entry_neg.is_negative is True
    assert entry_neg.value is None


async def test_cache_delete_and_exists() -> None:
    cache = InMemoryCache()
    key = "v1:tenant-1:sku:789"

    await cache.set(key, "available", ttl=60)
    assert await cache.exists(key) is True

    deleted = await cache.delete(key)
    assert deleted is True
    assert await cache.exists(key) is False


async def test_distributed_lock_owner_token_behavior() -> None:
    cache = InMemoryCache()
    key = "v1:tenant-1:product:123"

    # Worker 1 acquires lock
    token1 = await cache.acquire_lock(key, ttl=5)
    assert token1 is not None

    # Worker 2 attempts to acquire same lock
    token2 = await cache.acquire_lock(key, ttl=5)
    assert token2 is None

    # Worker 2 attempts to release with invalid token
    released_bad = await cache.release_lock(key, "wrong-token")
    assert released_bad is False

    # Worker 1 releases with valid token
    released_ok = await cache.release_lock(key, token1)
    assert released_ok is True

    # Now lock is available again
    token3 = await cache.acquire_lock(key, ttl=5)
    assert token3 is not None


async def test_get_or_set_cache_aside_single_flight() -> None:
    cache = InMemoryCache()
    key = "v1:tenant-1:product:calc"
    compute_count = 0

    async def expensive_query() -> dict[str, str]:
        nonlocal compute_count
        compute_count += 1
        return {"result": "computed"}

    # First call triggers factory
    val1 = await cache.get_or_set(key, expensive_query, ttl=60)
    assert val1 == {"result": "computed"}
    assert compute_count == 1

    # Second call uses cache
    val2 = await cache.get_or_set(key, expensive_query, ttl=60)
    assert val2 == {"result": "computed"}
    assert compute_count == 1  # Not computed again


async def test_get_or_set_negative_caching() -> None:
    cache = InMemoryCache()
    key = "v1:tenant-1:product:not-found"
    query_count = 0

    async def missing_resource_query() -> None:
        nonlocal query_count
        query_count += 1
        return None

    # First call: query returns None, caches negative entry
    val1 = await cache.get_or_set(key, missing_resource_query, ttl=60, negative_ttl=10)
    assert val1 is None
    assert query_count == 1

    entry = await cache.get_entry(key)
    assert entry.is_negative is True

    # Second call returns cached None without re-executing query
    val2 = await cache.get_or_set(key, missing_resource_query, ttl=60, negative_ttl=10)
    assert val2 is None
    assert query_count == 1


async def test_graceful_degradation_on_cache_outage() -> None:
    """When cache experiences an outage, business operations continue uninterrupted."""
    cache = InMemoryCache()
    cache.simulate_outage = True
    key = "v1:tenant-1:product:fallback"

    assert await cache.get(key) is None
    assert await cache.set(key, "data", ttl=60) is False
    assert await cache.exists(key) is False

    # get_or_set falls back to executing factory
    executed = False

    async def fallback_factory() -> str:
        nonlocal executed
        executed = True
        return "authoritative-db-record"

    result = await cache.get_or_set(key, fallback_factory, ttl=60)
    assert result == "authoritative-db-record"
    assert executed is True
