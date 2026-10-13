"""API integration tests for Search endpoints, tenant isolation, and admin controls."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import httpx
import pytest

from search.application.models import PRODUCTS_INDEX_ALIAS, build_product_document_id
from search.ports.indexer import SearchDocument
from shared.application.errors import SearchUnavailable

pytestmark = pytest.mark.api


async def _index_doc(
    app: object,
    tenant_id: str,
    product_id: str,
    name: str,
    status: str = "ACTIVE",
    sku_code: str | None = None,
) -> None:
    indexer = app.state.search_indexer
    doc_id = build_product_document_id(tenant_id, product_id)
    skus = (
        [{"sku_id": str(uuid.uuid4()), "sku_code": sku_code, "status": "ACTIVE"}]
        if sku_code
        else []
    )
    doc = SearchDocument(
        index_alias=PRODUCTS_INDEX_ALIAS,
        document_id=doc_id,
        tenant_id=tenant_id,
        version=1,
        body={
            "id": product_id,
            "tenant_id": tenant_id,
            "slug": name.lower().replace(" ", "-"),
            "name": name,
            "description": f"Description for {name}",
            "status": status,
            "attributes": {},
            "skus": skus,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "version": 1,
        },
    )
    await indexer.index((doc,))


async def test_search_api_tenant_isolation(client: httpx.AsyncClient, app: object) -> None:
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())

    await _index_doc(app, tenant_a, str(uuid.uuid4()), "Product Alpha")
    await _index_doc(app, tenant_b, str(uuid.uuid4()), "Product Beta")

    # Tenant A requests search: must only see Product Alpha
    resp_a = await client.get("/v1/search/products", headers={"X-Tenant-Id": tenant_a})
    assert resp_a.status_code == 200
    data_a = resp_a.json()
    assert data_a["total"] == 1
    assert data_a["items"][0]["name"] == "Product Alpha"
    assert data_a["items"][0]["tenant_id"] == tenant_a

    # Tenant B requests search: must only see Product Beta
    resp_b = await client.get("/v1/search/products", headers={"X-Tenant-Id": tenant_b})
    assert resp_b.status_code == 200
    data_b = resp_b.json()
    assert data_b["total"] == 1
    assert data_b["items"][0]["name"] == "Product Beta"
    assert data_b["items"][0]["tenant_id"] == tenant_b


async def test_search_api_requires_tenant_header(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/search/products")
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "invalid_request"
    assert "X-Tenant-Id" in body["error"]["message"]


async def test_search_api_query_and_filters(client: httpx.AsyncClient, app: object) -> None:
    tenant_id = str(uuid.uuid4())

    await _index_doc(
        app, tenant_id, str(uuid.uuid4()), "Gaming Laptop", status="ACTIVE", sku_code="LAPTOP-GAME"
    )
    await _index_doc(
        app, tenant_id, str(uuid.uuid4()), "Office Desk", status="DRAFT", sku_code="DESK-OFFICE"
    )

    headers = {"X-Tenant-Id": tenant_id}

    # Query with q=Gaming
    resp_q = await client.get("/v1/search/products?q=Gaming", headers=headers)
    assert resp_q.status_code == 200
    assert resp_q.json()["total"] == 1
    assert resp_q.json()["items"][0]["name"] == "Gaming Laptop"

    # Filter with status=DRAFT
    resp_status = await client.get("/v1/search/products?status=DRAFT", headers=headers)
    assert resp_status.status_code == 200
    assert resp_status.json()["total"] == 1
    assert resp_status.json()["items"][0]["name"] == "Office Desk"

    # Filter with sku=LAPTOP-GAME
    resp_sku = await client.get("/v1/search/products?sku=LAPTOP-GAME", headers=headers)
    assert resp_sku.status_code == 200
    assert resp_sku.json()["total"] == 1
    assert resp_sku.json()["items"][0]["name"] == "Gaming Laptop"


async def test_search_api_validation_errors(client: httpx.AsyncClient) -> None:
    headers = {"X-Tenant-Id": str(uuid.uuid4())}

    # Disallowed sort field
    resp_sort = await client.get("/v1/search/products?sort_by=secret_column", headers=headers)
    assert resp_sort.status_code == 400
    assert resp_sort.json()["error"]["code"] == "invalid_request"

    # Disallowed page size (> 100)
    resp_size = await client.get("/v1/search/products?page_size=500", headers=headers)
    assert resp_size.status_code == 422  # Handled by FastAPI Query(le=100)


async def test_search_api_backend_failure_maps_to_503(
    client: httpx.AsyncClient, app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = str(uuid.uuid4())
    headers = {"X-Tenant-Id": tenant_id}

    indexer = app.state.search_indexer
    monkeypatch.setattr(
        indexer, "search", AsyncMock(side_effect=SearchUnavailable("ES cluster unavailable"))
    )

    response = await client.get("/v1/search/products", headers=headers)
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "search_unavailable"


async def test_admin_reindex_and_reconcile_authorization(client: httpx.AsyncClient) -> None:
    # 1. Unauthenticated request / forged header -> 401 Unauthorized (AUTH-001)
    resp_reindex_user = await client.post(
        "/v1/search/reindex",
        headers={"X-Actor-Type": "sales_channel"},
        json={},
    )
    assert resp_reindex_user.status_code == 401
    assert resp_reindex_user.json()["error"]["code"] == "authentication_failed"

    resp_reconcile_forged = await client.post(
        "/v1/search/reconcile",
        headers={"X-Actor-Type": "system"},
        json={},
    )
    assert resp_reconcile_forged.status_code == 401
    assert resp_reconcile_forged.json()["error"]["code"] == "authentication_failed"

    # 2. Authenticated system/admin token -> 200 OK
    from bootstrap.config import Settings
    from identity.infrastructure.security import TokenService

    settings = Settings()
    token_service = TokenService(
        secret=settings.auth_jwt_secret,
        algorithm=settings.auth_jwt_algorithm,
        issuer=settings.auth_jwt_issuer,
        audience=settings.auth_jwt_audience,
    )
    token = token_service.create_access_token(
        {"sub": str(uuid.uuid4()), "type": "system", "role": "ADMIN", "permissions": ["*"]}
    )
    admin_headers = {"Authorization": f"Bearer {token}"}

    resp_reindex_sys = await client.post(
        "/v1/search/reindex",
        headers=admin_headers,
        json={},
    )
    assert resp_reindex_sys.status_code == 200
    assert resp_reindex_sys.json()["status"] == "COMPLETED"

    resp_reconcile_sys = await client.post(
        "/v1/search/reconcile",
        headers=admin_headers,
        json={},
    )
    assert resp_reconcile_sys.status_code == 200
    assert "matched" in resp_reconcile_sys.json()
