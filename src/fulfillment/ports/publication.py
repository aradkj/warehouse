"""Fulfilment's published contract - **placeholder for Phase 2**.

Phase 1 implements no fulfilment: allocation of warehouse work, split shipments,
external provider calls, retries and compensations all belong to Phase 2. This
module exists so the boundary is named and so the architecture test can prove
that nothing in Phase 1 accidentally depends on an unimplemented context.

The planned contract (documented, not implemented):

* ``POST /v1/fulfillments/{id}/retry`` and ``GET /v1/fulfillments/{id}``,
* consumption of ``OrderConfirmed`` and ``ReservationExpired`` integration
  events from the transactional outbox,
* a ``FulfillmentProvider`` outbound port for external carriers.
"""

from __future__ import annotations

#: Phase 2 will define the ``FulfillmentOperations`` protocol here.
PLANNED_PHASE = 2

__all__ = ["PLANNED_PHASE"]
