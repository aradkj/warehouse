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
from bootstrap.metrics import metrics, normalize_metric_path

logger = logging.getLogger("arad.api.access")

CORRELATION_HEADER = "X-Correlation-Id"
TRACEPARENT_HEADER = "traceparent"
MAX_CORRELATION_ID_LENGTH = 128


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Accept or generate a correlation id & W3C traceparent and propagate them everywhere.

    The correlation id is echoed on the response, attached to ``request.state`` (so error
    handlers can include it) and stored in a context variable (so every log line
    in the request carries it).
    Supports W3C TraceContext (``traceparent``) for distributed tracing across services.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming_corr = request.headers.get(CORRELATION_HEADER)
        incoming_trace = request.headers.get(TRACEPARENT_HEADER)

        if incoming_corr and 0 < len(incoming_corr) <= MAX_CORRELATION_ID_LENGTH:
            correlation_id = incoming_corr
        elif incoming_trace and incoming_trace.startswith("00-") and len(incoming_trace) >= 35:
            # Extract trace-id from W3C traceparent: 00-{trace_id}-{span_id}-{flags}
            parts = incoming_trace.split("-")
            correlation_id = parts[1] if len(parts) >= 2 else str(uuid.uuid4())
        else:
            correlation_id = str(uuid.uuid4())

        # Generate or preserve W3C traceparent header
        if incoming_trace and len(incoming_trace) >= 55:
            traceparent = incoming_trace
        else:
            trace_id = uuid.uuid4().hex
            span_id = uuid.uuid4().hex[:16]
            traceparent = f"00-{trace_id}-{span_id}-01"

        request.state.correlation_id = correlation_id
        request.state.traceparent = traceparent
        set_correlation_id(correlation_id)
        try:
            response = await call_next(request)
        finally:
            set_correlation_id(None)

        response.headers[CORRELATION_HEADER] = correlation_id
        response.headers[TRACEPARENT_HEADER] = traceparent
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
    """One structured log line per request, with latency, correlation id, and metrics."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        duration_s = time.perf_counter() - started
        duration_ms = round(duration_s * 1000, 3)

        norm_path = normalize_metric_path(request.url.path)
        # Emit Prometheus request rate and latency metrics with bounded cardinality
        metrics.increment_counter(
            "http_requests_total",
            labels={
                "method": request.method,
                "path": norm_path,
                "status": str(response.status_code),
            },
        )
        metrics.observe_histogram(
            "http_request_duration_seconds",
            duration_s,
            labels={"method": request.method, "path": norm_path},
        )

        logger.info(
            "request handled",
            extra={
                "correlation_id": getattr(request.state, "correlation_id", None),
                "traceparent": getattr(request.state, "traceparent", None),
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response


__all__ = [
    "CORRELATION_HEADER",
    "TRACEPARENT_HEADER",
    "AccessLogMiddleware",
    "CorrelationIdMiddleware",
    "RequestSizeLimitMiddleware",
]
