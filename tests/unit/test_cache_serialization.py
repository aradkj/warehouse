"""Unit tests for cache serialization and corrupted payload tolerance."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from shared.infrastructure.cache.serialization import (
    CacheSerializer,
)
from shared.ports.cache import NegativeCacheMarker

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class SampleProductPayload:
    id: str
    name: str
    price: Decimal


def test_serialize_and_deserialize_primitives() -> None:
    data = {"count": 42, "enabled": True, "name": "Test Item", "pi": 3.14}
    encoded = CacheSerializer.serialize(data)
    decoded = CacheSerializer.deserialize(encoded)
    assert decoded == data


def test_serialize_and_deserialize_rich_types() -> None:
    now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
    uid = uuid.uuid4()
    item = {
        "uid": uid,
        "timestamp": now,
        "amount": Decimal("19.99"),
    }
    encoded = CacheSerializer.serialize(item)
    decoded = CacheSerializer.deserialize(encoded)

    assert decoded["uid"] == {"$uuid": str(uid)}
    assert decoded["timestamp"] == {"$ts": now.isoformat()}
    assert decoded["amount"] == {"$decimal": "19.99"}


def test_serialize_and_deserialize_negative_cache_marker() -> None:
    marker = NegativeCacheMarker(reason="not_found")
    encoded = CacheSerializer.serialize(marker)
    decoded = CacheSerializer.deserialize(encoded)

    assert isinstance(decoded, NegativeCacheMarker)
    assert decoded.reason == "not_found"


def test_corrupted_json_payload_returns_none_gracefully() -> None:
    """A corrupted cache entry must be treated as a cache miss without raising."""
    malformed = "{'invalid': json; incomplete"
    decoded = CacheSerializer.deserialize(malformed)
    assert decoded is None


def test_invalid_utf8_bytes_returns_none_gracefully() -> None:
    bad_bytes = b"\x80\x81\x82not_utf8"
    decoded = CacheSerializer.deserialize(bad_bytes)
    assert decoded is None


def test_none_input_returns_none() -> None:
    assert CacheSerializer.deserialize(None) is None


def test_pickle_is_not_used() -> None:
    """Security assertion: pickle module must not be used for serialization."""
    sample = {"data": "safe"}
    encoded = CacheSerializer.serialize(sample)
    # JSON output starts with '{' or '[', never pickle protocol markers (b'\x80')
    assert encoded.startswith("{")
