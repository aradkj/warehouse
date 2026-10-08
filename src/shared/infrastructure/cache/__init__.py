"""Shared cache infrastructure package."""

from __future__ import annotations

from shared.infrastructure.cache.keys import CacheKeyBuilder, default_key_builder
from shared.infrastructure.cache.memory import InMemoryCache
from shared.infrastructure.cache.redis import RedisCacheAdapter
from shared.infrastructure.cache.serialization import (
    CacheSerializationError,
    CacheSerializer,
)

__all__ = [
    "CacheKeyBuilder",
    "CacheSerializationError",
    "CacheSerializer",
    "InMemoryCache",
    "RedisCacheAdapter",
    "default_key_builder",
]
