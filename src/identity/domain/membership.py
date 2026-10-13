"""Tenant membership model (Identity and Access context).

Represents the explicit association between a User and a Tenant.
A user may belong to multiple tenants, with independent membership statuses.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum

from shared.domain.identifiers import MembershipId, TenantId, UserId, new_id


class MembershipStatus(StrEnum):
    """Membership state within a specific tenant."""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class MembershipRole(StrEnum):
    """Administrative role within a specific tenant."""

    ADMIN = "ADMIN"
    MEMBER = "MEMBER"


class TenantMembership:
    """Explicit association between a User and a Tenant."""

    __slots__ = (
        "_created_at",
        "_id",
        "_role",
        "_roles",
        "_status",
        "_tenant_id",
        "_updated_at",
        "_user_id",
        "_version",
    )

    def __init__(
        self,
        *,
        id: MembershipId,
        user_id: UserId,
        tenant_id: TenantId,
        status: MembershipStatus,
        role: MembershipRole = MembershipRole.MEMBER,
        roles: Iterable[str] = (),
        created_at: datetime,
        updated_at: datetime,
        version: int = 1,
    ) -> None:
        self._id = id
        self._user_id = user_id
        self._tenant_id = tenant_id
        self._status = status
        self._role = role
        assigned = {r.strip().upper() for r in roles if r.strip()}
        if not assigned:
            assigned.add(role.value)
        self._roles = frozenset(assigned)
        if "ADMIN" in self._roles:
            self._role = MembershipRole.ADMIN
        else:
            self._role = MembershipRole.MEMBER
        self._created_at = created_at
        self._updated_at = updated_at
        self._version = max(1, version)

    @classmethod
    def create(
        cls,
        *,
        user_id: UserId,
        tenant_id: TenantId,
        now: datetime,
        id: MembershipId | None = None,
        status: MembershipStatus = MembershipStatus.ACTIVE,
        role: MembershipRole = MembershipRole.MEMBER,
        roles: Iterable[str] = (),
    ) -> TenantMembership:
        mem_id = id or MembershipId(new_id())
        return cls(
            id=mem_id,
            user_id=user_id,
            tenant_id=tenant_id,
            status=status,
            role=role,
            roles=roles,
            created_at=now,
            updated_at=now,
            version=1,
        )

    # -- properties --------------------------------------------------------
    @property
    def id(self) -> MembershipId:
        return self._id

    @property
    def user_id(self) -> UserId:
        return self._user_id

    @property
    def tenant_id(self) -> TenantId:
        return self._tenant_id

    @property
    def status(self) -> MembershipStatus:
        return self._status

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    @property
    def version(self) -> int:
        return self._version

    @property
    def is_active(self) -> bool:
        return self._status is MembershipStatus.ACTIVE

    @property
    def role(self) -> MembershipRole:
        return self._role

    @property
    def roles(self) -> frozenset[str]:
        return self._roles

    @property
    def is_admin(self) -> bool:
        return self._role == MembershipRole.ADMIN or "ADMIN" in self._roles

    def has_role(self, role_name: str) -> bool:
        """Check whether membership holds the given role name."""
        return role_name.strip().upper() in self._roles

    # -- transitions -------------------------------------------------------
    def disable(self, *, now: datetime) -> None:
        """Deactivate membership."""
        self._status = MembershipStatus.DISABLED
        self._updated_at = now

    def activate(self, *, now: datetime) -> None:
        """Reactivate membership."""
        self._status = MembershipStatus.ACTIVE
        self._updated_at = now

    def assign_role(self, role_name: str, *, now: datetime) -> None:
        """Assign an additional role to this membership."""
        clean_role = role_name.strip().upper()
        new_roles = set(self._roles)
        new_roles.add(clean_role)
        if clean_role == "ADMIN":
            self._role = MembershipRole.ADMIN
        self._roles = frozenset(new_roles)
        self._updated_at = now

    def revoke_role(self, role_name: str, *, now: datetime) -> None:
        """Revoke a role from this membership."""
        clean_role = role_name.strip().upper()
        if clean_role in self._roles:
            new_roles = set(self._roles)
            new_roles.remove(clean_role)
            if not new_roles:
                new_roles.add("MEMBER")
            if "ADMIN" in new_roles:
                self._role = MembershipRole.ADMIN
            else:
                self._role = MembershipRole.MEMBER
            self._roles = frozenset(new_roles)
            self._updated_at = now


__all__ = ["MembershipRole", "MembershipStatus", "TenantMembership"]
