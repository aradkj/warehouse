"""Seed data through the *public API*.

Every helper drives the same HTTP endpoints a real client uses, so seeding cannot
accidentally bypass a rule the API enforces (tenant scoping, product activation,
warehouse state, ...). Helpers assert the expected status and surface the response
body on failure, which turns a broken seed into one readable error instead of a
confusing assertion three layers later.
"""

from __future__ import annotations

from typing import Any

import httpx

TENANT_HEADER = "X-Tenant-Id"
IDEMPOTENCY_HEADER = "Idempotency-Key"
CORRELATION_HEADER = "X-Correlation-Id"


def tenant_headers(tenant_id: str, **extra: str) -> dict[str, str]:
    """Headers for a tenant-scoped request."""
    headers = {TENANT_HEADER: tenant_id}
    headers.update(extra)
    return headers


def idempotent_headers(tenant_id: str, key: str) -> dict[str, str]:
    """Headers for a mutation that must be idempotent."""
    return tenant_headers(tenant_id, **{IDEMPOTENCY_HEADER: key})


def expect(response: httpx.Response, *statuses: int) -> Any:
    """Assert the response status and return the decoded body."""
    expected = statuses or (200, 201)
    assert response.status_code in expected, (
        f"expected {expected}, got {response.status_code}: {response.text}"
    )
    return response.json()


async def create_tenant(
    client: httpx.AsyncClient, *, slug: str = "acme-store", name: str = "Acme Store"
) -> str:
    """Provision a tenant and return its id."""
    body = expect(await client.post("/v1/tenants", json={"slug": slug, "name": name}), 201)
    return str(body["id"])


async def create_product(
    client: httpx.AsyncClient,
    *,
    tenant_id: str,
    slug: str = "blue-tee",
    name: str = "Blue Tee",
    description: str | None = "A blue tee",
    activate: bool = True,
) -> str:
    """Create (and by default activate) a product; returns its id."""
    body = expect(
        await client.post(
            "/v1/products",
            json={"slug": slug, "name": name, "description": description},
            headers=tenant_headers(tenant_id),
        ),
        201,
    )
    product_id = str(body["id"])
    if activate:
        expect(
            await client.post(
                f"/v1/products/{product_id}/activate", headers=tenant_headers(tenant_id)
            ),
            200,
        )
    return product_id


async def create_sku(
    client: httpx.AsyncClient,
    *,
    tenant_id: str,
    product_id: str,
    code: str = "TEE-BLUE-M",
    name: str = "Blue Tee (M)",
    unit_price_minor: int = 2599,
    currency: str = "GBP",
) -> str:
    """Create a SKU on a product; returns its id."""
    body = expect(
        await client.post(
            f"/v1/products/{product_id}/skus",
            json={
                "code": code,
                "name": name,
                "unit_price_minor": unit_price_minor,
                "currency": currency,
            },
            headers=tenant_headers(tenant_id),
        ),
        201,
    )
    return str(body["id"])


async def create_warehouse(
    client: httpx.AsyncClient, *, tenant_id: str, code: str = "LON1", name: str = "London DC"
) -> str:
    """Register a warehouse; returns its id."""
    body = expect(
        await client.post(
            "/v1/warehouses",
            json={"code": code, "name": name},
            headers=tenant_headers(tenant_id),
        ),
        201,
    )
    return str(body["id"])


async def adjust_stock(
    client: httpx.AsyncClient,
    *,
    tenant_id: str,
    sku_id: str,
    warehouse_id: str,
    quantity_delta: int,
    reason: str = "GOODS_RECEIPT",
    expect_status: int = 200,
) -> Any:
    """Adjust stock and return the response body."""
    return expect(
        await client.post(
            "/v1/inventory/adjustments",
            json={
                "sku_id": sku_id,
                "warehouse_id": warehouse_id,
                "quantity_delta": quantity_delta,
                "reason": reason,
            },
            headers=tenant_headers(tenant_id),
        ),
        expect_status,
    )


async def availability(
    client: httpx.AsyncClient, *, tenant_id: str, sku_id: str | None = None
) -> list[dict[str, Any]]:
    """Current availability rows for the tenant (optionally filtered by SKU)."""
    params = {"sku_id": sku_id} if sku_id else None
    body = expect(
        await client.get(
            "/v1/inventory/availability", params=params, headers=tenant_headers(tenant_id)
        ),
        200,
    )
    return list(body["items"])


async def stock_position(
    client: httpx.AsyncClient, *, tenant_id: str, sku_id: str
) -> dict[str, int]:
    """``{"on_hand", "reserved", "available"}`` for a SKU (single warehouse tests)."""
    rows = await availability(client, tenant_id=tenant_id, sku_id=sku_id)
    assert len(rows) == 1, f"expected exactly one stock row, got {rows}"
    return {
        "on_hand": rows[0]["on_hand"],
        "reserved": rows[0]["reserved"],
        "available": rows[0]["available"],
    }


async def submit_order(
    client: httpx.AsyncClient,
    *,
    tenant_id: str,
    lines: list[dict[str, Any]],
    idempotency_key: str,
    customer_reference: str | None = None,
) -> httpx.Response:
    """POST /v1/orders with an idempotency key (the endpoint requires one)."""
    payload: dict[str, Any] = {"lines": lines}
    if customer_reference is not None:
        payload["customer_reference"] = customer_reference
    return await client.post(
        "/v1/orders",
        json=payload,
        headers=idempotent_headers(tenant_id, idempotency_key),
    )


async def provision_catalog(
    client: httpx.AsyncClient,
    *,
    tenant_id: str,
    slug: str = "blue-tee",
    code: str = "TEE-BLUE-M",
    unit_price_minor: int = 2599,
) -> tuple[str, str]:
    """Create an active product with one active SKU: ``(product_id, sku_id)``."""
    product_id = await create_product(client, tenant_id=tenant_id, slug=slug)
    sku_id = await create_sku(
        client,
        tenant_id=tenant_id,
        product_id=product_id,
        code=code,
        unit_price_minor=unit_price_minor,
    )
    return product_id, sku_id


__all__ = [
    "CORRELATION_HEADER",
    "IDEMPOTENCY_HEADER",
    "TENANT_HEADER",
    "adjust_stock",
    "availability",
    "create_product",
    "create_sku",
    "create_tenant",
    "create_warehouse",
    "expect",
    "idempotent_headers",
    "provision_catalog",
    "stock_position",
    "submit_order",
    "tenant_headers",
]
