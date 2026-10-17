"""Integration tests for audit search query generation, filtering, sorting, and cache invalidation.

Covers:
- SRCH-001: Audit queries execute against actual audit mappings without order/product filter leakage.
- API-002: resource_type and resource_id filters are propagated and filter audit records.
- SRCH-003: Explicit per-index sort allowlists; sorting by action, resource_type, policy_decision,
  occurred_at; rejection of disallowed fields; deterministic cursor traversal.
- SRCH-002: Invalidation of search caches on projection updates.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from search.adapters.elasticsearch import ElasticsearchIndexAdapter
from search.application.audit_service import SearchAuditService
from search.application.models import (
    AUDIT_INDEX_MAPPINGS,
    AuditSearchDocument,
)
from search.application.projector import ProductSearchProjector
from search.ports.indexer import SearchQuery
from shared.application.errors import InvalidRequest
from shared.infrastructure.cache.redis import RedisCacheAdapter

pytestmark = [pytest.mark.integration, pytest.mark.elasticsearch, pytest.mark.redis]

ES_URL = os.environ.get(
    "ARAD_ELASTICSEARCH_URL", os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")
)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
async def es_adapter() -> AsyncIterator[ElasticsearchIndexAdapter]:
    adapter = ElasticsearchIndexAdapter.from_url(url=ES_URL, request_timeout=5.0, max_retries=2)
    try:
        await adapter.client.info()
    except Exception:
        pytest.skip(f"Elasticsearch is unreachable at {ES_URL}")
    yield adapter
    await adapter.client.close()


@pytest.fixture
async def redis_cache() -> AsyncIterator[RedisCacheAdapter]:
    cache = RedisCacheAdapter.from_url(REDIS_URL, default_ttl_seconds=60)
    try:
        await cache.ping()
    except Exception:
        pytest.skip(f"Redis is unreachable at {REDIS_URL}")
    yield cache
    await cache.close()


@pytest.fixture
async def audit_test_index(es_adapter: ElasticsearchIndexAdapter) -> AsyncIterator[str]:
    test_alias = f"audit_logs_test_{uuid.uuid4().hex[:8]}"
    physical_index = f"{test_alias}_v1"
    await es_adapter.ensure_index(
        index_name=physical_index,
        mappings=dict(AUDIT_INDEX_MAPPINGS),
        aliases=(test_alias,),
    )
    yield test_alias
    with contextlib.suppress(Exception):
        await es_adapter.client.indices.delete(index=physical_index, ignore_unavailable=True)


@pytest.mark.asyncio
async def test_audit_search_filters_and_tenant_isolation(
    es_adapter: ElasticsearchIndexAdapter,
    audit_test_index: str,
) -> None:
    """SRCH-001 & API-002: Verify audit search works against actual mapping without order filters."""
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    now = datetime.now(UTC)

    doc_a1 = AuditSearchDocument(
        id=str(uuid.uuid4()),
        tenant_id=tenant_a,
        actor_id="user_admin_1",
        action="order.confirm",
        resource_type="order",
        resource_id="ord-101",
        policy_decision="ALLOW",
        decision_reason="authorized",
        occurred_at=(now - timedelta(minutes=10)).isoformat(),
        details={"ip": "10.0.0.1"},
    )
    doc_a2 = AuditSearchDocument(
        id=str(uuid.uuid4()),
        tenant_id=tenant_a,
        actor_id="user_admin_2",
        action="product.archive",
        resource_type="product",
        resource_id="prod-202",
        policy_decision="DENY",
        decision_reason="insufficient_permissions",
        occurred_at=(now - timedelta(minutes=5)).isoformat(),
        details={"ip": "10.0.0.2"},
    )
    # Different tenant
    doc_b1 = AuditSearchDocument(
        id=str(uuid.uuid4()),
        tenant_id=tenant_b,
        actor_id="user_admin_1",
        action="order.confirm",
        resource_type="order",
        resource_id="ord-999",
        policy_decision="ALLOW",
        decision_reason="authorized",
        occurred_at=now.isoformat(),
        details={"ip": "10.0.0.3"},
    )

    await es_adapter.index(
        (
            doc_a1.to_search_document(alias=audit_test_index),
            doc_a2.to_search_document(alias=audit_test_index),
            doc_b1.to_search_document(alias=audit_test_index),
        ),
        refresh=True,
    )

    # 1. Action filter
    query_action = SearchQuery(
        tenant_id=tenant_a,
        index_alias=audit_test_index,
        action="order.confirm",
    )
    res_action = await es_adapter.search(query_action)
    assert res_action.total == 1
    assert res_action.items[0].id == doc_a1.id

    # 2. Actor filter
    query_actor = SearchQuery(
        tenant_id=tenant_a,
        index_alias=audit_test_index,
        actor_id="user_admin_2",
    )
    res_actor = await es_adapter.search(query_actor)
    assert res_actor.total == 1
    assert res_actor.items[0].id == doc_a2.id

    # 3. Resource type and ID filter (API-002)
    query_resource = SearchQuery(
        tenant_id=tenant_a,
        index_alias=audit_test_index,
        resource_type="product",
        resource_id="prod-202",
    )
    res_resource = await es_adapter.search(query_resource)
    assert res_resource.total == 1
    assert res_resource.items[0].id == doc_a2.id

    # 4. Tenant isolation: tenant_a cannot see tenant_b's records
    query_all_a = SearchQuery(
        tenant_id=tenant_a,
        index_alias=audit_test_index,
    )
    res_all_a = await es_adapter.search(query_all_a)
    assert res_all_a.total == 2
    ids_a = {item.id for item in res_all_a.items}
    assert doc_b1.id not in ids_a

    # 5. Empty result for non-matching filter
    query_empty = SearchQuery(
        tenant_id=tenant_a,
        index_alias=audit_test_index,
        action="nonexistent.action",
    )
    res_empty = await es_adapter.search(query_empty)
    assert res_empty.total == 0
    assert len(res_empty.items) == 0


@pytest.mark.asyncio
async def test_audit_search_sorting_and_unsupported_rejection(
    es_adapter: ElasticsearchIndexAdapter,
    audit_test_index: str,
) -> None:
    """SRCH-003: Verify supported audit sort fields, deterministic sorting, and rejection."""
    tenant_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    docs = [
        AuditSearchDocument(
            id=f"audit_{idx}",
            tenant_id=tenant_id,
            actor_id=f"actor_{idx}",
            action=action,
            resource_type=rtype,
            resource_id=f"res_{idx}",
            policy_decision=decision,
            occurred_at=(now - timedelta(minutes=idx * 2)).isoformat(),
        )
        for idx, (action, rtype, decision) in enumerate(
            [
                ("alpha.act", "order", "ALLOW"),
                ("beta.act", "inventory", "DENY"),
                ("gamma.act", "warehouse", "ALLOW"),
            ]
        )
    ]

    await es_adapter.index(
        tuple(d.to_search_document(alias=audit_test_index) for d in docs),
        refresh=True,
    )

    # 1. Sort by action ascending
    q_sort_action = SearchQuery(
        tenant_id=tenant_id,
        index_alias=audit_test_index,
        sort_by="action",
        sort_order="asc",
    )
    res_action = await es_adapter.search(q_sort_action)
    assert [item.raw_source.get("action") for item in res_action.items] == [
        "alpha.act",
        "beta.act",
        "gamma.act",
    ]

    # 2. Sort by resource_type ascending
    q_sort_rtype = SearchQuery(
        tenant_id=tenant_id,
        index_alias=audit_test_index,
        sort_by="resource_type",
        sort_order="asc",
    )
    res_rtype = await es_adapter.search(q_sort_rtype)
    assert [item.raw_source.get("resource_type") for item in res_rtype.items] == [
        "inventory",
        "order",
        "warehouse",
    ]

    # 3. Sort by policy_decision
    q_sort_decision = SearchQuery(
        tenant_id=tenant_id,
        index_alias=audit_test_index,
        sort_by="policy_decision",
        sort_order="asc",
    )
    res_decision = await es_adapter.search(q_sort_decision)
    decisions = [item.raw_source.get("policy_decision") for item in res_decision.items]
    assert decisions == ["ALLOW", "ALLOW", "DENY"]

    # 4. Rejection of unsupported sort field
    q_bad_sort = SearchQuery(
        tenant_id=tenant_id,
        index_alias=audit_test_index,
        sort_by="unindexed_secret_column",
    )
    with pytest.raises(
        InvalidRequest, match="Sorting by 'unindexed_secret_column' is not permitted"
    ):
        await es_adapter.search(q_bad_sort)


@pytest.mark.asyncio
async def test_audit_search_cursor_pagination_multi_page(
    es_adapter: ElasticsearchIndexAdapter,
    audit_test_index: str,
) -> None:
    """SRCH-003: Verify multi-page cursor traversal with search_after and stable tie-breaker."""
    tenant_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    # Create 5 audit records with same occurred_at timestamp to test tiebreaker
    same_time = now.isoformat()
    docs = [
        AuditSearchDocument(
            id=f"audit_item_{i:02d}",
            tenant_id=tenant_id,
            actor_id="actor_system",
            action="order.process",
            resource_type="order",
            resource_id=f"ord_{i}",
            occurred_at=same_time,
        )
        for i in range(5)
    ]

    await es_adapter.index(
        tuple(d.to_search_document(alias=audit_test_index) for d in docs),
        refresh=True,
    )

    service = SearchAuditService(
        indexer=es_adapter,
        index_alias=audit_test_index,
    )

    # Page 1: 2 items
    p1 = await service.search(
        tenant_id=tenant_id, page_size=2, sort_by="occurred_at", sort_order="asc"
    )
    assert len(p1.items) == 2
    assert p1.next_cursor is not None

    # Page 2: using cursor
    p2 = await service.search(
        tenant_id=tenant_id,
        cursor=p1.next_cursor,
        page_size=2,
        sort_by="occurred_at",
        sort_order="asc",
    )
    assert len(p2.items) == 2
    assert p2.next_cursor is not None
    # No duplicate items between page 1 and page 2
    p1_ids = {item.id for item in p1.items}
    p2_ids = {item.id for item in p2.items}
    assert p1_ids.isdisjoint(p2_ids)

    # Page 3: last item
    p3 = await service.search(
        tenant_id=tenant_id,
        cursor=p2.next_cursor,
        page_size=2,
        sort_by="occurred_at",
        sort_order="asc",
    )
    assert len(p3.items) == 1
    assert p3.items[0].id not in p1_ids
    assert p3.items[0].id not in p2_ids


@pytest.mark.asyncio
async def test_search_cache_invalidation_on_projection(
    es_adapter: ElasticsearchIndexAdapter,
    redis_cache: RedisCacheAdapter,
    audit_test_index: str,
) -> None:
    """SRCH-002: Verify that projection updates bump search cache version and invalidate stale results."""
    tenant_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    service = SearchAuditService(
        indexer=es_adapter,
        cache=redis_cache,
        index_alias=audit_test_index,
    )

    # 1. Initial search - returns 0 results and caches the page
    res_1 = await service.search(tenant_id=tenant_id, action="order.create")
    assert res_1.total == 0

    # 2. Projector indexes a new audit document
    projector = ProductSearchProjector(
        indexer=es_adapter,
        cache=redis_cache,
        index_alias=audit_test_index,
    )

    new_doc = AuditSearchDocument(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        actor_id="actor_projection",
        action="order.create",
        resource_type="order",
        resource_id="ord-fresh-999",
        occurred_at=now.isoformat(),
    )
    # Call internal helper that writes to ES and bumps Redis search version
    await projector._index_audit_document(new_doc, alias=audit_test_index)
    # Refresh ES so it's searchable immediately
    await es_adapter.client.indices.refresh(index=audit_test_index)

    # 3. Repeat exact same search query - must NOT return stale cached 0 items
    res_2 = await service.search(tenant_id=tenant_id, action="order.create")
    assert res_2.total == 1
    assert res_2.items[0].id == new_doc.id
    assert res_2.items[0].raw_source.get("resource_id") == "ord-fresh-999"
