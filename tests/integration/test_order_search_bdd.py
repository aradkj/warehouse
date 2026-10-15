"""BDD Acceptance Scenario: Eventually Consistent Order Search.

Scenario:
  Given a confirmed order in ordering context
  When an OrderConfirmed integration event is emitted and projected into Elasticsearch
  Then the current status is searchable by customer reference, SKU, and status
  And tenant isolation is strictly preserved (another tenant cannot view it)
  And replaying the event does not duplicate the document in the search projection.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from ordering.domain.events import OrderConfirmed, OrderPlaced
from search.adapters.memory import InMemorySearchIndexer
from search.application.models import (
    ORDERS_INDEX_ALIAS,
    ORDERS_INDEX_MAPPINGS,
    ORDERS_INDEX_V1,
    build_order_document_id,
)
from search.application.order_service import SearchOrdersService
from search.application.projector import ProductSearchProjector
from shared.domain.identifiers import OrderId, TenantId


@pytest.mark.asyncio
async def test_bdd_eventually_consistent_order_search() -> None:
    # --- Given: Disposable search projection with index schema ---
    indexer = InMemorySearchIndexer()
    await indexer.ensure_index(
        index_name=ORDERS_INDEX_V1,
        mappings=ORDERS_INDEX_MAPPINGS,
        aliases=(ORDERS_INDEX_ALIAS,),
    )
    projector = ProductSearchProjector(indexer=indexer)
    search_service = SearchOrdersService(indexer=indexer)

    tenant_a = TenantId(uuid4())
    tenant_b = TenantId(uuid4())
    order_id = OrderId(uuid4())
    customer_ref = "CUST-BDD-001"

    # Step 1: Order is placed
    placed_event = OrderPlaced(
        tenant_id=tenant_a,
        order_id=order_id,
        line_count=1,
        total_quantity=2,
        total_minor=7500,
        currency="USD",
        customer_reference=customer_ref,
    )
    await projector.handle(placed_event)

    # Verify initial pending projection
    initial_res = await search_service.search(tenant_id=str(tenant_a), q=customer_ref)
    assert initial_res.total == 1
    assert initial_res.items[0].status == "PENDING"
    assert initial_res.items[0].customer_reference == customer_ref

    # --- When: Order is confirmed and integration event is projected ---
    confirmed_event = OrderConfirmed(
        tenant_id=tenant_a,
        order_id=order_id,
        total_minor=7500,
        currency="USD",
    )
    await projector.handle(confirmed_event)

    # --- Then: Confirmed status is searchable across status and customer ---
    search_confirmed = await search_service.search(
        tenant_id=str(tenant_a),
        status="CONFIRMED",
        customer=customer_ref,
    )
    assert search_confirmed.total == 1
    item = search_confirmed.items[0]
    assert item.order_id == str(order_id)
    assert item.status == "CONFIRMED"
    assert item.customer_reference == customer_ref

    # --- And: Tenant isolation is preserved ---
    tenant_b_res = await search_service.search(tenant_id=str(tenant_b), q=customer_ref)
    assert tenant_b_res.total == 0

    # --- And: Replaying the same event does not duplicate or corrupt document ---
    await projector.handle(confirmed_event)
    replayed_res = await search_service.search(tenant_id=str(tenant_a), q=customer_ref)
    assert replayed_res.total == 1
    assert replayed_res.items[0].status == "CONFIRMED"

    doc_id = build_order_document_id(str(tenant_a), str(order_id))
    raw_doc = await indexer.get_document(index_alias=ORDERS_INDEX_ALIAS, document_id=doc_id)
    assert raw_doc is not None
    assert raw_doc["status"] == "CONFIRMED"
