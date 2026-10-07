"""Catalog domain errors."""

from __future__ import annotations

from typing import Any

from shared.domain.errors import DomainError


class InvalidProduct(DomainError):
    """A product could not be constructed or revised as requested."""

    code = "invalid_product"


class InvalidProductTransition(DomainError):
    """The requested product lifecycle transition is not allowed."""

    code = "invalid_product_transition"

    def __init__(self, current: str, requested: str) -> None:
        super().__init__(
            f"a product in state {current} cannot move to {requested}",
            details={"from": current, "to": requested},
        )
        self.current = current
        self.requested = requested


class InvalidSkuTransition(DomainError):
    """The requested SKU lifecycle transition is not allowed."""

    code = "invalid_sku_transition"

    def __init__(self, current: str, requested: str) -> None:
        super().__init__(
            f"a sku in state {current} cannot move to {requested}",
            details={"from": current, "to": requested},
        )
        self.current = current
        self.requested = requested


class ProductNotPurchasable(DomainError):
    """The product/SKU pair is not sellable (draft, archived or inactive)."""

    code = "product_not_purchasable"

    def __init__(self, sku_id: Any, *, reason: str) -> None:
        super().__init__(
            "the requested sku is not purchasable",
            details={"sku_id": str(sku_id), "reason": reason},
        )


__all__ = [
    "InvalidProduct",
    "InvalidProductTransition",
    "InvalidSkuTransition",
    "ProductNotPurchasable",
]
