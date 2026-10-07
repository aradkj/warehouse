"""HTTP middleware: correlation ids, access logs and request size limits.

Middleware is *transport* concern only. It must not contain business logic; the
only thing it decides is whether a request is structurally acceptable and which
correlation id it carries.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from bootstrap.errors import error_body
from bootstrap.logging import set_correlation_id

logger = logging.getLogger("arad.api.access")

CORRELATION_HEADER = "X-Correlation-Id"
MAX_CORRELATION_ID_LENGTH = 128


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Accept or generate a correlation id and propagate it everywhere.

    The id is echoed on the response, attached to ``request.state`` (so error
    handlers can include it) and stored in a context variable (so every log line
    in the request carries it). Phase 2 will put it in the outbox envelope, which
    is how a message can be traced back to the HTTP request that caused it.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(CORRELATION_HEADER)
        if incoming and 0 < len(incoming) <= MAX_CORRELATION_ID_LENGTH:
            correlation_id = incoming
        else:
            correlation_id = str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        set_correlation_id(correlation_id)
        try:
            response = await call_next(request)
        finally:
            set_correlation_id(None)
        response.headers[CORRELATION_HEADER] = correlation_id
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized bodies before any parsing happens."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                declared = -1
            if declared > self._max_bytes:
                return JSONResponse(
                    status_code=413,
                    content=error_body(
                        code="payload_too_large",
                        message=f"request bodies may not exceed {self._max_bytes} bytes",
                        details={"limit_bytes": self._max_bytes},
                        correlation_id=getattr(request.state, "correlation_id", None),
                        error_type="request",
                    ),
                )
        return await call_next(request)


class AccessLogMiddleware(BaseHTTPMiddleware):
    """One structured log line per request, with latency and correlation id."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        logger.info(
            "request handled",
            extra={
                "correlation_id": getattr(request.state, "correlation_id", None),
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response


__all__ = [
    "CORRELATION_HEADER",
    "AccessLogMiddleware",
    "CorrelationIdMiddleware",
    "RequestSizeLimitMiddleware",
]
