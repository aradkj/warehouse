"""Real Redis integration tests: connection, TTL, tenant isolation,
stampede protection, and degradation.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import redis.asyncio as aioredis

from shared.infrastructure.cache.keys import default_key_builder
from shared.infrastructure.cache.redis import RedisCacheAdapter

pytestmark = [pytest.mark.integration, pytest.mark.redis]

REDIS_URL = os.environ.get("ARAD_REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
async def redis_adapter() -> RedisCacheAdapter:
    adapter = RedisCacheAdapter.from_url(REDIS_URL, socket_timeout=2.0)
    # Verify connectivity before test runs
    if not await adapter.ping():
        pytest.skip(f"Redis is unreachable at {REDIS_URL}")
    yield adapter
    await adapter.close()


async def test_redis_connection_ping(redis_adapter: RedisCacheAdapter) -> None:
    assert await redis_adapter.ping() is True


async def test_redis_set_get_and_delete(redis_adapter: RedisCacheAdapter) -> None:
    tenant_id = uuid.uuid4()
    item_id = uuid.uuid4()
    key = default_key_builder.product(tenant_id, item_id)
    payload = {"name": "Mechanical Keyboard", "stock": 15, "active": True}

    # Miss initially
    assert await redis_adapter.get(key) is None

    # Set with TTL
    assert await redis_adapter.set(key, payload, ttl=60) is True

    # Get
    retrieved = await redis_adapter.get(key)
    assert retrieved == payload

    # Exists
    assert await redis_adapter.exists(key) is True

    # Delete
    assert await redis_adapter.delete(key) is True
    assert await redis_adapter.exists(key) is False
    assert await redis_adapter.get(key) is None


async def test_redis_ttl_expiration(redis_adapter: RedisCacheAdapter) -> None:
    """Validate that TTL is genuinely enforced by Redis."""
    tenant_id = uuid.uuid4()
    key = default_key_builder.resource(tenant_id, "short-lived", "1")

    # Set key with 1 second TTL
    assert await redis_adapter.set(key, "ephemeral-data", ttl=1) is True
    assert await redis_adapter.get(key) == "ephemeral-data"

    # Wait for expiration
    await asyncio.sleep(1.2)

    # Key must now be absent
    assert await redis_adapter.get(key) is None
    assert await redis_adapter.exists(key) is False


async def test_redis_expire_updates_ttl(redis_adapter: RedisCacheAdapter) -> None:
    tenant_id = uuid.uuid4()
    key = default_key_builder.resource(tenant_id, "ttl-test", "2")

    await redis_adapter.set(key, "data", ttl=10)
    assert await redis_adapter.exists(key) is True

    # Update expiration to 1 second
    assert await redis_adapter.expire(key, 1) is True
    await asyncio.sleep(1.2)
    assert await redis_adapter.get(key) is None


async def test_redis_multi_tenant_isolation(redis_adapter: RedisCacheAdapter) -> None:
    """Prove that identical resource IDs across distinct tenants store and
    retrieve distinct values.
    """
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    shared_product_id = uuid.uuid4()

    key_a = default_key_builder.product(tenant_a, shared_product_id)
    key_b = default_key_builder.product(tenant_b, shared_product_id)

    assert key_a != key_b

    # Tenant A sets a price of $10
    await redis_adapter.set(key_a, {"tenant": "A", "price": 10}, ttl=60)
    # Tenant B sets a price of $20
    await redis_adapter.set(key_b, {"tenant": "B", "price": 20}, ttl=60)

    val_a = await redis_adapter.get(key_a)
    val_b = await redis_adapter.get(key_b)

    assert val_a == {"tenant": "A", "price": 10}
    assert val_b == {"tenant": "B", "price": 20}
    assert val_a != val_b

    # Deleting tenant A's key leaves tenant B untouched
    await redis_adapter.delete(key_a)
    assert await redis_adapter.get(key_a) is None
    assert await redis_adapter.get(key_b) == {"tenant": "B", "price": 20}

    await redis_adapter.delete(key_b)


async def test_redis_negative_caching(redis_adapter: RedisCacheAdapter) -> None:
    """Negative cache entries store not-found indicators with short TTL."""
    tenant_id = uuid.uuid4()
    key = default_key_builder.sku(tenant_id, uuid.uuid4())

    assert await redis_adapter.set_negative(key, ttl=30) is True

    entry = await redis_adapter.get_entry(key)
    assert entry.is_hit is True
    assert entry.is_negative is True
    assert entry.is_miss is False
    assert entry.value is None

    # Normal get returns None (or marker representation)
    assert await redis_adapter.get(key) is None

    await redis_adapter.delete(key)


async def test_redis_distributed_lock_ownership_and_release(
    redis_adapter: RedisCacheAdapter,
) -> None:
    """Lock must be exclusive and only releasable by the owning token."""
    lock_target = "v1:tenant-x:item:999"

    # Worker 1 acquires lock
    token1 = await redis_adapter.acquire_lock(lock_target, ttl=5)
    assert token1 is not None

    # Worker 2 attempts acquisition -> rejected
    token2 = await redis_adapter.acquire_lock(lock_target, ttl=5)
    assert token2 is None

    # Worker 2 attempts illegal release with wrong token
    bad_release = await redis_adapter.release_lock(lock_target, "bogus-token")
    assert bad_release is False

    # Worker 1 releases with valid token
    good_release = await redis_adapter.release_lock(lock_target, token1)
    assert good_release is True

    # Lock is now acquirable again
    token3 = await redis_adapter.acquire_lock(lock_target, ttl=5)
    assert token3 is not None
    await redis_adapter.release_lock(lock_target, token3)


async def test_redis_concurrent_get_or_set_stampede_protection(
    redis_adapter: RedisCacheAdapter,
) -> None:
    """Multiple concurrent workers requesting the same uncached key invoke factory only once."""
    tenant_id = uuid.uuid4()
    key = default_key_builder.resource(tenant_id, "computed", uuid.uuid4())
    factory_calls = 0

    async def expensive_computation() -> dict[str, str]:
        nonlocal factory_calls
        factory_calls += 1
        await asyncio.sleep(0.1)  # Simulate DB latency
        return {"data": "expensive-result"}

    # Launch 5 concurrent callers simultaneously
    tasks = [
        redis_adapter.get_or_set(key, expensive_computation, ttl=60)
        for _ in range(5)
    ]
    results = await asyncio.gather(*tasks)

    # All 5 callers received identical computed data
    for res in results:
        assert res == {"data": "expensive-result"}

    # Factory was executed exactly once!
    assert factory_calls == 1

    await redis_adapter.delete(key)


async def test_redis_corrupted_payload_handled_safely(
    redis_adapter: RedisCacheAdapter,
) -> None:
    """Inject invalid JSON directly into Redis; adapter must treat as miss
    and evict corrupted key.
    """
    tenant_id = uuid.uuid4()
    key = default_key_builder.resource(tenant_id, "corrupt", "item-1")

    # Manually poison Redis key with broken string
    raw_redis: aioredis.Redis = redis_adapter._redis
    await raw_redis.set(key, "{not-valid-json: broken!!!")

    # Adapter reads corrupted key
    entry = await redis_adapter.get_entry(key)
    assert entry.is_miss is True
    assert entry.value is None

    # Key should have been evicted
    assert await redis_adapter.exists(key) is False


async def test_redis_graceful_degradation_when_unreachable() -> None:
    """When Redis is down or unreachable, cache operations fail gracefully
    without raising exceptions.
    """
    # Point to an unreachable port with immediate timeout
    dead_adapter = RedisCacheAdapter.from_url(
        "redis://127.0.0.1:59999/0",
        socket_timeout=0.1,
        socket_connect_timeout=0.1,
    )
    key = "v1:test:unreachable:1"

    try:
        # Operations do not raise
        assert await dead_adapter.get(key) is None
        assert await dead_adapter.set(key, "val", ttl=10) is False
        assert await dead_adapter.delete(key) is False
        assert await dead_adapter.exists(key) is False
        assert await dead_adapter.acquire_lock(key) is None
        assert await dead_adapter.ping() is False

        # get_or_set seamlessly executes the factory
        called = False

        async def db_fallback() -> str:
            nonlocal called
            called = True
            return "from_postgres"

        result = await dead_adapter.get_or_set(key, db_fallback, ttl=10)
        assert result == "from_postgres"
        assert called is True
    finally:
        await dead_adapter.close()
