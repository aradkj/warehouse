"""Unit tests for Order, Fulfillment Exception, and Audit Search projections and query services."""

from __future__ import annotations

import pytest

from ordering.domain.events import OrderConfirmed, OrderPlaced
from search.adapters.memory import InMemorySearchIndexer
from search.application.models import (
    AUDIT_INDEX_ALIAS,
    AUDIT_INDEX_MAPPINGS,
    AUDIT_INDEX_V1,
    FULFILLMENT_EXCEPTIONS_INDEX_ALIAS,
    FULFILLMENT_EXCEPTIONS_INDEX_MAPPINGS,
    FULFILLMENT_EXCEPTIONS_INDEX_V1,
    ORDERS_INDEX_ALIAS,
    ORDERS_INDEX_MAPPINGS,
    ORDERS_INDEX_V1,
    AuditSearchDocument,
    OrderSearchDocument,
    build_fulfillment_exception_document_id,
    build_order_document_id,
)
from search.application.order_service import SearchOrdersService
from search.application.projector import ProductSearchProjector
from search.ports.indexer import SearchQuery
from shared.domain.identifiers import OrderId, TenantId, new_id


class TestOrderSearchAndProjections:
    @pytest.mark.asyncio
    async def test_order_search_service_filtering(self) -> None:
        adapter = InMemorySearchIndexer()
        await adapter.ensure_index(
            index_name=ORDERS_INDEX_V1,
            mappings=ORDERS_INDEX_MAPPINGS,
            aliases=(ORDERS_INDEX_ALIAS,),
        )
        service = SearchOrdersService(indexer=adapter)

        tenant_a = str(new_id())
        tenant_b = str(new_id())
        order_1 = str(new_id())
        order_2 = str(new_id())

        doc1 = OrderSearchDocument(
            id=order_1,
            tenant_id=tenant_a,
            status="CONFIRMED",
            customer_reference="CUST-ALPHA",
            currency="USD",
            total_minor=5000,
            warehouse_ids=["wh-1"],
            skus=[{"sku_code": "SKU-RED-S", "quantity": 2}],
        )
        doc2 = OrderSearchDocument(
            id=order_2,
            tenant_id=tenant_b,
            status="CONFIRMED",
            customer_reference="CUST-ALPHA",
            currency="USD",
            total_minor=2000,
            warehouse_ids=["wh-1"],
            skus=[{"sku_code": "SKU-RED-S", "quantity": 1}],
        )

        await adapter.index((doc1.to_search_document(), doc2.to_search_document()))

        # Tenant A search sees only order 1
        res_a = await service.search(tenant_id=tenant_a, q="CUST-ALPHA")
        assert res_a.total == 1
        assert res_a.items[0].order_id == order_1

        # Tenant B search sees only order 2
        res_b = await service.search(tenant_id=tenant_b, q="CUST-ALPHA")
        assert res_b.total == 1
        assert res_b.items[0].order_id == order_2

        # SKU filter
        res_sku = await service.search(tenant_id=tenant_a, sku="SKU-RED-S")
        assert res_sku.total == 1

        # Status filter
        res_status = await service.search(tenant_id=tenant_a, status="CANCELLED")
        assert res_status.total == 0

    @pytest.mark.asyncio
    async def test_order_projector_event_handling_and_idempotence(self) -> None:
        adapter = InMemorySearchIndexer()
        await adapter.ensure_index(
            index_name=ORDERS_INDEX_V1,
            mappings=ORDERS_INDEX_MAPPINGS,
            aliases=(ORDERS_INDEX_ALIAS,),
        )
        await adapter.ensure_index(
            index_name=FULFILLMENT_EXCEPTIONS_INDEX_V1,
            mappings=FULFILLMENT_EXCEPTIONS_INDEX_MAPPINGS,
            aliases=(FULFILLMENT_EXCEPTIONS_INDEX_ALIAS,),
        )
        await adapter.ensure_index(
            index_name=AUDIT_INDEX_V1,
            mappings=AUDIT_INDEX_MAPPINGS,
            aliases=(AUDIT_INDEX_ALIAS,),
        )

        projector = ProductSearchProjector(indexer=adapter)

        tenant_id = TenantId(new_id())
        order_id = OrderId(new_id())

        placed_event = OrderPlaced(
            tenant_id=tenant_id,
            order_id=order_id,
            line_count=1,
            total_quantity=3,
            total_minor=12000,
            currency="EUR",
            customer_reference="ORD-REF-001",
        )

        # Project OrderPlaced
        await projector.handle(placed_event)

        doc_id = build_order_document_id(str(tenant_id), str(order_id))
        stored = await adapter.get_document(index_alias=ORDERS_INDEX_ALIAS, document_id=doc_id)
        assert stored is not None
        assert stored["status"] == "PENDING"
        assert stored["customer_reference"] == "ORD-REF-001"
        assert stored["total_minor"] == 12000

        # Project OrderConfirmed
        confirmed_event = OrderConfirmed(
            tenant_id=tenant_id,
            order_id=order_id,
            total_minor=12000,
            currency="EUR",
        )
        await projector.handle(confirmed_event)

        updated = await adapter.get_document(index_alias=ORDERS_INDEX_ALIAS, document_id=doc_id)
        assert updated is not None
        assert updated["status"] == "CONFIRMED"

        # Replay event — document remains stable and idempotent
        await projector.handle(confirmed_event)
        updated2 = await adapter.get_document(index_alias=ORDERS_INDEX_ALIAS, document_id=doc_id)
        assert updated2 is not None
        assert updated2["status"] == "CONFIRMED"

    @pytest.mark.asyncio
    async def test_fulfillment_exception_projection(self) -> None:
        adapter = InMemorySearchIndexer()
        await adapter.ensure_index(
            index_name=FULFILLMENT_EXCEPTIONS_INDEX_V1,
            mappings=FULFILLMENT_EXCEPTIONS_INDEX_MAPPINGS,
            aliases=(FULFILLMENT_EXCEPTIONS_INDEX_ALIAS,),
        )
        projector = ProductSearchProjector(indexer=adapter)

        tenant_id = str(new_id())
        f_id = str(new_id())
        order_id = str(new_id())

        envelope = {
            "event_type": "FulfillmentFailed",
            "payload": {
                "tenant_id": tenant_id,
                "fulfillment_id": f_id,
                "order_id": order_id,
                "warehouse_id": "wh-east",
                "reason": "Address unreachable",
                "is_permanent": True,
                "version": 2,
            },
        }

        await projector._handle_event_dict(envelope)
        doc_id = build_fulfillment_exception_document_id(tenant_id, f_id)
        exc_doc = await adapter.get_document(
            index_alias=FULFILLMENT_EXCEPTIONS_INDEX_ALIAS,
            document_id=doc_id,
        )
        assert exc_doc is not None
        assert exc_doc["status"] == "FAILED"
        assert exc_doc["is_permanent"] is True
        assert exc_doc["exception_type"] == "CarrierPermanentFailure"
        assert exc_doc["reason"] == "Address unreachable"

    @pytest.mark.asyncio
    async def test_audit_search_projection(self) -> None:
        adapter = InMemorySearchIndexer()
        await adapter.ensure_index(
            index_name=AUDIT_INDEX_V1,
            mappings=AUDIT_INDEX_MAPPINGS,
            aliases=(AUDIT_INDEX_ALIAS,),
        )

        tenant_id = str(new_id())
        audit_id = str(new_id())

        audit_doc = AuditSearchDocument(
            id=audit_id,
            tenant_id=tenant_id,
            actor_id="user-42",
            action="order.cancel",
            resource_type="order",
            resource_id="ord-99",
            policy_decision="ALLOW",
            decision_reason="Policy allows cancellation",
            correlation_id="corr-xyz",
        )

        await adapter.index((audit_doc.to_search_document(),))

        res = await adapter.search(
            SearchQuery(index_alias=AUDIT_INDEX_ALIAS, tenant_id=tenant_id, q="cancellation")
        )
        assert res.total == 1
