"""Fulfillment application commands and queries."""

from __future__ import annotations

from dataclasses import dataclass

from shared.application.context import RequestContext
from shared.domain.identifiers import (
    FulfillmentId,
    OrderId,
    SkuId,
    TenantId,
    WarehouseId,
)
from shared.domain.value_objects import Quantity


@dataclass(frozen=True, slots=True)
class TaskRequirement:
    """Request line for a fulfillment task."""

    sku_id: SkuId
    quantity: Quantity


@dataclass(frozen=True, slots=True)
class RequestFulfillmentCommand:
    """Command to initiate warehouse fulfillment for an order."""

    tenant_id: TenantId
    context: RequestContext
    order_id: OrderId
    warehouse_id: WarehouseId
    requirements: tuple[TaskRequirement, ...]
    max_retries: int = 3


@dataclass(frozen=True, slots=True)
class RetryFulfillmentCommand:
    """Command to retry an eligible failed fulfillment."""

    tenant_id: TenantId
    context: RequestContext
    fulfillment_id: FulfillmentId


@dataclass(frozen=True, slots=True)
class CompensateFulfillmentCommand:
    """Command to trigger compensation for a permanently failed fulfillment."""

    tenant_id: TenantId
    context: RequestContext
    fulfillment_id: FulfillmentId
    reason: str


@dataclass(frozen=True, slots=True)
class GetFulfillmentQuery:
    """Query to fetch a fulfillment by ID."""

    tenant_id: TenantId
    context: RequestContext
    fulfillment_id: FulfillmentId


__all__ = [
    "CompensateFulfillmentCommand",
    "GetFulfillmentQuery",
    "RequestFulfillmentCommand",
    "RetryFulfillmentCommand",
    "TaskRequirement",
]
