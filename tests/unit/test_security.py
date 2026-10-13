"""Unit tests for PasswordHasher, TokenService, and security utilities."""

from __future__ import annotations

from typing import Any

import pytest

from identity.infrastructure.security import (
    PasswordHasher,
    TokenService,
    generate_secure_secret,
    hash_token,
)
from shared.application.errors import AuthenticationFailed


def test_password_hasher_hash_and_verify() -> None:
    hasher = PasswordHasher()
    password = "SuperSecretPassword123!"

    hashed = hasher.hash(password)
    assert hashed != password
    assert len(hashed) > 20

    # Verification success
    assert hasher.verify(password, hashed) is True

    # Verification failure
    assert hasher.verify("WrongPassword123!", hashed) is False
    assert hasher.verify("", hashed) is False


def test_hash_token_deterministic() -> None:
    token = "random_token_string_abc"
    h1 = hash_token(token)
    h2 = hash_token(token)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex digest
    assert h1 != hash_token("different_token")


def test_generate_secure_secret() -> None:
    s1 = generate_secure_secret(32)
    s2 = generate_secure_secret(32)
    assert len(s1) >= 32
    assert s1 != s2


def test_token_service_lifecycle() -> None:
    service = TokenService(
        secret="test-secret-at-least-32-characters-long-12345",
        algorithm="HS256",
        issuer="arad-test",
        audience="arad-api-test",
        access_token_ttl_seconds=3600,
    )

    claims = {"sub": "user-uuid-123", "email": "test@arad.io", "type": "access"}
    token = service.create_access_token(claims)
    assert isinstance(token, str)

    # Valid decode
    decoded = service.verify_access_token(token)
    assert decoded["sub"] == "user-uuid-123"
    assert decoded["email"] == "test@arad.io"
    assert decoded["iss"] == "arad-test"
    assert decoded["aud"] == "arad-api-test"


def test_token_service_rejects_expired_token() -> None:
    service = TokenService(
        secret="test-secret-at-least-32-characters-long-12345",
        algorithm="HS256",
        issuer="arad-test",
        audience="arad-api-test",
        access_token_ttl_seconds=-10,  # Expired immediately
    )
    token = service.create_access_token({"sub": "expired-user"})
    with pytest.raises(AuthenticationFailed, match="token has expired"):
        service.verify_access_token(token)


def test_token_service_rejects_tampered_token() -> None:
    service = TokenService(
        secret="test-secret-at-least-32-characters-long-12345",
        algorithm="HS256",
        issuer="arad-test",
        audience="arad-api-test",
        access_token_ttl_seconds=3600,
    )
    token = service.create_access_token({"sub": "valid-user"})
    tampered = token[:-4] + "abcd"

    with pytest.raises(AuthenticationFailed, match="invalid token signature or format"):
        service.verify_access_token(tampered)


def test_token_service_rejects_issuer_or_audience_mismatch() -> None:
    service_a = TokenService(
        secret="test-secret-at-least-32-characters-long-12345",
        algorithm="HS256",
        issuer="issuer-a",
        audience="aud-a",
        access_token_ttl_seconds=3600,
    )
    service_b = TokenService(
        secret="test-secret-at-least-32-characters-long-12345",
        algorithm="HS256",
        issuer="issuer-b",
        audience="aud-b",
        access_token_ttl_seconds=3600,
    )

    token = service_a.create_access_token({"sub": "user"})
    with pytest.raises(AuthenticationFailed):
        service_b.verify_access_token(token)


def test_password_hasher_production_mode_raises_if_dependency_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    orig_import = builtins.__import__

    def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "argon2":
            raise ImportError("No module named 'argon2'")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    # In production configuration (allow_fallback=False), missing argon2 must raise RuntimeError
    with pytest.raises(RuntimeError, match="argon2-cffi is required in production configuration"):
        PasswordHasher(allow_fallback=False)

    # In dev/test configuration (allow_fallback=True), missing argon2 logs warning and falls back
    hasher = PasswordHasher(allow_fallback=True)
    hashed = hasher.hash("FallbackPassword123!")
    assert hashed.startswith("$scrypt$")
    assert hasher.verify("FallbackPassword123!", hashed) is True


def test_password_hasher_needs_rehash_upgrade() -> None:
    hasher = PasswordHasher(allow_fallback=True)
    scrypt_sample = "$scrypt$16384$8$1$c2FsdHNhbHQ=$a2V5a2V5"
    # When argon2 is available, any scrypt hash should report needs_rehash=True
    assert hasher.needs_rehash(scrypt_sample) is True

    # A freshly hashed argon2 password should report needs_rehash=False
    argon2_hash = hasher.hash("NewPassword123!")
    if argon2_hash.startswith("$argon2"):
        assert hasher.needs_rehash(argon2_hash) is False


def test_token_service_invitation_tokens() -> None:
    from uuid import uuid4

    from shared.domain.identifiers import TenantId

    service = TokenService(
        secret="test-secret-at-least-32-characters-long-12345",
        algorithm="HS256",
        issuer="arad-test",
        audience="arad-api-test",
    )

    t_id = TenantId(uuid4())
    token = service.create_invitation_token(
        tenant_id=t_id,
        email="invitee@arad.io",
        role="ADMIN",
    )
    assert isinstance(token, str)

    payload = service.verify_invitation_token(token)
    assert payload["type"] == "invitation"
    assert payload["tenant_id"] == str(t_id)
    assert payload["email"] == "invitee@arad.io"
    assert payload["role"] == "ADMIN"

    # Tampered token fails
    with pytest.raises(AuthenticationFailed, match="invalid invitation token"):
        service.verify_invitation_token(token + "xyz")
