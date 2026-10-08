"""Unit tests for Search caching, tenant key isolation, and graceful degradation."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from search.adapters.memory import InMemorySearchIndexer
from search.application.models import PRODUCTS_INDEX_ALIAS, build_product_document_id
from search.application.service import SearchProductsService
from search.ports.indexer import SearchDocument
from shared.infrastructure.cache.keys import default_key_builder
from shared.infrastructure.cache.memory import InMemoryCache
from shared.ports.cache import CachePort

pytestmark = pytest.mark.unit


def _make_doc(tenant_id: str, product_id: str, name: str) -> SearchDocument:
    return SearchDocument(
        index_alias=PRODUCTS_INDEX_ALIAS,
        document_id=build_product_document_id(tenant_id, product_id),
        tenant_id=tenant_id,
        version=1,
        body={
            "id": product_id,
            "tenant_id": tenant_id,
            "slug": name.lower().replace(" ", "-"),
            "name": name,
            "description": None,
            "status": "ACTIVE",
            "attributes": {},
            "skus": [],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "version": 1,
        },
    )


def test_search_cache_key_tenant_isolation() -> None:
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    query_hash = "abcdef0123456789"

    key_a = default_key_builder.search(tenant_a, "products", query_hash)
    key_b = default_key_builder.search(tenant_b, "products", query_hash)

    assert key_a == f"v1:{tenant_a}:search:products:{query_hash}"
    assert key_b == f"v1:{tenant_b}:search:products:{query_hash}"
    assert key_a != key_b


async def test_search_cache_hit_and_miss() -> None:
    tenant_id = str(uuid.uuid4())
    indexer = InMemorySearchIndexer()
    cache = InMemoryCache()
    service = SearchProductsService(indexer=indexer, cache=cache, cache_ttl_seconds=300)

    doc = _make_doc(tenant_id, str(uuid.uuid4()), "Cached Item")
    await indexer.index((doc,))

    # First search: cache miss -> indexer queried -> cache stored
    res1 = await service.search(tenant_id=tenant_id, q="Cached")
    assert res1.total == 1
    assert res1.items[0].name == "Cached Item"

    # Now verify cache has an entry
    hash_str = service._compute_query_hash(
        q="Cached",
        status=None,
        sku=None,
        page=1,
        page_size=20,
        sort_by="created_at",
        sort_order="desc",
    )
    cache_key = default_key_builder.search(tenant_id, "products", hash_str)
    cached_payload = await cache.get(cache_key)
    assert cached_payload is not None
    assert cached_payload["total"] == 1

    # Modify indexer directly (simulate document removal without cache invalidation)
    await indexer.delete(index_alias=PRODUCTS_INDEX_ALIAS, document_ids=(doc.document_id,))

    # Second search with service: should hit cache and still return the cached result!
    res2 = await service.search(tenant_id=tenant_id, q="Cached")
    assert res2.total == 1
    assert res2.items[0].name == "Cached Item"


async def test_search_graceful_degradation_on_cache_failure() -> None:
    tenant_id = str(uuid.uuid4())
    indexer = InMemorySearchIndexer()

    # Broken cache mock that throws exceptions on every operation
    failing_cache = AsyncMock(spec=CachePort)
    failing_cache.get.side_effect = RuntimeError("Redis connection timeout")
    failing_cache.set.side_effect = RuntimeError("Redis connection timeout")

    service = SearchProductsService(indexer=indexer, cache=failing_cache)

    doc = _make_doc(tenant_id, str(uuid.uuid4()), "Resilient Product")
    await indexer.index((doc,))

    # Search MUST NOT raise 500 or crash merely because Redis is unavailable:
    result = await service.search(tenant_id=tenant_id)
    assert result.total == 1
    assert result.items[0].name == "Resilient Product"
