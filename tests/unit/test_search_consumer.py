"""Unit tests for Search projection consumer setup."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from search.adapters.memory import InMemorySearchIndexer
from search.adapters.messaging import CATALOG_EVENT_TYPES, create_search_projection_consumer
from shared.infrastructure.messaging.consumer import IdempotentConsumer

pytestmark = pytest.mark.unit


def test_create_search_projection_consumer_registers_all_events() -> None:
    mock_db = MagicMock()
    indexer = InMemorySearchIndexer()

    consumer, _ = create_search_projection_consumer(
        database=mock_db,
        indexer=indexer,
    )

    assert isinstance(consumer, IdempotentConsumer)
    # Check that all 8 catalog events are registered handlers in the consumer
    for event_type in CATALOG_EVENT_TYPES:
        assert event_type in consumer._handlers
