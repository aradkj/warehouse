"""API integration tests for Order Search, Fulfillment endpoints, and Audit events."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from tests.api.test_access_api import setup_tenant_and_users
from tests.support import seed

pytestmark = pytest.mark.api


async def test_order_search_endpoints(client: httpx.AsyncClient) -> None:
    tenant_id, admin_token, _, _, _, _ = await setup_tenant_and_users(client)
    headers = {"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id}

    # Search via /v1/orders/search
    resp1 = await client.get("/v1/orders/search?q=TEST&status=PENDING", headers=headers)
    body1 = seed.expect(resp1, 200)
    assert "items" in body1
    assert "total" in body1
    assert "page" in body1

    # Search via /v1/search/orders
    resp2 = await client.get("/v1/search/orders?q=TEST&page_size=10", headers=headers)
    body2 = seed.expect(resp2, 200)
    assert "items" in body2
    assert "total" in body2


async def test_fulfillment_lifecycle_api(client: httpx.AsyncClient) -> None:
    tenant_id, admin_token, _, _, _, _ = await setup_tenant_and_users(client)
    headers = {"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id}

    order_id = str(uuid4())
    warehouse_id = str(uuid4())
    sku_id = str(uuid4())

    # 1. POST /v1/fulfillments
    payload = {
        "order_id": order_id,
        "warehouse_id": warehouse_id,
        "requirements": [
            {"sku_id": sku_id, "quantity": 2},
        ],
        "max_retries": 3,
    }
    create_resp = await client.post(
        "/v1/fulfillments",
        headers={**headers, "Idempotency-Key": f"f-req-{uuid4().hex[:8]}"},
        json=payload,
    )
    f_data = seed.expect(create_resp, 201)
    fulfillment_id = f_data["id"]
    assert f_data["order_id"] == order_id
    assert f_data["status"] == "ALLOCATED"
    assert len(f_data["tasks"]) == 1

    # 2. GET /v1/fulfillments/{id}
    get_resp = await client.get(f"/v1/fulfillments/{fulfillment_id}", headers=headers)
    fetched = seed.expect(get_resp, 200)
    assert fetched["id"] == fulfillment_id
    assert fetched["status"] == "ALLOCATED"

    # 3. Cross-tenant access is rejected
    tenant_2_id, admin_2_token, _, _, _, _ = await setup_tenant_and_users(client)
    t2_headers = {"Authorization": f"Bearer {admin_2_token}", "X-Tenant-Id": tenant_2_id}

    forbidden_resp = await client.get(f"/v1/fulfillments/{fulfillment_id}", headers=t2_headers)
    assert forbidden_resp.status_code in (403, 404)


async def test_audit_events_endpoint(client: httpx.AsyncClient) -> None:
    tenant_id, admin_token, _, _, _, _ = await setup_tenant_and_users(client)
    headers = {"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id}

    # Fetch audit events
    audit_resp = await client.get("/v1/audit/events?limit=25", headers=headers)
    body = seed.expect(audit_resp, 200)
    assert "items" in body
    assert "total" in body
    assert isinstance(body["items"], list)
