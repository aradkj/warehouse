"""Elasticsearch implementation of the SearchIndexer port.

Maintains denormalized search read models in Elasticsearch using async I/O,
external optimistic versioning, deterministic document IDs, and resilient retries.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from elasticsearch import AsyncElasticsearch
from elasticsearch.exceptions import ConflictError, NotFoundError, TransportError

from search.ports.indexer import SearchDocument, SearchIndexer

if TYPE_CHECKING:
    from bootstrap.config import Settings

logger = logging.getLogger(__name__)


class ElasticsearchIndexAdapter(SearchIndexer):
    """Production Elasticsearch adapter implementing SearchIndexer."""

    def __init__(self, client: AsyncElasticsearch) -> None:
        self._client = client

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        request_timeout: float = 10.0,
        max_retries: int = 3,
        retry_on_timeout: bool = True,
    ) -> ElasticsearchIndexAdapter:
        """Construct adapter from connection URL."""
        client = AsyncElasticsearch(
            hosts=[url],
            request_timeout=request_timeout,
            max_retries=max_retries,
            retry_on_timeout=retry_on_timeout,
        )
        return cls(client)

    @classmethod
    def from_settings(cls, settings: Settings) -> ElasticsearchIndexAdapter:
        """Construct adapter from application settings."""
        return cls.from_url(
            settings.elasticsearch_url,
            request_timeout=settings.elasticsearch_timeout_seconds,
            max_retries=settings.elasticsearch_max_retries,
        )

    @property
    def client(self) -> AsyncElasticsearch:
        """Underlying async Elasticsearch client instance."""
        return self._client

    async def index(self, documents: tuple[SearchDocument, ...]) -> None:
        """Index one or more search documents.

        Employs ``version_type='external_gte'`` to ensure that out-of-order older
        events cannot overwrite newer document state. Version conflicts are logged
        and ignored idempotently.
        """
        for doc in documents:
            version = max(1, doc.version)
            try:
                await self._client.index(
                    index=doc.index_alias,
                    id=doc.document_id,
                    document=doc.body,
                    version=version,
                    version_type="external_gte",
                )
                logger.debug(
                    "Indexed document %s in %s (version=%d)",
                    doc.document_id,
                    doc.index_alias,
                    version,
                )
            except ConflictError:
                logger.info(
                    "Document %s in %s already at or ahead of version %d; skipping stale event",
                    doc.document_id,
                    doc.index_alias,
                    version,
                )
            except TransportError as exc:
                logger.error(
                    "Failed to index document %s in %s: %s",
                    doc.document_id,
                    doc.index_alias,
                    exc,
                )
                raise

    async def delete(self, *, index_alias: str, document_ids: tuple[str, ...]) -> None:
        """Delete one or more documents from the search index.

        Missing documents are treated as idempotent successes.
        """
        for doc_id in document_ids:
            try:
                await self._client.delete(index=index_alias, id=doc_id)
                logger.debug("Deleted document %s from %s", doc_id, index_alias)
            except NotFoundError:
                logger.debug(
                    "Document %s already deleted or not found in %s; ignoring",
                    doc_id,
                    index_alias,
                )
            except TransportError as exc:
                logger.error(
                    "Failed to delete document %s from %s: %s",
                    doc_id,
                    index_alias,
                    exc,
                )
                raise

    async def get_document(
        self, *, index_alias: str, document_id: str
    ) -> dict[str, object] | None:
        """Retrieve raw document payload by id, or None if not found."""
        try:
            response = await self._client.get(index=index_alias, id=document_id)
            source: Any = response.get("_source")
            if isinstance(source, dict):
                return source
            return None
        except NotFoundError:
            return None
        except TransportError as exc:
            logger.error(
                "Error retrieving document %s from %s: %s",
                document_id,
                index_alias,
                exc,
            )
            raise

    async def ensure_index(
        self,
        *,
        index_name: str,
        mappings: dict[str, object],
        aliases: tuple[str, ...] = (),
    ) -> bool:
        """Idempotently create index with mappings and aliases if it does not exist.

        Does NOT destroy or alter existing indexes if already present.
        """
        exists = await self._client.indices.exists(index=index_name)
        if exists:
            return False

        aliases_dict: dict[str, dict[str, Any]] = {alias: {} for alias in aliases}
        settings_body: dict[str, Any] = {
            "number_of_shards": 1,
            "number_of_replicas": 0,
        }

        try:
            await self._client.indices.create(
                index=index_name,
                mappings=mappings,
                settings=settings_body,
                aliases=aliases_dict,
            )
            logger.info(
                "Created Elasticsearch index '%s' with aliases %s",
                index_name,
                aliases,
            )
            return True
        except ConflictError:
            # Race condition: another process created the index concurrently
            return False

    async def ping(self) -> bool:
        """Probe Elasticsearch cluster availability."""
        try:
            return bool(await self._client.ping())
        except Exception:
            return False

    async def close(self) -> None:
        """Close client connection pool."""
        await self._client.close()


__all__ = ["ElasticsearchIndexAdapter"]
