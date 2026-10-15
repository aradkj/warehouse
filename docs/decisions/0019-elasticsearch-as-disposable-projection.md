# 19. Elasticsearch as a Disposable Projection

## Context
Full-text search, faceted filtering, and multi-aggregate reporting across Products, Orders, Fulfillment Exceptions, and Audit logs require fast, denormalized read indices. However, using Elasticsearch as a transactional database introduces consistency and split-brain hazards.

## Decision
Elasticsearch is treated strictly as a **disposable projection**:
1. PostgreSQL is the sole authoritative system of record.
2. Projections are built asynchronously by consuming integration events.
3. Every document uses deterministic IDs (`{tenant_id}:{aggregate_id}`) and version tracking.
4. If Elasticsearch indexes are corrupted, deleted, or upgraded, the complete search cluster can be reconstructed from PostgreSQL using zero-downtime alias swaps without data loss.

## Alternatives Considered
- *Using Elasticsearch as the primary store for orders or products:* Rejected because Elasticsearch lacks ACID transactions, cross-document constraints, and row-level locking.
- *Relying only on PostgreSQL `tsvector` full-text search:* Rejected because cross-aggregate faceted filtering, fuzzy matching, and customer-facing catalog search require distributed full-text performance.

## Consequences
- Guarantees zero risk of financial or inventory data loss from search cluster outages.
- Requires eventual consistency management and background reconciliation jobs.
