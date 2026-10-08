"""Unit tests for ProductSearchProjector and cache invalidation."""

from __future__ import annotations

import uuid

import pytest

from catalog.ports.events import (
    ProductActivated,
    ProductArchived,
    ProductCreated,
    ProductRevised,
    SkuActivated,
    SkuCreated,
    SkuDeactivated,
    SkuPriceChanged,
)
from search.adapters.memory import InMemorySearchIndexer
from search.application.models import PRODUCTS_INDEX_ALIAS, build_product_document_id
from search.application.projector import ProductSearchProjector
from shared.domain.identifiers import ProductId, SkuId, TenantId
from shared.infrastructure.cache.keys import default_key_builder
from shared.infrastructure.cache.memory import InMemoryCache

pytestmark = [pytest.mark.unit, pytest.mark.projection]


@pytest.mark.asyncio
async def test_projector_product_lifecycle_and_cache_invalidation() -> None:
    indexer = InMemorySearchIndexer()
    cache = InMemoryCache()
    projector = ProductSearchProjector(indexer=indexer, cache=cache)

    t_id = TenantId(uuid.uuid4())
    p_id = ProductId(uuid.uuid4())
    prod_cache_key = default_key_builder.product(t_id, p_id)

    # Pre-populate cache to test invalidation
    await cache.set(prod_cache_key, {"cached": "data"}, ttl=60)
    assert await cache.get(prod_cache_key) is not None

    # 1. ProductCreated
    created_event = ProductCreated(
        tenant_id=t_id,
        product_id=p_id,
        slug="blue-jeans",
        name="Blue Jeans",
    )
    await projector.handle_event(created_event)

    doc_id = build_product_document_id(t_id, p_id)
    doc = await indexer.get_document(index_alias=PRODUCTS_INDEX_ALIAS, document_id=doc_id)
    assert doc is not None
    assert doc["name"] == "Blue Jeans"
    assert doc["slug"] == "blue-jeans"
    assert doc["status"] == "DRAFT"
    # Verify cache key was invalidated
    assert await cache.get(prod_cache_key) is None

    # 2. ProductRevised
    await cache.set(prod_cache_key, {"cached": "revised"}, ttl=60)
    revised_event = ProductRevised(
        tenant_id=t_id,
        product_id=p_id,
        slug="blue-denim-jeans",
        name="Blue Denim Jeans",
    )
    await projector.handle_event(revised_event)

    doc = await indexer.get_document(index_alias=PRODUCTS_INDEX_ALIAS, document_id=doc_id)
    assert doc is not None
    assert doc["slug"] == "blue-denim-jeans"
    assert doc["name"] == "Blue Denim Jeans"
    assert doc["version"] == 2
    assert await cache.get(prod_cache_key) is None

    # 3. ProductActivated
    await cache.set(prod_cache_key, {"cached": "active"}, ttl=60)
    activated_event = ProductActivated(tenant_id=t_id, product_id=p_id)
    await projector.handle_event(activated_event)

    doc = await indexer.get_document(index_alias=PRODUCTS_INDEX_ALIAS, document_id=doc_id)
    assert doc is not None
    assert doc["status"] == "ACTIVE"
    assert doc["version"] == 3
    assert await cache.get(prod_cache_key) is None

    # 4. ProductArchived
    await cache.set(prod_cache_key, {"cached": "archived"}, ttl=60)
    archived_event = ProductArchived(tenant_id=t_id, product_id=p_id)
    await projector.handle_event(archived_event)

    doc = await indexer.get_document(index_alias=PRODUCTS_INDEX_ALIAS, document_id=doc_id)
    assert doc is not None
    assert doc["status"] == "ARCHIVED"
    assert doc["version"] == 4
    assert await cache.get(prod_cache_key) is None


@pytest.mark.asyncio
async def test_projector_sku_lifecycle_and_cache_invalidation() -> None:
    indexer = InMemorySearchIndexer()
    cache = InMemoryCache()
    projector = ProductSearchProjector(indexer=indexer, cache=cache)

    t_id = TenantId(uuid.uuid4())
    p_id = ProductId(uuid.uuid4())
    s_id = SkuId(uuid.uuid4())
    prod_cache_key = default_key_builder.product(t_id, p_id)
    sku_cache_key = default_key_builder.sku(t_id, s_id)

    # 1. ProductCreated
    await projector.handle_event(
        ProductCreated(tenant_id=t_id, product_id=p_id, slug="shoes", name="Running Shoes")
    )

    # 2. SkuCreated
    await cache.set(prod_cache_key, {"cached": "prod"}, ttl=60)
    await cache.set(sku_cache_key, {"cached": "sku"}, ttl=60)

    sku_created = SkuCreated(
        tenant_id=t_id,
        product_id=p_id,
        sku_id=s_id,
        sku_code="SHOE-BLK-42",
        unit_price_minor=9900,
        currency="USD",
    )
    await projector.handle_event(sku_created)

    doc_id = build_product_document_id(t_id, p_id)
    doc = await indexer.get_document(index_alias=PRODUCTS_INDEX_ALIAS, document_id=doc_id)
    assert doc is not None
    assert len(doc["skus"]) == 1
    assert doc["skus"][0]["sku_code"] == "SHOE-BLK-42"
    assert doc["skus"][0]["unit_price_minor"] == 9900
    assert doc["skus"][0]["status"] == "ACTIVE"
    # Both product and SKU caches must be invalidated
    assert await cache.get(prod_cache_key) is None
    assert await cache.get(sku_cache_key) is None

    # 3. SkuPriceChanged
    await cache.set(prod_cache_key, {"cached": "prod"}, ttl=60)
    await cache.set(sku_cache_key, {"cached": "sku"}, ttl=60)

    sku_price = SkuPriceChanged(
        tenant_id=t_id,
        sku_id=s_id,
        previous_price_minor=9900,
        new_price_minor=8500,
        currency="USD",
        product_id=p_id,
    )
    await projector.handle_event(sku_price)

    doc = await indexer.get_document(index_alias=PRODUCTS_INDEX_ALIAS, document_id=doc_id)
    assert doc is not None
    assert doc["skus"][0]["unit_price_minor"] == 8500
    assert await cache.get(prod_cache_key) is None
    assert await cache.get(sku_cache_key) is None

    # 4. SkuDeactivated
    await cache.set(prod_cache_key, {"cached": "prod"}, ttl=60)
    await cache.set(sku_cache_key, {"cached": "sku"}, ttl=60)

    sku_deact = SkuDeactivated(tenant_id=t_id, sku_id=s_id, product_id=p_id)
    await projector.handle_event(sku_deact)

    doc = await indexer.get_document(index_alias=PRODUCTS_INDEX_ALIAS, document_id=doc_id)
    assert doc is not None
    assert doc["skus"][0]["status"] == "INACTIVE"
    assert await cache.get(prod_cache_key) is None
    assert await cache.get(sku_cache_key) is None

    # 5. SkuActivated
    sku_act = SkuActivated(tenant_id=t_id, sku_id=s_id, product_id=p_id)
    await projector.handle_event(sku_act)

    doc = await indexer.get_document(index_alias=PRODUCTS_INDEX_ALIAS, document_id=doc_id)
    assert doc is not None
    assert doc["skus"][0]["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_projector_multi_tenant_isolation() -> None:
    indexer = InMemorySearchIndexer()
    projector = ProductSearchProjector(indexer=indexer)

    t1 = TenantId(uuid.uuid4())
    t2 = TenantId(uuid.uuid4())
    shared_prod_id = ProductId(uuid.uuid4())

    await projector.handle_event(
        ProductCreated(tenant_id=t1, product_id=shared_prod_id, slug="item", name="Tenant 1 Item")
    )
    await projector.handle_event(
        ProductCreated(tenant_id=t2, product_id=shared_prod_id, slug="item", name="Tenant 2 Item")
    )

    doc_id1 = build_product_document_id(t1, shared_prod_id)
    doc_id2 = build_product_document_id(t2, shared_prod_id)
    doc_t1 = await indexer.get_document(index_alias=PRODUCTS_INDEX_ALIAS, document_id=doc_id1)
    doc_t2 = await indexer.get_document(index_alias=PRODUCTS_INDEX_ALIAS, document_id=doc_id2)

    assert doc_t1 is not None and doc_t2 is not None
    assert doc_t1["tenant_id"] == str(t1)
    assert doc_t2["tenant_id"] == str(t2)
    assert doc_t1["name"] == "Tenant 1 Item"
    assert doc_t2["name"] == "Tenant 2 Item"


@pytest.mark.asyncio
async def test_projector_raw_event_dictionary_handling() -> None:
    indexer = InMemorySearchIndexer()
    cache = InMemoryCache()
    projector = ProductSearchProjector(indexer=indexer, cache=cache)

    t_id = str(uuid.uuid4())
    p_id = str(uuid.uuid4())

    msg = {
        "event_name": "ProductCreated",
        "payload": {
            "tenant_id": t_id,
            "product_id": p_id,
            "slug": "hoodie",
            "name": "Cozy Hoodie",
            "version": 1,
        },
    }
    await projector.handle_event(msg)

    doc_id = build_product_document_id(t_id, p_id)
    doc = await indexer.get_document(index_alias=PRODUCTS_INDEX_ALIAS, document_id=doc_id)
    assert doc is not None
    assert doc["name"] == "Cozy Hoodie"
    assert doc["slug"] == "hoodie"


@pytest.mark.asyncio
async def test_projector_graceful_cache_failure() -> None:
    class FailingCache(InMemoryCache):
        async def delete(self, key: str) -> bool:
            raise RuntimeError("Redis connection timeout")

    indexer = InMemorySearchIndexer()
    failing_cache = FailingCache()
    projector = ProductSearchProjector(indexer=indexer, cache=failing_cache)

    t_id = TenantId(uuid.uuid4())
    p_id = ProductId(uuid.uuid4())

    await projector.handle_event(
        ProductCreated(tenant_id=t_id, product_id=p_id, slug="desk", name="Standing Desk")
    )

    doc_id = build_product_document_id(t_id, p_id)
    doc = await indexer.get_document(index_alias=PRODUCTS_INDEX_ALIAS, document_id=doc_id)
    assert doc is not None
    assert doc["name"] == "Standing Desk"
