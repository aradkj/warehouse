"""Catalog request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from bootstrap.schemas.common import MoneySchema, PageSchema
from catalog.application.commands import ProductView, SkuView
from catalog.domain.value_objects import ProductStatus, SkuStatus
from shared.domain.value_objects import Money

SlugField = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=3, max_length=80),
]
SkuCodeField = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")]
CurrencyField = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z]{3}$", min_length=3, max_length=3)
]


class CreateProductRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: SlugField
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    attributes: dict[str, Any] = Field(default_factory=dict)


class ReviseProductRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    attributes: dict[str, Any] | None = None


class CreateSkuRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: SkuCodeField
    name: str = Field(min_length=1, max_length=200)
    unit_price_minor: int = Field(ge=0, description="List price in minor units.")
    currency: CurrencyField
    attributes: dict[str, Any] = Field(default_factory=dict)


class ChangeSkuPriceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_price_minor: int = Field(ge=0)
    currency: CurrencyField


class SkuResponse(BaseModel):
    id: UUID
    product_id: UUID
    code: str
    name: str
    status: SkuStatus
    unit_price: MoneySchema
    attributes: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    version: int

    @classmethod
    def from_view(cls, view: SkuView) -> SkuResponse:
        return cls(
            id=view.id,
            product_id=view.product_id,
            code=view.code,
            name=view.name,
            status=view.status,
            unit_price=MoneySchema(amount_minor=view.unit_price_minor, currency=view.currency),
            attributes=dict(view.attributes),
            created_at=view.created_at,
            updated_at=view.updated_at,
            version=view.version,
        )


class ProductResponse(BaseModel):
    id: UUID
    slug: str
    name: str
    description: str | None
    status: ProductStatus
    attributes: dict[str, Any]
    skus: list[SkuResponse]
    created_at: datetime
    updated_at: datetime
    version: int

    @classmethod
    def from_view(cls, view: ProductView) -> ProductResponse:
        return cls(
            id=view.id,
            slug=view.slug,
            name=view.name,
            description=view.description,
            status=view.status,
            attributes=dict(view.attributes),
            skus=[SkuResponse.from_view(sku) for sku in view.skus],
            created_at=view.created_at,
            updated_at=view.updated_at,
            version=view.version,
        )


class ProductPageResponse(PageSchema[ProductResponse]):
    pass


def money_schema(money: Money) -> MoneySchema:
    return MoneySchema(amount_minor=money.amount_minor, currency=money.currency)


__all__ = [
    "ChangeSkuPriceRequest",
    "CreateProductRequest",
    "CreateSkuRequest",
    "ProductPageResponse",
    "ProductResponse",
    "ReviseProductRequest",
    "SkuResponse",
    "money_schema",
]
