"""Fulfillment unit of work port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from fulfillment.ports.repositories import FulfillmentRepository
from inventory.ports.repositories import WarehouseRepository
from ordering.ports.repositories import OrderRepository
from shared.application.use_case import ApplicationUnitOfWork


@runtime_checkable
class FulfillmentUnitOfWork(ApplicationUnitOfWork, Protocol):
    """Transaction boundary for the fulfillment bounded context."""

    fulfillments: FulfillmentRepository
    orders: OrderRepository
    warehouses: WarehouseRepository


__all__ = ["FulfillmentUnitOfWork"]
