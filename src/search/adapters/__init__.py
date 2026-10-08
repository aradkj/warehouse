"""Search adapters."""

from search.adapters.elasticsearch import ElasticsearchIndexAdapter
from search.adapters.memory import InMemorySearchIndexer
from search.adapters.messaging import (
    CATALOG_EVENT_TYPES,
    create_search_projection_consumer,
)

__all__ = [
    "CATALOG_EVENT_TYPES",
    "ElasticsearchIndexAdapter",
    "InMemorySearchIndexer",
    "create_search_projection_consumer",
]
