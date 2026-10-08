"""Search application service.

Coordinates tenant-scoped search queries, input validation, allowlisted sorting,
Redis cache-aside with stampede protection, cursor pagination, and execution
against the SearchIndexer port.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from typing import Any, Final

from search.application.models import PRODUCTS_INDEX_ALIAS
from search.ports.indexer import (
    SearchIndexer,
    SearchPageResult,
    SearchQuery,
)
from shared.application.errors import InvalidRequest
from shared.infrastructure.cache.keys import default_key_builder
from shared.ports.cache import CachePort

logger = logging.getLogger(__name__)

ALLOWED_SORT_FIELDS: Final[frozenset[str]] = frozenset(
    {"created_at", "updated_at", "name", "status", "slug"}
)
DEFAULT_MAX_PAGE_SIZE: Final[int] = 100


class SearchProductsService:
    """Application query service for product catalog search."""

    def __init__(
        self,
        indexer: SearchIndexer,
        cache: CachePort | None = None,
        *,
        cache_ttl_seconds: int = 60,
        max_page_size: int = DEFAULT_MAX_PAGE_SIZE,
        index_alias: str = PRODUCTS_INDEX_ALIAS,
    ) -> None:
        self._indexer = indexer
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds
        self._max_page_size = max_page_size
        self._index_alias = index_alias

    def _compute_base_hash(
        self,
        *,
        q: str | None,
        status: str | None,
        sku: str | None,
        sort_by: str,
        sort_order: str,
    ) -> str:
        """Compute query hash for cursor validation (excluding pagination)."""
        normalized = (
            f"q={q or ''}&status={status or ''}&sku={sku or ''}&sort={sort_by}:{sort_order}"
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def _compute_query_hash(
        self,
        *,
        q: str | None,
        status: str | None,
        sku: str | None,
        page: int,
        page_size: int,
        sort_by: str,
        sort_order: str,
        cursor: str | None = None,
    ) -> str:
        """Compute a deterministic hash representing the normalized query parameters."""
        normalized = (
            f"q={q or ''}&status={status or ''}&sku={sku or ''}&"
            f"p={page}&s={page_size}&sort={sort_by}:{sort_order}&cur={cursor or ''}"
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    async def search(
        self,
        *,
        tenant_id: str,
        q: str | None = None,
        status: str | None = None,
        sku: str | None = None,
        page: int = 1,
        page_size: int = 20,
        limit: int | None = None,
        cursor: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> SearchPageResult:
        """Execute tenant-scoped search with caching, stampede protection, and cursor support."""
        # 1. Input validation
        if not tenant_id or not tenant_id.strip():
            raise InvalidRequest("tenant_id is required for search queries")

        effective_page_size = limit if limit is not None else page_size
        if page < 1:
            raise InvalidRequest("page must be greater than or equal to 1", details={"page": page})

        if effective_page_size < 1:
            raise InvalidRequest(
                "page_size must be greater than or equal to 1",
                details={"page_size": effective_page_size},
            )

        if effective_page_size > self._max_page_size:
            raise InvalidRequest(
                f"page_size cannot exceed maximum of {self._max_page_size}",
                details={
                    "page_size": effective_page_size,
                    "max_page_size": self._max_page_size,
                },
            )

        normalized_sort_by = sort_by.strip().lower()
        if normalized_sort_by not in ALLOWED_SORT_FIELDS:
            raise InvalidRequest(
                f"sorting by '{sort_by}' is not permitted",
                details={"allowed_fields": sorted(ALLOWED_SORT_FIELDS)},
            )

        normalized_sort_order = sort_order.strip().lower()
        if normalized_sort_order not in ("asc", "desc"):
            raise InvalidRequest(
                "sort_order must be either 'asc' or 'desc'",
                details={"sort_order": sort_order},
            )

        cleaned_q = q.strip() if q and q.strip() else None
        cleaned_status = status.strip().upper() if status and status.strip() else None
        cleaned_sku = sku.strip() if sku and sku.strip() else None

        base_hash = self._compute_base_hash(
            q=cleaned_q,
            status=cleaned_status,
            sku=cleaned_sku,
            sort_by=normalized_sort_by,
            sort_order=normalized_sort_order,
        )

        # 2. Cursor validation and extraction
        search_after_tuple: tuple[Any, ...] | None = None
        if cursor is not None and cursor.strip():
            try:
                raw_bytes = base64.urlsafe_b64decode(cursor.strip())
                cursor_payload = json.loads(raw_bytes.decode("utf-8"))
                if not isinstance(cursor_payload, dict):
                    raise ValueError("Invalid cursor format")
                if cursor_payload.get("v") != 1:
                    raise ValueError("Unsupported cursor version")
                if str(cursor_payload.get("t", "")) != tenant_id:
                    raise InvalidRequest("Search cursor does not match authenticated tenant")
                if str(cursor_payload.get("qh", "")) != base_hash:
                    raise InvalidRequest(
                        "Search cursor cannot be used with different query, filter, or sort"
                    )
                sa = cursor_payload.get("sa")
                if not isinstance(sa, list) or not sa:
                    raise ValueError("Invalid search_after array in cursor")
                search_after_tuple = tuple(sa)
            except InvalidRequest:
                raise
            except Exception as exc:
                raise InvalidRequest("Invalid search cursor") from exc

        # 3. Query hash and tenant search cache generation
        query_hash = self._compute_query_hash(
            q=cleaned_q,
            status=cleaned_status,
            sku=cleaned_sku,
            page=page,
            page_size=effective_page_size,
            sort_by=normalized_sort_by,
            sort_order=normalized_sort_order,
            cursor=cursor,
        )

        current_gen: int | None = None
        if self._cache is not None:
            try:
                ver_key = default_key_builder.search_version(tenant_id)
                ver_val = await self._cache.get(ver_key)
                if ver_val is not None:
                    current_gen = int(ver_val)
            except Exception:
                current_gen = None

        cache_key = default_key_builder.search(
            tenant_id=tenant_id,
            resource_type="products",
            query_hash=query_hash,
            version=current_gen,
        )

        query = SearchQuery(
            tenant_id=tenant_id,
            q=cleaned_q,
            status=cleaned_status,
            sku=cleaned_sku,
            page=page,
            page_size=effective_page_size,
            sort_by=normalized_sort_by,
            sort_order=normalized_sort_order,
            index_alias=self._index_alias,
            cursor=cursor,
            search_after=search_after_tuple,
        )

        # 4. Stampede-protected search factory
        async def _execute_search() -> dict[str, Any]:
            t0 = time.monotonic()
            raw_result = await self._indexer.search(query)
            duration_ms = (time.monotonic() - t0) * 1000
            logger.info(
                "Elasticsearch search executed: tenant=%s, items=%d, total=%d, duration=%.2fms",
                tenant_id,
                len(raw_result.items),
                raw_result.total,
                duration_ms,
            )

            opaque_next_cursor: str | None = None
            if raw_result.next_cursor:
                try:
                    sa_list = json.loads(raw_result.next_cursor)
                    token_data = {
                        "v": 1,
                        "t": tenant_id,
                        "qh": base_hash,
                        "sa": sa_list,
                    }
                    token_json = json.dumps(token_data)
                    opaque_next_cursor = base64.urlsafe_b64encode(
                        token_json.encode("utf-8")
                    ).decode("ascii")
                except Exception as err:
                    logger.warning("Failed to encode next_cursor token: %s", err)
                    opaque_next_cursor = None

            page_result = SearchPageResult(
                items=raw_result.items,
                total=raw_result.total,
                page=raw_result.page,
                page_size=raw_result.page_size,
                total_pages=raw_result.total_pages,
                next_cursor=opaque_next_cursor,
            )
            return page_result.to_dict()

        if self._cache is not None:
            try:
                cached_dict = await self._cache.get_or_set(
                    key=cache_key,
                    factory=_execute_search,
                    ttl=self._cache_ttl_seconds,
                )
                if isinstance(cached_dict, dict):
                    return SearchPageResult.from_dict(cached_dict)
            except Exception as exc:
                logger.warning(
                    "Search cache lookup/stampede failed for %s, falling back to ES: %s",
                    cache_key,
                    exc,
                )

        # 5. Direct execution fallback
        dict_result = await _execute_search()
        return SearchPageResult.from_dict(dict_result)


__all__ = ["ALLOWED_SORT_FIELDS", "DEFAULT_MAX_PAGE_SIZE", "SearchProductsService"]
