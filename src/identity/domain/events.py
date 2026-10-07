"""Identity domain events."""

from __future__ import annotations

from dataclasses import dataclass

from shared.domain.events import TenantDomainEvent


@dataclass(frozen=True, kw_only=True, slots=True)
class TenantRegistered(TenantDomainEvent):
    slug: str
    name: str


@dataclass(frozen=True, kw_only=True, slots=True)
class TenantRenamed(TenantDomainEvent):
    name: str


__all__ = ["TenantRegistered", "TenantRenamed"]
