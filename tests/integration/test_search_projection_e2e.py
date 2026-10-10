"""End-to-end integration tests for Search projection and Redis invalidation."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest

from catalog.ports.events import ProductCreated, SkuCreated, SkuPriceChanged
from search.adapters.elasticsearch import ElasticsearchIndexAdapter
from search.application.models import (
    PRODUCTS_INDEX_MAPPINGS,
    build_product_document_id,
)
from search.application.projector import ProductSearchProjector
from shared.domain.identifiers import ProductId, SkuId, TenantId
from shared.infrastructure.cache.keys import default_key_builder
from shared.infrastructure.cache.redis import RedisCacheAdapter

pytestmark = [pytest.mark.integration, pytest.mark.elasticsearch, pytest.mark.redis]

ES_URL = os.environ.get(
    "ARAD_ELASTICSEARCH_URL", os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")
)
REDIS_URL = os.environ.get("ARAD_REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
async def es_adapter() -> AsyncIterator[ElasticsearchIndexAdapter]:
    adapter = ElasticsearchIndexAdapter.from_url(url=ES_URL, request_timeout=3.0, max_retries=2)
    if not await adapter.ping():
        await adapter.close()
        pytest.skip(f"Elasticsearch is unreachable at {ES_URL}")
    yield adapter
    await adapter.close()


@pytest.fixture
async def redis_adapter() -> AsyncIterator[RedisCacheAdapter]:
    adapter = RedisCacheAdapter.from_url(REDIS_URL, socket_timeout=2.0)
    if not await adapter.ping():
        await adapter.close()
        pytest.skip(f"Redis is unreachable at {REDIS_URL}")
    yield adapter
    await adapter.close()


@pytest.fixture
async def e2e_index(es_adapter: ElasticsearchIndexAdapter) -> AsyncIterator[str]:
    unique_suffix = uuid.uuid4().hex[:8]
    index_name = f"e2e-products-{unique_suffix}"
    alias_name = f"e2e-alias-{unique_suffix}"
    await es_adapter.ensure_index(
        index_name=index_name,
        mappings=PRODUCTS_INDEX_MAPPINGS,
        aliases=(alias_name,),
    )
    yield alias_name
    try:
        if es_adapter._client is not None:
            await es_adapter._client.indices.delete(index=index_name, ignore_unavailable=True)
    except Exception:
        pass


async def test_projection_indexes_es_and_invalidates_redis(
    es_adapter: ElasticsearchIndexAdapter,
    redis_adapter: RedisCacheAdapter,
    e2e_index: str,
) -> None:
    projector = ProductSearchProjector(
        indexer=es_adapter,
        cache=redis_adapter,
        index_alias=e2e_index,
    )

    t_id = TenantId(uuid.uuid4())
    p_id = ProductId(uuid.uuid4())
    s_id = SkuId(uuid.uuid4())

    prod_cache_key = default_key_builder.product(t_id, p_id)
    sku_cache_key = default_key_builder.sku(t_id, s_id)

    # 1. Warm Redis caches
    await redis_adapter.set(prod_cache_key, {"name": "Old Product"}, ttl=120)
    await redis_adapter.set(sku_cache_key, {"price": 1000}, ttl=120)
    assert await redis_adapter.get(prod_cache_key) is not None
    assert await redis_adapter.get(sku_cache_key) is not None

    # 2. Project ProductCreated
    created = ProductCreated(
        tenant_id=t_id,
        product_id=p_id,
        slug="leather-boots",
        name="Leather Boots",
    )
    await projector.handle_event(created)

    # Verify ES doc exists
    doc_id = build_product_document_id(t_id, p_id)
    doc = await es_adapter.get_document(index_alias=e2e_index, document_id=doc_id)
    assert doc is not None
    assert doc["name"] == "Leather Boots"
    assert doc["slug"] == "leather-boots"
    # Verify Redis cache invalidated
    assert await redis_adapter.get(prod_cache_key) is None

    # 3. Project SkuCreated
    await redis_adapter.set(prod_cache_key, {"name": "Cached Product"}, ttl=120)
    await redis_adapter.set(sku_cache_key, {"price": 5000}, ttl=120)

    sku_created = SkuCreated(
        tenant_id=t_id,
        product_id=p_id,
        sku_id=s_id,
        sku_code="BOOT-BRN-44",
        unit_price_minor=12000,
        currency="USD",
    )
    await projector.handle_event(sku_created)

    doc = await es_adapter.get_document(index_alias=e2e_index, document_id=doc_id)
    assert doc is not None
    assert len(doc["skus"]) == 1
    assert doc["skus"][0]["sku_code"] == "BOOT-BRN-44"
    assert doc["skus"][0]["unit_price_minor"] == 12000
    assert await redis_adapter.get(prod_cache_key) is None
    assert await redis_adapter.get(sku_cache_key) is None

    # 4. Project SkuPriceChanged
    sku_price = SkuPriceChanged(
        tenant_id=t_id,
        sku_id=s_id,
        previous_price_minor=12000,
        new_price_minor=11000,
        currency="USD",
        product_id=p_id,
    )
    await projector.handle_event(sku_price)

    doc = await es_adapter.get_document(index_alias=e2e_index, document_id=doc_id)
    assert doc is not None
    assert doc["skus"][0]["unit_price_minor"] == 11000


async def test_search_consumer_message_processing_and_idempotency(
    es_adapter: ElasticsearchIndexAdapter,
    redis_adapter: RedisCacheAdapter,
    e2e_index: str,
) -> None:
    import json
    from search.adapters.messaging import create_search_projection_consumer
    from shared.infrastructure.db.session import Database
    from tests.conftest import test_database_url as get_test_database_url
    from tests.integration.test_rabbitmq_retry import FakeIncomingMessage

    db = Database(get_test_database_url(), pool_size=2)
    try:
        consumer, projector = create_search_projection_consumer(
            database=db,
            indexer=es_adapter,
            cache=redis_adapter,
        )
        projector._index_alias = e2e_index

        t_id = str(uuid.uuid4())
        p_id = str(uuid.uuid4())
        event_id = str(uuid.uuid4())

        from sqlalchemy import text
        async with db.new_scope() as scope:
            await scope.session.execute(
                text(
                    "INSERT INTO tenants (id, slug, name, status, version) "
                    "VALUES (:id, :slug, 'Consumer Test Tenant', 'ACTIVE', 1)"
                ),
                {"id": uuid.UUID(t_id), "slug": f"consumer-{uuid.uuid4().hex[:6]}"},
            )
            await scope.commit()

        payload = {
            "event_id": event_id,
            "event_type": "ProductCreated",
            "tenant_id": t_id,
            "schema_version": 1,
            "correlation_id": str(uuid.uuid4()),
            "payload": {
                "tenant_id": t_id,
                "product_id": p_id,
                "slug": "ergonomic-chair",
                "name": "Ergonomic Chair",
                "version": 1,
            },
        }

        msg = FakeIncomingMessage(body=json.dumps(payload).encode("utf-8"), message_id=event_id)

        # 1. First delivery
        await consumer.process_message(msg)
        assert msg.acked is True

        # Check ES doc
        doc_id = f"{t_id}:{p_id}"
        doc = await es_adapter.get_document(index_alias=e2e_index, document_id=doc_id)
        assert doc is not None
        assert doc["name"] == "Ergonomic Chair"
        assert doc["slug"] == "ergonomic-chair"

        # 2. Duplicate delivery (should be recognized as duplicate and acked safely)
        dup_msg = FakeIncomingMessage(body=json.dumps(payload).encode("utf-8"), message_id=event_id)
        await consumer.process_message(dup_msg)
        assert dup_msg.acked is True
    finally:
        await db.dispose()

