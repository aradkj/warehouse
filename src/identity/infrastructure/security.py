"""Security infrastructure: password hashing, token creation and validation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError

from shared.application.context import ActorType
from shared.application.errors import (
    AccountDisabled,
    AuthenticationFailed,
    Forbidden,
    MembershipDisabled,
    TenantSuspended,
    Unauthorized,
)
from shared.domain.identifiers import MembershipId, TenantId

logger = logging.getLogger(__name__)


class PasswordHasher:
    """Argon2id password hasher with explicit scrypt fallback for dev/test environments."""

    def __init__(self, *, allow_fallback: bool = True) -> None:
        self._allow_fallback = allow_fallback
        self._argon2_hasher: Any = None
        try:
            import argon2

            self._argon2_hasher = argon2.PasswordHasher(
                time_cost=2,
                memory_cost=19456,
                parallelism=1,
                hash_len=32,
                type=argon2.Type.ID,
            )
        except ImportError as exc:
            if not self._allow_fallback:
                raise RuntimeError(
                    "argon2-cffi is required in production configuration for "
                    "secure password hashing. Silent fallback to scrypt is disabled in production."
                ) from exc
            logger.warning(
                "argon2-cffi not installed; falling back to hashlib.scrypt for password hashing"
            )

    @property
    def allows_fallback(self) -> bool:
        return self._allow_fallback

    def hash(self, password: str) -> str:
        """Hash a plaintext password securely."""
        if not password or len(password) < 8:
            raise ValueError("password must be at least 8 characters")
        if len(password) > 1024:
            raise ValueError("password exceeds maximum length")

        if self._argon2_hasher is not None:
            return str(self._argon2_hasher.hash(password))

        # Fallback scrypt (only permitted in dev/test)
        salt = os.urandom(16)
        key = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=16384,
            r=8,
            p=1,
            maxmem=32 * 1024 * 1024,
        )
        salt_b64 = base64.b64encode(salt).decode("ascii")
        key_b64 = base64.b64encode(key).decode("ascii")
        return f"$scrypt$16384$8$1${salt_b64}${key_b64}"

    def verify(self, password: str, password_hash: str) -> bool:
        """Verify plaintext password against hash using constant-time comparison."""
        if not password or not password_hash:
            return False

        if password_hash.startswith("$argon2"):
            if self._argon2_hasher is None:
                try:
                    import argon2

                    self._argon2_hasher = argon2.PasswordHasher()
                except ImportError:
                    return False
            try:
                return bool(self._argon2_hasher.verify(password_hash, password))
            except Exception:
                return False

        if password_hash.startswith("$scrypt$"):
            parts = password_hash.split("$")
            if len(parts) != 7:
                return False
            try:
                n = int(parts[2])
                r = int(parts[3])
                p = int(parts[4])
                salt = base64.b64decode(parts[5].encode("ascii"))
                expected = base64.b64decode(parts[6].encode("ascii"))
                calculated = hashlib.scrypt(
                    password.encode("utf-8"),
                    salt=salt,
                    n=n,
                    r=r,
                    p=p,
                    maxmem=32 * 1024 * 1024,
                )
                return hmac.compare_digest(calculated, expected)
            except Exception:
                return False

        return False

    def needs_rehash(self, password_hash: str) -> bool:
        """Check if a password hash should be upgraded (e.g. from scrypt to Argon2id)."""
        if not password_hash:
            return True
        if password_hash.startswith("$scrypt$"):
            # Any scrypt hash needs to be upgraded to Argon2id whenever argon2 is available
            return self._argon2_hasher is not None
        if password_hash.startswith("$argon2"):
            if self._argon2_hasher is not None:
                try:
                    return bool(self._argon2_hasher.check_needs_rehash(password_hash))
                except Exception:
                    return False
            return False
        return True


class TokenService:
    """Signs and validates JWT access tokens."""

    def __init__(
        self,
        *,
        secret: str,
        algorithm: str = "HS256",
        issuer: str = "arad-platform",
        audience: str = "arad-api",
        access_token_ttl_seconds: int = 900,
    ) -> None:
        self._secret = secret
        self._algorithm = algorithm
        self._issuer = issuer
        self._audience = audience
        self._ttl_seconds = access_token_ttl_seconds

    @property
    def access_token_ttl_seconds(self) -> int:
        return self._ttl_seconds

    def create_access_token(
        self,
        claims: dict[str, Any] | None = None,
        *,
        sub: str | None = None,
        principal_type: ActorType | str | None = None,
        tenant_id: TenantId | None = None,
        membership_id: MembershipId | None = None,
        expires_delta: timedelta | None = None,
        extra_claims: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        """Create a signed JWT access token."""
        now = datetime.now(UTC)
        ttl = expires_delta or timedelta(seconds=self._ttl_seconds)
        exp = now + ttl

        payload: dict[str, Any] = {
            "type": "access",
            "jti": str(uuid.uuid4()),
            "iss": self._issuer,
            "aud": self._audience,
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
        }

        if claims:
            payload.update(claims)

        if sub is not None:
            payload["sub"] = str(sub)
        if principal_type is not None:
            payload["principal_type"] = str(principal_type)
        if tenant_id is not None:
            payload["tenant_id"] = str(tenant_id)
        if membership_id is not None:
            payload["membership_id"] = str(membership_id)

        if extra_claims:
            payload.update(extra_claims)
        if kwargs:
            payload.update(kwargs)

        return str(
            jwt.encode(
                payload,
                self._secret,
                algorithm=self._algorithm,
            )
        )

    def decode_access_token(self, token: str) -> dict[str, Any]:
        """Decode and validate a signed JWT access token."""
        try:
            # Explicitly restrict algorithms to prevent algorithm-switching attacks
            payload: dict[str, Any] = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                issuer=self._issuer,
                audience=self._audience,
                options={
                    "require": ["exp", "iat", "sub", "type", "iss", "aud"],
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except ExpiredSignatureError as exc:
            raise AuthenticationFailed(
                "access token has expired", details={"reason": "token_expired"}
            ) from exc
        except InvalidTokenError as exc:
            raise AuthenticationFailed(
                "invalid token signature or format", details={"reason": str(exc)}
            ) from exc
        except Exception as exc:
            raise AuthenticationFailed(
                "could not parse access token", details={"reason": str(exc)}
            ) from exc

        return payload

    def verify_access_token(self, token: str) -> dict[str, Any]:
        """Alias for decode_access_token."""
        return self.decode_access_token(token)

    def create_invitation_token(
        self,
        *,
        tenant_id: TenantId,
        email: str | None = None,
        role: str = "MEMBER",
        expires_delta: timedelta | None = None,
    ) -> str:
        """Create a signed invitation token for tenant onboarding."""
        now = datetime.now(UTC)
        ttl = expires_delta or timedelta(days=7)
        payload: dict[str, Any] = {
            "type": "invitation",
            "jti": str(uuid.uuid4()),
            "iss": self._issuer,
            "aud": self._audience,
            "iat": int(now.timestamp()),
            "exp": int((now + ttl).timestamp()),
            "tenant_id": str(tenant_id),
            "role": role,
        }
        if email is not None:
            payload["email"] = email.strip().lower()
        return str(jwt.encode(payload, self._secret, algorithm=self._algorithm))

    def verify_invitation_token(self, token: str) -> dict[str, Any]:
        """Verify and decode a signed tenant invitation token."""
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                issuer=self._issuer,
                audience=self._audience,
                options={
                    "require": ["exp", "iat", "type", "iss", "aud", "tenant_id"],
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
            if payload.get("type") != "invitation":
                raise AuthenticationFailed("invalid token type; expected invitation token")
            return payload
        except ExpiredSignatureError as exc:
            raise AuthenticationFailed(
                "invitation token has expired", details={"reason": "invitation_expired"}
            ) from exc
        except InvalidTokenError as exc:
            raise AuthenticationFailed(
                "invalid invitation token signature or format", details={"reason": str(exc)}
            ) from exc
        except Exception as exc:
            raise AuthenticationFailed(
                "could not parse invitation token", details={"reason": str(exc)}
            ) from exc


def generate_secure_secret(nbytes: int = 32) -> str:
    """Generate cryptographically secure random token (URL safe)."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Deterministic hash for indexing/matching stored refresh tokens."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


__all__ = [
    "AccountDisabled",
    "AuthenticationFailed",
    "Forbidden",
    "MembershipDisabled",
    "PasswordHasher",
    "TenantSuspended",
    "TokenService",
    "Unauthorized",
    "generate_secure_secret",
    "hash_token",
]
