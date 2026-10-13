"""User session and refresh token family model."""

from __future__ import annotations

import uuid
from datetime import datetime

from shared.domain.identifiers import UserId, new_id


class UserSession:
    """Server-side session tracking a refresh-token family."""

    __slots__ = (
        "_created_at",
        "_expires_at",
        "_family_id",
        "_id",
        "_is_revoked",
        "_rotated_at",
        "_token_hash",
        "_user_id",
    )

    def __init__(
        self,
        *,
        id: uuid.UUID,
        user_id: UserId,
        token_hash: str,
        family_id: uuid.UUID,
        is_revoked: bool,
        expires_at: datetime,
        created_at: datetime,
        rotated_at: datetime | None = None,
    ) -> None:
        self._id = id
        self._user_id = user_id
        self._token_hash = token_hash
        self._family_id = family_id
        self._is_revoked = is_revoked
        self._expires_at = expires_at
        self._created_at = created_at
        self._rotated_at = rotated_at

    @classmethod
    def create(
        cls,
        *,
        user_id: UserId,
        token_hash: str,
        expires_at: datetime,
        now: datetime,
        family_id: uuid.UUID | None = None,
        id: uuid.UUID | None = None,
    ) -> UserSession:
        return cls(
            id=id or new_id(),
            user_id=user_id,
            token_hash=token_hash,
            family_id=family_id or new_id(),
            is_revoked=False,
            expires_at=expires_at,
            created_at=now,
            rotated_at=None,
        )

    # -- properties --------------------------------------------------------
    @property
    def id(self) -> uuid.UUID:
        return self._id

    @property
    def user_id(self) -> UserId:
        return self._user_id

    @property
    def token_hash(self) -> str:
        return self._token_hash

    @property
    def family_id(self) -> uuid.UUID:
        return self._family_id

    @property
    def is_revoked(self) -> bool:
        return self._is_revoked

    @property
    def expires_at(self) -> datetime:
        return self._expires_at

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def rotated_at(self) -> datetime | None:
        return self._rotated_at

    def is_expired(self, now: datetime) -> bool:
        return now >= self._expires_at

    def is_valid(self, now: datetime) -> bool:
        return not self._is_revoked and not self.is_expired(now)

    def revoke(self) -> None:
        self._is_revoked = True

    def rotate(self, *, now: datetime) -> None:
        self._rotated_at = now


__all__ = ["UserSession"]
