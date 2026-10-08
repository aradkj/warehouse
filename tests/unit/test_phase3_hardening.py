"""Unit tests verifying Phase 3 hardening requirements.

Tests cover:
- Cursor validation, encoding/decoding, tamper protection, tenant isolation
- Search cache versioning, generation invalidation, cross-tenant isolation
- Bulk index retry logic and permanent failure classification
- Keyset pagination and bounded-memory reconciliation
- Concurrent reindexing collision prevention via distributed locks
"""

from __future__ import annotations

import base64
import json
import uuid
from unittest.mock import AsyncMock

import pytest
from tests.unit.test_reindex_unit import MockCatalogSearchReader

from search.adapters.memory import InMemorySearchIndexer
from search.application.models import (
    PRODUCTS_INDEX_ALIAS,
    PRODUCTS_INDEX_SCHEMA_VERSION,
    build_product_document_id,
)
from search.application.projector import ProductSearchProjector
from search.application.reconciliation import ReconciliationService
from search.application.reindex import ReindexingService
from search.application.service import SearchProductsService
from search.ports.indexer import (
    BulkIndexResult,
    SearchDocument,
)
from shared.application.errors import ConcurrencyConflict, InvalidRequest
from shared.infrastructure.cache.keys import default_key_builder
from shared.infrastructure.cache.memory import InMemoryCache
from shared.ports.cache import CachePort

pytestmark = pytest.mark.unit


def _create_doc(
    tenant_id: str, product_id: str, name: str, version: int = 1, status: str = "ACTIVE"
) -> SearchDocument:
    return SearchDocument(
        index_alias=PRODUCTS_INDEX_ALIAS,
        document_id=build_product_document_id(tenant_id, product_id),
        tenant_id=tenant_id,
        version=version,
        body={
            "id": product_id,
            "tenant_id": tenant_id,
            "slug": name.lower().replace(" ", "-"),
            "name": name,
            "description": f"Description for {name}",
            "status": status,
            "attributes": {},
            "skus": [],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "version": version,
        },
    )


# =============================================================================
# ISSUE 1 & 2: Search Cache Invalidation & Stampede Protection
# =============================================================================


async def test_search_cache_generation_bump_invalidates_stale_results() -> None:
    """Test A: Search result cached -> Catalog event bumps version -> fresh ES result returned."""
    tenant_id = str(uuid.uuid4())
    indexer = InMemorySearchIndexer()
    cache = InMemoryCache()
    service = SearchProductsService(indexer=indexer, cache=cache, cache_ttl_seconds=300)

    doc_v1 = _create_doc(tenant_id, "prod-1", "Original iPhone", version=1)
    await indexer.index((doc_v1,))

    # 1. Search cached
    res1 = await service.search(tenant_id=tenant_id, q="iPhone")
    assert len(res1.items) == 1
    assert res1.items[0].name == "Original iPhone"

    # 2. Simulate Catalog domain event through projector
    projector = ProductSearchProjector(indexer=indexer, cache=cache)

    # Update product in indexer and trigger event
    doc_v2 = _create_doc(tenant_id, "prod-1", "Updated iPhone 16", version=2)
    await indexer.index((doc_v2,), force=True)

    await projector.handle_event(
        {
            "event_name": "ProductRevised",
            "tenant_id": tenant_id,
            "payload": {
                "product_id": "prod-1",
                "slug": "updated-iphone-16",
                "name": "Updated iPhone 16",
                "version": 2,
            },
        }
    )

    # 3. Subsequent search for same query must return fresh Elasticsearch result!
    res2 = await service.search(tenant_id=tenant_id, q="iPhone")
    assert len(res2.items) == 1
    assert res2.items[0].name == "Updated iPhone 16"


async def test_search_cache_invalidation_cross_tenant_isolation() -> None:
    """Test D: Updating tenant A must never invalidate or affect tenant B's search cache."""
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    indexer = InMemorySearchIndexer()
    cache = InMemoryCache()
    service = SearchProductsService(indexer=indexer, cache=cache, cache_ttl_seconds=300)

    doc_a = _create_doc(tenant_a, "prod-a", "Tenant A Widget")
    doc_b = _create_doc(tenant_b, "prod-b", "Tenant B Widget")
    await indexer.index((doc_a, doc_b))

    # Search for both tenants
    await service.search(tenant_id=tenant_a, q="Widget")
    await service.search(tenant_id=tenant_b, q="Widget")

    # Verify initial generations
    ver_a_key = default_key_builder.search_version(tenant_a)
    ver_b_key = default_key_builder.search_version(tenant_b)

    # Update Tenant A only
    projector = ProductSearchProjector(indexer=indexer, cache=cache)
    await projector.handle_event(
        {
            "event_name": "ProductRevised",
            "tenant_id": tenant_a,
            "payload": {
                "product_id": "prod-a",
                "slug": "prod-a",
                "name": "Tenant A Widget",
                "version": 2,
            },
        }
    )

    ver_a = await cache.get(ver_a_key)
    ver_b = await cache.get(ver_b_key)

    # Tenant A version incremented; Tenant B version remains untouched
    assert ver_a is not None and int(ver_a) >= 1
    assert ver_b is None  # Never incremented


async def test_catalog_update_redis_outage_graceful_degradation() -> None:
    """Test C: Catalog update while Redis is unavailable succeeds without breaking domain flow."""
    tenant_id = str(uuid.uuid4())
    indexer = InMemorySearchIndexer()
    failing_cache = AsyncMock(spec=CachePort)
    failing_cache.increment.side_effect = RuntimeError("Redis connection lost")

    projector = ProductSearchProjector(indexer=indexer, cache=failing_cache)

    # Event handling MUST NOT throw despite Redis failure
    await projector.handle_event(
        {
            "event_name": "ProductCreated",
            "tenant_id": tenant_id,
            "payload": {
                "product_id": "prod-1",
                "slug": "resilient-item",
                "name": "Resilient Item",
                "version": 1,
            },
        }
    )


# =============================================================================
# ISSUE 3: Cursor Pagination
# =============================================================================


async def test_cursor_pagination_tamper_protection_and_validation() -> None:
    """Cursors must be opaque, validated, and reject mismatched tenant, query, or malformed data."""
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    indexer = InMemorySearchIndexer()
    service = SearchProductsService(indexer=indexer)

    # 1. Malformed base64 cursor
    with pytest.raises(InvalidRequest, match="Invalid search cursor"):
        await service.search(tenant_id=tenant_a, cursor="invalid-not-base64!!!")

    # 2. Cursor with mismatched tenant
    token_payload = {
        "v": 1,
        "t": tenant_b,  # Belongs to tenant B!
        "qh": "some-hash",
        "sa": ["2026-01-01", "id-1"],
    }
    encoded_token = base64.urlsafe_b64encode(json.dumps(token_payload).encode("utf-8")).decode()

    with pytest.raises(InvalidRequest, match="Search cursor does not match authenticated tenant"):
        await service.search(tenant_id=tenant_a, cursor=encoded_token)

    # 3. Cursor with incompatible query parameters (mismatched hash)
    token_tenant_a = {
        "v": 1,
        "t": tenant_a,
        "qh": "different-query-hash-value",
        "sa": ["2026-01-01", "id-1"],
    }
    encoded_mismatch = base64.urlsafe_b64encode(json.dumps(token_tenant_a).encode("utf-8")).decode()

    with pytest.raises(
        InvalidRequest, match="Search cursor cannot be used with different query, filter, or sort"
    ):
        await service.search(tenant_id=tenant_a, q="Brand New Query", cursor=encoded_mismatch)


# =============================================================================
# ISSUE 4: Bulk Failure Handling
# =============================================================================


def test_bulk_index_result_semantics() -> None:
    """BulkIndexResult exposes structured metrics and backwards-compatible integer evaluation."""
    success_result = BulkIndexResult(
        successful_count=100,
        failed_count=0,
        retry_count=0,
        permanent_failure_count=0,
    )
    assert success_result.is_success is True
    assert int(success_result) == 100

    failed_result = BulkIndexResult(
        successful_count=95,
        failed_count=5,
        retry_count=3,
        permanent_failure_count=2,
    )
    assert failed_result.is_success is False
    assert int(failed_result) == 95
    assert failed_result.permanent_failure_count == 2


# =============================================================================
# ISSUE 5: Memory-Bounded Reconciliation
# =============================================================================


async def test_reconciliation_memory_bounded_streaming() -> None:
    """Reconciliation must process in bounded chunks rather than fetching full datasets."""
    tenant_id = str(uuid.uuid4())
    indexer = InMemorySearchIndexer()

    # Create 5 documents
    docs = [_create_doc(tenant_id, f"prod-{i}", f"Item {i}", version=1) for i in range(5)]
    await indexer.index(tuple(docs))

    # Mock reader with small batch size
    reader = MockCatalogSearchReader(docs)
    service = ReconciliationService(
        indexer=indexer, reader=reader, batch_size=2, alias=PRODUCTS_INDEX_ALIAS
    )

    result = await service.reconcile(tenant_id=tenant_id)
    assert result.total_authoritative == 5
    assert result.total_search == 5
    assert result.matched == 5
    assert len(result.discrepancies) == 0


async def test_reconciliation_detects_all_discrepancy_modes() -> None:
    """Detects missing in ES, missing in PG, stale version, and auto-repairs without mutating PG."""
    tenant_id = str(uuid.uuid4())
    indexer = InMemorySearchIndexer()

    doc_in_sync = _create_doc(tenant_id, "p1", "Synced Item", version=1)
    doc_missing_in_es = _create_doc(tenant_id, "p2", "Missing in ES", version=1)
    doc_stale_in_es_pg = _create_doc(tenant_id, "p3", "Stale in ES", version=3)
    doc_stale_in_es_idx = _create_doc(tenant_id, "p3", "Stale in ES", version=1)
    doc_orphan_in_es = _create_doc(tenant_id, "p4", "Ghost in ES", version=1)

    # PG authoritative has: p1, p2, p3 (ver 3)
    reader = MockCatalogSearchReader([doc_in_sync, doc_missing_in_es, doc_stale_in_es_pg])

    # ES has: p1, p3 (ver 1), p4
    await indexer.index((doc_in_sync, doc_stale_in_es_idx, doc_orphan_in_es))

    service = ReconciliationService(
        indexer=indexer, reader=reader, batch_size=2, alias=PRODUCTS_INDEX_ALIAS
    )

    # 1. Detect discrepancies
    result = await service.reconcile(tenant_id=tenant_id, auto_repair=False)
    types = {d.discrepancy_type for d in result.discrepancies}
    assert "missing_in_elasticsearch" in types
    assert "version_mismatch" in types
    assert "missing_in_postgresql" in types

    # 2. Unidirectional auto-repair (PG -> ES only)
    repair_result = await service.reconcile(tenant_id=tenant_id, auto_repair=True)
    assert repair_result.repaired_count == 3

    # Verify ES is in sync now
    after_result = await service.reconcile(tenant_id=tenant_id, auto_repair=False)
    assert len(after_result.discrepancies) == 0
    assert after_result.matched == 3


# =============================================================================
# ISSUE 6 & 7: Concurrent Reindexing Collision & Safety
# =============================================================================


async def test_concurrent_reindex_collision_prevention() -> None:
    """Concurrent reindexing attempts must fail fast with ConcurrencyConflict if lock is held."""
    indexer = InMemorySearchIndexer()
    cache = InMemoryCache()
    reader = MockCatalogSearchReader([])
    service = ReindexingService(indexer=indexer, reader=reader, cache=cache)

    # Manually hold lock
    lock_key = f"lock:reindex:{PRODUCTS_INDEX_ALIAS}"
    await cache.acquire_lock(lock_key, ttl=300)

    # Attempting reindex while lock is held must raise ConcurrencyConflict
    with pytest.raises(ConcurrencyConflict, match="Reindexing currently in progress"):
        await service.reindex()


async def test_reindex_index_naming_explicit_version() -> None:
    """Index names must include alias, schema version, and collision-safe random suffix."""
    indexer = InMemorySearchIndexer()
    reader = MockCatalogSearchReader([])
    service = ReindexingService(indexer=indexer, reader=reader)

    res = await service.reindex()
    expected_prefix = f"{PRODUCTS_INDEX_ALIAS}-v{PRODUCTS_INDEX_SCHEMA_VERSION}-"
    assert res.index_name.startswith(expected_prefix)
