"""Application-layer cache interface and helpers.

Provides convenient re-exports of cache ports and key formatting for use cases.
"""

from __future__ import annotations

from shared.ports.cache import CacheEntry, CachePort, NegativeCacheMarker

__all__ = [
    "CacheEntry",
    "CachePort",
    "NegativeCacheMarker",
]
