"""Structured logging foundation.

Phase 1 does not ship the observability stack (no OpenTelemetry, no Prometheus -
those are Phase 5). It does ship the piece every later phase depends on: logs are
*structured*, and every log line emitted while handling a request carries the
request's correlation id.

The correlation id is stored in a :class:`contextvars.ContextVar`, which is
async-safe: concurrent requests cannot leak identifiers into each other's logs.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_correlation_id: ContextVar[str | None] = ContextVar("arad_correlation_id", default=None)

_RESERVED = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


def set_correlation_id(value: str | None) -> None:
    _correlation_id.set(value)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        correlation_id = getattr(record, "correlation_id", None) or get_correlation_id()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key != "correlation_id" and not key.startswith("_"):
                payload[key] = _safe(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class PlainFormatter(logging.Formatter):
    """Human-readable formatter for local development."""

    def format(self, record: logging.LogRecord) -> str:
        correlation_id = getattr(record, "correlation_id", None) or get_correlation_id()
        prefix = f"[{correlation_id}] " if correlation_id else ""
        message = record.getMessage()
        return f"{self.formatTime(record)} {record.levelname:<8} {record.name} {prefix}{message}"


def _safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    return str(value)


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Install the root handler once (idempotent, safe under uvicorn reloads)."""
    root = logging.getLogger()
    formatter: logging.Formatter = (
        JsonFormatter() if json_output else PlainFormatter("%(asctime)s %(levelname)s %(name)s")
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    # Replace handlers we installed previously, keep third-party ones untouched.
    root.handlers = [handler]
    root.setLevel(level.upper())
    # uvicorn installs its own handlers; make them use our formatter too.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.propagate = False


__all__ = [
    "JsonFormatter",
    "PlainFormatter",
    "configure_logging",
    "get_correlation_id",
    "set_correlation_id",
]
