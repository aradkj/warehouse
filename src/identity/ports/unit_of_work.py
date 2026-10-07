"""Identity unit-of-work port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from identity.ports.repositories import TenantRepository
from shared.application.use_case import ApplicationUnitOfWork


@runtime_checkable
class IdentityUnitOfWork(ApplicationUnitOfWork, Protocol):
    """Transactional view over the identity persistence ports."""

    tenants: TenantRepository


__all__ = ["IdentityUnitOfWork"]
