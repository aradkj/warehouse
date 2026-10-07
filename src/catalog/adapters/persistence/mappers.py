"""Row <-> aggregate mapping for the catalog context.

Mapping is explicit and lives in the adapter: the domain never sees a database
row, and a column rename cannot silently change business behaviour.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.engine import RowMapping

from catalog.domain.product import Product
from catalog.domain.sku import Sku
from catalog.domain.value_objects import (
    ProductSlug,
    ProductStatus,
    SkuCode,
    SkuStatus,
    normalise_attribute_input,
)
from shared.domain.identifiers import ProductId, SkuId, TenantId
from shared.domain.value_objects import Money


def product_insert_values(product: Product) -> dict[str, Any]:
    return {
        "id": product.id,
        "tenant_id": product.tenant_id,
        "slug": product.slug.value,
        "name": product.name,
        "description": product.description,
        "attributes": product.attributes.as_dict(),
        "status": product.status.value,
        "created_at": product.created_at,
        "updated_at": product.updated_at,
        "version": product.version,
    }


def product_update_values(product: Product) -> dict[str, Any]:
    return {
        "name": product.name,
        "description": product.description,
        "attributes": product.attributes.as_dict(),
        "status": product.status.value,
        "updated_at": product.updated_at,
    }


def product_to_domain(row: RowMapping) -> Product:
    return Product(
        id=ProductId(row["id"]),
        tenant_id=TenantId(row["tenant_id"]),
        slug=ProductSlug(row["slug"]),
        name=row["name"],
        description=row["description"],
        attributes=normalise_attribute_input(row["attributes"] or {}),
        status=ProductStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


def sku_insert_values(sku: Sku) -> dict[str, Any]:
    return {
        "id": sku.id,
        "tenant_id": sku.tenant_id,
        "product_id": sku.product_id,
        "code": sku.code.value,
        "name": sku.name,
        "attributes": sku.attributes.as_dict(),
        "unit_price_minor": sku.unit_price.amount_minor,
        "currency": sku.unit_price.currency,
        "status": sku.status.value,
        "created_at": sku.created_at,
        "updated_at": sku.updated_at,
        "version": sku.version,
    }


def sku_update_values(sku: Sku) -> dict[str, Any]:
    return {
        "name": sku.name,
        "attributes": sku.attributes.as_dict(),
        "unit_price_minor": sku.unit_price.amount_minor,
        "currency": sku.unit_price.currency,
        "status": sku.status.value,
        "updated_at": sku.updated_at,
    }


def sku_to_domain(row: RowMapping) -> Sku:
    return Sku(
        id=SkuId(row["id"]),
        tenant_id=TenantId(row["tenant_id"]),
        product_id=ProductId(row["product_id"]),
        code=SkuCode(row["code"]),
        name=row["name"],
        attributes=normalise_attribute_input(row["attributes"] or {}),
        unit_price=Money(row["unit_price_minor"], row["currency"]),
        status=SkuStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


__all__ = [
    "product_insert_values",
    "product_to_domain",
    "product_update_values",
    "sku_insert_values",
    "sku_to_domain",
    "sku_update_values",
]
