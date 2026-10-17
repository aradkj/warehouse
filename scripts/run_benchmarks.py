#!/usr/bin/env python3
"""Execute reproducible performance scenarios and output completion metrics (Phase 5 Workstream A).

Profiles supported:
- smoke: 5 concurrent workers, 50 requests
- baseline: 20 concurrent workers, 200 requests
- stress: 50 concurrent workers, 500 requests
- spike: 100 concurrent workers burst
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# Ensure repository root and src are on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx
from tests.performance.benchmark_suite import (
    TARGET_COMMAND_P95_MS,
    TARGET_READ_P95_MS,
    BenchmarkResult,
)

from bootstrap.app import create_app
from bootstrap.config import Settings


async def execute_benchmarks(
    base_url: str | None = None,
    profile: str = "baseline",
    database_url: str | None = None,
) -> list[BenchmarkResult]:
    db_url = database_url or os.environ.get(
        "ARAD_DATABASE_URL", "postgresql+asyncpg://arad:arad@localhost:5432/arad"
    )
    settings = Settings(
        database_url=db_url,
        environment="testing",
        auth_jwt_secret="benchmark-secret-key-must-be-32-chars-long-secure",
        db_pool_size=30,
        db_max_overflow=30,
    )

    # Select client transport: local ASGI or remote HTTP
    transport: httpx.AsyncBaseTransport
    app = None
    if base_url:
        client_kwargs: dict[str, Any] = {"base_url": base_url.rstrip("/")}
    else:
        app = create_app(settings=settings)
        transport = httpx.ASGITransport(app=app)
        client_kwargs = {"transport": transport, "base_url": "http://testserver"}

    @asynccontextmanager
    async def app_scope() -> AsyncIterator[None]:
        if app is not None:
            async with app.router.lifespan_context(app):
                yield
        else:
            yield

    profile_configs = {
        "smoke": {"concurrency": 5, "requests": 50},
        "baseline": {"concurrency": 20, "requests": 200},
        "stress": {"concurrency": 50, "requests": 500},
        "spike": {"concurrency": 100, "requests": 500},
    }
    cfg = profile_configs.get(profile, profile_configs["baseline"])
    concurrency = cfg["concurrency"]
    requests_count = cfg["requests"]

    print("=" * 80)
    print(f"ARAD PLATFORM PERFORMANCE BENCHMARKS — PROFILE: {profile.upper()}")
    print(f"Concurrency: {concurrency} | Total Requests / Scenario: {requests_count}")
    print("=" * 80)

    results: list[BenchmarkResult] = []

    async with app_scope(), httpx.AsyncClient(**client_kwargs, timeout=30.0) as client:
        # Step 0: Setup seed tenant, catalog, and stock for realistic benchmarking
        tenant_slug = f"bench-{uuid.uuid4().hex[:6]}"
        reg_resp = await client.post(
            "/v1/tenants",
            json={"slug": tenant_slug, "name": "Benchmark Tenant"},
        )
        if reg_resp.status_code not in (200, 201):
            raise RuntimeError(f"Failed to provision benchmark tenant: {reg_resp.text}")
        tenant_id = reg_resp.json()["id"]
        headers = {"X-Tenant-Id": tenant_id}

        prod_resp = await client.post(
            "/v1/products",
            json={"slug": f"prod-{uuid.uuid4().hex[:6]}", "name": "Benchmarking Item"},
            headers=headers,
        )
        product_id = prod_resp.json()["id"]

        # Activate product
        await client.post(f"/v1/products/{product_id}/activate", headers=headers)

        # Create SKU
        sku_code = f"SKU-BENCH-{uuid.uuid4().hex[:4]}"
        sku_resp = await client.post(
            f"/v1/products/{product_id}/skus",
            json={
                "code": sku_code,
                "name": "Benchmark SKU",
                "unit_price_minor": 1999,
                "currency": "GBP",
            },
            headers=headers,
        )
        sku_id = sku_resp.json()["id"]

        # Warehouse & stock
        wh_resp = await client.post(
            "/v1/warehouses",
            json={"code": f"WH-{uuid.uuid4().hex[:4]}", "name": "Bench Warehouse"},
            headers=headers,
        )
        warehouse_id = wh_resp.json()["id"]

        # Stock 10,000 units for benchmarking mutations
        await client.post(
            "/v1/inventory/adjustments",
            json={
                "sku_id": sku_id,
                "warehouse_id": warehouse_id,
                "quantity_delta": 10000,
                "reason": "goods_receipt",
            },
            headers=headers,
        )
        # Ensure search indices and index test product for search benchmark
        if (
            app is not None
            and hasattr(app.state, "search_indexer")
            and hasattr(app.state.search_indexer, "ensure_index")
        ):
            from search.application.models import (
                ORDERS_INDEX_ALIAS,
                ORDERS_INDEX_MAPPINGS,
                ORDERS_INDEX_V1,
                PRODUCTS_INDEX_ALIAS,
                PRODUCTS_INDEX_MAPPINGS,
                PRODUCTS_INDEX_V1,
            )
            from search.ports.indexer import SearchDocument

            await app.state.search_indexer.ensure_index(
                index_name=PRODUCTS_INDEX_V1,
                mappings=PRODUCTS_INDEX_MAPPINGS,
                aliases=(PRODUCTS_INDEX_ALIAS,),
            )
            await app.state.search_indexer.ensure_index(
                index_name=ORDERS_INDEX_V1,
                mappings=ORDERS_INDEX_MAPPINGS,
                aliases=(ORDERS_INDEX_ALIAS,),
            )
            await app.state.search_indexer.index(
                (
                    SearchDocument(
                        index_alias=PRODUCTS_INDEX_ALIAS,
                        document_id=f"{tenant_id}:{product_id}",
                        tenant_id=tenant_id,
                        version=1,
                        body={
                            "id": product_id,
                            "tenant_id": tenant_id,
                            "slug": f"prod-bench-{uuid.uuid4().hex[:6]}",
                            "name": "Benchmarking Item",
                            "description": "Benchmark Product Item",
                            "status": "ACTIVE",
                            "attributes": {},
                            "skus": [
                                {
                                    "id": sku_id,
                                    "code": sku_code,
                                    "name": "Benchmark SKU",
                                    "unit_price_minor": 1999,
                                    "currency": "GBP",
                                }
                            ],
                            "created_at": "2026-10-15T12:00:00Z",
                            "updated_at": "2026-10-15T12:00:00Z",
                            "version": 1,
                        },
                    ),
                ),
                refresh=True,
            )

        # ----------------------------------------------------------------------
        # Warm-up Phase (Establish DB pool, Redis connection, ES warmup)
        # ----------------------------------------------------------------------
        print(
            f"\nPriming connection pools and caches (concurrent warm-up with {concurrency} workers)..."
        )

        async def _warmup_worker() -> None:
            for _ in range(5):
                await client.get(f"/v1/products/{product_id}", headers=headers)
                await client.get("/v1/search/products?q=Bench&status=ACTIVE", headers=headers)

        await asyncio.gather(*(_warmup_worker() for _ in range(concurrency)))

        # ----------------------------------------------------------------------
        # Scenario 1: Product & Catalog Reads (Target: P95 < 250ms)
        # ----------------------------------------------------------------------
        print("\n[Scenario 1/6] Benchmarking Product & Catalog Reads...")
        latencies_s1: list[float] = []
        failures_s1 = 0
        sem = asyncio.Semaphore(concurrency)
        start_time = time.perf_counter()

        async def worker_s1() -> None:
            nonlocal failures_s1
            async with sem:
                t0 = time.perf_counter()
                r = await client.get(f"/v1/products/{product_id}", headers=headers)
                dur = (time.perf_counter() - t0) * 1000.0
                latencies_s1.append(dur)
                if r.status_code != 200:
                    failures_s1 += 1

        await asyncio.gather(*(worker_s1() for _ in range(requests_count)))
        total_dur_s1 = time.perf_counter() - start_time
        latencies_s1.sort()
        p50 = latencies_s1[int(len(latencies_s1) * 0.50)]
        p95 = latencies_s1[int(len(latencies_s1) * 0.95)]
        p99 = latencies_s1[int(len(latencies_s1) * 0.99)]
        res_s1 = BenchmarkResult(
            scenario_name="Catalog Reads (GET /v1/products/{id})",
            total_requests=requests_count,
            successful_requests=requests_count - failures_s1,
            failed_requests=failures_s1,
            duration_seconds=round(total_dur_s1, 3),
            throughput_rps=round(requests_count / total_dur_s1, 1),
            latency_p50_ms=round(p50, 2),
            latency_p95_ms=round(p95, 2),
            latency_p99_ms=round(p99, 2),
            target_p95_ms=TARGET_READ_P95_MS,
            target_met=(p95 < TARGET_READ_P95_MS and failures_s1 == 0),
        )
        results.append(res_s1)

        # ----------------------------------------------------------------------
        # Scenario 2: Inventory Availability Reads (Target: P95 < 250ms)
        # ----------------------------------------------------------------------
        print("[Scenario 2/6] Benchmarking Inventory Availability Reads...")
        latencies_s2: list[float] = []
        failures_s2 = 0
        start_time = time.perf_counter()

        async def worker_s2() -> None:
            nonlocal failures_s2
            async with sem:
                t0 = time.perf_counter()
                r = await client.get(f"/v1/inventory/availability?sku_id={sku_id}", headers=headers)
                dur = (time.perf_counter() - t0) * 1000.0
                latencies_s2.append(dur)
                if r.status_code != 200:
                    failures_s2 += 1

        await asyncio.gather(*(worker_s2() for _ in range(requests_count)))
        total_dur_s2 = time.perf_counter() - start_time
        latencies_s2.sort()
        res_s2 = BenchmarkResult(
            scenario_name="Inventory Availability (GET /v1/inventory/availability)",
            total_requests=requests_count,
            successful_requests=requests_count - failures_s2,
            failed_requests=failures_s2,
            duration_seconds=round(total_dur_s2, 3),
            throughput_rps=round(requests_count / total_dur_s2, 1),
            latency_p50_ms=round(latencies_s2[int(len(latencies_s2) * 0.50)], 2),
            latency_p95_ms=round(latencies_s2[int(len(latencies_s2) * 0.95)], 2),
            latency_p99_ms=round(latencies_s2[int(len(latencies_s2) * 0.99)], 2),
            target_p95_ms=TARGET_READ_P95_MS,
            target_met=(
                latencies_s2[int(len(latencies_s2) * 0.95)] < TARGET_READ_P95_MS
                and failures_s2 == 0
            ),
        )
        results.append(res_s2)

        # ----------------------------------------------------------------------
        # Scenario 3: Order Creation with Unique Idempotency Keys (Target: P95 < 500ms)
        # ----------------------------------------------------------------------
        print("[Scenario 3/6] Benchmarking Transactional Order Creation (Unique Keys)...")
        latencies_s3: list[float] = []
        failures_s3 = 0
        start_time = time.perf_counter()

        async def worker_s3(idx: int) -> None:
            nonlocal failures_s3
            async with sem:
                key = f"order-bench-unique-{idx}-{uuid.uuid4().hex[:6]}"
                order_headers = dict(headers)
                order_headers["Idempotency-Key"] = key
                t0 = time.perf_counter()
                r = await client.post(
                    "/v1/orders",
                    json={"lines": [{"sku_id": sku_id, "quantity": 1}]},
                    headers=order_headers,
                )
                dur = (time.perf_counter() - t0) * 1000.0
                latencies_s3.append(dur)
                if r.status_code not in (200, 201):
                    failures_s3 += 1

        await asyncio.gather(*(worker_s3(i) for i in range(requests_count)))
        total_dur_s3 = time.perf_counter() - start_time
        latencies_s3.sort()
        res_s3 = BenchmarkResult(
            scenario_name="Order Creation (POST /v1/orders - Unique Keys)",
            total_requests=requests_count,
            successful_requests=requests_count - failures_s3,
            failed_requests=failures_s3,
            duration_seconds=round(total_dur_s3, 3),
            throughput_rps=round(requests_count / total_dur_s3, 1),
            latency_p50_ms=round(latencies_s3[int(len(latencies_s3) * 0.50)], 2),
            latency_p95_ms=round(latencies_s3[int(len(latencies_s3) * 0.95)], 2),
            latency_p99_ms=round(latencies_s3[int(len(latencies_s3) * 0.99)], 2),
            target_p95_ms=TARGET_COMMAND_P95_MS,
            target_met=(
                latencies_s3[int(len(latencies_s3) * 0.95)] < TARGET_COMMAND_P95_MS
                and failures_s3 == 0
            ),
        )
        results.append(res_s3)

        # ----------------------------------------------------------------------
        # Scenario 4: Idempotent Replay with Identical Idempotency Keys (Target: P95 < 250ms)
        # ----------------------------------------------------------------------
        print("[Scenario 4/6] Benchmarking Idempotent Order Replay (Identical Key)...")
        latencies_s4: list[float] = []
        failures_s4 = 0
        replay_key = f"order-replay-key-{uuid.uuid4().hex[:8]}"
        replay_headers = dict(headers)
        replay_headers["Idempotency-Key"] = replay_key
        # Initial submission
        init_res = await client.post(
            "/v1/orders",
            json={"lines": [{"sku_id": sku_id, "quantity": 1}]},
            headers=replay_headers,
        )
        assert init_res.status_code in (200, 201)

        start_time = time.perf_counter()

        async def worker_s4() -> None:
            nonlocal failures_s4
            async with sem:
                t0 = time.perf_counter()
                r = await client.post(
                    "/v1/orders",
                    json={"lines": [{"sku_id": sku_id, "quantity": 1}]},
                    headers=replay_headers,
                )
                dur = (time.perf_counter() - t0) * 1000.0
                latencies_s4.append(dur)
                if r.status_code not in (200, 201):
                    failures_s4 += 1

        await asyncio.gather(*(worker_s4() for _ in range(requests_count)))
        total_dur_s4 = time.perf_counter() - start_time
        latencies_s4.sort()
        res_s4 = BenchmarkResult(
            scenario_name="Order Replay (POST /v1/orders - Identical Key)",
            total_requests=requests_count,
            successful_requests=requests_count - failures_s4,
            failed_requests=failures_s4,
            duration_seconds=round(total_dur_s4, 3),
            throughput_rps=round(requests_count / total_dur_s4, 1),
            latency_p50_ms=round(latencies_s4[int(len(latencies_s4) * 0.50)], 2),
            latency_p95_ms=round(latencies_s4[int(len(latencies_s4) * 0.95)], 2),
            latency_p99_ms=round(latencies_s4[int(len(latencies_s4) * 0.99)], 2),
            target_p95_ms=TARGET_READ_P95_MS,
            target_met=(
                latencies_s4[int(len(latencies_s4) * 0.95)] < TARGET_READ_P95_MS
                and failures_s4 == 0
            ),
        )
        results.append(res_s4)

        # ----------------------------------------------------------------------
        # Scenario 5: Filtered Search Queries (Target: P95 < 250ms)
        # ----------------------------------------------------------------------
        print("[Scenario 5/6] Benchmarking Filtered Search Queries...")
        latencies_s5: list[float] = []
        failures_s5 = 0
        start_time = time.perf_counter()

        async def worker_s5() -> None:
            nonlocal failures_s5
            async with sem:
                t0 = time.perf_counter()
                r = await client.get("/v1/search/products?q=Bench&status=ACTIVE", headers=headers)
                dur = (time.perf_counter() - t0) * 1000.0
                latencies_s5.append(dur)
                if r.status_code != 200:
                    failures_s5 += 1

        await asyncio.gather(*(worker_s5() for _ in range(requests_count)))
        total_dur_s5 = time.perf_counter() - start_time
        latencies_s5.sort()
        res_s5 = BenchmarkResult(
            scenario_name="Filtered Search (GET /v1/search/products)",
            total_requests=requests_count,
            successful_requests=requests_count - failures_s5,
            failed_requests=failures_s5,
            duration_seconds=round(total_dur_s5, 3),
            throughput_rps=round(requests_count / total_dur_s5, 1),
            latency_p50_ms=round(latencies_s5[int(len(latencies_s5) * 0.50)], 2),
            latency_p95_ms=round(latencies_s5[int(len(latencies_s5) * 0.95)], 2),
            latency_p99_ms=round(latencies_s5[int(len(latencies_s5) * 0.99)], 2),
            target_p95_ms=TARGET_READ_P95_MS,
            target_met=(
                latencies_s5[int(len(latencies_s5) * 0.95)] < TARGET_READ_P95_MS
                and failures_s5 == 0
            ),
        )
        results.append(res_s5)

        # ----------------------------------------------------------------------
        # Scenario 6: Mixed Read/Write Realistic Traffic (70% read, 30% write)
        # ----------------------------------------------------------------------
        print("[Scenario 6/6] Benchmarking Mixed Read/Write Traffic...")
        latencies_s6: list[float] = []
        failures_s6 = 0
        start_time = time.perf_counter()

        async def worker_s6(idx: int) -> None:
            nonlocal failures_s6
            async with sem:
                t0 = time.perf_counter()
                is_write = random.random() < 0.30
                if is_write:
                    key = f"mixed-order-{idx}-{uuid.uuid4().hex[:6]}"
                    o_headers = dict(headers)
                    o_headers["Idempotency-Key"] = key
                    r = await client.post(
                        "/v1/orders",
                        json={"lines": [{"sku_id": sku_id, "quantity": 1}]},
                        headers=o_headers,
                    )
                    ok = r.status_code in (200, 201)
                else:
                    r = await client.get(f"/v1/products/{product_id}", headers=headers)
                    ok = r.status_code == 200

                dur = (time.perf_counter() - t0) * 1000.0
                latencies_s6.append(dur)
                if not ok:
                    failures_s6 += 1

        await asyncio.gather(*(worker_s6(i) for i in range(requests_count)))
        total_dur_s6 = time.perf_counter() - start_time
        latencies_s6.sort()
        res_s6 = BenchmarkResult(
            scenario_name="Mixed Read/Write Workload (70/30 Profile)",
            total_requests=requests_count,
            successful_requests=requests_count - failures_s6,
            failed_requests=failures_s6,
            duration_seconds=round(total_dur_s6, 3),
            throughput_rps=round(requests_count / total_dur_s6, 1),
            latency_p50_ms=round(latencies_s6[int(len(latencies_s6) * 0.50)], 2),
            latency_p95_ms=round(latencies_s6[int(len(latencies_s6) * 0.95)], 2),
            latency_p99_ms=round(latencies_s6[int(len(latencies_s6) * 0.99)], 2),
            target_p95_ms=TARGET_COMMAND_P95_MS,
            target_met=(
                latencies_s6[int(len(latencies_s6) * 0.95)] < TARGET_COMMAND_P95_MS
                and failures_s6 == 0
            ),
        )
        results.append(res_s6)

    # Print Summary Table
    print("\n" + "=" * 115)
    print(
        f"{'Scenario':<42} | {'Reqs':<6} | {'Throughput':<10} | {'P50 (ms)':<9} | {'P95 (ms)':<9} | {'Target':<9} | {'Status'}"
    )
    print("-" * 115)
    for r in results:
        status_str = "MET [PASS]" if r.target_met else "MISSED [FAIL]"
        print(
            f"{r.scenario_name:<42} | {r.total_requests:<6} | {f'{r.throughput_rps} rps':<10} | "
            f"{r.latency_p50_ms:<9.2f} | {r.latency_p95_ms:<9.2f} | {f'< {r.target_p95_ms}ms':<9} | {status_str}"
        )
    print("=" * 115 + "\n")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Arad Platform Performance Benchmarking Tool")
    parser.add_argument(
        "--profile", choices=["smoke", "baseline", "stress", "spike"], default="baseline"
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Base HTTP URL (if testing running server, e.g. http://localhost:8000)",
    )
    parser.add_argument("--db-url", default=None, help="PostgreSQL database URL")

    args = parser.parse_args()
    results = asyncio.run(
        execute_benchmarks(
            base_url=args.url,
            profile=args.profile,
            database_url=args.db_url,
        )
    )
    all_passed = all(r.target_met for r in results)
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
