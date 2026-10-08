"""Unit tests for ReconciliationService: discrepancy detection and unidirectional auto-repair."""

from __future__ import annotations

import copy
import uuid

import pytest
from tests.unit.test_reindex_unit import MockCatalogSearchReader

from search.adapters.memory import InMemorySearchIndexer
from search.application.models import PRODUCTS_INDEX_ALIAS, build_product_document_id
from search.application.reconciliation import ReconciliationService
from search.ports.indexer import SearchDocument

pytestmark = pytest.mark.unit


def _make_doc(tenant_id: str, product_id: str, name: str, version: int = 1) -> SearchDocument:
    return SearchDocument(
        index_alias=PRODUCTS_INDEX_ALIAS,
        document_id=build_product_document_id(tenant_id, product_id),
        tenant_id=tenant_id,
        version=version,
        body={
            "id": product_id,
            "tenant_id": tenant_id,
            "slug": name.lower(),
            "name": name,
            "status": "ACTIVE",
            "version": version,
        },
    )


async def test_reconciliation_identical_state() -> None:
    indexer = InMemorySearchIndexer()
    tenant_id = str(uuid.uuid4())
    doc = _make_doc(tenant_id, "p1", "Widget", version=1)

    # In sync: both have doc version 1
    reader = MockCatalogSearchReader([doc])
    await indexer.index((doc,))

    service = ReconciliationService(indexer=indexer, reader=reader, alias=PRODUCTS_INDEX_ALIAS)
    result = await service.reconcile(tenant_id=tenant_id)

    assert result.total_authoritative == 1
    assert result.total_search == 1
    assert result.matched == 1
    assert len(result.discrepancies) == 0


async def test_reconciliation_detects_all_discrepancy_types() -> None:
    indexer = InMemorySearchIndexer()
    tenant_1 = str(uuid.uuid4())

    # Case 1: missing in ES (in PG, but not in ES)
    doc_missing_in_es = _make_doc(tenant_1, "missing-in-es", "Item 1", version=1)

    # Case 2: version mismatch (PG is ahead at ver 3, ES is stale at ver 2)
    doc_pg_v3 = _make_doc(tenant_1, "stale-doc", "Item 2", version=3)
    doc_es_v2 = _make_doc(tenant_1, "stale-doc", "Item 2", version=2)

    # Case 3: missing in PG / orphaned in ES (in ES, but deleted/absent in PG)
    doc_orphan_es = _make_doc(tenant_1, "orphan-es", "Ghost Item", version=1)

    reader = MockCatalogSearchReader([doc_missing_in_es, doc_pg_v3])
    await indexer.index((doc_es_v2, doc_orphan_es))

    service = ReconciliationService(indexer=indexer, reader=reader, alias=PRODUCTS_INDEX_ALIAS)
    result = await service.reconcile()

    types = {d.discrepancy_type for d in result.discrepancies}
    assert "missing_in_elasticsearch" in types
    assert "version_mismatch" in types
    assert "missing_in_postgresql" in types
    assert len(result.discrepancies) == 3


async def test_reconciliation_unidirectional_auto_repair() -> None:
    indexer = InMemorySearchIndexer()
    tenant_id = str(uuid.uuid4())

    # Source PostgreSQL truth:
    doc1 = _make_doc(tenant_id, "p1", "Fresh Item", version=1)
    doc2 = _make_doc(tenant_id, "p2", "Updated Item", version=5)
    pg_docs = [doc1, doc2]
    reader = MockCatalogSearchReader(pg_docs)

    # Elasticsearch initial state:
    # doc1 is missing
    # doc2 is stale at version 2
    # doc3 is orphaned
    doc2_stale = _make_doc(tenant_id, "p2", "Stale Item", version=2)
    doc3_orphan = _make_doc(tenant_id, "p3", "Orphaned Item", version=1)
    await indexer.index((doc2_stale, doc3_orphan))

    # Keep snapshot of PostgreSQL reader to prove it is NEVER modified
    pg_snapshot = copy.deepcopy(reader._documents)

    service = ReconciliationService(indexer=indexer, reader=reader, alias=PRODUCTS_INDEX_ALIAS)
    result = await service.reconcile(auto_repair=True)

    assert result.repaired_count == 3

    # INVARIANT: PostgreSQL reader was NOT mutated at all!
    assert reader._documents == pg_snapshot

    # INVARIANT: Elasticsearch now matches PostgreSQL!
    reconciled_after = await service.reconcile(auto_repair=False)
    assert len(reconciled_after.discrepancies) == 0
    assert reconciled_after.matched == 2

    # Check repaired documents in ES
    es_doc1 = await indexer.get_document(
        index_alias=PRODUCTS_INDEX_ALIAS, document_id=doc1.document_id
    )
    assert es_doc1 is not None
    assert es_doc1["name"] == "Fresh Item"

    es_doc2 = await indexer.get_document(
        index_alias=PRODUCTS_INDEX_ALIAS, document_id=doc2.document_id
    )
    assert es_doc2 is not None
    assert es_doc2["version"] == 5

    # Check orphan was purged from ES
    es_doc3 = await indexer.get_document(
        index_alias=PRODUCTS_INDEX_ALIAS, document_id=doc3_orphan.document_id
    )
    assert es_doc3 is None
