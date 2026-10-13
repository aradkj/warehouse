"""Role model (Identity and Access context).

Represents a named collection of permissions.
Roles can be system-defined (global across all tenants) or tenant-scoped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime

from identity.domain.permission import (
    ALL_PERMISSIONS,
    Permission,
    has_permission,
    is_valid_permission_identifier,
)
from shared.domain.errors import InvalidValue
from shared.domain.identifiers import RoleId, TenantId, new_id

_ROLE_NAME_PATTERN = re.compile(r"^[A-Z0-9_\-]{2,64}$")

# Built-in system role names
SYSTEM_ROLE_ADMIN = "ADMIN"
SYSTEM_ROLE_MEMBER = "MEMBER"
SYSTEM_ROLE_SUPPORT = "SUPPORT_AGENT"
SYSTEM_ROLE_AUDITOR = "AUDITOR"

# Default permissions for built-in roles
DEFAULT_ADMIN_PERMISSIONS: frozenset[str] = frozenset(ALL_PERMISSIONS)

DEFAULT_MEMBER_PERMISSIONS: frozenset[str] = frozenset(
    {
        Permission.ORDERS_READ.value,
        Permission.ORDERS_CREATE.value,
        Permission.ORDERS_FINANCIALS_READ.value,
        Permission.INVENTORY_READ.value,
        Permission.CATALOG_PRODUCTS_READ.value,
    }
)

DEFAULT_SUPPORT_PERMISSIONS: frozenset[str] = frozenset(
    {
        Permission.ORDERS_READ.value,
        Permission.INVENTORY_READ.value,
        Permission.CATALOG_PRODUCTS_READ.value,
    }
)

DEFAULT_AUDITOR_PERMISSIONS: frozenset[str] = frozenset(
    {
        Permission.ORDERS_READ.value,
        Permission.ORDERS_FINANCIALS_READ.value,
        Permission.INVENTORY_READ.value,
        Permission.CATALOG_PRODUCTS_READ.value,
        Permission.AUDIT_EVENTS_READ.value,
        Permission.ACCESS_ROLES_READ.value,
        Permission.ACCESS_POLICIES_READ.value,
    }
)

SYSTEM_ROLES_PERMISSIONS: dict[str, frozenset[str]] = {
    SYSTEM_ROLE_ADMIN: DEFAULT_ADMIN_PERMISSIONS,
    SYSTEM_ROLE_MEMBER: DEFAULT_MEMBER_PERMISSIONS,
    SYSTEM_ROLE_SUPPORT: DEFAULT_SUPPORT_PERMISSIONS,
    SYSTEM_ROLE_AUDITOR: DEFAULT_AUDITOR_PERMISSIONS,
}

RESERVED_ROLE_NAMES: frozenset[str] = frozenset(SYSTEM_ROLES_PERMISSIONS.keys())


class Role:
    """Named role entity mapping to a set of permission patterns."""

    __slots__ = (
        "_created_at",
        "_description",
        "_id",
        "_is_system",
        "_name",
        "_permissions",
        "_tenant_id",
        "_updated_at",
        "_version",
    )

    def __init__(
        self,
        *,
        id: RoleId,
        tenant_id: TenantId | None,
        name: str,
        description: str,
        is_system: bool,
        permissions: Iterable[str],
        created_at: datetime,
        updated_at: datetime,
        version: int = 1,
    ) -> None:
        self._id = id
        self._tenant_id = tenant_id
        self._name = _validate_role_name(name)
        self._description = description.strip()
        self._is_system = is_system
        self._permissions = frozenset(_validate_permissions(permissions))
        self._created_at = created_at
        self._updated_at = updated_at
        self._version = max(1, version)

    @classmethod
    def create(
        cls,
        *,
        name: str,
        description: str,
        permissions: Iterable[str],
        now: datetime,
        tenant_id: TenantId | None = None,
        is_system: bool = False,
        id: RoleId | None = None,
    ) -> Role:
        cleaned_name = name.strip().upper()
        if not is_system and cleaned_name in RESERVED_ROLE_NAMES:
            raise InvalidValue(
                f"role name '{name}' is reserved for built-in system roles",
                details={"name": name, "reserved": sorted(RESERVED_ROLE_NAMES)},
            )
        role_id = id or RoleId(new_id())
        return cls(
            id=role_id,
            tenant_id=tenant_id,
            name=name,
            description=description,
            is_system=is_system,
            permissions=permissions,
            created_at=now,
            updated_at=now,
            version=1,
        )

    # -- properties --------------------------------------------------------
    @property
    def id(self) -> RoleId:
        return self._id

    @property
    def tenant_id(self) -> TenantId | None:
        return self._tenant_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def is_system(self) -> bool:
        return self._is_system

    @property
    def permissions(self) -> frozenset[str]:
        return self._permissions

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    @property
    def version(self) -> int:
        return self._version

    def has_permission(self, permission: str) -> bool:
        """Check if this role provides the requested permission."""
        return has_permission(self._permissions, permission)

    # -- mutations ---------------------------------------------------------
    def update_permissions(self, new_permissions: Iterable[str], *, now: datetime) -> None:
        """Update permissions set for tenant-defined roles."""
        if self._is_system:
            raise InvalidValue(
                "system roles cannot be modified",
                details={"role_name": self._name},
            )
        self._permissions = frozenset(_validate_permissions(new_permissions))
        self._updated_at = now

    def update_description(self, description: str, *, now: datetime) -> None:
        """Update role description."""
        if self._is_system:
            raise InvalidValue(
                "system roles cannot be modified",
                details={"role_name": self._name},
            )
        self._description = description.strip()
        self._updated_at = now


def _validate_role_name(name: str) -> str:
    cleaned = name.strip().upper()
    if not _ROLE_NAME_PATTERN.match(cleaned):
        raise InvalidValue(
            "role name must be 2-64 uppercase alphanumeric characters, hyphens or underscores",
            details={"name": name},
        )
    return cleaned


def _validate_permissions(permissions: Iterable[str]) -> list[str]:
    validated: list[str] = []
    for perm in permissions:
        perm_clean = perm.strip()
        if not is_valid_permission_identifier(perm_clean):
            raise InvalidValue(
                f"invalid permission identifier: {perm}",
                details={"permission": perm},
            )
        validated.append(perm_clean)
    return validated


__all__ = [
    "DEFAULT_ADMIN_PERMISSIONS",
    "DEFAULT_AUDITOR_PERMISSIONS",
    "DEFAULT_MEMBER_PERMISSIONS",
    "DEFAULT_SUPPORT_PERMISSIONS",
    "RESERVED_ROLE_NAMES",
    "SYSTEM_ROLES_PERMISSIONS",
    "SYSTEM_ROLE_ADMIN",
    "SYSTEM_ROLE_AUDITOR",
    "SYSTEM_ROLE_MEMBER",
    "SYSTEM_ROLE_SUPPORT",
    "Role",
]
