"""Access management schemas (RBAC & ABAC)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from identity.application.commands import PolicyView, RoleView


class PermissionResponse(BaseModel):
    name: str
    description: str
    category: str


class PermissionListResponse(BaseModel):
    permissions: list[PermissionResponse]
    total: int = 0


class CreateRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=255)
    permissions: list[str] = Field(default_factory=list)


class RoleResponse(BaseModel):
    id: UUID
    tenant_id: UUID | None
    name: str
    description: str
    is_system: bool
    permissions: list[str]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_view(cls, view: RoleView) -> RoleResponse:
        return cls(
            id=UUID(view.id),
            tenant_id=UUID(view.tenant_id) if view.tenant_id else None,
            name=view.name,
            description=view.description,
            is_system=view.is_system,
            permissions=sorted(view.permissions),
            created_at=view.created_at,
            updated_at=view.updated_at,
        )


class AssignRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_name: str = Field(min_length=1, max_length=64)


class CreatePolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=255)
    effect: str = Field(pattern="^(ALLOW|DENY)$")
    actions: list[str] = Field(min_length=1)
    resources: list[str] = Field(default_factory=list)
    resource_types: list[str] = Field(default_factory=list)
    conditions: dict[str, Any] = Field(default_factory=dict)
    condition: dict[str, Any] = Field(default_factory=dict)


class UpdatePolicyStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(pattern="^(ACTIVE|INACTIVE)$")


class PolicyResponse(BaseModel):
    id: UUID
    tenant_id: UUID | None
    name: str
    description: str
    effect: str
    actions: list[str]
    resource_types: list[str]
    condition: dict[str, Any]
    status: str
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_view(cls, view: PolicyView) -> PolicyResponse:
        return cls(
            id=UUID(view.id),
            tenant_id=UUID(view.tenant_id) if view.tenant_id else None,
            name=view.name,
            description=view.description,
            effect=view.effect,
            actions=sorted(view.actions),
            resource_types=sorted(view.resources),
            condition=view.conditions or {},
            status=view.status,
            version=view.version,
            created_at=view.created_at,
            updated_at=view.updated_at,
        )


class DecisionEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    resource_id: str | None = None
    resource_tenant_id: UUID | None = None
    resource_attributes: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, Any] = Field(default_factory=dict)


class DecisionEvaluationResponse(BaseModel):
    allowed: bool
    reason: str
    reason_code: str
    action: str
    resource_type: str
    policy_id: UUID | None = None
    missing_permissions: list[str] = Field(default_factory=list)
