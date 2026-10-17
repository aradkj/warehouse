"""Unit and integration tests for Phase E remediation (SEC-001 and API-003).

SEC-001: Restrict synthetic wildcard ADMIN principals, ensure staging/production fail-closed.
API-003: Standardize unhandled exception responses to RFC 7807 problem details.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from bootstrap.config import Settings
from bootstrap.dependencies import get_authenticated_tenant_context
from bootstrap.errors import register_exception_handlers
from shared.application.errors import AuthenticationFailed

pytestmark = pytest.mark.api


class TestSec001ConfigAndDependencyFailClosed:
    """Verifies that non-local environments strictly enforce authentication."""

    def test_staging_mode_rejects_auth_required_false(self) -> None:
        with pytest.raises(ValueError, match="ARAD_AUTH_REQUIRED must be set to true"):
            Settings(
                environment="staging",
                auth_required=False,
                auth_jwt_secret="a" * 32,
            )

    def test_stage_mode_rejects_auth_required_false(self) -> None:
        with pytest.raises(ValueError, match="ARAD_AUTH_REQUIRED must be set to true"):
            Settings(
                environment="stage",
                auth_required=False,
                auth_jwt_secret="a" * 32,
            )

    @pytest.mark.asyncio
    async def test_synthetic_admin_rejected_when_is_production(self) -> None:
        """Even if principal is None, production and non-dev environments fail closed."""
        tenant_id = str(uuid.uuid4())
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-tenant-id", tenant_id.encode())],
        }
        request = Request(scope)

        prod_settings = Settings(
            environment="production",
            auth_required=True,
            auth_jwt_secret="c7e2b19f4a6d8e03512b4897f123456789abcdef0123456789abcdef012345",
        )
        fake_db = None  # Not reached because fail-closed happens before database access
        with pytest.raises(AuthenticationFailed, match="authentication required"):
            await get_authenticated_tenant_context(
                request=request,
                settings=prod_settings,
                principal=None,
                database=fake_db,  # type: ignore[arg-type]
                tenant_header=tenant_id,
            )


class TestApi003Rfc7807UnhandledExceptionHandling:
    """Verifies that unhandled exceptions produce RFC 7807 problem details without leaking details."""

    @pytest.mark.asyncio
    async def test_unhandled_exception_returns_rfc7807_problem_details(self) -> None:
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/trigger-unhandled")
        async def trigger_unhandled() -> None:
            raise RuntimeError("sensitive internal database password in memory")

        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
            headers={"X-Correlation-Id": "test-trace-12345"},
        ) as client:
            response = await client.get("/trigger-unhandled")

            assert response.status_code == 500
            assert "application/problem+json" in response.headers.get("content-type", "")
            assert response.headers.get("X-Correlation-Id") == "test-trace-12345"

            data = response.json()
            # RFC 7807 top-level fields
            assert data["type"] == "about:blank"
            assert data["title"] == "Internal Server Error"
            assert data["status"] == 500
            assert data["detail"] == "the request could not be completed"
            assert data["instance"] == "/trigger-unhandled"
            assert data["correlation_id"] == "test-trace-12345"

            # Sensitive text should never be present anywhere in the response body
            assert "sensitive" not in response.text
            assert "password" not in response.text
            assert "RuntimeError" not in response.text

            # Backward compatibility error dictionary is also present
            assert "error" in data
            assert data["error"]["type"] == "internal"
            assert data["error"]["code"] == "internal_error"
            assert data["error"]["correlation_id"] == "test-trace-12345"
