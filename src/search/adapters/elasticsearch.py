"""Elasticsearch implementation of the SearchIndexer port.

Maintains denormalized search read models in Elasticsearch using async I/O,
external optimistic versioning, deterministic document IDs, atomic alias swaps,
strict tenant isolation, and resilient retries.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import TYPE_CHECKING, Any

from elasticsearch import AsyncElasticsearch
from elasticsearch.exceptions import (
    ConflictError,
    ConnectionTimeout,
    NotFoundError,
    TransportError,
)
from elasticsearch.exceptions import (
    ConnectionError as EsConnectionError,
)

from search.ports.indexer import (
    BulkIndexResult,
    SearchDocument,
    SearchIndexer,
    SearchPageResult,
    SearchQuery,
    SearchResultItem,
)
from shared.application.errors import SearchUnavailable

if TYPE_CHECKING:
    from bootstrap.config import Settings

logger = logging.getLogger(__name__)

ALLOWED_SORT_FIELDS: dict[str, str] = {
    "created_at": "created_at",
    "updated_at": "updated_at",
    "name": "name.keyword",
    "status": "status",
    "slug": "slug",
}


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

    async def index(
        self,
        documents: tuple[SearchDocument, ...],
        *,
        force: bool = False,
        refresh: bool | str = False,
    ) -> None:
        """Index one or more search documents.

        Employs ``version_type='external_gte'`` unless force=True to ensure that
        out-of-order older events cannot overwrite newer document state. Version
        conflicts are logged and ignored idempotently.
        """
        for doc in documents:
            version = max(1, doc.version)
            try:
                kwargs: dict[str, Any] = {
                    "index": doc.index_alias,
                    "id": doc.document_id,
                    "document": doc.body,
                }
                if refresh is not False:
                    kwargs["refresh"] = refresh
                if not force:
                    kwargs["version"] = version
                    kwargs["version_type"] = "external_gte"

                await self._client.index(**kwargs)
                logger.debug(
                    "Indexed document %s in %s (version=%d, force=%s)",
                    doc.document_id,
                    doc.index_alias,
                    version,
                    force,
                )
            except ConflictError:
                logger.info(
                    "Document %s in %s already at or ahead of version %d; skipping stale event",
                    doc.document_id,
                    doc.index_alias,
                    version,
                )
            except (TransportError, EsConnectionError, ConnectionTimeout) as exc:
                logger.error(
                    "Failed to index document %s in %s: %s",
                    doc.document_id,
                    doc.index_alias,
                    exc,
                )
                raise SearchUnavailable(
                    "Search indexer is unavailable",
                    details={"error": exc.__class__.__name__},
                ) from exc

    async def bulk_index(
        self,
        documents: tuple[SearchDocument, ...],
        *,
        force: bool = False,
        refresh: bool | str = False,
    ) -> BulkIndexResult:
        """Bulk index documents with retry for transient failures."""
        if not documents:
            return BulkIndexResult(successful_count=0)

        docs_by_id = {doc.document_id: doc for doc in documents}
        pending_ids = list(docs_by_id.keys())
        successful_count = 0
        permanent_failure_count = 0
        retry_count = 0
        errors: list[dict[str, Any]] = []

        max_retries = 3
        attempt = 0

        while pending_ids and attempt <= max_retries:
            operations: list[dict[str, Any]] = []
            for doc_id in pending_ids:
                doc = docs_by_id[doc_id]
                op_meta: dict[str, Any] = {
                    "_index": doc.index_alias,
                    "_id": doc.document_id,
                }
                if not force:
                    op_meta["version"] = max(1, doc.version)
                    op_meta["version_type"] = "external_gte"

                operations.append({"index": op_meta})
                operations.append(dict(doc.body))

            try:
                bulk_kwargs: dict[str, Any] = {"operations": operations}
                if refresh is not False:
                    bulk_kwargs["refresh"] = refresh
                response = await self._client.bulk(**bulk_kwargs)
            except (TransportError, EsConnectionError, ConnectionTimeout) as exc:
                if attempt < max_retries:
                    attempt += 1
                    retry_count += 1
                    delay = 0.05 * (2**attempt)
                    logger.warning(
                        "Bulk request error, retrying attempt %d in %.2fs: %s",
                        attempt,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error("Bulk index failed after retries: %s", exc)
                raise SearchUnavailable(
                    "Search backend bulk indexing failed",
                    details={"error": exc.__class__.__name__},
                ) from exc

            retryable_ids: list[str] = []
            items = response.get("items", [])
            for item in items:
                action = item.get("index", {})
                status = action.get("status", 0)
                doc_id = action.get("_id", "")
                if status in (200, 201):
                    successful_count += 1
                elif status == 409 and not force:
                    # Version conflict on external_gte is an idempotent no-op
                    successful_count += 1
                elif status in (429, 502, 503, 504):
                    retryable_ids.append(doc_id)
                else:
                    permanent_failure_count += 1
                    err_details = action.get("error", {})
                    errors.append({"document_id": doc_id, "status": status, "error": err_details})
                    logger.error(
                        "Permanent bulk indexing failure for %s (status %d): %s",
                        doc_id,
                        status,
                        err_details,
                    )

            if retryable_ids and attempt < max_retries:
                attempt += 1
                retry_count += 1
                pending_ids = retryable_ids
                delay = 0.05 * (2**attempt)
                logger.warning(
                    "Retrying %d failed bulk items in %.2fs (attempt %d)",
                    len(retryable_ids),
                    delay,
                    attempt,
                )
                await asyncio.sleep(delay)
            else:
                if retryable_ids:
                    permanent_failure_count += len(retryable_ids)
                    for r_id in retryable_ids:
                        errors.append(
                            {"document_id": r_id, "status": 503, "error": "Retries exhausted"}
                        )
                break

        return BulkIndexResult(
            successful_count=successful_count,
            failed_count=permanent_failure_count,
            retry_count=retry_count,
            permanent_failure_count=permanent_failure_count,
            errors=tuple(errors),
        )

    async def delete(
        self,
        *,
        index_alias: str,
        document_ids: tuple[str, ...],
        refresh: bool | str = False,
    ) -> None:
        """Delete one or more documents from the search index."""
        for doc_id in document_ids:
            try:
                delete_kwargs: dict[str, Any] = {"index": index_alias, "id": doc_id}
                if refresh is not False:
                    delete_kwargs["refresh"] = refresh
                await self._client.delete(**delete_kwargs)
                logger.debug("Deleted document %s from %s", doc_id, index_alias)
                logger.debug("Deleted document %s from %s", doc_id, index_alias)
            except NotFoundError:
                logger.debug(
                    "Document %s already deleted or not found in %s; ignoring",
                    doc_id,
                    index_alias,
                )
            except (TransportError, EsConnectionError, ConnectionTimeout) as exc:
                logger.error(
                    "Failed to delete document %s from %s: %s",
                    doc_id,
                    index_alias,
                    exc,
                )
                raise SearchUnavailable(
                    "Search backend delete failed",
                    details={"error": exc.__class__.__name__},
                ) from exc

    async def get_document(self, *, index_alias: str, document_id: str) -> dict[str, object] | None:
        """Retrieve raw document payload by id, or None if not found."""
        try:
            response = await self._client.get(index=index_alias, id=document_id)
            source: Any = response.get("_source")
            if isinstance(source, dict):
                return source
            return None
        except NotFoundError:
            return None
        except (TransportError, EsConnectionError, ConnectionTimeout) as exc:
            logger.error(
                "Error retrieving document %s from %s: %s",
                document_id,
                index_alias,
                exc,
            )
            raise SearchUnavailable(
                "Search backend lookup failed",
                details={"error": exc.__class__.__name__},
            ) from exc

    async def search(self, query: SearchQuery) -> SearchPageResult:
        """Execute a tenant-safe search query against Elasticsearch."""
        # 1. Mandatory tenant filter (Strict Isolation)
        filter_clauses: list[dict[str, Any]] = [{"term": {"tenant_id": query.tenant_id}}]

        if query.status:
            filter_clauses.append({"term": {"status": query.status}})

        if query.sku:
            filter_clauses.append(
                {
                    "nested": {
                        "path": "skus",
                        "query": {"term": {"skus.sku_code": query.sku}},
                    }
                }
            )

        must_clauses: list[dict[str, Any]] = []
        if query.q and query.q.strip():
            cleaned_q = query.q.strip()
            must_clauses.append(
                {
                    "bool": {
                        "should": [
                            {
                                "multi_match": {
                                    "query": cleaned_q,
                                    "fields": ["name^3", "slug^2", "description"],
                                    "type": "best_fields",
                                }
                            },
                            {
                                "nested": {
                                    "path": "skus",
                                    "query": {
                                        "multi_match": {
                                            "query": cleaned_q,
                                            "fields": ["skus.sku_code^2"],
                                        }
                                    },
                                }
                            },
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )

        query_body: dict[str, Any] = {
            "bool": {
                "filter": filter_clauses,
            }
        }
        if must_clauses:
            query_body["bool"]["must"] = must_clauses

        # 2. Safe sort resolution
        sort_field = ALLOWED_SORT_FIELDS.get(query.sort_by, "created_at")
        sort_order = "asc" if query.sort_order.lower() == "asc" else "desc"
        sort_clause: list[dict[str, Any]] = [
            {sort_field: {"order": sort_order}},
            {"id": {"order": "asc"}},  # Stable tie-breaker
        ]

        # 3. Safe pagination bounds and search_after
        safe_page = max(1, query.page)
        safe_page_size = max(1, query.page_size)

        search_kwargs: dict[str, Any] = {
            "index": query.index_alias,
            "query": query_body,
            "sort": sort_clause,
            "size": safe_page_size,
        }
        if query.search_after:
            search_kwargs["search_after"] = list(query.search_after)
        else:
            from_offset = (safe_page - 1) * safe_page_size
            search_kwargs["from_"] = from_offset

        try:
            raw_response = await self._client.search(**search_kwargs)
        except (TransportError, EsConnectionError, ConnectionTimeout) as exc:
            logger.error("Search query execution failed: %s", exc)
            raise SearchUnavailable(
                "Search backend is temporarily unavailable",
                details={"error": exc.__class__.__name__},
            ) from exc

        hits_data = raw_response.get("hits", {})
        total_val = hits_data.get("total", {})
        total = total_val.get("value", 0) if isinstance(total_val, dict) else int(total_val or 0)

        hits_list = hits_data.get("hits", [])
        items: list[SearchResultItem] = []
        for hit in hits_list:
            source = hit.get("_source", {})
            score = hit.get("_score")
            # Defensive tenant check: discard if tenant doesn't match
            if source.get("tenant_id") != query.tenant_id:
                logger.warning(
                    "Search hit tenant mismatch: expected %s, got %s",
                    query.tenant_id,
                    source.get("tenant_id"),
                )
                continue

            items.append(
                SearchResultItem(
                    id=str(source.get("id", "")),
                    tenant_id=str(source.get("tenant_id", "")),
                    slug=str(source.get("slug", "")),
                    name=str(source.get("name", "")),
                    description=source.get("description"),
                    status=str(source.get("status", "DRAFT")),
                    attributes=dict(source.get("attributes", {})),
                    skus=tuple(dict(s) for s in source.get("skus", ())),
                    created_at=str(source.get("created_at", "")),
                    updated_at=str(source.get("updated_at", "")),
                    version=int(source.get("version", 1)),
                    score=float(score) if score is not None else None,
                )
            )

        next_cursor: str | None = None
        if hits_list and len(hits_list) == safe_page_size:
            last_hit_sort = hits_list[-1].get("sort")
            if last_hit_sort:
                next_cursor = json.dumps(last_hit_sort)

        total_pages = max(1, math.ceil(total / safe_page_size)) if total > 0 else 1
        return SearchPageResult(
            items=tuple(items),
            total=total,
            page=safe_page,
            page_size=safe_page_size,
            total_pages=total_pages,
            next_cursor=next_cursor,
        )

    async def ensure_index(
        self,
        *,
        index_name: str,
        mappings: dict[str, object],
        aliases: tuple[str, ...] = (),
    ) -> bool:
        """Idempotently create index with mappings and aliases if not present."""
        try:
            exists = await self._client.indices.exists(index=index_name)
            if exists:
                return False

            aliases_dict: dict[str, dict[str, Any]] = {alias: {} for alias in aliases}
            settings_body: dict[str, Any] = {
                "number_of_shards": 1,
                "number_of_replicas": 0,
            }

            await self._client.indices.create(
                index=index_name,
                mappings=mappings,
                settings=settings_body,
                aliases=aliases_dict,
            )
            logger.info("Created Elasticsearch index '%s' with aliases %s", index_name, aliases)
            return True
        except ConflictError:
            return False
        except (TransportError, EsConnectionError, ConnectionTimeout) as exc:
            logger.error("ensure_index failed for %s: %s", index_name, exc)
            raise SearchUnavailable(
                "Failed to ensure search index",
                details={"index": index_name, "error": exc.__class__.__name__},
            ) from exc

    async def switch_alias(
        self,
        *,
        alias: str,
        new_index: str,
        old_indices: tuple[str, ...] = (),
    ) -> None:
        """Atomically update an alias to point to new_index."""
        try:
            current_old = old_indices
            if not current_old:
                current_old = await self.list_indices_for_alias(alias=alias)

            actions: list[dict[str, Any]] = []
            for old in current_old:
                if old != new_index:
                    actions.append({"remove": {"index": old, "alias": alias}})
            actions.append({"add": {"index": new_index, "alias": alias}})

            await self._client.indices.update_aliases(actions=actions)
            logger.info(
                "Switched alias '%s' to '%s' (removed from %s)",
                alias,
                new_index,
                current_old,
            )
        except (TransportError, EsConnectionError, ConnectionTimeout) as exc:
            logger.error("Failed to switch alias '%s': %s", alias, exc)
            raise SearchUnavailable(
                "Failed to switch alias",
                details={"alias": alias, "new_index": new_index, "error": exc.__class__.__name__},
            ) from exc

    async def list_indices_for_alias(self, *, alias: str) -> tuple[str, ...]:
        """Return the physical index names currently assigned to an alias."""
        try:
            result = await self._client.indices.get_alias(name=alias)
            return tuple(str(idx) for idx in result)
        except NotFoundError:
            return ()
        except (TransportError, EsConnectionError, ConnectionTimeout) as exc:
            logger.error("Failed to list indices for alias %s: %s", alias, exc)
            raise SearchUnavailable(
                "Failed to lookup alias",
                details={"alias": alias, "error": exc.__class__.__name__},
            ) from exc

    async def delete_index(self, *, index_name: str) -> bool:
        """Delete an index by name."""
        try:
            exists = await self._client.indices.exists(index=index_name)
            if not exists:
                return False
            await self._client.indices.delete(index=index_name, ignore_unavailable=True)
            logger.info("Deleted Elasticsearch index '%s'", index_name)
            return True
        except (TransportError, EsConnectionError, ConnectionTimeout) as exc:
            logger.error("Failed to delete index %s: %s", index_name, exc)
            raise SearchUnavailable(
                "Failed to delete search index",
                details={"index": index_name, "error": exc.__class__.__name__},
            ) from exc

    async def count_documents(self, *, index_or_alias: str, tenant_id: str | None = None) -> int:
        """Count total documents in an index, optionally scoped to a tenant."""
        query: dict[str, Any] = {"match_all": {}}
        if tenant_id:
            query = {"term": {"tenant_id": tenant_id}}

        try:
            res = await self._client.count(index=index_or_alias, query=query)
            return int(res.get("count", 0))
        except (TransportError, EsConnectionError, ConnectionTimeout) as exc:
            logger.error("Failed to count documents in %s: %s", index_or_alias, exc)
            raise SearchUnavailable(
                "Failed to count documents in search index",
                details={"index": index_or_alias, "error": exc.__class__.__name__},
            ) from exc

    async def get_document_keys(
        self, *, index_or_alias: str, tenant_id: str | None = None
    ) -> dict[str, tuple[str, int]]:
        """Return mapping of document_id -> (tenant_id, version) for reconciliation."""
        query: dict[str, Any] = {"match_all": {}}
        if tenant_id:
            query = {"term": {"tenant_id": tenant_id}}

        result: dict[str, tuple[str, int]] = {}
        try:
            response = await self._client.search(
                index=index_or_alias,
                query=query,
                source_includes=["tenant_id", "version"],
                size=10000,
            )
            for hit in response.get("hits", {}).get("hits", []):
                doc_id = str(hit.get("_id", ""))
                src = hit.get("_source", {})
                t_id = str(src.get("tenant_id", ""))
                ver = int(src.get("version", 1))
                result[doc_id] = (t_id, ver)
            return result
        except (TransportError, EsConnectionError, ConnectionTimeout) as exc:
            logger.error("Failed to get document keys from %s: %s", index_or_alias, exc)
            raise SearchUnavailable(
                "Failed to read keys from search index",
                details={"index": index_or_alias, "error": exc.__class__.__name__},
            ) from exc

    async def refresh_index(self, *, index_name: str) -> None:
        """Explicitly refresh an index so newly indexed documents become searchable."""
        try:
            await self._client.indices.refresh(index=index_name)
        except (TransportError, EsConnectionError, ConnectionTimeout) as exc:
            logger.error("Failed to refresh index %s: %s", index_name, exc)
            raise SearchUnavailable(
                "Failed to refresh index", details={"index": index_name}
            ) from exc

    async def get_document_keys_chunk(
        self,
        *,
        index_or_alias: str,
        tenant_id: str | None = None,
        search_after: tuple[Any, ...] | None = None,
        limit: int = 500,
    ) -> tuple[dict[str, tuple[str, int]], tuple[Any, ...] | None]:
        """Fetch a chunk of document keys using search_after for bounded memory."""
        query: dict[str, Any] = {"match_all": {}}
        if tenant_id:
            query = {"term": {"tenant_id": tenant_id}}

        sort_clause = [{"id": {"order": "asc"}}]
        search_kwargs: dict[str, Any] = {
            "index": index_or_alias,
            "query": query,
            "source_includes": ["tenant_id", "version"],
            "sort": sort_clause,
            "size": limit,
        }
        if search_after:
            search_kwargs["search_after"] = list(search_after)

        try:
            response = await self._client.search(**search_kwargs)
            hits = response.get("hits", {}).get("hits", [])
            result: dict[str, tuple[str, int]] = {}
            for hit in hits:
                doc_id = str(hit.get("_id", ""))
                src = hit.get("_source", {})
                result[doc_id] = (str(src.get("tenant_id", "")), int(src.get("version", 1)))

            next_sa = (
                tuple(hits[-1]["sort"])
                if len(hits) == limit and "sort" in hits[-1]
                else None
            )
            return result, next_sa
        except (TransportError, EsConnectionError, ConnectionTimeout) as exc:
            logger.error("Failed to fetch document keys chunk from %s: %s", index_or_alias, exc)
            raise SearchUnavailable(
                "Failed to read keys from search index",
                details={"index": index_or_alias, "error": exc.__class__.__name__},
            ) from exc

    async def ping(self) -> bool:
        """Probe Elasticsearch cluster availability."""
        try:
            return bool(await self._client.ping())
        except Exception:
            return False

    async def close(self) -> None:
        """Close client connection pool."""
        await self._client.close()


__all__ = ["ALLOWED_SORT_FIELDS", "ElasticsearchIndexAdapter"]
