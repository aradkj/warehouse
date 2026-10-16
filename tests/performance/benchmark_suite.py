"""Performance and load testing benchmark harness (Phase 5 Workstream A).

Executes reproducible workloads against the Arad Platform over HTTP:
1. Product & Catalog Reads
2. Tenant-scoped authenticated reads
3. Filtered full-text search queries
4. Order creation with unique Idempotency-Keys
5. Repeated order submission with identical Idempotency-Keys (replay)
6. Inventory availability reads
7. Mixed realistic read/write profile

Measures: Throughput (RPS), Error Rate, P50, P95, P99 latency in milliseconds.
Verifies specification targets:
- P95 read latency < 250 ms
- P95 transactional command latency < 500 ms
"""

from __future__ import annotations

import asyncio
import dataclasses
import statistics
import time
from typing import Any

import httpx

TARGET_READ_P95_MS = 250.0
TARGET_COMMAND_P95_MS = 500.0


@dataclasses.dataclass
class BenchmarkResult:
    scenario_name: str
    total_requests: int
    successful_requests: int
    failed_requests: int
    duration_seconds: float
    throughput_rps: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    target_p95_ms: float
    target_met: bool


class BenchmarkRunner:
    """Async load testing engine."""

    def __init__(self, base_url: str = "http://localhost:8000") -> None:
        self.base_url = base_url.rstrip("/")

    async def _timed_request(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> tuple[float, bool]:
        url = f"{self.base_url}{path}"
        start = time.perf_counter()
        try:
            resp = await client.request(method, url, headers=headers, json=json)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            is_ok = 200 <= resp.status_code < 400
            return elapsed_ms, is_ok
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return elapsed_ms, False

    async def run_scenario(
        self,
        name: str,
        task_func: Any,
        concurrency: int = 10,
        total_requests: int = 100,
        target_p95_ms: float = TARGET_READ_P95_MS,
    ) -> BenchmarkResult:
        latencies: list[float] = []
        successes = 0
        failures = 0

        semaphore = asyncio.Semaphore(concurrency)
        started_at = time.perf_counter()

        async with httpx.AsyncClient(timeout=30.0) as client:

            async def worker() -> None:
                nonlocal successes, failures
                async with semaphore:
                    lat, ok = await task_func(client)
                    latencies.append(lat)
                    if ok:
                        successes += 1
                    else:
                        failures += 1

            tasks = [asyncio.create_task(worker()) for _ in range(total_requests)]
            await asyncio.gather(*tasks)

        duration = time.perf_counter() - started_at
        throughput = total_requests / duration if duration > 0 else 0.0

        latencies.sort()
        p50 = statistics.median(latencies) if latencies else 0.0
        p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0.0
        p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0.0

        return BenchmarkResult(
            scenario_name=name,
            total_requests=total_requests,
            successful_requests=successes,
            failed_requests=failures,
            duration_seconds=round(duration, 3),
            throughput_rps=round(throughput, 1),
            latency_p50_ms=round(p50, 2),
            latency_p95_ms=round(p95, 2),
            latency_p99_ms=round(p99, 2),
            target_p95_ms=target_p95_ms,
            target_met=(p95 <= target_p95_ms and failures == 0),
        )
