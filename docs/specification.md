# Project Specification: Distributed Order and Inventory Orchestration Platform

## 1. Project Summary

Build a production-grade, multi-tenant platform that coordinates orders, inventory reservations, fulfillment, and shipment tracking across multiple warehouses and sales channels.

The system must remain consistent under concurrent demand, tolerate partial failures, provide near-real-time search, enforce fine-grained access policies, and process long-running workflows asynchronously.

This is best implemented initially as a **modular monolith with independent workers**. Bounded contexts remain isolated through hexagonal architecture and domain events, allowing selected modules to become microservices later without rewriting the domain.

## 2. Business Problem

Retailers sell inventory through websites, marketplaces, and physical stores. Multiple channels may attempt to reserve the same stock concurrently. Warehouse updates can arrive late, messages can be delivered more than once, and external fulfillment systems may be temporarily unavailable.

The platform must:

- Prevent overselling.
- Reserve inventory atomically.
- Support partial fulfillment across warehouses.
- Process duplicate requests and events safely.
- Keep database state and published events consistent.
- Offer fast full-text and filtered search.
- Apply tenant-aware, attribute-based authorization.
- Maintain an auditable history of important decisions.
- Recover automatically from temporary infrastructure failures.

## 3. Primary Users

- Operations managers monitoring orders and inventory.
- Warehouse operators processing allocated fulfillment work.
- Customer-support agents investigating orders.
- Tenant administrators managing users, policies, and facilities.
- Auditors accessing immutable activity and authorization records.
- External sales channels submitting orders through APIs and events.

## 4. Suggested Technology Stack

- Python 3.12+
- FastAPI and Pydantic
- PostgreSQL with SQLAlchemy 2 and Alembic
- Redis for caching, distributed locks, rate limits, and reservation expiry metadata
- Elasticsearch for order, product, and audit search
- RabbitMQ for durable event delivery
- Celery for asynchronous and scheduled jobs
- Docker and Docker Compose
- pytest, Testcontainers, Hypothesis, and pytest-bdd or Behave
- OpenTelemetry, Prometheus, and structured JSON logging
- Optional Kubernetes deployment for the advanced phase

## 5. Bounded Contexts

### Identity and Access

Owns users, service accounts, roles, attributes, policies, tenant membership, authentication sessions, and authorization audit records.

### Catalog

Owns products, SKUs, searchable attributes, channel-specific metadata, and product lifecycle state.

### Inventory

Owns warehouses, stock levels, inventory movements, reservations, reservation expiry, and availability calculations.

### Ordering

Owns orders, order lines, pricing snapshots, order state transitions, and idempotency records.

### Fulfillment

Owns warehouse allocation, fulfillment tasks, split shipments, retryable external-provider operations, and compensating actions.

### Search and Reporting

Consumes domain events and builds denormalized Elasticsearch projections. It never becomes the authoritative source for transactional decisions.

### Audit

Records business actions, policy decisions, actor context, correlation identifiers, and before/after state where appropriate.

## 6. Core Business Workflows

### Order submission

1. A sales channel submits an order with a tenant-scoped idempotency key.
2. The Ordering context validates the request and creates a pending order.
3. The Inventory context attempts an atomic reservation.
4. Stock may be allocated across multiple warehouses.
5. The order is confirmed when the required inventory is secured.
6. Failure causes the order to be rejected or placed into a configurable back-order state.
7. Events are stored in the transactional outbox before the database transaction commits.
8. The outbox relay publishes those events to RabbitMQ.
9. Celery workers create fulfillment tasks and update Elasticsearch projections.

### Reservation expiration

1. Inventory is reserved for a configurable period.
2. Celery periodically identifies expired reservations.
3. The reservation is released using an idempotent domain operation.
4. An `InventoryReservationExpired` event is stored in the outbox.
5. Downstream projections and affected orders are updated asynchronously.

### Fulfillment failure and compensation

1. A worker submits a shipment request to an external provider.
2. Temporary failures are retried with exponential backoff and jitter.
3. Permanent failure triggers a compensating workflow.
4. Inventory is released or reallocated according to domain policy.
5. Every state transition is auditable.

## 7. Domain Model

Important aggregates include:

- `Order`
- `InventoryItem`
- `Reservation`
- `Fulfillment`
- `Warehouse`
- `AccessPolicy`

Example invariants:

- Available stock cannot fall below zero.
- A confirmed order must have valid reservations for all required lines.
- Released or expired reservations cannot be confirmed.
- An order cannot move backward through its lifecycle except through an explicit compensating transition.
- Commands with the same tenant and idempotency key cannot produce duplicate business operations.
- Cross-tenant references are always invalid.
- Domain objects never depend on FastAPI, SQLAlchemy, Redis, RabbitMQ, or Elasticsearch.

## 8. Hexagonal Architecture

Each bounded context should use the following layers:

```text
Inbound adapters
  FastAPI endpoints
  RabbitMQ consumers
  Celery tasks
         |
         v
Application layer
  Commands and queries
  Use-case orchestration
  Transaction boundaries
         |
         v
Domain layer
  Aggregates
  Value objects
  Domain services
  Business policies
  Domain events
         |
         v
Outbound ports
  Repositories
  Event publisher
  Cache
  Search index
  Policy evaluator
  External fulfillment provider
         |
         v
Outbound adapters
  PostgreSQL
  Redis
  Elasticsearch
  RabbitMQ
  External APIs
```

Dependency direction always points toward the domain. Infrastructure implementations satisfy interfaces defined by the application or domain layers.

## 9. Database Requirements

PostgreSQL is the system of record.

The implementation must demonstrate:

- Explicit transaction boundaries.
- Optimistic concurrency through version columns.
- Row locking where inventory contention requires it.
- Unique constraints supporting idempotency.
- Tenant-aware indexes and queries.
- Composite and partial indexes justified through execution plans.
- Cursor-based pagination.
- Alembic migrations with forward and rollback strategies.
- Soft deletion only where the business requires historical recovery.
- Time-based partitioning for high-volume audit and outbox tables.
- Archival and retention policies.
- Read replicas or replica-ready query separation as an advanced feature.
- Row-level security as defense in depth for tenant isolation.
- Documented backup, restoration, and zero-downtime migration procedures.

## 10. Transactional Outbox

Every domain event that must leave the service is written to an `outbox_events` table in the same transaction as the aggregate change.

An outbox relay must:

- Claim unpublished records safely across multiple workers.
- Publish messages to RabbitMQ.
- Mark records as published only after broker confirmation.
- Retry failures without losing records.
- Support dead-letter handling.
- Attach event ID, tenant ID, correlation ID, causation ID, schema version, and timestamp.
- Clean up or archive old records safely.

Delivery is **at least once**. Consumers must therefore use an inbox or processed-message table to provide idempotent behavior.

## 11. RabbitMQ and Celery

RabbitMQ provides durable messaging for:

- Domain integration events.
- Fulfillment workflow commands.
- Search projection updates.
- Notification requests.
- Dead-letter queues.
- Retry queues.

Celery handles:

- Reservation expiration.
- Outbox dispatching.
- Fulfillment-provider calls.
- Search reindexing.
- Scheduled reconciliation.
- Report generation.
- Recovery and replay operations.

Workers must define retry limits, backoff strategies, time limits, acknowledgement behavior, idempotency rules, and dead-letter handling. A failed task must not silently disappear.

## 12. Redis Caching

Redis may cache:

- Product summaries.
- Tenant configuration.
- Compiled ABAC policies.
- Frequently accessed order views.
- Short-lived availability estimates.
- Rate-limit counters.

Required cache behavior:

- Cache-aside strategy.
- Tenant-prefixed keys.
- Explicit TTLs.
- Versioned key formats.
- Invalidation triggered by committed domain events.
- Protection against cache stampedes.
- Negative caching where appropriate.
- Graceful degradation when Redis is unavailable.

Inventory correctness must never depend solely on cached values.

## 13. Elasticsearch

Elasticsearch provides denormalized search for:

- Orders by customer, SKU, status, warehouse, and date.
- Products using full-text and faceted search.
- Fulfillment exceptions.
- Audit records.

Requirements:

- Versioned index mappings.
- Aliases supporting zero-downtime reindexing.
- Bulk indexing workers.
- Eventual-consistency documentation.
- Replayable projection building.
- Dead-letter handling for malformed documents.
- Reconciliation between PostgreSQL and Elasticsearch.
- Tenant filters applied to every query.

Elasticsearch must not be used as the source of truth for order or inventory transitions.

## 14. ABAC Authorization

Authorization decisions should consider attributes rather than roles alone.

Example attributes:

- Subject: tenant, department, clearance level, assigned warehouses, employment status.
- Resource: tenant, warehouse, order value, sensitivity, owner, lifecycle state.
- Action: read, create, approve, cancel, allocate, export.
- Environment: request origin, authentication strength, business hours, risk score.

Example policies:

- A warehouse operator can update fulfillment tasks only for assigned warehouses.
- A support agent can view orders for their tenant but cannot see payment-sensitive fields.
- Orders above a configured value require an approver with sufficient clearance.
- Auditors receive read-only access and cannot perform operational actions.
- Service accounts can publish inventory updates only for explicitly assigned facilities.

The authorization engine must return both a decision and a reason. Decisions should be recorded with policy version, actor, resource, and correlation ID.

## 15. API Requirements

Representative endpoints:

```text
POST   /v1/orders
GET    /v1/orders/{order_id}
POST   /v1/orders/{order_id}/cancel
POST   /v1/orders/{order_id}/confirm
GET    /v1/orders/search

POST   /v1/inventory/adjustments
GET    /v1/inventory/availability
POST   /v1/reservations
DELETE /v1/reservations/{reservation_id}

GET    /v1/fulfillments/{fulfillment_id}
POST   /v1/fulfillments/{fulfillment_id}/retry

POST   /v1/access/policies
GET    /v1/audit/events
```

API behavior must include:

- Idempotency keys for mutation endpoints.
- Optimistic concurrency headers or version fields.
- Structured error responses.
- Correlation IDs.
- Cursor pagination.
- Tenant isolation.
- OpenAPI documentation.
- Request validation and size limits.
- Authentication and ABAC enforcement.

## 16. Testing Strategy

### Test-driven development

Business rules are implemented through a red-green-refactor cycle.

The test suite should contain:

- Fast unit tests for aggregates and value objects.
- Application-service tests using fake ports.
- Repository contract tests shared between fake and PostgreSQL implementations.
- Integration tests using real PostgreSQL, Redis, RabbitMQ, and Elasticsearch containers.
- API contract tests.
- Consumer and message-schema tests.
- Property-based tests for inventory invariants.
- Concurrency tests attempting to oversell stock.
- Failure-injection tests for broker, cache, search, and worker outages.
- End-to-end BDD scenarios.

### Test boundaries

Domain tests must not require infrastructure. Infrastructure tests must verify actual adapters rather than mocks. End-to-end tests should cover only critical business journeys.

## 17. BDD Acceptance Criteria

### Prevent overselling under concurrent demand

```gherkin
Feature: Concurrent inventory reservation

  Scenario: Two orders compete for the last unit
    Given tenant "northwind" has 1 available unit of SKU "CAMERA-1"
    When two customers concurrently request 1 unit of SKU "CAMERA-1"
    Then exactly one reservation should be confirmed
    And the other reservation should be rejected
    And available inventory should be 0
    And inventory should never become negative
```

### Idempotent order submission

```gherkin
Feature: Idempotent order creation

  Scenario: A client retries after losing the HTTP response
    Given no order exists for idempotency key "checkout-8472"
    When the client submits the same order twice with key "checkout-8472"
    Then only one order should exist
    And both responses should identify the same order
    And inventory should be reserved only once
```

### Reliable event publication

```gherkin
Feature: Transactional event delivery

  Scenario: RabbitMQ is unavailable after an order is committed
    Given an order is ready to be confirmed
    And RabbitMQ is unavailable
    When the order confirmation transaction commits
    Then the order should be confirmed in PostgreSQL
    And an unpublished event should exist in the outbox
    When RabbitMQ becomes available
    Then the event should eventually be published
    And consumers should apply it no more than once
```

### Attribute-based access control

```gherkin
Feature: Warehouse-scoped authorization

  Scenario: An operator tries to modify another warehouse
    Given operator "olivia" belongs to tenant "northwind"
    And operator "olivia" is assigned only to warehouse "LAX"
    And a fulfillment belongs to warehouse "SEA"
    When operator "olivia" attempts to update the fulfillment
    Then access should be denied
    And the decision reason should identify the warehouse policy
    And the denied attempt should be recorded in the audit log
```

### Eventually consistent search

```gherkin
Feature: Order search projection

  Scenario: A newly confirmed order is indexed
    Given order "ORD-1001" has just been confirmed
    When its integration event is processed
    Then the order should become searchable in Elasticsearch
    And its search document should contain the current status
    And reprocessing the event should not create a duplicate document
```

## 18. Non-Functional Requirements

The system should target:

- No lost committed domain events.
- No negative inventory under supported concurrency.
- P95 read latency below 250 ms under the agreed load profile.
- P95 transactional command latency below 500 ms, excluding asynchronous completion.
- Search projection delay below five seconds during normal operation.
- Horizontal scaling of API and worker processes.
- Recovery from process restarts without manual data repair.
- Complete tenant isolation.
- Structured logs, traces, metrics, and correlation across HTTP requests and messages.
- Graceful shutdown of APIs and workers.
- Defined recovery-point and recovery-time objectives.

## 19. Containerization

Docker images should use:

- Multi-stage builds.
- Non-root runtime users.
- Pinned dependencies.
- Health and readiness checks.
- Minimal base images.
- Separate API, worker, scheduler, and outbox-relay processes.
- Runtime configuration through environment variables or mounted secrets.

Docker Compose should start:

- FastAPI application
- PostgreSQL
- Redis
- RabbitMQ with management UI
- Elasticsearch
- Celery workers
- Celery scheduler
- Outbox relay
- Optional observability stack

The API container must not be considered ready until its essential dependencies and database migrations are valid.

## 20. Observability and Operations

Required operational signals include:

- HTTP request rate, latency, and error count.
- Database pool saturation and slow-query metrics.
- Cache hit rate and Redis failures.
- RabbitMQ queue depth and oldest-message age.
- Celery task duration, retries, and failures.
- Outbox backlog and publication delay.
- Elasticsearch indexing delay and rejected documents.
- Authorization denials by policy.
- Distributed traces spanning API, database transaction, outbox relay, broker, and worker.

Operational runbooks should cover queue backlog, failed migrations, stuck outbox records, poison messages, cache outages, search reindexing, and inventory reconciliation.

## 21. Delivery Phases

### Phase 1: Domain foundation

Implement bounded contexts, aggregates, ports, unit tests, PostgreSQL repositories, migrations, and basic FastAPI endpoints.

### Phase 2: Reliable asynchronous processing

Introduce RabbitMQ, the transactional outbox, idempotent consumers, Celery workers, retries, dead-letter queues, and reservation expiry.

### Phase 3: Search and caching

Add Redis caching, invalidation, Elasticsearch projections, search endpoints, reindexing, and reconciliation.

### Phase 4: Security and tenancy

Add authentication, ABAC policies, field-level filtering, tenant isolation, authorization auditing, and PostgreSQL row-level security.

### Phase 5: Production hardening

Add load testing, concurrency testing, observability, failure injection, container security, deployment manifests, backup procedures, and operational runbooks.

## 22. Senior-Level Deliverables

A complete submission should include:

- Architecture decision records.
- Context and container diagrams.
- Domain model and bounded-context map.
- OpenAPI specification.
- Versioned event schemas.
- Database schema and migration strategy.
- ABAC policy model.
- Threat model.
- Docker-based local environment.
- Automated TDD and BDD test suites.
- Load-test and failure-test results.
- Monitoring dashboards and alerts.
- Operational runbooks.
- A technical explanation of consistency, idempotency, retry, and failure-recovery guarantees.

## 23. Evaluation Criteria

The project should be judged primarily on whether it:

1. Protects domain invariants under concurrency.
2. Separates business logic from infrastructure.
3. Handles duplicate delivery and partial failure correctly.
4. Keeps database mutations and event publication reliable.
5. Applies authorization consistently across HTTP and message-driven entry points.
6. Demonstrates meaningful database design and query analysis.
7. Treats cache and search data as disposable projections.
8. Provides useful automated tests rather than maximizing test count.
9. Is observable and operationally recoverable.
10. Documents important engineering tradeoffs and rejected alternatives.

## 24. Recommended Repository Structure

```text
src/
  shared/
    domain/
    application/
    infrastructure/
  identity/
    domain/
    application/
    ports/
    adapters/
  catalog/
    domain/
    application/
    ports/
    adapters/
  inventory/
    domain/
    application/
    ports/
    adapters/
  ordering/
    domain/
    application/
    ports/
    adapters/
  fulfillment/
    domain/
    application/
    ports/
    adapters/
  search/
  audit/
  bootstrap/
    api.py
    workers.py
    dependencies.py

tests/
  unit/
  application/
  contract/
  integration/
  acceptance/
  performance/

migrations/
docker/
docs/
  architecture/
  decisions/
  runbooks/
```

## 25. Definition of Done

The project is complete when a reviewer can run the containerized environment, execute the automated test suite, submit competing orders, observe correct inventory behavior, interrupt RabbitMQ or Elasticsearch, restore those services, and verify that the platform recovers without losing committed work or violating tenant access policies.