"""Ordering unit-of-work port.

Ordering composes three collaborators inside one transaction:

* its own ``orders`` repository,
* the catalog's *published* query port (prices and purchasability),
* the inventory's *published* operations port (holds, confirmations, releases).

All three share the same database session, so "create order + reserve stock +
confirm" is one atomic unit - and, importantly, the use cases stay free of any
knowledge about *how* the other contexts are implemented.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from catalog.ports.publication import CatalogQuery
from inventory.ports.publication import InventoryOperations
from ordering.ports.repositories import OrderRepository
from shared.application.use_case import ApplicationUnitOfWork


@runtime_checkable
class OrderingUnitOfWork(ApplicationUnitOfWork, Protocol):
    """Transactional view over the ordering context's collaborators."""

    orders: OrderRepository
    catalog: CatalogQuery
    inventory: InventoryOperations


__all__ = ["OrderingUnitOfWork"]
