"""Shared response schemas.

Pydantic models are the *only* thing the API exposes: SQLAlchemy tables and
domain aggregates never cross the HTTP boundary (a rule enforced by an
architecture test). Money is always rendered as integer minor units plus the
ISO-4217 currency - the same representation the domain and the database use, so
no rounding can be introduced by the transport layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class MoneySchema(BaseModel):
    """Money as integer minor units plus currency."""

    model_config = ConfigDict(frozen=True)

    amount_minor: int = Field(description="Amount in the currency's minor unit (e.g. cents).")
    currency: str = Field(min_length=3, max_length=3, description="ISO-4217 currency code.")


class PageSchema[T](BaseModel):
    """Cursor-paginated collection envelope."""

    items: list[T]
    next_cursor: str | None = Field(
        default=None,
        description="Opaque cursor for the next page; absent when the page is the last one.",
    )


class ErrorDetail(BaseModel):
    """The ``error`` object of every non-2xx response."""

    code: str = Field(description="Stable, machine-readable error code.")
    message: str
    details: dict[str, object] = Field(default_factory=dict)
    correlation_id: str | None = None
    type: str = Field(description="Error family: domain, application, request, http or internal.")


class ErrorResponse(BaseModel):
    """Documented shape of every error response (see ``bootstrap/errors.py``)."""

    error: ErrorDetail


class HealthResponse(BaseModel):
    status: str
    environment: str
    #: Coarse dependency results for orchestrators (``{"database": "ok"}``).
    #: Deliberately non-verbose: a health endpoint is not a diagnostics endpoint,
    #: and it must never leak connection strings or exception text.
    checks: dict[str, str] = {}


def uuid_str(value: UUID) -> str:
    return str(value)


def isoformat(value: datetime) -> str:
    return value.isoformat()


__all__ = [
    "ErrorDetail",
    "ErrorResponse",
    "HealthResponse",
    "MoneySchema",
    "PageSchema",
    "isoformat",
    "uuid_str",
]
