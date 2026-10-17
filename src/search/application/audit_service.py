"""Audit search application service."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any, Final

from search.application.models import AUDIT_INDEX_ALIAS
from search.ports.indexer import SearchIndexer, SearchPageResult, SearchQuery
from shared.application.errors import InvalidRequest
from shared.ports.cache import CachePort

logger = logging.getLogger(__name__)

ALLOWED_AUDIT_SORT_FIELDS: Final[frozenset[str]] = frozenset(
    {"occurred_at", "action", "resource_type", "policy_decision"}
)
DEFAULT_MAX_PAGE_SIZE: Final[int] = 100


class SearchAuditService:
    """Application query service for audit log search across actor, action, resource, etc."""

    def __init__(
        self,
        indexer: SearchIndexer,
        cache: CachePort | None = None,
        *,
        cache_ttl_seconds: int = 60,
        max_page_size: int = DEFAULT_MAX_PAGE_SIZE,
        index_alias: str = AUDIT_INDEX_ALIAS,
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
        action: str | None,
        actor_id: str | None,
        resource_type: str | None,
        resource_id: str | None,
        date_from: str | None,
        date_to: str | None,
        sort_by: str,
        sort_order: str,
    ) -> str:
        q_norm = (q or "").strip()
        action_norm = (action or "").strip()
        actor_norm = (actor_id or "").strip()
        res_type_norm = (resource_type or "").strip()
        res_id_norm = (resource_id or "").strip()
        date_from_norm = (date_from or "").strip()
        date_to_norm = (date_to or "").strip()
        sort_by_norm = (sort_by or "").strip().lower()
        sort_order_norm = (sort_order or "").strip().lower()

        normalized = (
            f"q={q_norm}&action={action_norm}&actor={actor_norm}&rtype={res_type_norm}&"
            f"rid={res_id_norm}&dfrom={date_from_norm}&dto={date_to_norm}&"
            f"sort={sort_by_norm}:{sort_order_norm}"
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    async def search(
        self,
        *,
        tenant_id: str,
        q: str | None = None,
        action: str | None = None,
        actor_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        page: int = 1,
        page_size: int = 20,
        limit: int | None = None,
        cursor: str | None = None,
        sort_by: str = "occurred_at",
        sort_order: str = "desc",
    ) -> SearchPageResult:
        if not tenant_id or not tenant_id.strip():
            raise InvalidRequest("tenant_id is required for audit search")

        sort_by_norm = sort_by.strip().lower()
        if sort_by_norm not in ALLOWED_AUDIT_SORT_FIELDS:
            raise InvalidRequest(
                f"invalid sort_by field '{sort_by}'",
                details={"allowed": sorted(ALLOWED_AUDIT_SORT_FIELDS)},
            )

        sort_order_norm = sort_order.strip().lower()
        if sort_order_norm not in ("asc", "desc"):
            raise InvalidRequest(
                f"invalid sort_order '{sort_order}'", details={"allowed": ["asc", "desc"]}
            )

        effective_page_size = limit if limit is not None else page_size
        if not 1 <= effective_page_size <= self._max_page_size:
            raise InvalidRequest(
                f"page_size/limit must be between 1 and {self._max_page_size}",
                details={"page_size": effective_page_size},
            )

        search_after_values: tuple[Any, ...] | None = None
        if cursor:
            try:
                decoded_bytes = base64.urlsafe_b64decode(cursor.encode("ascii"))
                cursor_payload = json.loads(decoded_bytes.decode("utf-8"))
                if not isinstance(cursor_payload, dict):
                    raise ValueError("cursor payload must be a dict")

                cursor_base_hash = cursor_payload.get("h")
                cursor_tenant_id = cursor_payload.get("t")
                expected_base_hash = self._compute_base_hash(
                    q=q,
                    action=action,
                    actor_id=actor_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    date_from=date_from,
                    date_to=date_to,
                    sort_by=sort_by_norm,
                    sort_order=sort_order_norm,
                )

                if cursor_tenant_id != tenant_id:
                    raise InvalidRequest(
                        "pagination cursor is not valid for this tenant",
                        details={"cursor": cursor},
                    )
                if cursor_base_hash != expected_base_hash:
                    raise InvalidRequest(
                        "pagination cursor does not match the current search query",
                        details={"cursor": cursor},
                    )

                raw_values = cursor_payload.get("v")
                if not isinstance(raw_values, list) or len(raw_values) < 2:
                    raise ValueError("cursor values must contain sort token and tiebreaker id")
                search_after_values = tuple(raw_values)
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                if isinstance(exc, InvalidRequest):
                    raise
                raise InvalidRequest(
                    "malformed or invalid pagination cursor", details={"cursor": cursor}
                ) from exc

        query_hash = self._compute_base_hash(
            q=q,
            action=action,
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id,
            date_from=date_from,
            date_to=date_to,
            sort_by=sort_by_norm,
            sort_order=sort_order_norm,
        )

        query = SearchQuery(
            tenant_id=tenant_id,
            q=q.strip() if q and q.strip() else None,
            action=action.strip() if action and action.strip() else None,
            actor_id=actor_id.strip() if actor_id and actor_id.strip() else None,
            resource_type=(
                resource_type.strip() if resource_type and resource_type.strip() else None
            ),
            resource_id=resource_id.strip() if resource_id and resource_id.strip() else None,
            date_from=date_from.strip() if date_from and date_from.strip() else None,
            date_to=date_to.strip() if date_to and date_to.strip() else None,
            page=max(1, page),
            page_size=effective_page_size,
            sort_by=sort_by_norm,
            sort_order=sort_order_norm,
            index_alias=self._index_alias,
            cursor=cursor,
            search_after=search_after_values,
        )

        async def _execute_search() -> dict[str, Any]:
            result = await self._indexer.search(query)
            opaque_next_cursor: str | None = None
            if result.next_cursor:
                try:
                    sort_tokens = json.loads(result.next_cursor)
                    cursor_dict = {
                        "v": sort_tokens,
                        "h": query_hash,
                        "t": tenant_id,
                    }
                    opaque_next_cursor = base64.urlsafe_b64encode(
                        json.dumps(cursor_dict).encode("utf-8")
                    ).decode("ascii")
                except Exception as exc:
                    logger.warning("Failed to encode next_cursor: %s", exc)
                    opaque_next_cursor = None

            page_result = SearchPageResult(
                items=result.items,
                total=result.total,
                page=result.page,
                page_size=result.page_size,
                total_pages=result.total_pages,
                next_cursor=opaque_next_cursor,
            )
            return page_result.to_dict()

        if self._cache is not None:
            current_gen: int | None = None
            try:
                from shared.infrastructure.cache.keys import default_key_builder

                ver_key = default_key_builder.search_version(tenant_id)
                ver_val = await self._cache.get(ver_key)
                if ver_val is not None:
                    current_gen = int(ver_val)
            except Exception:
                current_gen = None

            from shared.infrastructure.cache.keys import default_key_builder

            cache_key = default_key_builder.search(
                tenant_id=tenant_id,
                resource_type="audit",
                query_hash=query_hash,
                version=current_gen,
            )
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
                    "Audit search cache lookup failed for %s, falling back to indexer: %s",
                    cache_key,
                    exc,
                )

        dict_result = await _execute_search()
        return SearchPageResult.from_dict(dict_result)


__all__ = ["ALLOWED_AUDIT_SORT_FIELDS", "SearchAuditService"]
