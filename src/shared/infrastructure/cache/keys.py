"""Centralized cache key builder with mandatory tenant scoping and versioning.

Format standard:
    v{version}:{tenant_id}:{resource}:{identifier}[:{qualifier}...]

Guarantees:
* Multi-tenant isolation: identical resource IDs belonging to different tenants
  produce distinct, non-overlapping keys.
* Versioning: changes to serialization schemas or projection models can increment
  the version prefix to invalidate stale caches without flush operations.
* Collision resistance: resource types and identifiers are strictly delimited.
"""

from __future__ import annotations

import re
import uuid
from typing import Final

DEFAULT_VERSION: Final[int] = 1
_INVALID_SEGMENT_CHARS = re.compile(r"[\s:]")


def _sanitize_segment(segment: str, name: str) -> str:
    """Validate and sanitize a key segment to prevent delimiter injection."""
    cleaned = segment.strip()
    if not cleaned:
        raise ValueError(f"Cache key {name} cannot be empty")
    if _INVALID_SEGMENT_CHARS.search(cleaned):
        raise ValueError(
            f"Cache key {name} '{cleaned}' contains illegal characters (colons or whitespace)"
        )
    return cleaned


def _coerce_id(val: uuid.UUID | str, name: str) -> str:
    """Normalize UUID or string identifier."""
    if isinstance(val, uuid.UUID):
        return str(val)
    return _sanitize_segment(str(val), name)


class CacheKeyBuilder:
    """Centralized factory for deterministic, tenant-isolated cache keys."""

    def __init__(self, version: int = DEFAULT_VERSION) -> None:
        if version < 1:
            raise ValueError("Cache key version must be a positive integer")
        self._version = version
        self._prefix = f"v{self._version}"

    @property
    def version(self) -> int:
        return self._version

    def resource(
        self,
        tenant_id: uuid.UUID | str,
        resource_type: str,
        identifier: uuid.UUID | str,
        *qualifiers: str,
    ) -> str:
        """Construct a versioned, tenant-isolated key for an arbitrary resource."""
        tenant_str = _coerce_id(tenant_id, "tenant_id")
        res_str = _sanitize_segment(resource_type.lower(), "resource_type")
        id_str = _coerce_id(identifier, "identifier")

        segments = [self._prefix, tenant_str, res_str, id_str]
        for idx, qual in enumerate(qualifiers):
            segments.append(_sanitize_segment(qual, f"qualifier_{idx}"))

        return ":".join(segments)

    def product(self, tenant_id: uuid.UUID | str, product_id: uuid.UUID | str) -> str:
        """Build cache key for a Catalog Product."""
        return self.resource(tenant_id, "product", product_id)

    def sku(self, tenant_id: uuid.UUID | str, sku_id: uuid.UUID | str) -> str:
        """Build cache key for a Catalog SKU."""
        return self.resource(tenant_id, "sku", sku_id)

    def order(self, tenant_id: uuid.UUID | str, order_id: uuid.UUID | str) -> str:
        """Build cache key for an Order."""
        return self.resource(tenant_id, "order", order_id)

    def inventory(
        self,
        tenant_id: uuid.UUID | str,
        sku_id: uuid.UUID | str,
        warehouse_id: uuid.UUID | str | None = None,
    ) -> str:
        """Build cache key for Inventory stock level."""
        if warehouse_id is not None:
            return self.resource(
                tenant_id, "inventory", sku_id, _coerce_id(warehouse_id, "warehouse_id")
            )
        return self.resource(tenant_id, "inventory", sku_id)

    def search_version(self, tenant_id: uuid.UUID | str) -> str:
        """Build cache key tracking the generation/version of a tenant's search index."""
        tenant_str = _coerce_id(tenant_id, "tenant_id")
        return f"{self._prefix}:{tenant_str}:search:_version"

    def search(
        self,
        tenant_id: uuid.UUID | str,
        resource_type: str,
        query_hash: str,
        version: int | None = None,
    ) -> str:
        """Build cache key for a tenant-scoped search query.

        Format: v{version}:{tenant_id}:search:{resource_type}:v{gen}:{query_hash}
        """
        tenant_str = _coerce_id(tenant_id, "tenant_id")
        res_str = _sanitize_segment(resource_type.lower(), "resource_type")
        hash_str = _sanitize_segment(query_hash, "query_hash")
        if version is not None:
            return f"{self._prefix}:{tenant_str}:search:{res_str}:v{version}:{hash_str}"
        return f"{self._prefix}:{tenant_str}:search:{res_str}:{hash_str}"

    def lock(self, key: str) -> str:
        """Build distributed lock key corresponding to a cache key."""
        return f"lock:{key}"


# Process-wide default builder
default_key_builder = CacheKeyBuilder(DEFAULT_VERSION)

__all__ = [
    "DEFAULT_VERSION",
    "CacheKeyBuilder",
    "default_key_builder",
]
