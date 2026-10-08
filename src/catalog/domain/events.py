"""Catalog domain events."""

from __future__ import annotations

from dataclasses import dataclass

from shared.domain.events import TenantDomainEvent
from shared.domain.identifiers import ProductId, SkuId


@dataclass(frozen=True, kw_only=True, slots=True)
class ProductCreated(TenantDomainEvent):
    product_id: ProductId
    slug: str
    name: str


@dataclass(frozen=True, kw_only=True, slots=True)
class ProductRevised(TenantDomainEvent):
    product_id: ProductId
    slug: str
    name: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class ProductActivated(TenantDomainEvent):
    product_id: ProductId


@dataclass(frozen=True, kw_only=True, slots=True)
class ProductArchived(TenantDomainEvent):
    product_id: ProductId


@dataclass(frozen=True, kw_only=True, slots=True)
class SkuCreated(TenantDomainEvent):
    product_id: ProductId
    sku_id: SkuId
    sku_code: str
    unit_price_minor: int
    currency: str


@dataclass(frozen=True, kw_only=True, slots=True)
class SkuPriceChanged(TenantDomainEvent):
    sku_id: SkuId
    previous_price_minor: int
    new_price_minor: int
    currency: str
    product_id: ProductId | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class SkuDeactivated(TenantDomainEvent):
    sku_id: SkuId
    product_id: ProductId | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class SkuActivated(TenantDomainEvent):
    sku_id: SkuId
    product_id: ProductId | None = None


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
