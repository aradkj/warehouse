"""Catalog unit-of-work port.

The unit of work is the *composition root* of one transaction for this context:
it yields the repositories that must share a session, plus the idempotency
registry that must commit atomically with the business change.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from catalog.ports.repositories import ProductRepository, SkuRepository
from shared.application.use_case import ApplicationUnitOfWork


@runtime_checkable
class CatalogUnitOfWork(ApplicationUnitOfWork, Protocol):
    """Transactional view over the catalog persistence ports."""

    products: ProductRepository
    skus: SkuRepository


__all__ = ["CatalogUnitOfWork"]
