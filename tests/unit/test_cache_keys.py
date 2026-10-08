"""Unit tests for centralized cache key generation and multi-tenant isolation."""

from __future__ import annotations

import uuid

import pytest

from shared.infrastructure.cache.keys import CacheKeyBuilder, default_key_builder

pytestmark = pytest.mark.unit


def test_cache_key_structure() -> None:
    tenant_id = uuid.uuid4()
    product_id = uuid.uuid4()

    key = default_key_builder.product(tenant_id, product_id)
    expected = f"v1:{tenant_id}:product:{product_id}"
    assert key == expected


def test_tenant_isolation_identical_resource_ids_have_different_keys() -> None:
    """Multi-tenancy guarantee: identical resource IDs across distinct tenants
    must never collide.
    """
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    product_id = uuid.uuid4()

    key_a = default_key_builder.product(tenant_a, product_id)
    key_b = default_key_builder.product(tenant_b, product_id)

    assert key_a != key_b
    assert str(tenant_a) in key_a and str(tenant_b) not in key_a
    assert str(tenant_b) in key_b and str(tenant_a) not in key_b


def test_versioning_produces_distinct_keys() -> None:
    """Schema version change produces distinct keys without needing cache flush."""
    tenant_id = uuid.uuid4()
    order_id = uuid.uuid4()

    builder_v1 = CacheKeyBuilder(version=1)
    builder_v2 = CacheKeyBuilder(version=2)

    key_v1 = builder_v1.order(tenant_id, order_id)
    key_v2 = builder_v2.order(tenant_id, order_id)

    assert key_v1.startswith("v1:")
    assert key_v2.startswith("v2:")
    assert key_v1 != key_v2


def test_domain_resource_helpers() -> None:
    tenant_id = uuid.uuid4()
    sku_id = uuid.uuid4()
    wh_id = uuid.uuid4()

    sku_key = default_key_builder.sku(tenant_id, sku_id)
    assert sku_key == f"v1:{tenant_id}:sku:{sku_id}"

    inv_key = default_key_builder.inventory(tenant_id, sku_id)
    assert inv_key == f"v1:{tenant_id}:inventory:{sku_id}"

    inv_wh_key = default_key_builder.inventory(tenant_id, sku_id, wh_id)
    assert inv_wh_key == f"v1:{tenant_id}:inventory:{sku_id}:{wh_id}"


def test_lock_key_formatting() -> None:
    tenant_id = uuid.uuid4()
    product_id = uuid.uuid4()
    resource_key = default_key_builder.product(tenant_id, product_id)

    lock_key = default_key_builder.lock(resource_key)
    assert lock_key == f"lock:v1:{tenant_id}:product:{product_id}"


def test_invalid_key_segments_raise_value_error() -> None:
    tenant_id = uuid.uuid4()

    # Empty tenant
    with pytest.raises(ValueError, match="cannot be empty"):
        default_key_builder.resource("", "product", "123")

    # Colons in identifier
    with pytest.raises(ValueError, match="illegal characters"):
        default_key_builder.resource(tenant_id, "product", "invalid:id")

    # Whitespace in resource type
    with pytest.raises(ValueError, match="illegal characters"):
        default_key_builder.resource(tenant_id, "prod uct", "123")
