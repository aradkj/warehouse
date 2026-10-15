"""Permission catalog and matching rules (Identity and Access context).

Defines the system-wide permissions catalog and wildcard matching semantics.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum


class Permission(StrEnum):
    """Canonical permission identifiers across all bounded contexts."""

    # Orders
    ORDERS_READ = "orders.read"
    ORDERS_CREATE = "orders.create"
    ORDERS_CANCEL = "orders.cancel"
    ORDERS_CONFIRM = "orders.confirm"
    ORDERS_FINANCIALS_READ = "orders.financials.read"

    # Inventory & Reservations
    INVENTORY_READ = "inventory.read"
    INVENTORY_ADJUST = "inventory.adjust"
    RESERVATIONS_CREATE = "reservations.create"
    RESERVATIONS_RELEASE = "reservations.release"

    # Catalog
    CATALOG_PRODUCTS_READ = "catalog.products.read"
    CATALOG_PRODUCTS_MANAGE = "catalog.products.manage"

    # Access Management
    ACCESS_ROLES_READ = "access.roles.read"
    ACCESS_ROLES_MANAGE = "access.roles.manage"
    ACCESS_POLICIES_READ = "access.policies.read"
    ACCESS_POLICIES_MANAGE = "access.policies.manage"

    # Fulfillment
    FULFILLMENTS_READ = "fulfillments.read"
    FULFILLMENTS_CREATE = "fulfillments.create"
    FULFILLMENTS_MODIFY = "fulfillments.modify"

    # Audit
    AUDIT_EVENTS_READ = "audit.events.read"


ALL_PERMISSIONS: frozenset[str] = frozenset(p.value for p in Permission)

_PERMISSION_PATTERN = re.compile(r"^[a-z0-9_\-]+(\.[a-z0-9_\-]+)*(\.\*)?$")


def is_valid_permission_identifier(name: str) -> bool:
    """Check if a string matches valid permission identifier syntax."""
    if not name or len(name) > 64:
        return False
    if name == "*":
        return True
    return bool(_PERMISSION_PATTERN.match(name))


def is_known_permission(permission: str) -> bool:
    """Check if the permission is part of the canonical system catalog."""
    return permission in ALL_PERMISSIONS


def match_permission(pattern: str, target_permission: str) -> bool:
    """Evaluate whether a granted permission pattern covers a target permission.

    Supports:
    - Exact match: 'orders.read' == 'orders.read'
    - Wildcard prefix: 'orders.*' matches 'orders.read', 'orders.create'
    - Global wildcard: '*' matches all permissions
    """
    if pattern == "*" or pattern == target_permission:
        return True

    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        return target_permission == prefix or target_permission.startswith(f"{prefix}.")

    return False


def has_permission(granted_patterns: Iterable[str], required_permission: str) -> bool:
    """Check whether any granted permission pattern satisfies the required permission."""
    return any(match_permission(pattern, required_permission) for pattern in granted_patterns)


__all__ = [
    "ALL_PERMISSIONS",
    "Permission",
    "has_permission",
    "is_known_permission",
    "is_valid_permission_identifier",
    "match_permission",
]
