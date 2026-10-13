"""Physical schema for the identity context.

See ``docs/architecture/data-model.md`` for the ER overview and the rationale of
each constraint. Tables are SQLAlchemy Core objects: adapters write explicit SQL
so that locking, upserts and optimistic-concurrency guards stay visible.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from identity.domain.membership import MembershipRole, MembershipStatus
from identity.domain.service_account import ServiceAccountStatus
from identity.domain.tenant import TenantStatus
from identity.domain.user import UserStatus
from shared.infrastructure.db.base import metadata
from shared.infrastructure.db.types import TimestampCol, UuidCol

_TENANT_STATUS_LIST = ", ".join(f"'{status.value}'" for status in TenantStatus)
_USER_STATUS_LIST = ", ".join(f"'{status.value}'" for status in UserStatus)
_MEMBERSHIP_STATUS_LIST = ", ".join(f"'{status.value}'" for status in MembershipStatus)
_MEMBERSHIP_ROLE_LIST = ", ".join(f"'{role.value}'" for role in MembershipRole)
_SA_STATUS_LIST = ", ".join(f"'{status.value}'" for status in ServiceAccountStatus)

tenants = Table(
    "tenants",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    # The slug is the tenant's public, human-chosen identifier; it is immutable.
    Column("slug", String(64), nullable=False),
    Column("name", String(160), nullable=False),
    Column("status", String(16), nullable=False, server_default=TenantStatus.ACTIVE.value),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("updated_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("version", Integer, nullable=False),
    UniqueConstraint("slug", name="uq_tenants_slug"),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$'", name="slug_format"),
    CheckConstraint("length(btrim(name)) > 0", name="name_not_empty"),
    CheckConstraint(f"status IN ({_TENANT_STATUS_LIST})", name="status_valid"),
)

users = Table(
    "users",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column("email", String(255), nullable=False),
    Column("password_hash", String(255), nullable=False),
    Column("status", String(16), nullable=False, server_default=UserStatus.ACTIVE.value),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("updated_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("version", Integer, nullable=False, server_default="1"),
    UniqueConstraint("email", name="uq_users_email"),
    CheckConstraint("version >= 1", name="ck_users_version_positive"),
    CheckConstraint(f"status IN ({_USER_STATUS_LIST})", name="ck_users_status_valid"),
    CheckConstraint("length(btrim(email)) > 0", name="ck_users_email_not_empty"),
)

tenant_memberships = Table(
    "tenant_memberships",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column(
        "user_id",
        UuidCol,
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_tenant_memberships_user_id_users"),
        nullable=False,
    ),
    Column(
        "tenant_id",
        UuidCol,
        ForeignKey(
            "tenants.id",
            ondelete="RESTRICT",
            name="fk_tenant_memberships_tenant_id_tenants",
        ),
        nullable=False,
    ),
    Column("status", String(16), nullable=False, server_default=MembershipStatus.ACTIVE.value),
    Column("role", String(16), nullable=False, server_default=MembershipRole.MEMBER.value),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("updated_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("version", Integer, nullable=False, server_default="1"),
    UniqueConstraint("user_id", "tenant_id", name="uq_tenant_memberships_user_tenant"),
    CheckConstraint("version >= 1", name="ck_tenant_memberships_version_positive"),
    CheckConstraint(
        f"status IN ({_MEMBERSHIP_STATUS_LIST})", name="ck_tenant_memberships_status_valid"
    ),
    CheckConstraint(f"role IN ({_MEMBERSHIP_ROLE_LIST})", name="ck_tenant_memberships_role_valid"),
    Index("ix_tenant_memberships_tenant_id", "tenant_id"),
    Index("ix_tenant_memberships_user_id", "user_id"),
)

user_sessions = Table(
    "user_sessions",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column(
        "user_id",
        UuidCol,
        ForeignKey("users.id", ondelete="CASCADE", name="fk_user_sessions_user_id_users"),
        nullable=False,
    ),
    Column("token_hash", String(128), nullable=False),
    Column("family_id", UuidCol, nullable=False),
    Column("is_revoked", Boolean, nullable=False, server_default="false"),
    Column("expires_at", TimestampCol, nullable=False),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("rotated_at", TimestampCol, nullable=True),
    Index("ix_user_sessions_user_id", "user_id"),
    Index("ix_user_sessions_family_id", "family_id"),
    Index("ix_user_sessions_token_hash", "token_hash"),
)

service_accounts = Table(
    "service_accounts",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column(
        "tenant_id",
        UuidCol,
        ForeignKey("tenants.id", ondelete="RESTRICT", name="fk_service_accounts_tenant_id_tenants"),
        nullable=False,
    ),
    Column("name", String(160), nullable=False),
    Column("client_id", String(64), nullable=False),
    Column("secret_hash", String(255), nullable=False),
    Column("status", String(16), nullable=False, server_default=ServiceAccountStatus.ACTIVE.value),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("updated_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("version", Integer, nullable=False, server_default="1"),
    UniqueConstraint("client_id", name="uq_service_accounts_client_id"),
    CheckConstraint("version >= 1", name="ck_service_accounts_version_positive"),
    CheckConstraint(f"status IN ({_SA_STATUS_LIST})", name="ck_service_accounts_status_valid"),
    Index("ix_service_accounts_tenant_id", "tenant_id"),
)

roles = Table(
    "roles",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column(
        "tenant_id",
        UuidCol,
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_roles_tenant_id_tenants"),
        nullable=True,
    ),
    Column("name", String(64), nullable=False),
    Column("description", String(255), nullable=False, server_default=""),
    Column("is_system", Boolean, nullable=False, server_default="false"),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("updated_at", TimestampCol, nullable=False, server_default=func.now()),
    UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),
    CheckConstraint("version >= 1", name="ck_roles_version_positive"),
    CheckConstraint("length(btrim(name)) > 0", name="ck_roles_name_not_empty"),
    Index("ix_roles_tenant_id", "tenant_id"),
)

role_permissions = Table(
    "role_permissions",
    metadata,
    Column(
        "role_id",
        UuidCol,
        ForeignKey("roles.id", ondelete="CASCADE", name="fk_role_permissions_role_id_roles"),
        primary_key=True,
        nullable=False,
    ),
    Column("permission", String(128), primary_key=True, nullable=False),
    Index("ix_role_permissions_role_id", "role_id"),
)

membership_roles = Table(
    "membership_roles",
    metadata,
    Column(
        "membership_id",
        UuidCol,
        ForeignKey(
            "tenant_memberships.id",
            ondelete="CASCADE",
            name="fk_membership_roles_membership_id_tenant_memberships",
        ),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "role_id",
        UuidCol,
        ForeignKey("roles.id", ondelete="CASCADE", name="fk_membership_roles_role_id_roles"),
        primary_key=True,
        nullable=False,
    ),
    Index("ix_membership_roles_membership_id", "membership_id"),
    Index("ix_membership_roles_role_id", "role_id"),
)

service_account_scopes = Table(
    "service_account_scopes",
    metadata,
    Column(
        "service_account_id",
        UuidCol,
        ForeignKey(
            "service_accounts.id",
            ondelete="CASCADE",
            name="fk_service_account_scopes_sa_id_service_accounts",
        ),
        primary_key=True,
        nullable=False,
    ),
    Column("scope", String(128), primary_key=True, nullable=False),
    Index("ix_service_account_scopes_sa_id", "service_account_id"),
)

access_policies = Table(
    "access_policies",
    metadata,
    Column("id", UuidCol, primary_key=True, nullable=False),
    Column(
        "tenant_id",
        UuidCol,
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_access_policies_tenant_id_tenants"),
        nullable=True,
    ),
    Column("name", String(128), nullable=False),
    Column("description", String(255), nullable=False, server_default=""),
    Column("effect", String(16), nullable=False),
    Column("actions", JSONB, nullable=False),
    Column("resources", JSONB, nullable=False),
    Column("conditions", JSONB, nullable=True),
    Column("status", String(16), nullable=False, server_default="ACTIVE"),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("created_at", TimestampCol, nullable=False, server_default=func.now()),
    Column("updated_at", TimestampCol, nullable=False, server_default=func.now()),
    CheckConstraint("version >= 1", name="ck_access_policies_version_positive"),
    CheckConstraint("effect IN ('ALLOW', 'DENY')", name="ck_access_policies_effect_valid"),
    CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="ck_access_policies_status_valid"),
    CheckConstraint("length(btrim(name)) > 0", name="ck_access_policies_name_not_empty"),
    Index("ix_access_policies_tenant_id", "tenant_id"),
    Index("ix_access_policies_status", "status"),
)

__all__ = [
    "access_policies",
    "membership_roles",
    "role_permissions",
    "roles",
    "service_account_scopes",
    "service_accounts",
    "tenant_memberships",
    "tenants",
    "user_sessions",
    "users",
]
