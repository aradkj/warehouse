"""Safe, deterministic JSON serialization for cache values.

Rules:
* NO Python pickle (security hazard, language coupling, brittle migrations).
* Deterministic canonical serialization for structured application values.
* Corrupted or malformed cache entries are safely logged and treated as cache misses.
* Seamlessly encodes and decodes :class:`~shared.ports.cache.NegativeCacheMarker`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from shared.application.serialization import canonical
from shared.ports.cache import NegativeCacheMarker

logger = logging.getLogger(__name__)

_NEGATIVE_CACHE_TAG = "$negative_cache_marker"


class CacheSerializationError(Exception):
    """Raised when an object cannot be serialized into JSON."""


class CacheSerializer:
    """Encodes and decodes application objects to/from JSON strings."""

    @staticmethod
    def serialize(value: Any) -> str:
        """Serialize an arbitrary application value to JSON string.

        Raises:
            CacheSerializationError: If serialization fails.
        """
        if isinstance(value, NegativeCacheMarker):
            payload = {
                _NEGATIVE_CACHE_TAG: True,
                "reason": value.reason,
                "created_at": value.created_at.isoformat(),
            }
            return json.dumps(payload, separators=(",", ":"))

        try:
            canonical_obj = canonical(value)
            return json.dumps(canonical_obj, separators=(",", ":"))
        except Exception as exc:
            logger.error("Failed to serialize cache value %r: %s", type(value), exc)
            raise CacheSerializationError(f"Cannot serialize {type(value)} to JSON") from exc

    @staticmethod
    def deserialize(raw: str | bytes | None) -> Any | None:
        """Safely deserialize a JSON string from cache.

        Returns:
            The deserialized Python object, or :class:`NegativeCacheMarker`.
            Returns ``None`` if ``raw`` is None or if parsing fails (corrupt payload).
        """
        if raw is None:
            return None

        if isinstance(raw, bytes):
            try:
                raw_str = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                logger.warning("Cache payload contains invalid UTF-8 bytes: %s", exc)
                return None
        else:
            raw_str = raw

        try:
            parsed = json.loads(raw_str)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("Corrupted cache entry encountered: %s", exc)
            return None

        if isinstance(parsed, dict) and parsed.get(_NEGATIVE_CACHE_TAG) is True:
            try:
                created_at = datetime.fromisoformat(parsed["created_at"])
                return NegativeCacheMarker(
                    reason=str(parsed.get("reason", "not_found")),
                    created_at=created_at,
                )
            except Exception:
                return NegativeCacheMarker()

        return parsed


__all__ = [
    "CacheSerializationError",
    "CacheSerializer",
]
