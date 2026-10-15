# 21. Fulfillment Bounded Context and Compensation Workflow

## Context
Once an order is confirmed, physical fulfillment requires allocating tasks to specific warehouses, dispatching shipment requests to external shipping carriers, tracking tracking numbers, handling carrier downtime, and compensating inventory when shipments permanently fail.

## Decision
We implement a dedicated `Fulfillment` bounded context:
1. `Fulfillment` is modeled as an aggregate root managing `FulfillmentTask` entities, carrier selection, tracking numbers, and retry counters.
2. Inbound requests execute through use cases: `RequestFulfillment`, `RetryFulfillment`, `CompensateFulfillment`, and `GetFulfillment`.
3. Transient carrier errors are tracked up to `max_retries`.
4. Permanent carrier failures or retry exhaustion transition the aggregate to `FAILED` and emit `FulfillmentFailed`.
5. Compensating transactions (`CompensateFulfillment`) transition the aggregate to `COMPENSATED`, emit `FulfillmentCompensated`, and trigger inventory release.

## Alternatives Considered
- *Inlining fulfillment directly into Ordering:* Rejected because fulfillment involves warehouse-specific operational constraints, external shipping APIs, and retry mechanics that dilute the ordering domain model.
- *Infrastructure-only compensation:* Rejected because compensation must be auditable, reflect domain policy, and enforce aggregate invariants.

## Consequences
- Clean separation of physical fulfillment and shipping concerns from logical order lifecycle.
- Full auditability and compensation safety.
