# Bounded Contexts

The Warehouse platform is organized into discrete bounded contexts following Domain-Driven Design (DDD). Each context maintains its own domain model, language, aggregates, and persistence mappings.

---

## 1. Context Matrix

| Bounded Context | Implementation Status | Core Aggregates & Entities | Primary Responsibilities |
|---|---|---|---|
| **Identity & Access** | **Implemented** | `User`, `Role`, `Permission`, `AccessPolicy`, `Tenant`, `TenantMembership`, `ServiceAccount` | Authentication (Argon2id, JWT), RBAC, dynamic ABAC policies, session management, warehouse operator scoping. |
| **Catalog** | **Implemented** | `Product`, `Sku`, `AttributeSet` | Products, SKUs, searchable attributes, multi-channel pricing, product lifecycle (`DRAFT`, `ACTIVE`, `ARCHIVED`). |
| **Inventory** | **Implemented** | `InventoryItem`, `Warehouse`, `Reservation`, `StockMovement` | Warehouse inventory levels, atomic reservation, availability calculations, stock movements, reservation expiry. |
| **Ordering** | **Implemented** | `Order`, `OrderLine`, `PricingSnapshot` | Order lifecycle transitions, line items, pricing snapshots, reservation coverage verification. |
| **Fulfillment** | **Implemented** | `Fulfillment`, `FulfillmentTask`, `Carrier` | Allocation to warehouses, carrier dispatch, retry tracking, failure handling, and compensation workflows. |
| **Search & Reporting** | **Implemented** | `ProductSearchDocument`, `OrderSearchDocument`, `FulfillmentExceptionDocument`, `AuditSearchDocument` | Disposable Elasticsearch projections for Products, Orders, Exceptions, and Audit logs; atomic alias reindexing; reconciliation. |
| **Audit** | **Implemented** | `AuditLog` | Append-only audit log recording actor context, policy decisions, reasons, and correlation identifiers. |

---

## 2. Shared Kernel (`src/shared/`)

The shared kernel contains cross-cutting abstractions and primitives shared by all contexts:
- `shared.domain`: `AggregateRoot`, strongly typed IDs (`TenantId`, `OrderId`, `SkuId`, `FulfillmentId`, `WarehouseId`), `Money`, `Quantity`, `Clock`, `DomainEvent`.
- `shared.application`: `UseCase`, `Command`, `Query`, `RequestContext`, `IdempotencyStore`, `EventDispatcher`, `Pagination`.
- `shared.infrastructure`: Database connection pooling, `SqlAlchemyUnitOfWork`, outbox/inbox persistence, Redis caching adapter, RabbitMQ topology.

---

## 3. Inter-Context Communication Rules

1. **In-Process Published Ports:** Contexts interact locally via typed interface protocols (e.g. `InventoryReservationPort`) without direct coupling to foreign domain internals.
2. **Domain Integration Events:** Asynchronous cross-context reactions occur via domain events published to RabbitMQ through the Transactional Outbox.
3. **No Foreign Table Joins:** A context's repository never joins directly to another context's database tables.
