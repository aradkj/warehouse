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

# ============================================================================
# Product Catalog Search Read Model
# ============================================================================
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
    """Build a deterministic, tenant-isolated document ID for product search."""
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


# ============================================================================
# Order Search Read Model
# ============================================================================
ORDERS_INDEX_SCHEMA_VERSION: int = 1
ORDERS_INDEX_V1: str = "orders-v1"
ORDERS_INDEX_ALIAS: str = "orders"

ORDERS_INDEX_MAPPINGS: dict[str, object] = {
    "_meta": {
        "schema_version": ORDERS_INDEX_SCHEMA_VERSION,
    },
    "properties": {
        "id": {"type": "keyword"},
        "tenant_id": {"type": "keyword"},
        "order_id": {"type": "keyword"},
        "customer_id": {"type": "keyword"},
        "customer_reference": {
            "type": "text",
            "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
        },
        "status": {"type": "keyword"},
        "currency": {"type": "keyword"},
        "total_minor": {"type": "integer"},
        "warehouse_ids": {"type": "keyword"},
        "skus": {
            "type": "nested",
            "properties": {
                "sku_id": {"type": "keyword"},
                "sku_code": {"type": "keyword"},
                "quantity": {"type": "integer"},
                "unit_price_minor": {"type": "integer"},
            },
        },
        "created_at": {"type": "date"},
        "updated_at": {"type": "date"},
        "version": {"type": "integer"},
    },
}


def build_order_document_id(
    tenant_id: str | uuid.UUID,
    order_id: str | uuid.UUID,
) -> str:
    """Build a deterministic, tenant-isolated document ID for order search."""
    return f"{tenant_id}:{order_id}"


@dataclass(slots=True)
class OrderSearchDocument:
    """Denormalized read model representing a searchable order."""

    id: str
    tenant_id: str
    status: str
    customer_reference: str | None = None
    customer_id: str | None = None
    currency: str = "GBP"
    total_minor: int = 0
    warehouse_ids: list[str] = field(default_factory=list)
    skus: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    version: int = 1

    def to_search_document(self, alias: str = ORDERS_INDEX_ALIAS) -> SearchDocument:
        body: dict[str, object] = {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "order_id": self.id,
            "customer_id": self.customer_id,
            "customer_reference": self.customer_reference,
            "status": self.status,
            "currency": self.currency,
            "total_minor": self.total_minor,
            "warehouse_ids": self.warehouse_ids,
            "skus": self.skus,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
        }
        return SearchDocument(
            index_alias=alias,
            document_id=build_order_document_id(self.tenant_id, self.id),
            tenant_id=self.tenant_id,
            version=self.version,
            body=body,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OrderSearchDocument:
        return cls(
            id=str(data.get("id") or data.get("order_id") or ""),
            tenant_id=str(data.get("tenant_id", "")),
            status=str(data.get("status", "PENDING")),
            customer_reference=data.get("customer_reference"),
            customer_id=data.get("customer_id"),
            currency=str(data.get("currency", "GBP")),
            total_minor=int(data.get("total_minor", 0)),
            warehouse_ids=list(data.get("warehouse_ids", [])),
            skus=list(data.get("skus", [])),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            version=int(data.get("version", 1)),
        )


# ============================================================================
# Fulfillment Exception Search Read Model
# ============================================================================
FULFILLMENT_EXCEPTIONS_INDEX_SCHEMA_VERSION: int = 1
FULFILLMENT_EXCEPTIONS_INDEX_V1: str = "fulfillment-exceptions-v1"
FULFILLMENT_EXCEPTIONS_INDEX_ALIAS: str = "fulfillment_exceptions"

FULFILLMENT_EXCEPTIONS_INDEX_MAPPINGS: dict[str, object] = {
    "_meta": {
        "schema_version": FULFILLMENT_EXCEPTIONS_INDEX_SCHEMA_VERSION,
    },
    "properties": {
        "id": {"type": "keyword"},
        "tenant_id": {"type": "keyword"},
        "fulfillment_id": {"type": "keyword"},
        "order_id": {"type": "keyword"},
        "warehouse_id": {"type": "keyword"},
        "exception_type": {"type": "keyword"},
        "status": {"type": "keyword"},
        "reason": {"type": "text"},
        "is_permanent": {"type": "boolean"},
        "created_at": {"type": "date"},
        "version": {"type": "integer"},
    },
}


def build_fulfillment_exception_document_id(
    tenant_id: str | uuid.UUID,
    fulfillment_id: str | uuid.UUID,
) -> str:
    """Build a deterministic, tenant-isolated document ID for fulfillment exception search."""
    return f"{tenant_id}:{fulfillment_id}"


@dataclass(slots=True)
class FulfillmentExceptionSearchDocument:
    """Read model representing fulfillment exceptions for operations monitoring."""

    id: str
    tenant_id: str
    fulfillment_id: str
    order_id: str
    warehouse_id: str
    exception_type: str
    status: str
    reason: str
    is_permanent: bool
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    version: int = 1

    def to_search_document(self, alias: str = FULFILLMENT_EXCEPTIONS_INDEX_ALIAS) -> SearchDocument:
        body: dict[str, object] = {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "fulfillment_id": self.fulfillment_id,
            "order_id": self.order_id,
            "warehouse_id": self.warehouse_id,
            "exception_type": self.exception_type,
            "status": self.status,
            "reason": self.reason,
            "is_permanent": self.is_permanent,
            "created_at": self.created_at,
            "version": self.version,
        }
        return SearchDocument(
            index_alias=alias,
            document_id=build_fulfillment_exception_document_id(
                self.tenant_id, self.fulfillment_id
            ),
            tenant_id=self.tenant_id,
            version=self.version,
            body=body,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FulfillmentExceptionSearchDocument:
        return cls(
            id=str(data.get("id", "")),
            tenant_id=str(data.get("tenant_id", "")),
            fulfillment_id=str(data.get("fulfillment_id", "")),
            order_id=str(data.get("order_id", "")),
            warehouse_id=str(data.get("warehouse_id", "")),
            exception_type=str(data.get("exception_type", "")),
            status=str(data.get("status", "")),
            reason=str(data.get("reason", "")),
            is_permanent=bool(data.get("is_permanent", False)),
            created_at=str(data.get("created_at", "")),
            version=int(data.get("version", 1)),
        )


# ============================================================================
# Audit Search Read Model
# ============================================================================
AUDIT_INDEX_SCHEMA_VERSION: int = 1
AUDIT_INDEX_V1: str = "audit-v1"
AUDIT_INDEX_ALIAS: str = "audit_logs"

AUDIT_INDEX_MAPPINGS: dict[str, object] = {
    "_meta": {
        "schema_version": AUDIT_INDEX_SCHEMA_VERSION,
    },
    "properties": {
        "id": {"type": "keyword"},
        "tenant_id": {"type": "keyword"},
        "actor_id": {"type": "keyword"},
        "action": {"type": "keyword"},
        "resource_type": {"type": "keyword"},
        "resource_id": {"type": "keyword"},
        "correlation_id": {"type": "keyword"},
        "causation_id": {"type": "keyword"},
        "policy_decision": {"type": "keyword"},
        "decision_reason": {"type": "keyword"},
        "occurred_at": {"type": "date"},
        "details": {"type": "object", "dynamic": True},
    },
}


def build_audit_document_id(
    tenant_id: str | uuid.UUID | None,
    audit_id: str | uuid.UUID,
) -> str:
    """Build a deterministic document ID for audit log search."""
    t_id = str(tenant_id) if tenant_id is not None else "system"
    return f"{t_id}:{audit_id}"


@dataclass(slots=True)
class AuditSearchDocument:
    """Read model representing an audit record for compliance search."""

    id: str
    tenant_id: str | None
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    correlation_id: str | None = None
    causation_id: str | None = None
    policy_decision: str = "ALLOW"
    decision_reason: str = "authorized"
    occurred_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    details: dict[str, Any] = field(default_factory=dict)

    def to_search_document(self, alias: str = AUDIT_INDEX_ALIAS) -> SearchDocument:
        body: dict[str, object] = {
            "id": self.id,
            "tenant_id": self.tenant_id or "system",
            "actor_id": self.actor_id,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "policy_decision": self.policy_decision,
            "decision_reason": self.decision_reason,
            "occurred_at": self.occurred_at,
            "details": self.details,
        }
        return SearchDocument(
            index_alias=alias,
            document_id=build_audit_document_id(self.tenant_id, self.id),
            tenant_id=self.tenant_id or "system",
            version=1,
            body=body,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditSearchDocument:
        return cls(
            id=str(data.get("id", "")),
            tenant_id=data.get("tenant_id"),
            actor_id=str(data.get("actor_id", "")),
            action=str(data.get("action", "")),
            resource_type=str(data.get("resource_type", "")),
            resource_id=str(data.get("resource_id", "")),
            correlation_id=data.get("correlation_id"),
            causation_id=data.get("causation_id"),
            policy_decision=str(data.get("policy_decision", "ALLOW")),
            decision_reason=str(data.get("decision_reason", "authorized")),
            occurred_at=str(data.get("occurred_at", "")),
            details=dict(data.get("details", {})),
        )


__all__ = [
    "AUDIT_INDEX_ALIAS",
    "AUDIT_INDEX_MAPPINGS",
    "AUDIT_INDEX_SCHEMA_VERSION",
    "AUDIT_INDEX_V1",
    "FULFILLMENT_EXCEPTIONS_INDEX_ALIAS",
    "FULFILLMENT_EXCEPTIONS_INDEX_MAPPINGS",
    "FULFILLMENT_EXCEPTIONS_INDEX_SCHEMA_VERSION",
    "FULFILLMENT_EXCEPTIONS_INDEX_V1",
    "ORDERS_INDEX_ALIAS",
    "ORDERS_INDEX_MAPPINGS",
    "ORDERS_INDEX_SCHEMA_VERSION",
    "ORDERS_INDEX_V1",
    "PRODUCTS_INDEX_ALIAS",
    "PRODUCTS_INDEX_MAPPINGS",
    "PRODUCTS_INDEX_SCHEMA_VERSION",
    "PRODUCTS_INDEX_V1",
    "AuditSearchDocument",
    "FulfillmentExceptionSearchDocument",
    "OrderSearchDocument",
    "ProductSearchDocument",
    "build_audit_document_id",
    "build_fulfillment_exception_document_id",
    "build_order_document_id",
    "build_product_document_id",
]
