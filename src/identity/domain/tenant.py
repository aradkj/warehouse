"""The Tenant aggregate (Identity and Access context).

Phase 1 needs tenants to exist because every tenant-owned row references one and
because the API resolves a tenant per request. Users, roles, service accounts,
sessions, policies and authorization audits are **Phase 4** and are deliberately
absent (see ``docs/architecture/bounded-contexts.md``).

The ``tenants`` table is the root of the tenancy tree: it is the only business
table that does not carry a ``tenant_id`` column, and every other business table
has a ``ON DELETE RESTRICT`` foreign key to it.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from identity.domain.events import TenantRegistered, TenantRenamed
from shared.domain.aggregate import AggregateRoot
from shared.domain.errors import InvalidValue
from shared.domain.identifiers import TenantId, new_id

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")

MAX_TENANT_NAME_LENGTH = 160


class TenantStatus(StrEnum):
    """Tenant lifecycle state."""

    ACTIVE = "ACTIVE"
    #: Suspension is a Phase 4 administrative action; the value exists from the
    #: start so that the column and its meaning never change.
    SUSPENDED = "SUSPENDED"


class Tenant(AggregateRoot):
    """Tenant aggregate root.

    A tenant is its own owner: ``tenant_id`` equals ``id``, which lets the
    aggregate reuse the shared versioning and event-collection machinery without
    weakening the invariant that every tenant-owned row names its owner.
    """

    __slots__ = ("_created_at", "_id", "_name", "_slug", "_status", "_updated_at")

    def __init__(
        self,
        *,
        id: TenantId,
        slug: str,
        name: str,
        status: TenantStatus,
        created_at: datetime,
        updated_at: datetime,
        version: int = 0,
    ) -> None:
        super().__init__(tenant_id=id, version=version)
        self._id = id
        self._slug = _validate_slug(slug)
        self._name = _validate_name(name)
        self._status = status
        self._created_at = created_at
        self._updated_at = updated_at

    # -- creation ----------------------------------------------------------
    @classmethod
    def register(cls, *, slug: str, name: str, now: datetime) -> Tenant:
        tenant_id = TenantId(new_id())
        tenant = cls(
            id=tenant_id,
            slug=slug,
            name=name,
            status=TenantStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            version=0,
        )
        tenant._record_event(
            TenantRegistered(tenant_id=tenant_id, slug=tenant.slug, name=tenant.name)
        )
        return tenant

    # -- state -------------------------------------------------------------
    @property
    def id(self) -> TenantId:
        return self._id

    @property
    def slug(self) -> str:
        return self._slug

    @property
    def name(self) -> str:
        return self._name

    @property
    def status(self) -> TenantStatus:
        return self._status

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    @property
    def is_active(self) -> bool:
        return self._status is TenantStatus.ACTIVE

    # -- behaviour ---------------------------------------------------------
    def rename(self, *, name: str, now: datetime) -> None:
        """Change the display name (the slug is immutable: it is an identifier)."""
        new_name = _validate_name(name)
        if new_name == self._name:
            return
        self._name = new_name
        self._updated_at = now
        self._record_event(TenantRenamed(tenant_id=self._id, name=new_name))


def _validate_slug(slug: str) -> str:
    if not _SLUG_RE.match(slug):
        raise InvalidValue(
            "tenant slug must be 3-64 lowercase letters, digits and dashes",
            details={"slug": slug},
        )
    return slug


def _validate_name(name: str) -> str:
    stripped = name.strip()
    if not stripped or len(stripped) > MAX_TENANT_NAME_LENGTH:
        raise InvalidValue(
            f"tenant name must be 1-{MAX_TENANT_NAME_LENGTH} characters",
            details={"field": "name"},
        )
    return stripped


__all__ = ["MAX_TENANT_NAME_LENGTH", "Tenant", "TenantStatus"]
