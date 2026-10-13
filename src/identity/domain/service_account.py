"""Service Account model (Identity and Access context).

Represents a machine/non-human principal explicitly bound to a tenant.
Credentials are authenticated using a hashed client secret.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum

from shared.domain.errors import InvalidValue
from shared.domain.identifiers import ServiceAccountId, TenantId, new_id

_CLIENT_ID_RE = re.compile(r"^[a-zA-Z0-9_\-\.]{3,64}$")


class ServiceAccountStatus(StrEnum):
    """Service account state."""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class ServiceAccount:
    """Service account entity."""

    __slots__ = (
        "_client_id",
        "_created_at",
        "_id",
        "_name",
        "_scopes",
        "_secret_hash",
        "_status",
        "_tenant_id",
        "_updated_at",
        "_version",
    )

    def __init__(
        self,
        *,
        id: ServiceAccountId,
        tenant_id: TenantId,
        name: str,
        client_id: str,
        secret_hash: str,
        status: ServiceAccountStatus,
        created_at: datetime,
        updated_at: datetime,
        scopes: Iterable[str] = (),
        version: int = 1,
    ) -> None:
        self._id = id
        self._tenant_id = tenant_id
        self._name = _validate_name(name)
        self._client_id = _validate_client_id(client_id)
        self._secret_hash = secret_hash
        self._status = status
        self._scopes = _validate_scopes(scopes)
        self._created_at = created_at
        self._updated_at = updated_at
        self._version = max(1, version)

    @classmethod
    def create(
        cls,
        *,
        tenant_id: TenantId,
        name: str,
        client_id: str,
        secret_hash: str,
        now: datetime,
        scopes: Iterable[str] = (),
        id: ServiceAccountId | None = None,
    ) -> ServiceAccount:
        sa_id = id or ServiceAccountId(new_id())
        return cls(
            id=sa_id,
            tenant_id=tenant_id,
            name=name,
            client_id=client_id,
            secret_hash=secret_hash,
            status=ServiceAccountStatus.ACTIVE,
            scopes=scopes,
            created_at=now,
            updated_at=now,
            version=1,
        )

    # -- properties --------------------------------------------------------
    @property
    def id(self) -> ServiceAccountId:
        return self._id

    @property
    def tenant_id(self) -> TenantId:
        return self._tenant_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def secret_hash(self) -> str:
        return self._secret_hash

    @property
    def status(self) -> ServiceAccountStatus:
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
    def token_version(self) -> int:
        return self._version

    @property
    def is_active(self) -> bool:
        return self._status is ServiceAccountStatus.ACTIVE

    @property
    def scopes(self) -> frozenset[str]:
        return self._scopes

    def has_scope(self, required_permission: str) -> bool:
        from identity.domain.permission import has_permission

        return has_permission(self._scopes, required_permission)

    # -- transitions -------------------------------------------------------
    def disable(self, *, now: datetime) -> None:
        """Deactivate service account."""
        self._status = ServiceAccountStatus.DISABLED
        self._updated_at = now

    def activate(self, *, now: datetime) -> None:
        """Reactivate service account."""
        self._status = ServiceAccountStatus.ACTIVE
        self._updated_at = now

    def rotate_secret(self, new_secret_hash: str, *, now: datetime) -> None:
        """Rotate client secret hash and increment token version."""
        self._secret_hash = new_secret_hash
        self._updated_at = now
        self._version += 1

    def update_scopes(self, new_scopes: Iterable[str], *, now: datetime) -> None:
        """Update allowed scopes."""
        self._scopes = _validate_scopes(new_scopes)
        self._updated_at = now


def _validate_name(name: str) -> str:
    stripped = name.strip()
    if not stripped or len(stripped) > 160:
        raise InvalidValue(
            "service account name must be 1-160 characters",
            details={"field": "name"},
        )
    return stripped


def _validate_client_id(client_id: str) -> str:
    stripped = client_id.strip()
    if not _CLIENT_ID_RE.match(stripped):
        raise InvalidValue(
            "client_id must be 3-64 alphanumeric characters, underscores, dots or dashes",
            details={"client_id": client_id},
        )
    return stripped


def _validate_scopes(scopes: Iterable[str]) -> frozenset[str]:
    from identity.domain.permission import is_known_permission

    validated: set[str] = set()
    for s in scopes:
        if not isinstance(s, str):
            raise InvalidValue("scope must be a string", details={"scope": str(s)})
        stripped = s.strip()
        if not stripped:
            continue
        if stripped == "*" or stripped.endswith(".*"):
            raise InvalidValue(
                f"wildcard scopes are not permitted for service accounts: '{stripped}'",
                details={"scope": stripped},
            )
        if not is_known_permission(stripped):
            raise InvalidValue(
                f"unknown service account scope: '{stripped}'",
                details={"scope": stripped},
            )
        validated.add(stripped)
    return frozenset(validated)


__all__ = ["ServiceAccount", "ServiceAccountStatus"]
