"""Published catalog domain events for cross-context subscribers."""

from __future__ import annotations

from catalog.domain.events import (
    ProductActivated,
    ProductArchived,
    ProductCreated,
    ProductRevised,
    SkuActivated,
    SkuCreated,
    SkuDeactivated,
    SkuPriceChanged,
)

__all__ = [
    "ProductActivated",
    "ProductArchived",
    "ProductCreated",
    "ProductRevised",
    "SkuActivated",
    "SkuCreated",
    "SkuDeactivated",
    "SkuPriceChanged",
]
