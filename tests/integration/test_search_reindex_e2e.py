"""End-to-end integration tests for Search, Reindexing, and Reconciliation with real services."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import insert, select

from catalog.adapters.persistence.tables import products, skus
from identity.adapters.persistence.tables import tenants
from search.adapters.elasticsearch import ElasticsearchIndexAdapter
from search.adapters.postgres import SqlAlchemyCatalogSearchReader
from search.application.models import PRODUCTS_INDEX_MAPPINGS
from search.application.reconciliation import ReconciliationService
from search.application.reindex import ReindexingService
from search.application.service import SearchProductsService
from shared.infrastructure.cache.redis import RedisCacheAdapter
from shared.infrastructure.db.session import Database

pytestmark = [pytest.mark.integration, pytest.mark.elasticsearch, pytest.mark.redis]

ES_URL = os.environ.get(
    "ARAD_ELASTICSEARCH_URL", os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")
)
REDIS_URL = os.environ.get("ARAD_REDIS_URL", "redis://localhost:6379/0")
DB_URL = os.environ.get(
    "ARAD_TEST_DATABASE_URL", "postgresql+asyncpg://arad:arad@localhost:5432/arad_test"
)


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
async def test_db() -> AsyncIterator[Database]:
    db = Database(DB_URL)
    yield db
    await db.dispose()


@pytest.fixture
async def e2e_alias(es_adapter: ElasticsearchIndexAdapter) -> AsyncIterator[str]:
    suffix = uuid.uuid4().hex[:8]
    initial_index = f"e2e-reindex-{suffix}-v1"
    alias_name = f"e2e-reindex-alias-{suffix}"

    await es_adapter.ensure_index(
        index_name=initial_index,
        mappings=PRODUCTS_INDEX_MAPPINGS,
        aliases=(alias_name,),
    )
    yield alias_name

    # Cleanup any indices created for this test alias
    try:
        indices = await es_adapter.list_indices_for_alias(alias=alias_name)
        for idx in indices:
            await es_adapter.delete_index(index_name=idx)
    except Exception:
        pass


async def _seed_product(
    db: Database, tenant_id: uuid.UUID, product_id: uuid.UUID, slug: str, name: str
) -> None:
    async with db.session_factory() as session:
        # Ensure tenant exists
        existing_t = await session.execute(select(tenants.c.id).where(tenants.c.id == tenant_id))
        if existing_t.scalar_one_or_none() is None:
            await session.execute(
                insert(tenants).values(
                    id=tenant_id,
                    slug=f"t-{tenant_id.hex[:6]}",
                    name="Tenant",
                    version=1,
                )
            )
        await session.execute(
            insert(products).values(
                id=product_id,
                tenant_id=tenant_id,
                slug=slug,
                name=name,
                description=f"Desc for {name}",
                status="ACTIVE",
                attributes={},
                version=1,
            )
        )
        sku_id = uuid.uuid4()
        await session.execute(
            insert(skus).values(
                id=sku_id,
                tenant_id=tenant_id,
                product_id=product_id,
                code=f"SKU-{slug.upper()}",
                name=f"{name} SKU",
                attributes={},
                unit_price_minor=5000,
                currency="USD",
                status="ACTIVE",
                version=1,
            )
        )
        await session.commit()


async def test_e2e_reindex_search_and_reconciliation(
    es_adapter: ElasticsearchIndexAdapter,
    redis_adapter: RedisCacheAdapter,
    test_db: Database,
    e2e_alias: str,
) -> None:
    # 1. Seed two products for Tenant A and one for Tenant B in PostgreSQL
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    p1 = uuid.uuid4()
    p2 = uuid.uuid4()
    p3 = uuid.uuid4()

    await _seed_product(test_db, tenant_a, p1, "coffee-mug", "Ceramic Coffee Mug")
    await _seed_product(test_db, tenant_a, p2, "tea-pot", "Glass Tea Pot")
    await _seed_product(test_db, tenant_b, p3, "coffee-maker", "Espresso Coffee Maker")

    reader = SqlAlchemyCatalogSearchReader(test_db)

    # 2. Reindex into Elasticsearch
    reindex_service = ReindexingService(
        indexer=es_adapter,
        reader=reader,
        cache=redis_adapter,
        batch_size=50,
        alias=e2e_alias,
    )
    reindex_result = await reindex_service.reindex()
    assert reindex_result.status == "COMPLETED"
    assert reindex_result.total_indexed >= 3

    # 3. Search query with strict tenant isolation
    search_service = SearchProductsService(
        indexer=es_adapter,
        cache=redis_adapter,
        index_alias=e2e_alias,
        cache_ttl_seconds=60,
    )

    # Tenant A searches for "Coffee": should only find Ceramic Coffee Mug,
    # NOT Tenant B's Coffee Maker!
    res_a = await search_service.search(tenant_id=str(tenant_a), q="Coffee")
    assert res_a.total == 1
    assert res_a.items[0].name == "Ceramic Coffee Mug"
    assert res_a.items[0].tenant_id == str(tenant_a)

    # Tenant B searches for "Coffee": should find Espresso Coffee Maker
    res_b = await search_service.search(tenant_id=str(tenant_b), q="Coffee")
    assert res_b.total == 1
    assert res_b.items[0].name == "Espresso Coffee Maker"

    # 4. Reconciliation: Should be in sync
    reconcile_service = ReconciliationService(
        indexer=es_adapter,
        reader=reader,
        alias=e2e_alias,
    )
    rec_result = await reconcile_service.reconcile(tenant_id=str(tenant_a))
    assert len(rec_result.discrepancies) == 0
    assert rec_result.matched == 2

    # 5. Inject discrepancy in ES and auto-repair
    doc_id_to_delete = f"{tenant_a}:{p1}"
    await es_adapter.delete(index_alias=e2e_alias, document_ids=(doc_id_to_delete,))

    # Confirm reconciliation detects missing document
    discrepant = await reconcile_service.reconcile(tenant_id=str(tenant_a), auto_repair=False)
    assert len(discrepant.discrepancies) == 1
    assert discrepant.discrepancies[0].discrepancy_type == "missing_in_elasticsearch"
    assert discrepant.discrepancies[0].document_id == doc_id_to_delete

    # Run auto-repair
    repaired = await reconcile_service.reconcile(tenant_id=str(tenant_a), auto_repair=True)
    assert repaired.repaired_count == 1

    # Verify document is restored in ES
    restored_doc = await es_adapter.get_document(
        index_alias=e2e_alias, document_id=doc_id_to_delete
    )
    assert restored_doc is not None
    assert restored_doc["name"] == "Ceramic Coffee Mug"

    # 6. REGRESSION TEST (PART A): Trigger reindex specifically scoped to tenant_a
    # It must NOT drop tenant_b's products from the shared alias!
    reindex_tenant_a_result = await reindex_service.reindex(tenant_id=str(tenant_a))
    assert reindex_tenant_a_result.status == "COMPLETED"

    # Tenant B's product MUST still be searchable through the shared alias!
    res_b_after = await search_service.search(tenant_id=str(tenant_b), q="Coffee")
    assert res_b_after.total == 1, "Tenant B product was lost during Tenant A reindex!"
    assert res_b_after.items[0].name == "Espresso Coffee Maker"

    # Tenant A's products must also be intact and searchable
    res_a_after = await search_service.search(tenant_id=str(tenant_a), q="Coffee")
    assert res_a_after.total == 1
    assert res_a_after.items[0].name == "Ceramic Coffee Mug"
