"""Physical schema for the catalog context.

Index decisions (measured with ``EXPLAIN (ANALYZE, BUFFERS)``; the plans are
recorded in ``docs/architecture/data-model.md``):

* ``ix_products_tenant_id_created_at_id`` - the product list endpoint is
  ``WHERE tenant_id = :t [AND status = :s] ORDER BY created_at DESC, id DESC``.
  The composite index on ``(tenant_id, created_at DESC, id DESC)`` serves both
  the filter and the ordering, so keyset pagination never sorts.
* ``ix_products_tenant_id_status_created_at_id`` - the status-filtered variant
  selects far fewer rows and PostgreSQL prefers a narrower, fully-covered
  ordering; measured plans select this index when ``status`` is supplied.
* ``ix_skus_tenant_id_product_id`` - batch loading of a product's SKUs
  (``WHERE tenant_id = ? AND product_id = ANY(...)``), which is the read pattern
  behind ``GET /v1/products`` and ``GET /v1/products/{id}``.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)

from catalog.domain.value_objects import ProductStatus, SkuStatus
from identity.adapters.persistence.tables import tenants
from shared.infrastructure.db.base import metadata
from shared.infrastructure.db.types import JsonbCol, TimestampCol, UuidCol

_PRODUCT_STATUS_LIST = ", ".join(f"'{status.value}'" for status in ProductStatus)
_SKU_STATUS_LIST = ", ".join(f"'{status.value}'" for status in SkuStatus)

products = Table(
    "products",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column(
        "tenant_id",
        UuidCol,
        ForeignKey(tenants.c.id, ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("slug", String(80), nullable=False),
    Column("name", String(200), nullable=False),
    Column("description", Text, nullable=True),
    #: Validated attribute bag; JSONB because the shape is open-ended but every
    #: value is validated by the domain before it is stored.
    Column("attributes", JsonbCol, nullable=False, server_default="{}"),
    Column("status", String(16), nullable=False),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("updated_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("version", Integer, nullable=False),
    UniqueConstraint("tenant_id", "slug", name="uq_products_tenant_id_slug"),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint("slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name="slug_format"),
    CheckConstraint("length(btrim(name)) BETWEEN 1 AND 200", name="name_length"),
    CheckConstraint(f"status IN ({_PRODUCT_STATUS_LIST})", name="status_valid"),
)

Index(
    "ix_products_tenant_id_created_at_id",
    products.c.tenant_id,
    products.c.created_at.desc(),
    products.c.id.desc(),
)
Index(
    "ix_products_tenant_id_status_created_at_id",
    products.c.tenant_id,
    products.c.status,
    products.c.created_at.desc(),
    products.c.id.desc(),
)

skus = Table(
    "skus",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column(
        "tenant_id",
        UuidCol,
        ForeignKey(tenants.c.id, ondelete="RESTRICT"),
        nullable=False,
    ),
    # A SKU cannot exist without its product, and a product that has ever been
    # sold cannot be deleted (products are archived, never deleted).
    Column(
        "product_id",
        UuidCol,
        ForeignKey(products.c.id, ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("code", String(64), nullable=False),
    Column("name", String(200), nullable=False),
    Column("attributes", JsonbCol, nullable=False, server_default="{}"),
    # Prices are stored as integer minor units: no floating point anywhere in the
    # money path (see ``shared.domain.value_objects.Money``).
    Column("unit_price_minor", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("status", String(16), nullable=False),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("updated_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("version", Integer, nullable=False),
    UniqueConstraint("tenant_id", "code", name="uq_skus_tenant_id_code"),
    UniqueConstraint("tenant_id", "id", name="uq_skus_tenant_id_id"),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint("code ~ '^[A-Z0-9][A-Z0-9._-]{1,63}$'", name="code_format"),
    CheckConstraint("unit_price_minor >= 0", name="unit_price_non_negative"),
    CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
    CheckConstraint(f"status IN ({_SKU_STATUS_LIST})", name="status_valid"),
    CheckConstraint("length(btrim(name)) BETWEEN 1 AND 200", name="name_length"),
)

Index("ix_skus_tenant_id_product_id", skus.c.tenant_id, skus.c.product_id)

__all__ = ["products", "skus"]
