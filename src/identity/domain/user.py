"""The User entity (Identity and Access context).

Represents a human principal in the platform. A user may have memberships
in multiple tenants.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from shared.domain.errors import InvalidValue
from shared.domain.identifiers import UserId, new_id

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_EMAIL_LENGTH = 255


class UserStatus(StrEnum):
    """User account lifecycle state."""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    LOCKED = "LOCKED"


def normalize_email(email: str) -> str:
    """Normalize and validate email address."""
    normalized = email.strip().lower()
    if not normalized or len(normalized) > MAX_EMAIL_LENGTH or not _EMAIL_RE.match(normalized):
        raise InvalidValue(
            "email must be a valid email address of 3-255 characters",
            details={"email": email},
        )
    return normalized


class User:
    """User entity."""

    __slots__ = (
        "_created_at",
        "_email",
        "_id",
        "_password_hash",
        "_status",
        "_updated_at",
        "_version",
    )

    def __init__(
        self,
        *,
        id: UserId,
        email: str,
        password_hash: str,
        status: UserStatus,
        created_at: datetime,
        updated_at: datetime,
        version: int = 1,
    ) -> None:
        self._id = id
        self._email = normalize_email(email)
        self._password_hash = password_hash
        self._status = status
        self._created_at = created_at
        self._updated_at = updated_at
        self._version = max(1, version)

    @classmethod
    def create(
        cls,
        *,
        email: str,
        password_hash: str,
        now: datetime,
        id: UserId | None = None,
    ) -> User:
        user_id = id or UserId(new_id())
        return cls(
            id=user_id,
            email=email,
            password_hash=password_hash,
            status=UserStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            version=1,
        )

    # -- properties --------------------------------------------------------
    @property
    def id(self) -> UserId:
        return self._id

    @property
    def email(self) -> str:
        return self._email

    @property
    def password_hash(self) -> str:
        return self._password_hash

    @property
    def status(self) -> UserStatus:
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
        return self._status is UserStatus.ACTIVE

    # -- state transitions -------------------------------------------------
    def change_password(self, new_password_hash: str, *, now: datetime) -> None:
        """Update password hash."""
        self._password_hash = new_password_hash
        self._updated_at = now

    def disable(self, *, now: datetime) -> None:
        """Deactivate account."""
        self._status = UserStatus.DISABLED
        self._updated_at = now

    def lock(self, *, now: datetime) -> None:
        """Lock account due to security trigger."""
        self._status = UserStatus.LOCKED
        self._updated_at = now

    def activate(self, *, now: datetime) -> None:
        """Reactivate account."""
        self._status = UserStatus.ACTIVE
        self._updated_at = now


__all__ = ["MAX_EMAIL_LENGTH", "User", "UserStatus", "normalize_email"]
