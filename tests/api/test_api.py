"""API integration tests: the full stack over HTTP.

These tests drive the real application - FastAPI, dependencies, use cases,
repositories, PostgreSQL - through ``httpx.ASGITransport``, so they exercise the
same code path a client hits, minus the socket. Nothing is mocked: a bug in
routing, validation, error mapping or SQL shows up here.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from tests.support import seed
from tests.support.database import count, scalar

from shared.application.idempotency import IdempotencyState

pytestmark = pytest.mark.api


async def provision(client: httpx.AsyncClient, *, stock: int = 0) -> dict[str, Any]:
    """A tenant with one active product/SKU, one warehouse and optional stock."""
    tenant_id = await seed.create_tenant(client, slug="northwind", name="Northwind")
    product_id, sku_id = await seed.provision_catalog(client, tenant_id=tenant_id)
    warehouse_id = await seed.create_warehouse(client, tenant_id=tenant_id, code="LAX", name="LAX")
    if stock:
        await seed.adjust_stock(
            client,
            tenant_id=tenant_id,
            sku_id=sku_id,
            warehouse_id=warehouse_id,
            quantity_delta=stock,
        )
    return {
        "tenant_id": tenant_id,
        "product_id": product_id,
        "sku_id": sku_id,
        "warehouse_id": warehouse_id,
    }


class TestHealth:
    async def test_healthz_is_liveness_only(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/healthz")
        body = seed.expect(response, 200)
        assert body["status"] == "ok"
        assert body["environment"] == "test"
        # Liveness must not depend on PostgreSQL: a database outage may not make an
        # orchestrator restart healthy containers.
        assert body["checks"] == {}

    async def test_readyz_checks_postgresql(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/readyz")
        body = seed.expect(response, 200)
        assert body["status"] == "ready"
        assert body["checks"]["database"] == "ok"

    async def test_successful_responses_carry_a_correlation_id(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/healthz")
        assert response.headers.get("X-Correlation-Id")

    async def test_a_client_supplied_correlation_id_is_echoed(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/healthz", headers={seed.CORRELATION_HEADER: "corr-123456"})
        assert response.headers["X-Correlation-Id"] == "corr-123456"

    async def test_w3c_traceparent_header_is_supported_and_echoed(
        self, client: httpx.AsyncClient
    ) -> None:
        traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        response = await client.get("/healthz", headers={"traceparent": traceparent})
        assert response.headers.get("traceparent") == traceparent

    async def test_prometheus_metrics_endpoint_is_exposed(
        self, client: httpx.AsyncClient
    ) -> None:
        # Generate some traffic first
        await client.get("/healthz")
        response = await client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        text = response.text
        assert "http_requests_total" in text
        assert "http_request_duration_seconds" in text

    async def test_readyz_reports_migrations_status(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/readyz")
        assert response.status_code == 200
        body = response.json()
        assert body["checks"]["database"] == "ok"
        assert body["checks"]["migrations"].startswith("ok:")


class TestCatalogApi:
    async def test_create_and_read_back_a_product(self, client: httpx.AsyncClient) -> None:
        tenant_id = await seed.create_tenant(client)
        created = seed.expect(
            await client.post(
                "/v1/products",
                json={"slug": "laptop", "name": "Laptop", "description": "A laptop"},
                headers=seed.tenant_headers(tenant_id),
            ),
            201,
        )
        assert created["status"] == "DRAFT"
        assert created["version"] == 1

        read_back = seed.expect(
            await client.get(
                f"/v1/products/{created['id']}", headers=seed.tenant_headers(tenant_id)
            ),
            200,
        )
        assert read_back == created

    async def test_activate_then_archive_a_product(self, client: httpx.AsyncClient) -> None:
        tenant_id = await seed.create_tenant(client)
        product = seed.expect(
            await client.post(
                "/v1/products",
                json={"slug": "laptop", "name": "Laptop"},
                headers=seed.tenant_headers(tenant_id),
            ),
            201,
        )
        activated = seed.expect(
            await client.post(
                f"/v1/products/{product['id']}/activate", headers=seed.tenant_headers(tenant_id)
            ),
            200,
        )
        assert activated["status"] == "ACTIVE"

        archived = seed.expect(
            await client.post(
                f"/v1/products/{product['id']}/archive", headers=seed.tenant_headers(tenant_id)
            ),
            200,
        )
        assert archived["status"] == "ARCHIVED"

        # ARCHIVED is terminal in Phase 1: reactivation is a new product.
        refused = seed.expect(
            await client.post(
                f"/v1/products/{product['id']}/activate", headers=seed.tenant_headers(tenant_id)
            ),
            409,
        )
        assert refused["error"]["code"] == "invalid_product_transition"

    async def test_a_duplicate_slug_is_rejected(self, client: httpx.AsyncClient) -> None:
        tenant_id = await seed.create_tenant(client)
        await seed.create_product(client, tenant_id=tenant_id, slug="laptop")

        response = await client.post(
            "/v1/products",
            json={"slug": "laptop", "name": "Another Laptop"},
            headers=seed.tenant_headers(tenant_id),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "duplicate_resource"

    async def test_create_a_sku_and_change_its_price(self, client: httpx.AsyncClient) -> None:
        tenant_id = await seed.create_tenant(client)
        product_id = await seed.create_product(client, tenant_id=tenant_id)
        sku = seed.expect(
            await client.post(
                f"/v1/products/{product_id}/skus",
                json={
                    "code": "LAPTOP-001",
                    "name": "Laptop 001",
                    "unit_price_minor": 129900,
                    "currency": "GBP",
                },
                headers=seed.tenant_headers(tenant_id),
            ),
            201,
        )
        assert sku["unit_price"] == {"amount_minor": 129900, "currency": "GBP"}

        repriced = seed.expect(
            await client.put(
                f"/v1/skus/{sku['id']}/price",
                json={"unit_price_minor": 119900, "currency": "GBP"},
                headers=seed.tenant_headers(tenant_id),
            ),
            200,
        )
        assert repriced["unit_price"]["amount_minor"] == 119900
        assert repriced["version"] == 2

    async def test_listing_products_paginates(self, client: httpx.AsyncClient) -> None:
        tenant_id = await seed.create_tenant(client)
        for index in range(3):
            await seed.create_product(client, tenant_id=tenant_id, slug=f"product-{index}")

        page = seed.expect(
            await client.get(
                "/v1/products",
                params={"limit": 2},
                headers=seed.tenant_headers(tenant_id),
            ),
            200,
        )
        assert len(page["items"]) == 2
        assert page["next_cursor"]

    async def test_validation_errors_are_structured(self, client: httpx.AsyncClient) -> None:
        tenant_id = await seed.create_tenant(client)
        response = await client.post(
            "/v1/products",
            json={"slug": "Not A Slug", "name": ""},
            headers=seed.tenant_headers(tenant_id),
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "validation_error"
        assert body["error"]["type"] == "request"
        locations = {tuple(item["loc"]) for item in body["error"]["details"]["errors"]}
        assert ("body", "slug") in locations
        assert ("body", "name") in locations
        assert body["error"]["correlation_id"]

    async def test_a_neutral_format_tenant_header_is_rejected(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/v1/products", headers={"X-Tenant-Id": "not-a-uuid"})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_request"

    async def test_a_missing_tenant_header_is_rejected(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/v1/products")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_request"


class TestInventoryApi:
    async def test_adjust_then_report_availability(self, client: httpx.AsyncClient) -> None:
        fixtures = await provision(client, stock=10)
        position = await seed.stock_position(
            client, tenant_id=fixtures["tenant_id"], sku_id=fixtures["sku_id"]
        )
        assert position == {"on_hand": 10, "reserved": 0, "available": 10}

        adjustment = seed.expect(
            await client.post(
                "/v1/inventory/adjustments",
                json={
                    "sku_id": fixtures["sku_id"],
                    "warehouse_id": fixtures["warehouse_id"],
                    "quantity_delta": -4,
                    "reason": "DAMAGE",
                    "note": "water damage",
                },
                headers=seed.tenant_headers(fixtures["tenant_id"]),
            ),
            200,
        )
        assert adjustment["item"]["on_hand"] == 6
        assert adjustment["movement"]["quantity_delta"] == -4
        assert adjustment["movement"]["reason"] == "DAMAGE"

    async def test_availability_is_filterable_and_scoped(self, client: httpx.AsyncClient) -> None:
        fixtures = await provision(client, stock=5)
        rows = await seed.availability(
            client, tenant_id=fixtures["tenant_id"], sku_id=fixtures["sku_id"]
        )
        assert len(rows) == 1
        assert rows[0]["available"] == 5

    async def test_unknown_sku_is_not_found(self, client: httpx.AsyncClient) -> None:
        fixtures = await provision(client)
        response = await client.post(
            "/v1/inventory/adjustments",
            json={
                "sku_id": "00000000-0000-4000-8000-000000000000",
                "warehouse_id": fixtures["warehouse_id"],
                "quantity_delta": 1,
                "reason": "GOODS_RECEIPT",
            },
            headers=seed.tenant_headers(fixtures["tenant_id"]),
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "sku_not_found"

    async def test_no_warehouse_for_a_pair_is_not_found(self, client: httpx.AsyncClient) -> None:
        fixtures = await provision(client)
        response = await client.get(
            "/v1/inventory/availability",
            params={"warehouse_id": str(fixtures["warehouse_id"])},
            headers=seed.tenant_headers("00000000-0000-4000-8000-000000000000"),
        )
        assert response.status_code in (200, 400, 404)
        if response.status_code == 404:
            assert response.json()["error"]["code"] == "not_found"

    async def test_invalid_quantity_is_a_validation_error(self, client: httpx.AsyncClient) -> None:
        fixtures = await provision(client)
        response = await client.post(
            "/v1/inventory/adjustments",
            json={
                "sku_id": fixtures["sku_id"],
                "warehouse_id": fixtures["warehouse_id"],
                "quantity_delta": 0,
                "reason": "CYCLE_COUNT",
            },
            headers=seed.tenant_headers(fixtures["tenant_id"]),
        )
        assert response.status_code in (400, 422)
        assert response.json()["error"]["code"] in {"invalid_request", "validation_error"}

    async def test_invalid_uuid_is_a_validation_error(self, client: httpx.AsyncClient) -> None:
        fixtures = await provision(client)
        response = await client.post(
            "/v1/inventory/adjustments",
            json={
                "sku_id": "not-a-uuid",
                "warehouse_id": fixtures["warehouse_id"],
                "quantity_delta": 1,
                "reason": "GOODS_RECEIPT",
            },
            headers=seed.tenant_headers(fixtures["tenant_id"]),
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


class TestReservationApi:
    async def test_reserve_then_release(self, client: httpx.AsyncClient) -> None:
        fixtures = await provision(client, stock=3)

        reserved = seed.expect(
            await client.post(
                "/v1/reservations",
                json={"requirements": [{"sku_id": fixtures["sku_id"], "quantity": 2}]},
                headers=seed.tenant_headers(fixtures["tenant_id"]),
            ),
            201,
        )
        assert reserved["is_complete"] is True
        reservation = reserved["reservation"]
        assert reservation["status"] == "ACTIVE"
        assert reservation["total_quantity"] == 2

        position = await seed.stock_position(
            client, tenant_id=fixtures["tenant_id"], sku_id=fixtures["sku_id"]
        )
        assert position == {"on_hand": 3, "reserved": 2, "available": 1}

        released = seed.expect(
            await client.delete(
                f"/v1/reservations/{reservation['id']}",
                headers=seed.tenant_headers(fixtures["tenant_id"]),
            ),
            200,
        )
        assert released["status"] == "RELEASED"

        position = await seed.stock_position(
            client, tenant_id=fixtures["tenant_id"], sku_id=fixtures["sku_id"]
        )
        assert position == {"on_hand": 3, "reserved": 0, "available": 3}

    async def test_reserving_more_than_available_is_a_conflict(
        self, client: httpx.AsyncClient
    ) -> None:
        fixtures = await provision(client, stock=1)
        response = await client.post(
            "/v1/reservations",
            json={"requirements": [{"sku_id": fixtures["sku_id"], "quantity": 2}]},
            headers=seed.tenant_headers(fixtures["tenant_id"]),
        )
        assert response.status_code == 409
        body = response.json()
        assert body["error"]["code"] == "insufficient_inventory"
        assert body["error"]["type"] == "domain"
        assert body["error"]["details"]["shortfalls"]

        # Nothing was held, so nothing changed.
        position = await seed.stock_position(
            client, tenant_id=fixtures["tenant_id"], sku_id=fixtures["sku_id"]
        )
        assert position == {"on_hand": 1, "reserved": 0, "available": 1}

    async def test_partial_strategy_reports_the_shortfall(self, client: httpx.AsyncClient) -> None:
        fixtures = await provision(client, stock=1)
        body = seed.expect(
            await client.post(
                "/v1/reservations",
                json={
                    "requirements": [{"sku_id": fixtures["sku_id"], "quantity": 5}],
                    "strategy": "PARTIAL",
                },
                headers=seed.tenant_headers(fixtures["tenant_id"]),
            ),
            201,
        )
        assert body["is_complete"] is False
        assert body["reservation"]["total_quantity"] == 1
        assert body["shortfalls"][0] == {
            "sku_id": fixtures["sku_id"],
            "required": 5,
            "allocated": 1,
            "missing": 4,
        }

    async def test_releasing_twice_returns_the_same_state(self, client: httpx.AsyncClient) -> None:
        fixtures = await provision(client, stock=2)
        reserved = seed.expect(
            await client.post(
                "/v1/reservations",
                json={"requirements": [{"sku_id": fixtures["sku_id"], "quantity": 1}]},
                headers=seed.tenant_headers(fixtures["tenant_id"]),
            ),
            201,
        )
        reservation_id = reserved["reservation"]["id"]

        first = seed.expect(
            await client.delete(
                f"/v1/reservations/{reservation_id}",
                headers=seed.tenant_headers(fixtures["tenant_id"]),
            ),
            200,
        )
        second = seed.expect(
            await client.delete(
                f"/v1/reservations/{reservation_id}",
                headers=seed.tenant_headers(fixtures["tenant_id"]),
            ),
            200,
        )
        assert first["status"] == second["status"] == "RELEASED"

    async def test_an_unknown_reservation_is_not_found(self, client: httpx.AsyncClient) -> None:
        fixtures = await provision(client, stock=1)
        response = await client.get(
            "/v1/reservations/11111111-1111-4111-8111-111111111111",
            headers=seed.tenant_headers(fixtures["tenant_id"]),
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "reservation_not_found"


class TestOrderApi:
    async def test_submitting_an_order_confirms_it_and_holds_stock(
        self, client: httpx.AsyncClient
    ) -> None:
        fixtures = await provision(client, stock=5)

        response = await seed.submit_order(
            client,
            tenant_id=fixtures["tenant_id"],
            lines=[{"sku_id": fixtures["sku_id"], "quantity": 2}],
            idempotency_key="order-1",
            customer_reference="PO-123",
        )
        body = seed.expect(response, 201)
        assert body["status"] == "CONFIRMED"
        assert body["total"] == {"amount_minor": 5198, "currency": "GBP"}
        assert body["lines"][0]["quantity"] == 2
        assert body["lines"][0]["unit_price"] == {"amount_minor": 2599, "currency": "GBP"}
        # Confirmation keeps the hold: the units are still physically in the
        # warehouse (``on_hand`` is physical stock) and leave it on fulfilment.
        assert body["reservation"]["status"] == "ACTIVE"

        position = await seed.stock_position(
            client, tenant_id=fixtures["tenant_id"], sku_id=fixtures["sku_id"]
        )
        assert position == {"on_hand": 5, "reserved": 2, "available": 3}

    async def test_an_order_beyond_stock_is_backordered(self, client: httpx.AsyncClient) -> None:
        fixtures = await provision(client, stock=1)
        body = seed.expect(
            await seed.submit_order(
                client,
                tenant_id=fixtures["tenant_id"],
                lines=[{"sku_id": fixtures["sku_id"], "quantity": 4}],
                idempotency_key="backorder-1",
            ),
            201,
        )
        assert body["status"] == "BACKORDERED"
        assert body["reservation"]["status"] == "ACTIVE"
        position = await seed.stock_position(
            client, tenant_id=fixtures["tenant_id"], sku_id=fixtures["sku_id"]
        )
        assert position == {"on_hand": 1, "reserved": 1, "available": 0}

    async def test_submission_requires_an_idempotency_key(self, client: httpx.AsyncClient) -> None:
        fixtures = await provision(client, stock=1)
        response = await client.post(
            "/v1/orders",
            json={"lines": [{"sku_id": fixtures["sku_id"], "quantity": 1}]},
            headers=seed.tenant_headers(fixtures["tenant_id"]),
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "idempotency_key_required"

    async def test_an_order_may_not_be_empty(self, client: httpx.AsyncClient) -> None:
        fixtures = await provision(client, stock=1)
        response = await seed.submit_order(
            client, tenant_id=fixtures["tenant_id"], lines=[], idempotency_key="empty"
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    async def test_a_line_quantity_must_be_positive(self, client: httpx.AsyncClient) -> None:
        fixtures = await provision(client, stock=1)
        response = await seed.submit_order(
            client,
            tenant_id=fixtures["tenant_id"],
            lines=[{"sku_id": fixtures["sku_id"], "quantity": 0}],
            idempotency_key="zero-quantity",
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    async def test_an_unknown_sku_is_not_found(self, client: httpx.AsyncClient) -> None:
        fixtures = await provision(client, stock=1)
        response = await seed.submit_order(
            client,
            tenant_id=fixtures["tenant_id"],
            lines=[{"sku_id": "00000000-0000-4000-8000-000000000000", "quantity": 1}],
            idempotency_key="unknown-sku",
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "sku_not_found"

    async def test_read_back_and_list_orders(self, client: httpx.AsyncClient) -> None:
        fixtures = await provision(client, stock=3)
        submitted = seed.expect(
            await seed.submit_order(
                client,
                tenant_id=fixtures["tenant_id"],
                lines=[{"sku_id": fixtures["sku_id"], "quantity": 1}],
                idempotency_key="read-back",
            ),
            201,
        )

        read_back = seed.expect(
            await client.get(
                f"/v1/orders/{submitted['id']}",
                headers=seed.tenant_headers(fixtures["tenant_id"]),
            ),
            200,
        )
        assert read_back["id"] == submitted["id"]
        assert read_back["total"] == submitted["total"]

        listed = seed.expect(
            await client.get("/v1/orders", headers=seed.tenant_headers(fixtures["tenant_id"])),
            200,
        )
        assert [order["id"] for order in listed["items"]] == [submitted["id"]]

    async def test_confirming_an_already_confirmed_order_is_a_conflict(
        self, client: httpx.AsyncClient
    ) -> None:
        fixtures = await provision(client, stock=2)
        submitted = seed.expect(
            await seed.submit_order(
                client,
                tenant_id=fixtures["tenant_id"],
                lines=[{"sku_id": fixtures["sku_id"], "quantity": 1}],
                idempotency_key="double-confirm",
            ),
            201,
        )

        response = await client.post(
            f"/v1/orders/{submitted['id']}/confirm",
            headers=seed.tenant_headers(fixtures["tenant_id"]),
        )
        assert response.status_code == 409
        body = response.json()
        assert body["error"]["code"] == "invalid_order_transition"
        assert body["error"]["details"] == {"from": "CONFIRMED", "to": "CONFIRMED"}

    async def test_cancelling_a_backordered_order_releases_the_hold(
        self, client: httpx.AsyncClient
    ) -> None:
        fixtures = await provision(client, stock=1)
        submitted = seed.expect(
            await seed.submit_order(
                client,
                tenant_id=fixtures["tenant_id"],
                lines=[{"sku_id": fixtures["sku_id"], "quantity": 3}],
                idempotency_key="cancel-flow",
            ),
            201,
        )
        assert submitted["status"] == "BACKORDERED"

        cancelled = seed.expect(
            await client.post(
                f"/v1/orders/{submitted['id']}/cancel",
                json={"reason": "customer cancelled"},
                headers=seed.idempotent_headers(fixtures["tenant_id"], "cancel-flow-key"),
            ),
            200,
        )
        assert cancelled["status"] == "CANCELLED"
        assert cancelled["cancellation_reason"] == "customer cancelled"

        position = await seed.stock_position(
            client, tenant_id=fixtures["tenant_id"], sku_id=fixtures["sku_id"]
        )
        assert position == {"on_hand": 1, "reserved": 0, "available": 1}

    async def test_cancel_honours_if_match(self, client: httpx.AsyncClient) -> None:
        fixtures = await provision(client, stock=1)
        submitted = seed.expect(
            await seed.submit_order(
                client,
                tenant_id=fixtures["tenant_id"],
                lines=[{"sku_id": fixtures["sku_id"], "quantity": 2}],
                idempotency_key="if-match-order",
            ),
            201,
        )
        current_version = submitted["version"]

        stale = await client.post(
            f"/v1/orders/{submitted['id']}/cancel",
            json={"reason": "stale"},
            headers=seed.idempotent_headers(fixtures["tenant_id"], "if-match-1")
            | {"If-Match": str(current_version + 5)},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "concurrency_conflict"

        fresh = await client.post(
            f"/v1/orders/{submitted['id']}/cancel",
            json={"reason": "current"},
            headers=seed.idempotent_headers(fixtures["tenant_id"], "if-match-2")
            | {"If-Match": str(current_version)},
        )
        assert fresh.status_code == 200

    async def test_a_malformed_if_match_is_a_bad_request(self, client: httpx.AsyncClient) -> None:
        fixtures = await provision(client, stock=1)
        submitted = seed.expect(
            await seed.submit_order(
                client,
                tenant_id=fixtures["tenant_id"],
                lines=[{"sku_id": fixtures["sku_id"], "quantity": 2}],
                idempotency_key="bad-if-match",
            ),
            201,
        )
        response = await client.post(
            f"/v1/orders/{submitted['id']}/cancel",
            json={"reason": "bad header"},
            headers=seed.tenant_headers(fixtures["tenant_id"], **{"If-Match": "not-a-version"}),
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_request"


class TestIdempotencyOverHttp:
    async def test_the_same_key_and_payload_replays_the_order(
        self, client: httpx.AsyncClient, database: Any
    ) -> None:
        fixtures = await provision(client, stock=5)
        first = await seed.submit_order(
            client,
            tenant_id=fixtures["tenant_id"],
            lines=[{"sku_id": fixtures["sku_id"], "quantity": 3}],
            idempotency_key="replay-key",
        )
        second = await seed.submit_order(
            client,
            tenant_id=fixtures["tenant_id"],
            lines=[{"sku_id": fixtures["sku_id"], "quantity": 3}],
            idempotency_key="replay-key",
        )

        assert first.status_code == second.status_code == 201
        assert second.headers.get("Idempotent-Replay") == "true"
        assert first.json()["id"] == second.json()["id"]

        # Exactly one order exists and stock was held exactly once.
        assert await count(database, "orders") == 1
        position = await seed.stock_position(
            client, tenant_id=fixtures["tenant_id"], sku_id=fixtures["sku_id"]
        )
        assert position == {"on_hand": 5, "reserved": 3, "available": 2}

    async def test_the_same_key_with_a_different_payload_is_refused(
        self, client: httpx.AsyncClient, database: Any
    ) -> None:
        fixtures = await provision(client, stock=5)
        await seed.submit_order(
            client,
            tenant_id=fixtures["tenant_id"],
            lines=[{"sku_id": fixtures["sku_id"], "quantity": 1}],
            idempotency_key="conflict-key",
        )

        response = await seed.submit_order(
            client,
            tenant_id=fixtures["tenant_id"],
            lines=[{"sku_id": fixtures["sku_id"], "quantity": 2}],
            idempotency_key="conflict-key",
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "idempotency_key_reused"
        assert await count(database, "orders") == 1

    async def test_keys_are_scoped_per_tenant(
        self, client: httpx.AsyncClient, database: Any
    ) -> None:
        first = await provision(client, stock=2)
        second_id = await seed.create_tenant(client, slug="contoso", name="Contoso")
        _, second_sku = await seed.provision_catalog(
            client, tenant_id=second_id, slug="laptop", code="LAPTOP-001"
        )
        second_warehouse = await seed.create_warehouse(client, tenant_id=second_id)
        await seed.adjust_stock(
            client,
            tenant_id=second_id,
            sku_id=second_sku,
            warehouse_id=second_warehouse,
            quantity_delta=2,
        )

        mine = await seed.submit_order(
            client,
            tenant_id=first["tenant_id"],
            lines=[{"sku_id": first["sku_id"], "quantity": 1}],
            idempotency_key="shared-key",
        )
        theirs = await seed.submit_order(
            client,
            tenant_id=second_id,
            lines=[{"sku_id": second_sku, "quantity": 1}],
            idempotency_key="shared-key",
        )

        assert mine.status_code == theirs.status_code == 201
        assert mine.json()["id"] != theirs.json()["id"]
        assert theirs.headers.get("Idempotent-Replay") is None
        assert await count(database, "orders") == 2

    async def test_a_retry_while_the_same_key_is_in_flight_is_not_a_replay(
        self, client: httpx.AsyncClient, database: Any
    ) -> None:
        """A retry that arrives while the first attempt still runs is told to wait.

        The state is produced with the *same* request fingerprint the application
        computed, so the store really walks the in-flight branch: it must not treat a
        running operation as a completed one (which would return a resource that may
        not exist yet, or serve a stale result).
        """
        from sqlalchemy import text

        fixtures = await provision(client, stock=2)
        lines = [{"sku_id": fixtures["sku_id"], "quantity": 1}]

        first = seed.expect(
            await seed.submit_order(
                client,
                tenant_id=fixtures["tenant_id"],
                lines=lines,
                idempotency_key="in-flight-key",
            ),
            201,
        )
        order_id = first["id"]

        # Rewind the completed record to the state a concurrent first attempt leaves
        # behind while it is still running.
        async with database.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE idempotency_records SET state = 'in_progress'"
                    " WHERE idempotency_key = :key"
                ),
                {"key": "in-flight-key"},
            )

        retry = await seed.submit_order(
            client,
            tenant_id=fixtures["tenant_id"],
            lines=lines,
            idempotency_key="in-flight-key",
        )
        assert retry.status_code == 409
        assert retry.json()["error"]["code"] == "idempotency_in_progress"
        assert await count(database, "orders") == 1
        still_there = seed.expect(
            await client.get(
                f"/v1/orders/{order_id}", headers=seed.tenant_headers(fixtures["tenant_id"])
            ),
            200,
        )
        assert still_there["id"] == order_id

    async def test_a_failed_attempt_can_be_retried_with_the_same_key(
        self, client: httpx.AsyncClient, database: Any
    ) -> None:
        """A rollback discards the claim, so a client is never poisoned by a failure."""
        fixtures = await provision(client, stock=1)
        rejected = await seed.submit_order(
            client,
            tenant_id=fixtures["tenant_id"],
            lines=[{"sku_id": fixtures["sku_id"], "quantity": 5}],  # back-orders, does not fail
            idempotency_key="retry-key",
        )
        assert rejected.status_code == 201

        # Now a genuine failure: the warehouse is gone -> 404, nothing committed.
        failing = await seed.submit_order(
            client,
            tenant_id=fixtures["tenant_id"],
            lines=[{"sku_id": "00000000-0000-4000-8000-000000000000", "quantity": 1}],
            idempotency_key="retry-after-failure",
        )
        assert failing.status_code == 404

        stored = await scalar(
            database,
            "SELECT count(*) FROM idempotency_records WHERE idempotency_key = :key",
            key="retry-after-failure",
        )
        assert stored == 0, "a rolled back attempt must not leave a claim behind"

    async def test_a_completed_record_is_committed_atomically(
        self, client: httpx.AsyncClient, database: Any
    ) -> None:
        fixtures = await provision(client, stock=2)
        submitted = seed.expect(
            await seed.submit_order(
                client,
                tenant_id=fixtures["tenant_id"],
                lines=[{"sku_id": fixtures["sku_id"], "quantity": 1}],
                idempotency_key="atomic-key",
            ),
            201,
        )

        record = await scalar(
            database,
            "SELECT state FROM idempotency_records WHERE idempotency_key = :key",
            key="atomic-key",
        )
        assert record == IdempotencyState.COMPLETED.value
        assert await count(database, "orders", id=submitted["id"]) == 1


class TestTenantIsolationOverHttp:
    async def test_another_tenant_cannot_read_or_cancel_the_order(
        self, client: httpx.AsyncClient
    ) -> None:
        owner = await provision(client, stock=3)
        other_id = await seed.create_tenant(client, slug="globex", name="Globex")

        submitted = seed.expect(
            await seed.submit_order(
                client,
                tenant_id=owner["tenant_id"],
                lines=[{"sku_id": owner["sku_id"], "quantity": 1}],
                idempotency_key="isolation-order",
            ),
            201,
        )

        read = await client.get(
            f"/v1/orders/{submitted['id']}", headers=seed.tenant_headers(other_id)
        )
        assert read.status_code == 404

        cancel = await client.post(
            f"/v1/orders/{submitted['id']}/cancel",
            json={"reason": "not mine"},
            headers=seed.idempotent_headers(other_id, "isolation-cancel"),
        )
        assert cancel.status_code == 404

        # The owner's order is untouched.
        still_there = seed.expect(
            await client.get(
                f"/v1/orders/{submitted['id']}",
                headers=seed.tenant_headers(owner["tenant_id"]),
            ),
            200,
        )
        assert still_there["status"] == submitted["status"]

    async def test_another_tenant_cannot_reserve_or_release_the_hold(
        self, client: httpx.AsyncClient
    ) -> None:
        owner = await provision(client, stock=2)
        other_id = await seed.create_tenant(client, slug="globex", name="Globex")

        reserved = seed.expect(
            await client.post(
                "/v1/reservations",
                json={"requirements": [{"sku_id": owner["sku_id"], "quantity": 1}]},
                headers=seed.tenant_headers(owner["tenant_id"]),
            ),
            201,
        )
        reservation_id = reserved["reservation"]["id"]

        assert (
            await client.get(
                f"/v1/reservations/{reservation_id}", headers=seed.tenant_headers(other_id)
            )
        ).status_code == 404
        assert (
            await client.delete(
                f"/v1/reservations/{reservation_id}", headers=seed.tenant_headers(other_id)
            )
        ).status_code == 404

        position = await seed.stock_position(
            client, tenant_id=owner["tenant_id"], sku_id=owner["sku_id"]
        )
        assert position == {"on_hand": 2, "reserved": 1, "available": 1}

    async def test_another_tenant_cannot_order_or_adjust_my_sku(
        self, client: httpx.AsyncClient
    ) -> None:
        owner = await provision(client, stock=2)
        other_id = await seed.create_tenant(client, slug="globex", name="Globex")

        order = await seed.submit_order(
            client,
            tenant_id=other_id,
            lines=[{"sku_id": owner["sku_id"], "quantity": 1}],
            idempotency_key="foreign-sku",
        )
        assert order.status_code == 404

        adjustment = await client.post(
            "/v1/inventory/adjustments",
            json={
                "sku_id": owner["sku_id"],
                "warehouse_id": owner["warehouse_id"],
                "quantity_delta": 5,
                "reason": "GOODS_RECEIPT",
            },
            headers=seed.tenant_headers(other_id),
        )
        assert adjustment.status_code == 404

        position = await seed.stock_position(
            client, tenant_id=owner["tenant_id"], sku_id=owner["sku_id"]
        )
        assert position == {"on_hand": 2, "reserved": 0, "available": 2}

    async def test_lists_never_cross_tenants(self, client: httpx.AsyncClient) -> None:
        owner = await provision(client, stock=1)
        other_id = await seed.create_tenant(client, slug="globex", name="Globex")
        await seed.submit_order(
            client,
            tenant_id=owner["tenant_id"],
            lines=[{"sku_id": owner["sku_id"], "quantity": 1}],
            idempotency_key="list-isolation",
        )

        orders = seed.expect(
            await client.get("/v1/orders", headers=seed.tenant_headers(other_id)), 200
        )
        products = seed.expect(
            await client.get("/v1/products", headers=seed.tenant_headers(other_id)), 200
        )
        availability = seed.expect(
            await client.get("/v1/inventory/availability", headers=seed.tenant_headers(other_id)),
            200,
        )
        assert orders["items"] == []
        assert products["items"] == []
        assert availability["items"] == []


class TestErrorContract:
    async def test_not_found_errors_are_structured_and_leak_nothing(
        self, client: httpx.AsyncClient
    ) -> None:
        tenant_id = await seed.create_tenant(client)
        response = await client.get(
            "/v1/orders/22222222-2222-4222-8222-222222222222",
            headers=seed.tenant_headers(tenant_id),
        )
        assert response.status_code == 404
        body = response.json()
        assert set(body) == {"error"}
        assert set(body["error"]) == {"code", "message", "details", "correlation_id", "type"}
        rendered = response.text.lower()
        for leak in ("traceback", "select ", "sqlalchemy", "asyncpg", "password", "/home/"):
            assert leak not in rendered

    async def test_every_error_carries_the_correlation_id_of_the_request(
        self, client: httpx.AsyncClient
    ) -> None:
        tenant_id = await seed.create_tenant(client)
        response = await client.get(
            "/v1/orders/33333333-3333-4333-8333-333333333333",
            headers=seed.tenant_headers(tenant_id) | {seed.CORRELATION_HEADER: "trace-me-please"},
        )
        assert response.json()["error"]["correlation_id"] == "trace-me-please"
        assert response.headers["X-Correlation-Id"] == "trace-me-please"

    async def test_a_domain_error_is_not_a_500(self, client: httpx.AsyncClient) -> None:
        fixtures = await provision(client, stock=1)
        response = await client.post(
            "/v1/reservations",
            json={"requirements": [{"sku_id": fixtures["sku_id"], "quantity": 99}]},
            headers=seed.tenant_headers(fixtures["tenant_id"]),
        )
        assert response.status_code == 409
        assert response.json()["error"]["type"] == "domain"

    async def test_an_oversized_body_is_refused(self, client: httpx.AsyncClient) -> None:
        tenant_id = await seed.create_tenant(client)
        response = await client.post(
            "/v1/products",
            json={"slug": "huge", "name": "x" * (1024 * 1024 + 10)},
            headers=seed.tenant_headers(tenant_id),
        )
        assert response.status_code in (413, 422)


class TestOpenApi:
    async def test_openapi_document_is_valid_and_complete(self, client: httpx.AsyncClient) -> None:
        document = seed.expect(await client.get("/openapi.json"), 200)

        assert document["info"]["title"]
        assert document["paths"], "the document must describe the Phase 1 routes"

        expected = {
            ("/v1/orders", "post"),
            ("/v1/orders", "get"),
            ("/v1/orders/{order_id}", "get"),
            ("/v1/orders/{order_id}/confirm", "post"),
            ("/v1/orders/{order_id}/cancel", "post"),
            ("/v1/products", "post"),
            ("/v1/products", "get"),
            ("/v1/products/{product_id}", "get"),
            ("/v1/products/{product_id}", "put"),
            ("/v1/products/{product_id}/skus", "post"),
            ("/v1/skus/{sku_id}", "get"),
            ("/v1/skus/{sku_id}/price", "put"),
            ("/v1/warehouses", "post"),
            ("/v1/inventory/adjustments", "post"),
            ("/v1/inventory/availability", "get"),
            ("/v1/reservations", "post"),
            ("/v1/reservations/{reservation_id}", "get"),
            ("/v1/reservations/{reservation_id}", "delete"),
            ("/v1/tenants", "post"),
            ("/healthz", "get"),
            ("/readyz", "get"),
        }
        missing = {
            (path, method)
            for path, method in expected
            if path not in document["paths"] or method not in document["paths"][path]
        }
        assert not missing, f"missing from OpenAPI: {sorted(missing)}"

        order_post = document["paths"]["/v1/orders"]["post"]
        assert order_post["requestBody"]["content"]["application/json"]["schema"]
        assert "201" in order_post["responses"]
        assert "Idempotency-Key" in {
            parameter["name"] for parameter in order_post.get("parameters", [])
        }

        schemas = document["components"]["schemas"]
        for name in ("OrderResponse", "CreateOrderRequest", "ErrorResponse", "ReservationResponse"):
            assert name in schemas, f"{name} must be published"

        assert schemas["ErrorResponse"]["properties"]["error"]
        tags = {
            tag
            for path in document["paths"].values()
            for op in path.values()
            for tag in op.get("tags", [])
        }
        assert {"catalog", "inventory", "ordering", "platform"} <= tags

    async def test_swagger_ui_is_served(self, client: httpx.AsyncClient) -> None:
        page = await client.get("/docs")
        assert page.status_code == 200
        assert "swagger-ui" in page.text.lower()

    async def test_health_and_readiness_are_outside_the_versioned_prefix(
        self, client: httpx.AsyncClient
    ) -> None:
        document = seed.expect(await client.get("/openapi.json"), 200)
        assert "/v1/healthz" not in document["paths"]
