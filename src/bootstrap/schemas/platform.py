"""Platform-level (tenant provisioning) schemas.

``POST /v1/tenants`` is the one Phase 1 write that is *not* tenant-scoped: there
is no tenant yet. It is a platform/onboarding operation, and its idempotency comes
from the unique tenant slug rather than from the tenant-scoped idempotency
registry. Authentication and tenant administration are Phase 4.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from identity.application.commands import TenantView
from identity.domain.tenant import TenantStatus


class RegisterTenantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
    name: str = Field(min_length=1, max_length=160)


class TenantResponse(BaseModel):
    id: UUID
    slug: str
    name: str
    status: TenantStatus
    created_at: datetime
    updated_at: datetime
    version: int
    invitation_token: str | None = None

    @classmethod
    def from_view(cls, view: TenantView, *, invitation_token: str | None = None) -> TenantResponse:
        return cls(
            id=view.id,
            slug=view.slug,
            name=view.name,
            status=view.status,
            created_at=view.created_at,
            updated_at=view.updated_at,
            version=view.version,
            invitation_token=invitation_token,
        )


__all__ = ["RegisterTenantRequest", "TenantResponse"]
