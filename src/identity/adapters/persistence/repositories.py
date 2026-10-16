"""SQLAlchemy implementations of identity persistence ports."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence

from sqlalchemy import delete, insert, or_, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from identity.adapters.persistence.tables import (
    access_policies,
    membership_roles,
    role_permissions,
    roles,
    service_account_scopes,
    service_accounts,
    tenant_memberships,
    tenants,
    user_sessions,
    users,
)
from identity.domain.membership import MembershipRole, MembershipStatus, TenantMembership
from identity.domain.policy import AccessPolicy, PolicyEffect, PolicyStatus
from identity.domain.role import Role
from identity.domain.service_account import ServiceAccount, ServiceAccountStatus
from identity.domain.session import UserSession
from identity.domain.tenant import Tenant, TenantStatus
from identity.domain.user import User, UserStatus
from shared.domain.identifiers import (
    MembershipId,
    PolicyId,
    RoleId,
    ServiceAccountId,
    TenantId,
    UserId,
)
from shared.infrastructure.db.execution import execute, execute_guarded_update

_TENANT_DUPLICATES: Mapping[str, tuple[str, str]] = {
    "uq_tenants_slug": ("tenant", "slug"),
}

_USER_DUPLICATES: Mapping[str, tuple[str, str]] = {
    "uq_users_email": ("user", "email"),
}

_MEMBERSHIP_DUPLICATES: Mapping[str, tuple[str, str]] = {
    "uq_tenant_memberships_user_tenant": ("tenant_membership", "user_id"),
}

_SA_DUPLICATES: Mapping[str, tuple[str, str]] = {
    "uq_service_accounts_client_id": ("service_account", "client_id"),
}

_ROLE_DUPLICATES: Mapping[str, tuple[str, str]] = {
    "uq_roles_tenant_name": ("role", "name"),
}


# ===========================================================================
# Tenant
# ===========================================================================
class SqlAlchemyTenantRepository:
    """Tenant persistence adapter."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, tenant: Tenant) -> None:
        await execute(
            self._session,
            insert(tenants).values(
                id=tenant.id,
                slug=tenant.slug,
                name=tenant.name,
                status=tenant.status.value,
                created_at=tenant.created_at,
                updated_at=tenant.updated_at,
                version=tenant.version,
            ),
            duplicates=_TENANT_DUPLICATES,
        )

    async def get(self, tenant_id: TenantId) -> Tenant | None:
        result = await self._session.execute(select(tenants).where(tenants.c.id == tenant_id))
        row = result.mappings().first()
        return _tenant_to_domain(row) if row is not None else None

    async def get_by_slug(self, slug: str) -> Tenant | None:
        result = await self._session.execute(select(tenants).where(tenants.c.slug == slug))
        row = result.mappings().first()
        return _tenant_to_domain(row) if row is not None else None

    async def save(self, tenant: Tenant, *, expected_version: int) -> None:
        statement = (
            update(tenants)
            .where(
                tenants.c.id == tenant.id,
                tenants.c.version == expected_version,
            )
            .values(
                name=tenant.name,
                status=tenant.status.value,
                updated_at=tenant.updated_at,
                version=expected_version + 1,
            )
        )
        await execute_guarded_update(
            self._session,
            statement,
            resource_type="tenant",
            resource_id=tenant.id,
            expected_version=expected_version,
        )


def _tenant_to_domain(row: RowMapping) -> Tenant:
    return Tenant(
        id=TenantId(row["id"]),
        slug=row["slug"],
        name=row["name"],
        status=TenantStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


# ===========================================================================
# User
# ===========================================================================
class SqlAlchemyUserRepository:
    """User persistence adapter."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user: User) -> None:
        await execute(
            self._session,
            insert(users).values(
                id=user.id,
                email=user.email,
                password_hash=user.password_hash,
                status=user.status.value,
                created_at=user.created_at,
                updated_at=user.updated_at,
                version=user.version,
            ),
            duplicates=_USER_DUPLICATES,
        )

    async def get(self, user_id: UserId) -> User | None:
        result = await self._session.execute(select(users).where(users.c.id == user_id))
        row = result.mappings().first()
        return _user_to_domain(row) if row is not None else None

    async def get_by_email(self, email: str) -> User | None:
        normalized = email.strip().lower()
        result = await self._session.execute(select(users).where(users.c.email == normalized))
        row = result.mappings().first()
        return _user_to_domain(row) if row is not None else None

    async def save(self, user: User, *, expected_version: int) -> None:
        statement = (
            update(users)
            .where(
                users.c.id == user.id,
                users.c.version == expected_version,
            )
            .values(
                password_hash=user.password_hash,
                status=user.status.value,
                updated_at=user.updated_at,
                version=expected_version + 1,
            )
        )
        await execute_guarded_update(
            self._session,
            statement,
            resource_type="user",
            resource_id=user.id,
            expected_version=expected_version,
        )


def _user_to_domain(row: RowMapping) -> User:
    return User(
        id=UserId(row["id"]),
        email=row["email"],
        password_hash=row["password_hash"],
        status=UserStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


# ===========================================================================
# Membership
# ===========================================================================
class SqlAlchemyMembershipRepository:
    """TenantMembership persistence adapter."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, membership: TenantMembership) -> None:
        await execute(
            self._session,
            insert(tenant_memberships).values(
                id=membership.id,
                user_id=membership.user_id,
                tenant_id=membership.tenant_id,
                status=membership.status.value,
                role=membership.role.value,
                created_at=membership.created_at,
                updated_at=membership.updated_at,
                version=membership.version,
            ),
            duplicates=_MEMBERSHIP_DUPLICATES,
        )
        if membership.roles:
            role_ids_query = select(roles.c.id).where(
                roles.c.name.in_(list(membership.roles)),
                or_(roles.c.tenant_id == membership.tenant_id, roles.c.tenant_id.is_(None)),
            )
            res = await self._session.execute(role_ids_query)
            for rid in res.scalars().all():
                await self._session.execute(
                    insert(membership_roles).values(
                        membership_id=membership.id,
                        role_id=rid,
                    )
                )

    async def get(self, membership_id: MembershipId) -> TenantMembership | None:
        result = await self._session.execute(
            select(tenant_memberships).where(tenant_memberships.c.id == membership_id)
        )
        row = result.mappings().first()
        if row is None:
            return None
        role_result = await self._session.execute(
            select(roles.c.name)
            .select_from(membership_roles.join(roles, membership_roles.c.role_id == roles.c.id))
            .where(membership_roles.c.membership_id == membership_id)
        )
        role_names = role_result.scalars().all()
        return _membership_to_domain(row, roles=role_names)

    async def get_by_user_and_tenant(
        self, user_id: UserId, tenant_id: TenantId
    ) -> TenantMembership | None:
        result = await self._session.execute(
            select(tenant_memberships).where(
                tenant_memberships.c.user_id == user_id,
                tenant_memberships.c.tenant_id == tenant_id,
            )
        )
        row = result.mappings().first()
        if row is None:
            return None
        role_result = await self._session.execute(
            select(roles.c.name)
            .select_from(membership_roles.join(roles, membership_roles.c.role_id == roles.c.id))
            .where(membership_roles.c.membership_id == row["id"])
        )
        role_names = role_result.scalars().all()
        return _membership_to_domain(row, roles=role_names)

    async def list_for_user(self, user_id: UserId) -> Sequence[TenantMembership]:
        result = await self._session.execute(
            select(tenant_memberships)
            .where(tenant_memberships.c.user_id == user_id)
            .order_by(tenant_memberships.c.created_at.asc())
        )
        rows = result.mappings().all()
        if not rows:
            return []
        mem_ids = [r["id"] for r in rows]
        role_result = await self._session.execute(
            select(membership_roles.c.membership_id, roles.c.name)
            .select_from(membership_roles.join(roles, membership_roles.c.role_id == roles.c.id))
            .where(membership_roles.c.membership_id.in_(mem_ids))
        )
        roles_by_mem: dict[uuid.UUID, list[str]] = {}
        for mid, rname in role_result.all():
            roles_by_mem.setdefault(mid, []).append(rname)
        return [_membership_to_domain(row, roles=roles_by_mem.get(row["id"], ())) for row in rows]

    async def list_for_tenant(self, tenant_id: TenantId) -> Sequence[TenantMembership]:
        result = await self._session.execute(
            select(tenant_memberships)
            .where(tenant_memberships.c.tenant_id == tenant_id)
            .order_by(tenant_memberships.c.created_at.asc())
        )
        rows = result.mappings().all()
        if not rows:
            return []
        mem_ids = [r["id"] for r in rows]
        role_result = await self._session.execute(
            select(membership_roles.c.membership_id, roles.c.name)
            .select_from(membership_roles.join(roles, membership_roles.c.role_id == roles.c.id))
            .where(membership_roles.c.membership_id.in_(mem_ids))
        )
        roles_by_mem: dict[uuid.UUID, list[str]] = {}
        for mid, rname in role_result.all():
            roles_by_mem.setdefault(mid, []).append(rname)
        return [_membership_to_domain(row, roles=roles_by_mem.get(row["id"], ())) for row in rows]

    async def save(self, membership: TenantMembership, *, expected_version: int) -> None:
        statement = (
            update(tenant_memberships)
            .where(
                tenant_memberships.c.id == membership.id,
                tenant_memberships.c.version == expected_version,
            )
            .values(
                status=membership.status.value,
                role=membership.role.value,
                updated_at=membership.updated_at,
                version=expected_version + 1,
            )
        )
        await execute_guarded_update(
            self._session,
            statement,
            resource_type="tenant_membership",
            resource_id=membership.id,
            expected_version=expected_version,
        )
        await self._session.execute(
            delete(membership_roles).where(membership_roles.c.membership_id == membership.id)
        )
        if membership.roles:
            role_names = [r.strip().upper() for r in membership.roles]
            from identity.domain.role import RESERVED_ROLE_NAMES

            role_ids_query = select(roles.c.id, roles.c.name, roles.c.is_system).where(
                roles.c.name.in_(role_names),
                or_(
                    roles.c.tenant_id == membership.tenant_id,
                    roles.c.is_system.is_(True),
                    roles.c.tenant_id.is_(None),
                ),
            )
            res = await self._session.execute(role_ids_query)
            found_roles = res.all()
            resolved_rids: set[uuid.UUID] = set()
            for rname in role_names:
                matching = [r for r in found_roles if r.name == rname]
                if not matching:
                    continue
                if rname in RESERVED_ROLE_NAMES:
                    chosen = next((r for r in matching if r.is_system), matching[0])
                else:
                    chosen = next((r for r in matching if not r.is_system), matching[0])
                resolved_rids.add(chosen.id)

            for rid in resolved_rids:
                await self._session.execute(
                    insert(membership_roles).values(
                        membership_id=membership.id,
                        role_id=rid,
                    )
                )


def _membership_to_domain(row: RowMapping, roles: Sequence[str] = ()) -> TenantMembership:
    return TenantMembership(
        id=MembershipId(row["id"]),
        user_id=UserId(row["user_id"]),
        tenant_id=TenantId(row["tenant_id"]),
        status=MembershipStatus(row["status"]),
        role=MembershipRole(row["role"]) if row.get("role") else MembershipRole.MEMBER,
        roles=roles,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


# ===========================================================================
# Session
# ===========================================================================
class SqlAlchemySessionRepository:
    """UserSession persistence adapter."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, session: UserSession) -> None:
        await self._session.execute(
            insert(user_sessions).values(
                id=session.id,
                user_id=session.user_id,
                token_hash=session.token_hash,
                family_id=session.family_id,
                is_revoked=session.is_revoked,
                expires_at=session.expires_at,
                created_at=session.created_at,
                rotated_at=session.rotated_at,
            )
        )

    async def get(self, session_id: uuid.UUID) -> UserSession | None:
        result = await self._session.execute(
            select(user_sessions).where(user_sessions.c.id == session_id)
        )
        row = result.mappings().first()
        return _session_to_domain(row) if row is not None else None

    async def get_by_token_hash(
        self, token_hash: str, *, for_update: bool = False
    ) -> UserSession | None:
        stmt = select(user_sessions).where(user_sessions.c.token_hash == token_hash)
        if for_update:
            stmt = stmt.with_for_update()
        result = await self._session.execute(stmt)
        row = result.mappings().first()
        return _session_to_domain(row) if row is not None else None

    async def revoke_family(self, family_id: uuid.UUID) -> None:
        await self._session.execute(
            update(user_sessions)
            .where(user_sessions.c.family_id == family_id)
            .values(is_revoked=True)
        )

    async def revoke_all_for_user(self, user_id: UserId) -> None:
        await self._session.execute(
            update(user_sessions).where(user_sessions.c.user_id == user_id).values(is_revoked=True)
        )

    async def save(self, session: UserSession) -> None:
        await self._session.execute(
            update(user_sessions)
            .where(user_sessions.c.id == session.id)
            .values(
                is_revoked=session.is_revoked,
                rotated_at=session.rotated_at,
            )
        )


def _session_to_domain(row: RowMapping) -> UserSession:
    return UserSession(
        id=row["id"],
        user_id=UserId(row["user_id"]),
        token_hash=row["token_hash"],
        family_id=row["family_id"],
        is_revoked=row["is_revoked"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
        rotated_at=row["rotated_at"],
    )


# ===========================================================================
# Service Account
# ===========================================================================
class SqlAlchemyServiceAccountRepository:
    """ServiceAccount persistence adapter."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, service_account: ServiceAccount) -> None:
        await execute(
            self._session,
            insert(service_accounts).values(
                id=service_account.id,
                tenant_id=service_account.tenant_id,
                name=service_account.name,
                client_id=service_account.client_id,
                secret_hash=service_account.secret_hash,
                status=service_account.status.value,
                created_at=service_account.created_at,
                updated_at=service_account.updated_at,
                version=service_account.version,
            ),
            duplicates=_SA_DUPLICATES,
        )
        for scope in service_account.scopes:
            await self._session.execute(
                insert(service_account_scopes).values(
                    service_account_id=service_account.id,
                    scope=scope,
                )
            )

    async def get(self, service_account_id: ServiceAccountId) -> ServiceAccount | None:
        result = await self._session.execute(
            select(service_accounts).where(service_accounts.c.id == service_account_id)
        )
        row = result.mappings().first()
        if row is None:
            return None
        scopes_res = await self._session.execute(
            select(service_account_scopes.c.scope).where(
                service_account_scopes.c.service_account_id == service_account_id
            )
        )
        scopes = [r[0] for r in scopes_res.all()]
        return _sa_to_domain(row, scopes)

    async def get_by_client_id(self, client_id: str) -> ServiceAccount | None:
        result = await self._session.execute(
            select(service_accounts).where(service_accounts.c.client_id == client_id)
        )
        row = result.mappings().first()
        if row is None:
            return None
        scopes_res = await self._session.execute(
            select(service_account_scopes.c.scope).where(
                service_account_scopes.c.service_account_id == row["id"]
            )
        )
        scopes = [r[0] for r in scopes_res.all()]
        return _sa_to_domain(row, scopes)

    async def list_for_tenant(self, tenant_id: TenantId) -> Sequence[ServiceAccount]:
        result = await self._session.execute(
            select(service_accounts)
            .where(service_accounts.c.tenant_id == tenant_id)
            .order_by(service_accounts.c.created_at.asc())
        )
        sa_rows = result.mappings().all()
        if not sa_rows:
            return []
        sa_ids = [r["id"] for r in sa_rows]
        scopes_res = await self._session.execute(
            select(
                service_account_scopes.c.service_account_id, service_account_scopes.c.scope
            ).where(service_account_scopes.c.service_account_id.in_(sa_ids))
        )
        scopes_by_sa: dict[uuid.UUID, list[str]] = {}
        for sa_id, sc in scopes_res.all():
            scopes_by_sa.setdefault(sa_id, []).append(sc)
        return [_sa_to_domain(r, scopes_by_sa.get(r["id"], [])) for r in sa_rows]

    async def save(self, service_account: ServiceAccount, *, expected_version: int) -> None:
        statement = (
            update(service_accounts)
            .where(
                service_accounts.c.id == service_account.id,
                service_accounts.c.version == expected_version,
            )
            .values(
                name=service_account.name,
                secret_hash=service_account.secret_hash,
                status=service_account.status.value,
                updated_at=service_account.updated_at,
                version=expected_version + 1,
            )
        )
        await execute_guarded_update(
            self._session,
            statement,
            resource_type="service_account",
            resource_id=service_account.id,
            expected_version=expected_version,
        )
        # Replace scopes
        await self._session.execute(
            delete(service_account_scopes).where(
                service_account_scopes.c.service_account_id == service_account.id
            )
        )
        for scope in service_account.scopes:
            await self._session.execute(
                insert(service_account_scopes).values(
                    service_account_id=service_account.id,
                    scope=scope,
                )
            )


def _sa_to_domain(row: RowMapping, scopes: Sequence[str] = ()) -> ServiceAccount:
    return ServiceAccount(
        id=ServiceAccountId(row["id"]),
        tenant_id=TenantId(row["tenant_id"]),
        name=row["name"],
        client_id=row["client_id"],
        secret_hash=row["secret_hash"],
        status=ServiceAccountStatus(row["status"]),
        scopes=scopes,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


# ===========================================================================
# Roles (Phase 4.2)
# ===========================================================================
class SqlAlchemyRoleRepository:
    """Role persistence adapter."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, role: Role) -> None:
        await execute(
            self._session,
            insert(roles).values(
                id=role.id,
                tenant_id=role.tenant_id,
                name=role.name,
                description=role.description,
                is_system=role.is_system,
                created_at=role.created_at,
                updated_at=role.updated_at,
                version=role.version,
            ),
            duplicates=_ROLE_DUPLICATES,
        )
        for perm in role.permissions:
            await self._session.execute(
                insert(role_permissions).values(
                    role_id=role.id,
                    permission=perm,
                )
            )

    async def get(self, role_id: RoleId) -> Role | None:
        result = await self._session.execute(select(roles).where(roles.c.id == role_id))
        row = result.mappings().first()
        if row is None:
            return None
        perms_res = await self._session.execute(
            select(role_permissions.c.permission).where(role_permissions.c.role_id == role_id)
        )
        perms = [r[0] for r in perms_res.all()]
        return _role_to_domain(row, perms)

    async def get_by_name(self, name: str, tenant_id: TenantId | None = None) -> Role | None:
        clean_name = name.strip().upper()
        from identity.domain.role import RESERVED_ROLE_NAMES

        # System roles have absolute precedence for reserved names
        if clean_name in RESERVED_ROLE_NAMES:
            query = select(roles).where(roles.c.name == clean_name, roles.c.is_system.is_(True))
            result = await self._session.execute(query)
            row = result.mappings().first()
            if row is None:
                return None
            perms_res = await self._session.execute(
                select(role_permissions.c.permission).where(role_permissions.c.role_id == row["id"])
            )
            perms = [r[0] for r in perms_res.all()]
            return _role_to_domain(row, perms)

        # Tenant role first, then fallback to system role
        conditions = [roles.c.name == clean_name]
        if tenant_id is not None:
            conditions.append((roles.c.tenant_id == tenant_id) | (roles.c.is_system.is_(True)))
        else:
            conditions.append(roles.c.is_system.is_(True))

        query = select(roles).where(*conditions).order_by(roles.c.tenant_id.desc().nullslast())
        result = await self._session.execute(query)
        row = result.mappings().first()
        if row is None:
            return None
        perms_res = await self._session.execute(
            select(role_permissions.c.permission).where(role_permissions.c.role_id == row["id"])
        )
        perms = [r[0] for r in perms_res.all()]
        return _role_to_domain(row, perms)

    async def list_for_tenant(
        self, tenant_id: TenantId | None, include_system: bool = True
    ) -> Sequence[Role]:
        cond = roles.c.tenant_id == tenant_id
        if include_system:
            cond = cond | (roles.c.is_system.is_(True))
        result = await self._session.execute(select(roles).where(cond).order_by(roles.c.name.asc()))
        role_rows = result.mappings().all()
        if not role_rows:
            return []
        role_ids = [r["id"] for r in role_rows]
        perms_res = await self._session.execute(
            select(role_permissions.c.role_id, role_permissions.c.permission).where(
                role_permissions.c.role_id.in_(role_ids)
            )
        )
        perms_by_role: dict[uuid.UUID, list[str]] = {}
        for r_id, p in perms_res.all():
            perms_by_role.setdefault(r_id, []).append(p)
        return [_role_to_domain(r, perms_by_role.get(r["id"], [])) for r in role_rows]

    async def save(self, role: Role, *, expected_version: int) -> None:
        statement = (
            update(roles)
            .where(roles.c.id == role.id, roles.c.version == expected_version)
            .values(
                description=role.description,
                updated_at=role.updated_at,
                version=expected_version + 1,
            )
        )
        await execute_guarded_update(
            self._session,
            statement,
            resource_type="role",
            resource_id=role.id,
            expected_version=expected_version,
        )
        await self._session.execute(
            delete(role_permissions).where(role_permissions.c.role_id == role.id)
        )
        for perm in role.permissions:
            await self._session.execute(
                insert(role_permissions).values(
                    role_id=role.id,
                    permission=perm,
                )
            )

    async def delete(self, role_id: RoleId) -> None:
        await self._session.execute(
            delete(roles).where(roles.c.id == role_id, roles.c.is_system.is_(False))
        )


def _role_to_domain(row: RowMapping, permissions: Sequence[str] = ()) -> Role:
    return Role(
        id=RoleId(row["id"]),
        tenant_id=TenantId(row["tenant_id"]) if row.get("tenant_id") else None,
        name=row["name"],
        description=row["description"],
        is_system=bool(row["is_system"]),
        permissions=permissions,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


# ===========================================================================
# Policies (Phase 4.2)
# ===========================================================================
class SqlAlchemyPolicyRepository:
    """AccessPolicy persistence adapter."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, policy: AccessPolicy) -> None:
        await execute(
            self._session,
            insert(access_policies).values(
                id=policy.id,
                tenant_id=policy.tenant_id,
                name=policy.name,
                description=policy.description,
                version=policy.version,
                effect=policy.effect.value,
                actions=list(policy.actions),
                resources=list(policy.resources),
                conditions=policy.conditions,
                status=policy.status.value,
                created_at=policy.created_at,
                updated_at=policy.updated_at,
            ),
        )

    async def get(self, policy_id: PolicyId) -> AccessPolicy | None:
        result = await self._session.execute(
            select(access_policies).where(access_policies.c.id == policy_id)
        )
        row = result.mappings().first()
        return _policy_to_domain(row) if row is not None else None

    async def list_for_tenant(
        self, tenant_id: TenantId | None, active_only: bool = False
    ) -> Sequence[AccessPolicy]:
        cond = (access_policies.c.tenant_id == tenant_id) | (access_policies.c.tenant_id.is_(None))
        if active_only:
            cond = cond & (access_policies.c.status == "ACTIVE")
        result = await self._session.execute(
            select(access_policies).where(cond).order_by(access_policies.c.created_at.asc())
        )
        return [_policy_to_domain(row) for row in result.mappings().all()]

    async def save(self, policy: AccessPolicy, *, expected_version: int) -> None:
        statement = (
            update(access_policies)
            .where(
                access_policies.c.id == policy.id,
                access_policies.c.version == expected_version,
            )
            .values(
                name=policy.name,
                description=policy.description,
                effect=policy.effect.value,
                actions=list(policy.actions),
                resources=list(policy.resources),
                conditions=policy.conditions,
                status=policy.status.value,
                updated_at=policy.updated_at,
                version=expected_version + 1,
            )
        )
        await execute_guarded_update(
            self._session,
            statement,
            resource_type="access_policy",
            resource_id=policy.id,
            expected_version=expected_version,
        )

    async def delete(self, policy_id: PolicyId) -> None:
        await self._session.execute(
            delete(access_policies).where(access_policies.c.id == policy_id)
        )


def _policy_to_domain(row: RowMapping) -> AccessPolicy:
    actions_raw = json.loads(row["actions"]) if isinstance(row["actions"], str) else row["actions"]
    resources_raw = (
        json.loads(row["resources"]) if isinstance(row["resources"], str) else row["resources"]
    )
    conditions_raw = (
        json.loads(row["conditions"])
        if isinstance(row.get("conditions"), str)
        else row.get("conditions")
    )
    return AccessPolicy(
        id=PolicyId(row["id"]),
        tenant_id=TenantId(row["tenant_id"]) if row.get("tenant_id") else None,
        name=row["name"],
        description=row["description"],
        effect=PolicyEffect(row["effect"]),
        actions=actions_raw,
        resources=resources_raw,
        conditions=conditions_raw,
        status=PolicyStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


__all__ = [
    "SqlAlchemyMembershipRepository",
    "SqlAlchemyPolicyRepository",
    "SqlAlchemyRoleRepository",
    "SqlAlchemyServiceAccountRepository",
    "SqlAlchemySessionRepository",
    "SqlAlchemyTenantRepository",
    "SqlAlchemyUserRepository",
]
