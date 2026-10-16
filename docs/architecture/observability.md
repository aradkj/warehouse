# Architecture Specification: Platform Observability & Telemetry

## 1. Overview and Principles

Phase 5 introduces complete operational observability for the Arad Distributed Order and Inventory Orchestration Platform across three pillars:
1. **Structured JSON Logs** with unified correlation IDs and W3C distributed traceparent propagation.
2. **Prometheus Metrics** tracking request rates, errors, P50/P95/P99 latency histograms, PostgreSQL connection pool saturation, transactional outbox backlog, and Redis cache efficiency.
3. **Health & Readiness Semantics** isolating process liveness (`/healthz`) from system readiness (`/readyz`), ensuring zero traffic is routed to unmigrated or partitioned instances.

---

## 2. Distributed Tracing & W3C TraceContext

The platform implements standard **W3C TraceContext** (`traceparent` header, format: `00-{trace_id}-{span_id}-{trace_flags}`).

### Trace Flow
```text
HTTP Request (traceparent)
       │
       ▼
FastAPI CorrelationIdMiddleware
  ├─ Extracts/generates trace_id and span_id
  ├─ Sets request.state.correlation_id & request.state.traceparent
  └─ Sets Python ContextVar for logger enrichment
       │
       ▼
Application Use Case (PostgreSQL Transaction)
  └─ Outbox Event row captures correlation_id & trace_metadata
       │
       ▼
Outbox Relay Worker
  └─ Passes traceparent & correlation_id in RabbitMQ message headers
       │
       ▼
Asynchronous Consumer (Celery / RabbitMQ Worker)
  └─ Reads message headers, sets ContextVar, preserves end-to-end trace
       │
       ▼
Elasticsearch Search Projection
  └─ Ingests document with correlation_id
```

### Trace Headers
- `X-Correlation-Id`: Client-visible correlation tracking (max 128 chars).
- `traceparent`: W3C distributed trace parent identifier.

---

## 3. Prometheus Metrics & Exposition

Metrics are exported on `GET /metrics` in Prometheus text exposition format (0.0.4).

### Exposed Metrics

| Metric Name | Type | Labels | Description |
| :--- | :--- | :--- | :--- |
| `http_requests_total` | Counter | `method`, `path`, `status` | Total HTTP requests handled. Path is normalized to route template to avoid cardinality explosion. |
| `http_request_duration_seconds` | Histogram | `method`, `path`, `le` | Request duration histogram with standard buckets (0.005s to 10.0s). |
| `database_pool_size` | Gauge | None | Configured maximum size of the SQLAlchemy async connection pool. |
| `database_connections_in_use` | Gauge | None | Current number of checked-out PostgreSQL connections. |
| `outbox_pending_events` | Gauge | None | Total count of outbox events awaiting publication in PostgreSQL. |
| `outbox_published_events_total` | Counter | None | Total count of events successfully acknowledged by RabbitMQ broker. |
| `outbox_failed_events_total` | Counter | `reason` | Total outbox publication retry attempts and dead-letter movements. |
| `cache_operations_total` | Counter | `operation`, `status` | Operations (`get`, `set`, `expire`, `lock`) and status (`hit`, `miss`, `negative`, `failure`). |
| `authorization_evaluations_total` | Counter | `action`, `decision` | ABAC and RBAC policy evaluations (`allow`, `deny`). |

### Path Normalization (Cardinality Protection)
Dynamic identifiers (e.g., UUIDs `/v1/orders/3fa85f64-5717-4562-b3fc-2c963f66afa6`) are replaced by template tokens (`/v1/orders/{order_id}`) via `normalize_metric_path()` before being used as Prometheus metric labels.

---

## 4. Health and Readiness Probes

### Liveness Probe (`/healthz`)
- **Purpose**: Indicates whether the FastAPI process is alive and responsive.
- **Dependencies Checked**: None. Deliberately does not touch PostgreSQL or Redis. An upstream database outage must not cause orchestrators to crash-loop healthy API containers.
- **Success Response**: HTTP 200 `{"status": "ok", "environment": "...", "checks": {}}`.

### Readiness Probe (`/readyz`)
- **Purpose**: Indicates whether the instance is ready to receive business traffic.
- **Dependencies Checked**:
  1. **PostgreSQL Ping**: Executes `SELECT 1` against the system of record.
  2. **Database Migrations Check**: Executes `SELECT version_num FROM alembic_version`. If the table does not exist or has no revision recorded, returns HTTP 503 `checks.migrations = "unmigrated"`.
  3. **Redis Ping**: Probes Redis cache. If Redis is unreachable, status is logged and `checks.cache = "unreachable"`, but readiness does not fail because the application supports graceful cache-aside degradation to PostgreSQL.
- **Failure Response**: HTTP 503 Service Unavailable `{"status": "degraded", ...}`.

---

## 5. Alerts & Runbook Integration

Prometheus alerting rules (`docker/prometheus/alerts.yml`) are configured with actionable severities and remediation guidance:
- `HighHttpErrorRate`: Triggers if HTTP 5xx > 1% over 5m.
- `HighHttpLatencyP95`: Triggers if P95 latency > 500ms over 3m.
- `DatabasePoolSaturation`: Triggers if pool checked-out connections > 80% over 2m.
- `OutboxEventsBacklog`: Triggers if pending outbox events > 500 over 5m.
- `RedisCacheFailures`: Triggers on sustained cache errors.
- `ApiReadinessDegraded`: Triggers if API instance readiness fails for > 1m.
