"""Base classes for application services (use cases).

Responsibilities that every command use case shares - and that therefore live
here *once*, rather than being re-implemented per endpoint:

* **transaction boundary**: one unit of work per command;
* **idempotency**: claim, replay, complete (see
  :mod:`shared.application.idempotency` and ADR-0006);
* **domain-event hand-off**: events collected by aggregates are pulled after a
  successful execution, so no caller can forget them.

Business rules stay in the domain: use cases orchestrate, they do not decide.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, ClassVar, Protocol, TypeVar, runtime_checkable

from shared.application.context import RequestContext
from shared.application.errors import IdempotencyKeyRequired, InvalidRequest
from shared.application.events import EventDispatcher, NullEventDispatcher
from shared.application.idempotency import (
    IdempotencyOutcome,
    IdempotencyRecord,
    IdempotencyStore,
    fingerprint,
)
from shared.application.unit_of_work import TransactionScope
from shared.domain.clock import Clock
from shared.domain.events import DomainEvent

#: Seven days is the platform default retention for idempotency records: long
#: enough to survive client retries across restarts, short enough to keep the
#: table small. Cleanup is a Phase 2 job; nothing in Phase 1 deletes records.
DEFAULT_IDEMPOTENCY_RETENTION = timedelta(days=7)

ViewT = TypeVar("ViewT")
CommandT = TypeVar("CommandT")


@runtime_checkable
class ApplicationUnitOfWork(TransactionScope, Protocol):
    """Minimal unit of work every use case needs.

    Context-specific unit-of-work protocols extend this with the repositories
    that context composes.
    """

    idempotency: IdempotencyStore


UowT = TypeVar("UowT", bound=ApplicationUnitOfWork)


@dataclass(frozen=True, slots=True)
class Outcome[ViewT]:
    """Result of a command use case."""

    view: ViewT
    status_code: int
    #: True when the request was answered from a stored idempotency record.
    replayed: bool = False


class Command(Protocol):
    """Marker: a command carries its ``RequestContext``.

    Declared as a read-only property (not a mutable attribute) so that the frozen
    dataclasses every command is built from satisfy it structurally.
    """

    @property
    def context(self) -> RequestContext:
        """The per-request context this command was issued with."""
        ...


@dataclass(frozen=True, slots=True)
class Executed[ViewT]:
    """What ``_execute`` returns: the view plus the resource it created/changed.

    ``events`` carries the domain events collected from the touched aggregates.
    The base class hands them to the :class:`EventDispatcher` *inside* the
    transaction, which is the seam Phase 2 replaces with the transactional
    outbox writer.
    """

    view: ViewT
    resource_type: str
    #: ``None`` when the operation produced no durable resource (for example a
    #: PARTIAL reservation that could hold nothing). Such operations are not
    #: recorded as idempotent results: replaying them is safe because they had no
    #: side effect in the first place.
    resource_id: Any | None
    events: tuple[DomainEvent, ...] = ()


class UseCase[CommandT: Command, ViewT, UowT: ApplicationUnitOfWork](ABC):
    """Base class for command (mutation) use cases."""

    #: Stable operation name recorded on idempotency records (never rename it
    #: without a migration: it is part of the stored audit trail).
    operation: ClassVar[str] = "unknown"
    #: HTTP status returned on first execution and on replay.
    success_status: ClassVar[int] = 200
    #: When true, the endpoint refuses to run without an ``Idempotency-Key``.
    idempotency_required: ClassVar[bool] = False
    #: When true, the use case bypasses PostgreSQL RLS (e.g. platform authentication).
    bypass_rls: ClassVar[bool] = False

    permission: ClassVar[str | None] = None
    resource_type: ClassVar[str | None] = None

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UowT],
        clock: Clock,
        events: EventDispatcher | None = None,
        idempotency_retention: timedelta = DEFAULT_IDEMPOTENCY_RETENTION,
        authorizer: Any | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._events: EventDispatcher = events or NullEventDispatcher()
        self._idempotency_retention = idempotency_retention
        self._authorizer = authorizer

    @staticmethod
    def _validate_tenant_consistency(command: Any, context: RequestContext | None) -> None:
        """Ensure tenant context is authoritative and fail-closed against cross-tenant tampering."""
        if context is None:
            return
        from shared.application.errors import Forbidden

        cmd_tenant = getattr(command, "tenant_id", None)
        ctx_tenant = getattr(context, "tenant_id", None)
        if cmd_tenant is not None and ctx_tenant is not None and str(cmd_tenant) != str(ctx_tenant):
            raise Forbidden(
                "tenant mismatch between command and execution context",
                details={"command_tenant": str(cmd_tenant), "context_tenant": str(ctx_tenant)},
            )

        principal = getattr(context, "principal", None)
        if principal is not None and getattr(principal, "active_tenant_id", None) is not None:
            active_tenant = str(principal.active_tenant_id)
            if cmd_tenant is not None and str(cmd_tenant) != active_tenant:
                raise Forbidden(
                    "tenant mismatch between command and principal active tenant",
                    details={"command_tenant": str(cmd_tenant), "principal_tenant": active_tenant},
                )
            if ctx_tenant is not None and str(ctx_tenant) != active_tenant:
                raise Forbidden(
                    "tenant mismatch between context and principal active tenant",
                    details={"context_tenant": str(ctx_tenant), "principal_tenant": active_tenant},
                )

    async def _authorize(
        self,
        context: RequestContext,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        resource_tenant_id: Any | None = None,
        resource_attributes: dict[str, Any] | None = None,
    ) -> None:
        """Evaluate authorization through the configured AuthorizationPort."""
        if self._authorizer is None:
            return

        from identity.ports.authorization import AuthorizationRequest, Principal
        from shared.application.context import ActorType
        from shared.application.errors import Forbidden

        principal: Principal | None = getattr(context, "principal", None)
        if principal is None and context.actor_type == ActorType.SYSTEM:
            principal = Principal(id="system", type=ActorType.SYSTEM)
        elif principal is None:
            from shared.application.errors import Unauthorized
            raise Unauthorized(
                "authentication required: no authenticated principal on execution context",
                details={"action": action, "resource_type": resource_type},
            )

        subject_attrs: dict[str, Any] = {}
        if getattr(principal, "attributes", None):
            subject_attrs.update(principal.attributes)

        env: dict[str, Any] = {"correlation_id": context.correlation_id}
        if getattr(context, "causation_id", None):
            env["causation_id"] = context.causation_id

        req = AuthorizationRequest(
            action=action,
            resource_type=resource_type,
            principal_id=principal.id,
            principal_type=principal.type,
            tenant_id=context.tenant_id,
            roles=principal.roles,
            permissions=principal.permissions,
            subject_attributes=subject_attrs,
            resource_id=resource_id,
            resource_tenant_id=resource_tenant_id or context.tenant_id,
            resource_attributes=resource_attributes or {},
            environment=env,
        )
        decision = await self._authorizer.authorize(req)
        if not decision.allowed:
            raise Forbidden(
                f"operation '{action}' not authorized on {resource_type}",
                details={
                    "reason": decision.reason.value,
                    "reason_details": decision.reason_details,
                },
            )

    async def _authorize_replay(
        self, uow: UowT, record: IdempotencyRecord, command: CommandT
    ) -> None:
        """Verify the replaying caller is authorized before returning idempotent replay result."""
        action = getattr(self, "permission", None) or self.operation
        res_type = getattr(self, "resource_type", None) or record.resource_type or "resource"
        tenant_id = self.tenant_of(command)
        await self._authorize(
            command.context,
            action=action,
            resource_type=res_type,
            resource_id=str(record.resource_id) if record.resource_id is not None else None,
            resource_tenant_id=tenant_id,
        )

    # -- public API --------------------------------------------------------
    async def __call__(self, command: CommandT) -> Outcome[ViewT]:
        context: RequestContext = command.context
        self._validate_tenant_consistency(command, context)
        tenant_id = self.tenant_of(command)

        # Early authorization check before modifying database or acquiring idempotency claims
        if self.permission is not None and self._authorizer is not None:
            await self._authorize(
                context,
                action=self.permission,
                resource_type=self.resource_type or "resource",
                resource_tenant_id=tenant_id,
            )

        key = context.idempotency_key
        if self.idempotency_required and not key:
            raise IdempotencyKeyRequired(
                "this operation requires an Idempotency-Key header",
                details={"operation": self.operation},
            )
        if key is not None and tenant_id is None:
            raise InvalidRequest(
                "idempotency keys are only supported for tenant-scoped operations",
                details={"operation": self.operation},
            )

        now = self._clock.now()
        async with self._uow_factory() as uow:
            scope = getattr(uow, "_scope", None)
            if scope is not None and hasattr(scope, "set_tenant_context"):
                active_tid = tenant_id or context.tenant_id
                active_uid = str(context.actor_id) if getattr(context, "actor_id", None) else None
                try:
                    await scope.set_tenant_context(active_tid, user_id=active_uid)
                except TypeError:
                    await scope.set_tenant_context(active_tid)

            claim = None
            if key is not None and tenant_id is not None:
                claim = await uow.idempotency.claim(
                    tenant_id=tenant_id,
                    key=key,
                    operation=self.operation,
                    request_hash=self.fingerprint(command),
                    now=now,
                    expires_at=now + self._idempotency_retention,
                )
                if claim.outcome is IdempotencyOutcome.REPLAY:
                    record = claim.record
                    await self._authorize_replay(uow, record, command)
                    view = await self._reload(uow, record, command)
                    return Outcome(
                        view=view,
                        status_code=record.response_status or self.success_status,
                        replayed=True,
                    )

            executed = await self._execute(uow, command)
            if executed.events:
                # Phase 2: this call writes to the transactional outbox inside the
                # very same transaction, so a committed change and its events can
                # never diverge.
                try:
                    await self._events.dispatch(executed.events, context=context)
                except TypeError:
                    await self._events.dispatch(executed.events)
            if claim is not None and key is not None and tenant_id is not None:
                if executed.resource_id is None:
                    # No durable resource was produced: drop the claim so the
                    # client may retry the same key later without being blocked
                    # by a stale in-progress record.
                    await uow.idempotency.abandon(tenant_id=tenant_id, key=key, now=now)
                else:
                    await uow.idempotency.complete(
                        tenant_id=tenant_id,
                        key=key,
                        resource_type=executed.resource_type,
                        resource_id=executed.resource_id,
                        response_status=self.success_status,
                        now=now,
                    )
            await uow.commit()
            return Outcome(view=executed.view, status_code=self.success_status)

    # -- extension points --------------------------------------------------
    @abstractmethod
    async def _execute(self, uow: UowT, command: CommandT) -> Executed[ViewT]:
        """Perform the operation inside the open transaction."""

    async def _reload(self, uow: UowT, record: IdempotencyRecord, command: CommandT) -> ViewT:
        """Rebuild the view for a replayed request.

        The default implementation fails loudly: a use case that supports
        idempotency must decide how to read back the resource it created.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement _reload to support idempotent replays"
        )

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def tenant_of(command: CommandT) -> Any:
        """Tenant a command acts on (``None`` for platform-scoped commands)."""
        return getattr(command, "tenant_id", None)

    @classmethod
    def fingerprint(cls, command: CommandT) -> str:
        """Fingerprint the business payload of a command (the context is excluded)."""
        fields = {
            field.name: getattr(command, field.name)
            for field in dataclasses.fields(command)  # type: ignore[arg-type]
            if field.name != "context"
        }
        return fingerprint(fields)


class QueryUseCase[CommandT: Command, ViewT, UowT: ApplicationUnitOfWork](ABC):
    """Base class for read-only use cases.

    Reads open a transaction scope too, which keeps the code uniform and gives
    consistent snapshot semantics, but they never commit and never write.
    """

    bypass_rls: ClassVar[bool] = False
    permission: ClassVar[str | None] = None
    resource_type: ClassVar[str | None] = None

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UowT],
        clock: Clock,
        authorizer: Any | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._authorizer = authorizer

    @staticmethod
    def _validate_tenant_consistency(command: Any, context: RequestContext | None) -> None:
        """Ensure tenant context is authoritative and fail-closed against cross-tenant tampering."""
        if context is None:
            return
        from shared.application.errors import Forbidden

        cmd_tenant = getattr(command, "tenant_id", None)
        ctx_tenant = getattr(context, "tenant_id", None)
        if cmd_tenant is not None and ctx_tenant is not None and str(cmd_tenant) != str(ctx_tenant):
            raise Forbidden(
                "tenant mismatch between command and execution context",
                details={"command_tenant": str(cmd_tenant), "context_tenant": str(ctx_tenant)},
            )

        principal = getattr(context, "principal", None)
        if principal is not None and getattr(principal, "active_tenant_id", None) is not None:
            active_tenant = str(principal.active_tenant_id)
            if cmd_tenant is not None and str(cmd_tenant) != active_tenant:
                raise Forbidden(
                    "tenant mismatch between command and principal active tenant",
                    details={"command_tenant": str(cmd_tenant), "principal_tenant": active_tenant},
                )
            if ctx_tenant is not None and str(ctx_tenant) != active_tenant:
                raise Forbidden(
                    "tenant mismatch between context and principal active tenant",
                    details={"context_tenant": str(ctx_tenant), "principal_tenant": active_tenant},
                )

    async def _authorize(
        self,
        context: RequestContext,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        resource_tenant_id: Any | None = None,
        resource_attributes: dict[str, Any] | None = None,
    ) -> None:
        """Evaluate authorization through the configured AuthorizationPort."""
        if self._authorizer is None:
            return

        from identity.ports.authorization import AuthorizationRequest, Principal
        from shared.application.context import ActorType
        from shared.application.errors import Forbidden

        principal: Principal | None = getattr(context, "principal", None)
        if principal is None and context.actor_type == ActorType.SYSTEM:
            principal = Principal(id="system", type=ActorType.SYSTEM)
        elif principal is None:
            from shared.application.errors import Unauthorized
            raise Unauthorized(
                "authentication required: no authenticated principal on execution context",
                details={"action": action, "resource_type": resource_type},
            )

        subject_attrs: dict[str, Any] = {}
        if getattr(principal, "attributes", None):
            subject_attrs.update(principal.attributes)

        env: dict[str, Any] = {"correlation_id": context.correlation_id}
        if getattr(context, "causation_id", None):
            env["causation_id"] = context.causation_id

        req = AuthorizationRequest(
            action=action,
            resource_type=resource_type,
            principal_id=principal.id,
            principal_type=principal.type,
            tenant_id=context.tenant_id,
            roles=principal.roles,
            permissions=principal.permissions,
            subject_attributes=subject_attrs,
            resource_id=resource_id,
            resource_tenant_id=resource_tenant_id or context.tenant_id,
            resource_attributes=resource_attributes or {},
            environment=env,
        )
        decision = await self._authorizer.authorize(req)
        if not decision.allowed:
            raise Forbidden(
                f"operation '{action}' not authorized on {resource_type}",
                details={
                    "reason": decision.reason.value,
                    "reason_details": decision.reason_details,
                },
            )

    async def __call__(self, command: CommandT) -> ViewT:
        context = getattr(command, "context", None)
        self._validate_tenant_consistency(command, context)
        tenant_id = getattr(command, "tenant_id", None) or (
            getattr(context, "tenant_id", None) if context else None
        )

        if self.permission is not None and self._authorizer is not None and context is not None:
            await self._authorize(
                context,
                action=self.permission,
                resource_type=self.resource_type or "resource",
                resource_tenant_id=tenant_id,
            )

        async with self._uow_factory() as uow:
            scope = getattr(uow, "_scope", None)
            if scope is not None and hasattr(scope, "set_tenant_context"):
                active_tid = tenant_id or (
                    getattr(context, "tenant_id", None) if context else None
                )
                active_uid = (
                    str(context.actor_id)
                    if (context and getattr(context, "actor_id", None))
                    else None
                )
                try:
                    await scope.set_tenant_context(active_tid, user_id=active_uid)
                except TypeError:
                    await scope.set_tenant_context(active_tid)
            return await self._execute(uow, command)

    @abstractmethod
    async def _execute(self, uow: UowT, command: CommandT) -> ViewT:
        """Compute the query result."""


def pull_events(*aggregates: Any) -> tuple[DomainEvent, ...]:
    """Collect and clear the domain events of one or more aggregates.

    Called immediately before ``uow.commit()``. Phase 2 hands the returned tuple
    to the transactional outbox inside the same transaction; Phase 1 keeps the
    seam without publishing anything.
    """
    collected: list[DomainEvent] = []
    for aggregate in aggregates:
        if aggregate is None:
            continue
        collected.extend(aggregate.pull_events())
    return tuple(collected)


__all__ = [
    "DEFAULT_IDEMPOTENCY_RETENTION",
    "ApplicationUnitOfWork",
    "Command",
    "Executed",
    "Outcome",
    "QueryUseCase",
    "UseCase",
    "pull_events",
]
