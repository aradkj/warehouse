"""Unit tests for ReindexingService: atomic alias swap, validation, and rollback."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from search.adapters.memory import InMemorySearchIndexer
from search.application.models import PRODUCTS_INDEX_ALIAS, build_product_document_id
from search.application.reindex import ReindexingService
from search.ports.indexer import SearchDocument
from search.ports.reader import CatalogSearchReader

pytestmark = pytest.mark.unit


class MockCatalogSearchReader(CatalogSearchReader):
    """In-memory mock reader implementing CatalogSearchReader protocol."""

    def __init__(self, documents: list[SearchDocument]) -> None:
        self._documents = documents

    async def get_search_document(
        self, *, tenant_id: str, product_id: str, target_alias: str = "products"
    ) -> SearchDocument | None:
        doc_id = build_product_document_id(tenant_id, product_id)
        for d in self._documents:
            if d.document_id == doc_id:
                return d
        return None

    async def stream_search_documents(
        self,
        *,
        tenant_id: str | None = None,
        target_alias: str = "products",
        batch_size: int = 200,
    ) -> AsyncIterator[tuple[SearchDocument, ...]]:
        filtered = [d for d in self._documents if tenant_id is None or d.tenant_id == tenant_id]
        for i in range(0, len(filtered), batch_size):
            # Retarget index_alias to the caller's target_alias
            batch = tuple(
                SearchDocument(
                    index_alias=target_alias,
                    document_id=d.document_id,
                    tenant_id=d.tenant_id,
                    version=d.version,
                    body=d.body,
                )
                for d in filtered[i : i + batch_size]
            )
            yield batch

    async def count_authoritative_products(self, *, tenant_id: str | None = None) -> int:
        if tenant_id is None:
            return len(self._documents)
        return sum(1 for d in self._documents if d.tenant_id == tenant_id)

    async def get_authoritative_keys(
        self, *, tenant_id: str | None = None
    ) -> dict[str, tuple[str, int]]:
        res = {}
        for d in self._documents:
            if tenant_id is None or d.tenant_id == tenant_id:
                res[d.document_id] = (d.tenant_id, d.version)
        return res

    async def stream_authoritative_keys(
        self, *, tenant_id: str | None = None, batch_size: int = 200
    ) -> AsyncIterator[dict[str, tuple[str, int]]]:
        all_keys = await self.get_authoritative_keys(tenant_id=tenant_id)
        items = list(all_keys.items())
        for i in range(0, len(items), batch_size):
            yield dict(items[i : i + batch_size])


def _make_doc(tenant_id: str, product_id: str, name: str) -> SearchDocument:
    return SearchDocument(
        index_alias=PRODUCTS_INDEX_ALIAS,
        document_id=build_product_document_id(tenant_id, product_id),
        tenant_id=tenant_id,
        version=1,
        body={
            "id": product_id,
            "tenant_id": tenant_id,
            "slug": name.lower(),
            "name": name,
            "status": "ACTIVE",
            "version": 1,
        },
    )


async def test_successful_reindex_workflow() -> None:
    indexer = InMemorySearchIndexer()
    tenant_1 = str(uuid.uuid4())
    tenant_2 = str(uuid.uuid4())

    # Pre-existing active index
    old_index = "products-old"
    await indexer.ensure_index(index_name=old_index, mappings={}, aliases=(PRODUCTS_INDEX_ALIAS,))
    # Old stale document in the old index
    stale_doc = _make_doc(tenant_1, "stale-id", "Stale Widget")
    await indexer.index((stale_doc,))

    # Source PostgreSQL database has 2 fresh products
    pg_docs = [
        _make_doc(tenant_1, "p1", "Product 1"),
        _make_doc(tenant_2, "p2", "Product 2"),
    ]
    reader = MockCatalogSearchReader(pg_docs)

    service = ReindexingService(
        indexer=indexer,
        reader=reader,
        batch_size=10,
        alias=PRODUCTS_INDEX_ALIAS,
    )

    result = await service.reindex(retain_old_indices=False)

    assert result.status == "COMPLETED"
    assert result.total_indexed == 2
    assert result.index_name != old_index

    # Verify alias now points to the new index
    active_indices = await indexer.list_indices_for_alias(alias=PRODUCTS_INDEX_ALIAS)
    assert active_indices == (result.index_name,)

    # Verify old index was deleted
    assert old_index not in indexer._indices

    # Verify new index contains the 2 documents
    doc1 = await indexer.get_document(
        index_alias=PRODUCTS_INDEX_ALIAS, document_id=build_product_document_id(tenant_1, "p1")
    )
    assert doc1 is not None
    assert doc1["name"] == "Product 1"

    # Verify stale document was purged
    stale = await indexer.get_document(
        index_alias=PRODUCTS_INDEX_ALIAS,
        document_id=build_product_document_id(tenant_1, "stale-id"),
    )
    assert stale is None


async def test_reindex_failure_safety_and_rollback() -> None:
    indexer = InMemorySearchIndexer()
    tenant_id = str(uuid.uuid4())

    # Healthy active index in production
    active_index = "products-active"
    await indexer.ensure_index(
        index_name=active_index, mappings={}, aliases=(PRODUCTS_INDEX_ALIAS,)
    )
    active_doc = _make_doc(tenant_id, "healthy", "Healthy Production Item")
    await indexer.index((active_doc,))

    # Faulty reader that reports count = 5 but streams 0 docs (simulating data corruption)
    class BrokenReader(MockCatalogSearchReader):
        async def count_authoritative_products(self, *, tenant_id: str | None = None) -> int:
            return 5

    reader = BrokenReader([])
    service = ReindexingService(indexer=indexer, reader=reader, alias=PRODUCTS_INDEX_ALIAS)

    # Reindex must fail validation and raise RuntimeError
    with pytest.raises(RuntimeError, match="Reindex verification failed"):
        await service.reindex()

    # CRITICAL INVARIANT: The healthy active index must still be active!
    active_indices = await indexer.list_indices_for_alias(alias=PRODUCTS_INDEX_ALIAS)
    assert active_indices == (active_index,)

    # The original healthy document must still be intact and searchable
    doc = await indexer.get_document(
        index_alias=PRODUCTS_INDEX_ALIAS,
        document_id=build_product_document_id(tenant_id, "healthy"),
    )
    assert doc is not None
    assert doc["name"] == "Healthy Production Item"


async def test_tenant_reindex_preserves_cross_tenant_products_on_shared_alias() -> None:
    """Reindexing a single tenant MUST NOT wipe out other tenants' products from shared alias."""
    indexer = InMemorySearchIndexer()
    tenant_1 = str(uuid.uuid4())
    tenant_2 = str(uuid.uuid4())

    # Pre-existing active index with products from both tenants
    old_index = "products-old"
    await indexer.ensure_index(index_name=old_index, mappings={}, aliases=(PRODUCTS_INDEX_ALIAS,))
    await indexer.index(
        (
            _make_doc(tenant_1, "p1", "Tenant 1 Old Widget"),
            _make_doc(tenant_2, "p2", "Tenant 2 Critical Widget"),
        )
    )

    # Authoritative DB has products for both tenants
    pg_docs = [
        _make_doc(tenant_1, "p1", "Tenant 1 Updated Widget"),
        _make_doc(tenant_2, "p2", "Tenant 2 Critical Widget"),
    ]
    reader = MockCatalogSearchReader(pg_docs)
    service = ReindexingService(indexer=indexer, reader=reader, alias=PRODUCTS_INDEX_ALIAS)

    # Reindex triggered specifically for tenant_1
    result = await service.reindex(tenant_id=tenant_1)
    assert result.status == "COMPLETED"

    # Both tenant_1 and tenant_2 products MUST be present in the new active index
    t1_doc = await indexer.get_document(
        index_alias=PRODUCTS_INDEX_ALIAS,
        document_id=build_product_document_id(tenant_1, "p1"),
    )
    assert t1_doc is not None
    assert t1_doc["name"] == "Tenant 1 Updated Widget"

    t2_doc = await indexer.get_document(
        index_alias=PRODUCTS_INDEX_ALIAS,
        document_id=build_product_document_id(tenant_2, "p2"),
    )
    assert t2_doc is not None, "Tenant 2 product was erroneously deleted during Tenant 1 reindex!"
    assert t2_doc["name"] == "Tenant 2 Critical Widget"


async def test_reindex_lock_cleanup_on_ensure_index_failure() -> None:
    """Lock must be safely released even if ensure_index raises before the bulk index loop."""
    reader = MockCatalogSearchReader([])

    class FailingIndexer(InMemorySearchIndexer):
        async def ensure_index(self, index_name: str, mappings: dict, aliases=()) -> None:
            raise ConnectionError("Elasticsearch cluster unreachable during index creation")

    class FakeLockCache:
        def __init__(self) -> None:
            self.lock_held = False
            self.token: str | None = None
            self.released_token: str | None = None

        async def acquire_lock(self, key: str, ttl: int = 600) -> str | None:
            self.lock_held = True
            self.token = "test-token-123"
            return self.token

        async def release_lock(self, key: str, token: str) -> bool:
            if self.token == token:
                self.lock_held = False
                self.released_token = token
                return True
            return False

    fake_cache = FakeLockCache()
    service = ReindexingService(
        indexer=FailingIndexer(),
        reader=reader,
        cache=fake_cache,  # type: ignore[arg-type]
        alias=PRODUCTS_INDEX_ALIAS,
    )

    with pytest.raises(ConnectionError, match="Elasticsearch cluster unreachable"):
        await service.reindex()

    # CRITICAL: Lock must be released despite failure in ensure_index!
    assert not fake_cache.lock_held
    assert fake_cache.released_token == "test-token-123"
