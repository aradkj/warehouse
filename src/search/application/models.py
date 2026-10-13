"""Search read models, constants, and index schemas for Phase 3.

Search documents are denormalized read models projected from domain events.
They are NOT domain entities and must never be treated as the system of record.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from search.ports.indexer import SearchDocument

PRODUCTS_INDEX_SCHEMA_VERSION: int = 1
PRODUCTS_INDEX_V1: str = "products-v1"
PRODUCTS_INDEX_ALIAS: str = "products"

PRODUCTS_INDEX_MAPPINGS: dict[str, object] = {
    "_meta": {
        "schema_version": PRODUCTS_INDEX_SCHEMA_VERSION,
    },
    "properties": {
        "id": {"type": "keyword"},
        "tenant_id": {"type": "keyword"},
        "slug": {"type": "keyword"},
        "name": {
            "type": "text",
            "fields": {
                "keyword": {"type": "keyword", "ignore_above": 256},
            },
        },
        "description": {"type": "text"},
        "status": {"type": "keyword"},
        "attributes": {"type": "object", "dynamic": True},
        "skus": {
            "type": "nested",
            "properties": {
                "sku_id": {"type": "keyword"},
                "sku_code": {"type": "keyword"},
                "unit_price_minor": {"type": "integer"},
                "currency": {"type": "keyword"},
                "status": {"type": "keyword"},
            },
        },
        "created_at": {"type": "date"},
        "updated_at": {"type": "date"},
        "version": {"type": "integer"},
    },
}


def build_product_document_id(
    tenant_id: str | uuid.UUID,
    product_id: str | uuid.UUID,
) -> str:
    """Build a deterministic, tenant-isolated document ID for Elasticsearch.

    Encodes both tenant_id and product_id to guarantee that identical product IDs
    across different tenants never overwrite or collide in the shared search index.
    """
    return f"{tenant_id}:{product_id}"


@dataclass(slots=True)
class ProductSearchDocument:
    """Denormalized read model representing a searchable product."""

    id: str
    tenant_id: str
    slug: str
    name: str
    description: str | None = None
    status: str = "DRAFT"
    attributes: dict[str, Any] = field(default_factory=dict)
    skus: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    version: int = 1

    def to_search_document(self, alias: str = PRODUCTS_INDEX_ALIAS) -> SearchDocument:
        """Render into the hexagonal SearchDocument port value object."""
        body: dict[str, object] = {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "attributes": self.attributes,
            "skus": self.skus,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
        }
        return SearchDocument(
            index_alias=alias,
            document_id=build_product_document_id(self.tenant_id, self.id),
            tenant_id=self.tenant_id,
            version=self.version,
            body=body,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProductSearchDocument:
        """Construct from Elasticsearch _source dictionary."""
        return cls(
            id=str(data.get("id", "")),
            tenant_id=str(data.get("tenant_id", "")),
            slug=str(data.get("slug", "")),
            name=str(data.get("name", "")),
            description=data.get("description"),
            status=str(data.get("status", "DRAFT")),
            attributes=dict(data.get("attributes", {})),
            skus=list(data.get("skus", [])),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            version=int(data.get("version", 1)),
        )


__all__ = [
    "PRODUCTS_INDEX_ALIAS",
    "PRODUCTS_INDEX_MAPPINGS",
    "PRODUCTS_INDEX_SCHEMA_VERSION",
    "PRODUCTS_INDEX_V1",
    "ProductSearchDocument",
    "build_product_document_id",
]
