"""Unit and pipeline integration test for Audit Search and Projection (Phase 4 Workstream).

Scenario:
1. An audit entry is recorded with actor, action, resource, tenant, and decision context.
2. The event is projected into the audit search read model (AuditSearchDocument).
3. SearchAuditService retrieves the indexed record with multi-attribute filtering.
4. Cross-tenant isolation is strictly enforced (Tenant A cannot see Tenant B's audit events).
5. Cursor tampering and query-mismatch rejection are verified.
"""

from __future__ import annotations

import pytest

from search.adapters.memory import InMemorySearchIndexer
from search.application.audit_service import SearchAuditService
from search.application.models import (
    AUDIT_INDEX_ALIAS,
    AUDIT_INDEX_MAPPINGS,
    AUDIT_INDEX_V1,
    AuditSearchDocument,
)
from search.application.projector import ProductSearchProjector
from shared.application.errors import InvalidRequest
from shared.domain.identifiers import new_id

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_audit_event_projection_and_search_pipeline() -> None:
    indexer = InMemorySearchIndexer()
    await indexer.ensure_index(
        index_name=AUDIT_INDEX_V1,
        mappings=AUDIT_INDEX_MAPPINGS,
        aliases=(AUDIT_INDEX_ALIAS,),
    )

    projector = ProductSearchProjector(indexer=indexer)
    search_service = SearchAuditService(indexer=indexer)

    tenant_a = str(new_id())
    tenant_b = str(new_id())
    audit_id_1 = str(new_id())
    audit_id_2 = str(new_id())

    # Simulate AuditLogged events dispatched from outbox/messaging
    event_1 = {
        "event_name": "AuditLogged",
        "payload": {
            "audit_id": audit_id_1,
            "tenant_id": tenant_a,
            "actor_id": "user-alice",
            "action": "order.cancel",
            "resource_type": "order",
            "resource_id": "ord-100",
            "correlation_id": "corr-1",
            "causation_id": "caus-1",
            "policy_decision": "ALLOW",
            "decision_reason": "Policy permits customer order cancellation",
            "occurred_at": "2026-10-15T10:00:00Z",
            "details": {"refund_amount_minor": 4500},
        },
    }

    event_2 = {
        "event_name": "AuditLogged",
        "payload": {
            "audit_id": audit_id_2,
            "tenant_id": tenant_b,
            "actor_id": "user-bob",
            "action": "order.cancel",
            "resource_type": "order",
            "resource_id": "ord-200",
            "correlation_id": "corr-2",
            "causation_id": "caus-2",
            "policy_decision": "DENY",
            "decision_reason": "Insufficient role privileges",
            "occurred_at": "2026-10-15T11:00:00Z",
            "details": {},
        },
    }

    # 1. Project events into search indexer
    await projector.handle_event(event_1)
    await projector.handle_event(event_2)

    # 2. Tenant A searches their audit logs -> sees event 1 only
    results_a = await search_service.search(
        tenant_id=tenant_a,
        q="cancellation",
    )
    assert results_a.total == 1
    item_a = results_a.items[0]
    assert item_a.id == audit_id_1
    assert item_a.raw_source["actor_id"] == "user-alice"
    assert item_a.raw_source["action"] == "order.cancel"
    assert item_a.raw_source["policy_decision"] == "ALLOW"

    # 3. Cross-tenant isolation: Tenant A cannot see Tenant B's audit events
    results_cross = await search_service.search(
        tenant_id=tenant_a,
        actor_id="user-bob",
    )
    assert results_cross.total == 0

    # 4. Tenant B searches their audit logs -> sees event 2 only
    results_b = await search_service.search(
        tenant_id=tenant_b,
        action="order.cancel",
    )
    assert results_b.total == 1
    item_b = results_b.items[0]
    assert item_b.id == audit_id_2
    assert item_b.raw_source["actor_id"] == "user-bob"
    assert item_b.raw_source["policy_decision"] == "DENY"

    # 5. Date filtering
    date_results = await search_service.search(
        tenant_id=tenant_a,
        date_from="2026-10-15T12:00:00Z",
    )
    assert date_results.total == 0


@pytest.mark.asyncio
async def test_audit_search_cursor_tampering_rejection() -> None:
    indexer = InMemorySearchIndexer()
    await indexer.ensure_index(
        index_name=AUDIT_INDEX_V1,
        mappings=AUDIT_INDEX_MAPPINGS,
        aliases=(AUDIT_INDEX_ALIAS,),
    )
    search_service = SearchAuditService(indexer=indexer)
    tenant_id = str(new_id())

    # Seed 3 audit entries
    for i in range(3):
        doc = AuditSearchDocument(
            id=f"audit-{i}",
            tenant_id=tenant_id,
            actor_id=f"user-{i}",
            action="product.archive",
            resource_type="product",
            resource_id=f"prod-{i}",
            occurred_at=f"2026-10-1{i}T12:00:00Z",
        )
        await indexer.index((doc.to_search_document(),))

    page1 = await search_service.search(
        tenant_id=tenant_id,
        action="product.archive",
        page_size=1,
    )
    assert page1.next_cursor is not None

    # Valid reuse succeeds
    page2 = await search_service.search(
        tenant_id=tenant_id,
        action="product.archive",
        cursor=page1.next_cursor,
        page_size=1,
    )
    assert page2.total == 3

    # Tampered action filter is rejected
    with pytest.raises(InvalidRequest, match="pagination cursor does not match"):
        await search_service.search(
            tenant_id=tenant_id,
            action="order.cancel",  # Tampered!
            cursor=page1.next_cursor,
            page_size=1,
        )

    # Cross-tenant cursor reuse is rejected
    diff_tenant = str(new_id())
    with pytest.raises(InvalidRequest, match="pagination cursor is not valid for this tenant"):
        await search_service.search(
            tenant_id=diff_tenant,
            action="product.archive",
            cursor=page1.next_cursor,
            page_size=1,
        )
