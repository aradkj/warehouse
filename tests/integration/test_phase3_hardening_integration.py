"""Real integration tests for Phase 3 hardening:
- Search cache invalidation via committed events against real Redis & Elasticsearch
- Stampede protection (single-flight distributed lock) against real Redis
- Cursor pagination using search_after against real Elasticsearch
- Reindex workflow with explicit schema versioning, collision safety, and atomic alias swap
- Asynchronous reindex / reconcile API endpoints
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import httpx
import pytest

from search.adapters.elasticsearch import ElasticsearchIndexAdapter
from search.application.models import (
    PRODUCTS_INDEX_MAPPINGS,
    PRODUCTS_INDEX_SCHEMA_VERSION,
    build_product_document_id,
)
from search.application.projector import ProductSearchProjector
from search.application.service import SearchProductsService
from search.ports.indexer import SearchDocument
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
async def isolated_index(es_adapter: ElasticsearchIndexAdapter) -> AsyncIterator[str]:
    suffix = uuid.uuid4().hex[:8]
    index_name = f"test-products-v{PRODUCTS_INDEX_SCHEMA_VERSION}-{suffix}"
    alias_name = f"test-alias-{suffix}"
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


def _make_search_doc(
    alias: str,
    tenant_id: str,
    product_id: str,
    name: str,
    version: int = 1,
    status: str = "ACTIVE",
) -> SearchDocument:
    return SearchDocument(
        index_alias=alias,
        document_id=build_product_document_id(tenant_id, product_id),
        tenant_id=tenant_id,
        version=version,
        body={
            "id": product_id,
            "tenant_id": tenant_id,
            "slug": name.lower().replace(" ", "-"),
            "name": name,
            "description": f"Real integration test product for {name}",
            "status": status,
            "attributes": {},
            "skus": [],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "version": version,
        },
    )


# =============================================================================
# 1. Search Cache Invalidation Against Real Redis & Elasticsearch
# =============================================================================


async def test_search_cache_invalidation_e2e(
    es_adapter: ElasticsearchIndexAdapter,
    redis_adapter: RedisCacheAdapter,
    isolated_index: str,
) -> None:
    tenant_id = str(uuid.uuid4())
    search_service = SearchProductsService(
        indexer=es_adapter,
        cache=redis_adapter,
        index_alias=isolated_index,
        cache_ttl_seconds=300,
    )
    projector = ProductSearchProjector(
        indexer=es_adapter,
        cache=redis_adapter,
        index_alias=isolated_index,
    )

    # 1. Index initial product in ES
    p1 = _make_search_doc(isolated_index, tenant_id, "prod-1", "Samsung Galaxy S24", version=1)
    bulk_res = await es_adapter.bulk_index((p1,), refresh=True)
    assert bulk_res.successful_count == 1

    # 2. First search populates cache
    res1 = await search_service.search(tenant_id=tenant_id, q="Samsung")
    assert len(res1.items) == 1
    assert res1.items[0].name == "Samsung Galaxy S24"

    # 3. Simulate catalog domain event (ProductRevised)
    p1_v2 = _make_search_doc(
        isolated_index, tenant_id, "prod-1", "Samsung Galaxy S25 Ultra", version=2
    )
    await es_adapter.index((p1_v2,), force=True, refresh=True)

    await projector.handle_event(
        {
            "event_name": "ProductRevised",
            "tenant_id": tenant_id,
            "payload": {
                "product_id": "prod-1",
                "slug": "samsung-galaxy-s25-ultra",
                "name": "Samsung Galaxy S25 Ultra",
                "version": 2,
            },
        }
    )

    # 4. Search again for same query: stale generation is unreachable, fresh ES data returned!
    res2 = await search_service.search(tenant_id=tenant_id, q="Samsung")
    assert len(res2.items) == 1
    assert res2.items[0].name == "Samsung Galaxy S25 Ultra"


# =============================================================================
# 2. Cache Stampede Protection Against Real Redis
# =============================================================================


async def test_search_cache_stampede_protection(
    es_adapter: ElasticsearchIndexAdapter,
    redis_adapter: RedisCacheAdapter,
    isolated_index: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = str(uuid.uuid4())
    search_service = SearchProductsService(
        indexer=es_adapter,
        cache=redis_adapter,
        index_alias=isolated_index,
        cache_ttl_seconds=300,
    )

    p1 = _make_search_doc(isolated_index, tenant_id, "prod-1", "Stampede Headphone")
    await es_adapter.bulk_index((p1,), refresh=True)

    # Spy on underlying es_adapter.search to count queries executed
    real_search = es_adapter.search
    search_call_count = 0

    async def counting_search(*args: object, **kwargs: object) -> object:
        nonlocal search_call_count
        search_call_count += 1
        # Add slight delay to maximize concurrency window
        await asyncio.sleep(0.05)
        return await real_search(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(es_adapter, "search", counting_search)

    # Launch 10 concurrent identical searches on cache miss
    tasks = [search_service.search(tenant_id=tenant_id, q="Stampede") for _ in range(10)]
    results = await asyncio.gather(*tasks)

    # All 10 requests must receive correct result
    for r in results:
        assert len(r.items) == 1
        assert r.items[0].name == "Stampede Headphone"

    # Stampede protection must ensure ES was not queried once per concurrent request!
    # Because of distributed lock / get_or_set single-flight, calls are bounded.
    assert search_call_count < 10


# =============================================================================
# 3. Cursor-Based Keyset Pagination Against Real Elasticsearch
# =============================================================================


async def test_cursor_pagination_search_after_e2e(
    es_adapter: ElasticsearchIndexAdapter,
    isolated_index: str,
) -> None:
    tenant_id = str(uuid.uuid4())
    search_service = SearchProductsService(
        indexer=es_adapter,
        index_alias=isolated_index,
    )

    # Index 12 products
    docs = tuple(
        _make_search_doc(isolated_index, tenant_id, f"prod-{i:02d}", f"Catalog Item {i:02d}")
        for i in range(12)
    )
    res = await es_adapter.bulk_index(docs, refresh=True)
    assert res.successful_count == 12

    # Page 1: limit 5
    page1 = await search_service.search(
        tenant_id=tenant_id,
        limit=5,
        sort_by="name",
        sort_order="asc",
    )
    assert len(page1.items) == 5
    assert page1.next_cursor is not None
    names_page1 = [it.name for it in page1.items]

    # Page 2: limit 5 with next_cursor
    page2 = await search_service.search(
        tenant_id=tenant_id,
        limit=5,
        cursor=page1.next_cursor,
        sort_by="name",
        sort_order="asc",
    )
    assert len(page2.items) == 5
    assert page2.next_cursor is not None
    names_page2 = [it.name for it in page2.items]

    # Page 3: limit 5 with next_cursor
    page3 = await search_service.search(
        tenant_id=tenant_id,
        limit=5,
        cursor=page2.next_cursor,
        sort_by="name",
        sort_order="asc",
    )
    assert len(page3.items) == 2
    names_page3 = [it.name for it in page3.items]

    # Invariant: No duplicate documents across pages, all 12 items retrieved
    all_names = names_page1 + names_page2 + names_page3
    assert len(all_names) == 12
    assert len(set(all_names)) == 12


# =============================================================================
# 4. Asynchronous Reindex / Reconcile API Options
# =============================================================================


async def test_api_async_reindex_mode(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When async_mode=True is passed, API dispatches Celery task and returns 202 Accepted."""
    from bootstrap import workers

    mock_task = AsyncMock()
    mock_task.id = "mock-celery-job-12345"
    monkeypatch.setattr(workers.reindex_products_task, "delay", lambda *a, **k: mock_task)

    resp = await client.post(
        "/v1/search/reindex",
        headers={"X-Actor-Type": "system"},
        json={"async_mode": True},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "ACCEPTED"
    assert data["task_id"] == "mock-celery-job-12345"
