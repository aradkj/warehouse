"""HTTP error contract.

One mechanism maps *both* domain and application errors onto stable HTTP
responses, so a new business rule cannot accidentally leak an unhandled 500 or a
stack trace. The mapping table is explicit: a new exception type must be
registered here (the fallback is a logged 500 with a generic body).

Response body (always present, always the same shape)::

    {
      "error": {
        "code": "insufficient_inventory",
        "message": "insufficient available inventory",
        "details": {"shortfalls": [...]},
        "correlation_id": "0f1c...",
        "type": "domain"
      }
    }

``code`` is the contract: it is stable, machine-readable and never localised.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from shared.application.errors import (
    ApplicationError,
    ConcurrencyConflict,
    DuplicateIdempotencyKey,
    DuplicateResource,
    IdempotencyKeyRequired,
    IdempotencyOperationInProgress,
    InvalidRequest,
    MissingReference,
    NotFound,
    SearchUnavailable,
)
from shared.domain.errors import CrossTenantAccess, DomainError, InvalidValue, InvariantViolation

logger = logging.getLogger("arad.api.errors")

#: Explicit exception -> HTTP status mapping. Order matters only for readability;
#: lookup walks the MRO, so registering a base class covers its subclasses.
_STATUS_BY_EXCEPTION: list[tuple[type[Exception], int]] = [
    # application
    (NotFound, status.HTTP_404_NOT_FOUND),
    (SearchUnavailable, status.HTTP_503_SERVICE_UNAVAILABLE),
    (InvalidRequest, status.HTTP_400_BAD_REQUEST),
    (IdempotencyKeyRequired, status.HTTP_400_BAD_REQUEST),
    (DuplicateResource, status.HTTP_409_CONFLICT),
    (DuplicateIdempotencyKey, status.HTTP_409_CONFLICT),
    (IdempotencyOperationInProgress, status.HTTP_409_CONFLICT),
    (ConcurrencyConflict, status.HTTP_409_CONFLICT),
    (MissingReference, status.HTTP_409_CONFLICT),
    # domain
    (CrossTenantAccess, status.HTTP_404_NOT_FOUND),
    (InvalidValue, status.HTTP_422_UNPROCESSABLE_CONTENT),
    (InvariantViolation, status.HTTP_500_INTERNAL_SERVER_ERROR),
]

#: Domain errors that are business outcomes rather than bugs. They are listed by
#: name so that the bootstrap layer does not have to import every context's
#: domain package (which would create a compile-time dependency from the API
#: layer onto the domain internals of each context).
_DOMAIN_STATUS_BY_CODE: dict[str, int] = {
    "insufficient_inventory": status.HTTP_409_CONFLICT,
    "reservation_expired": status.HTTP_409_CONFLICT,
    "invalid_reservation_transition": status.HTTP_409_CONFLICT,
    "invalid_reservation": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "invalid_adjustment": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "stock_adjustment_below_reserved": status.HTTP_409_CONFLICT,
    "warehouse_not_active": status.HTTP_409_CONFLICT,
    "invalid_warehouse_transition": status.HTTP_409_CONFLICT,
    "invalid_order": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "invalid_order_transition": status.HTTP_409_CONFLICT,
    "order_lines_not_reserved": status.HTTP_409_CONFLICT,
    "duplicate_order_line": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "product_not_purchasable": status.HTTP_409_CONFLICT,
    "invalid_product": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "invalid_product_transition": status.HTTP_409_CONFLICT,
    "invalid_sku_transition": status.HTTP_409_CONFLICT,
    "invalid_value": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "cross_tenant_access": status.HTTP_404_NOT_FOUND,
}


def http_status_for(exc: Exception) -> int:
    """Resolve the HTTP status for a domain/application error."""
    if isinstance(exc, DomainError):
        mapped = _DOMAIN_STATUS_BY_CODE.get(exc.code)
        if mapped is not None:
            return mapped
    for exception_type, http_status in _STATUS_BY_EXCEPTION:
        if isinstance(exc, exception_type):
            return http_status
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def error_body(
    *,
    code: str,
    message: str,
    details: dict[str, Any] | None,
    correlation_id: str | None,
    error_type: str,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "correlation_id": correlation_id,
            "type": error_type,
        }
    }


def _correlation_id(request: Request) -> str | None:
    return getattr(request.state, "correlation_id", None)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the platform's exception handlers to ``app``."""

    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError) -> JSONResponse:
        http_status = http_status_for(exc)
        if http_status >= 500:
            # An invariant violation is a bug in our code, not a client error:
            # log it with the correlation id and answer generically.
            logger.exception(
                "domain invariant violated",
                extra={"correlation_id": _correlation_id(request), "error_code": exc.code},
            )
            return JSONResponse(
                status_code=http_status,
                content=error_body(
                    code="internal_error",
                    message="the request could not be completed",
                    details=None,
                    correlation_id=_correlation_id(request),
                    error_type="internal",
                ),
            )
        return JSONResponse(
            status_code=http_status,
            content=error_body(
                code=exc.code,
                message=exc.message,
                details=exc.details,
                correlation_id=_correlation_id(request),
                error_type="domain",
            ),
        )

    @app.exception_handler(ApplicationError)
    async def _application_error(request: Request, exc: ApplicationError) -> JSONResponse:
        return JSONResponse(
            status_code=http_status_for(exc),
            content=error_body(
                code=exc.code,
                message=exc.message,
                details=exc.details,
                correlation_id=_correlation_id(request),
                error_type="application",
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Turn FastAPI's validation errors into the platform's error contract."""
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=error_body(
                code="validation_error",
                message="the request payload failed validation",
                details={"errors": _clean_validation_errors(exc.errors())},
                correlation_id=_correlation_id(request),
                error_type="request",
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content=error_body(
                code=f"http_{exc.status_code}",
                message=detail,
                details=None,
                correlation_id=_correlation_id(request),
                error_type="http",
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Never leak internals: the correlation id ties the response to the log entry.
        logger.exception("unhandled error", extra={"correlation_id": _correlation_id(request)})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_body(
                code="internal_error",
                message="the request could not be completed",
                details=None,
                correlation_id=_correlation_id(request),
                error_type="internal",
            ),
        )


def _clean_validation_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """Strip non-serialisable values (``ValueError`` instances) from pydantic errors."""
    cleaned: list[dict[str, Any]] = []
    for error in errors:
        if not isinstance(error, dict):  # pragma: no cover - pydantic always sends dicts
            continue
        item = {
            "loc": [str(part) for part in error.get("loc", ())],
            "type": error.get("type"),
            "msg": error.get("msg"),
        }
        context = error.get("ctx")
        if isinstance(context, dict):
            item["ctx"] = {key: str(value) for key, value in context.items()}
        cleaned.append(item)
    return cleaned


__all__ = [
    "error_body",
    "http_status_for",
    "register_exception_handlers",
]
