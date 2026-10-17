"""Order search application service."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any, Final

from search.application.models import ORDERS_INDEX_ALIAS
from search.ports.indexer import SearchIndexer, SearchPageResult, SearchQuery
from shared.application.errors import InvalidRequest
from shared.ports.cache import CachePort

logger = logging.getLogger(__name__)

ALLOWED_ORDER_SORT_FIELDS: Final[frozenset[str]] = frozenset(
    {"created_at", "updated_at", "status", "total_minor"}
)
DEFAULT_MAX_PAGE_SIZE: Final[int] = 100


class SearchOrdersService:
    """Application query service for order search across customer, SKU, status, and dates."""

    def __init__(
        self,
        indexer: SearchIndexer,
        cache: CachePort | None = None,
        *,
        cache_ttl_seconds: int = 60,
        max_page_size: int = DEFAULT_MAX_PAGE_SIZE,
        index_alias: str = ORDERS_INDEX_ALIAS,
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
        customer: str | None,
        warehouse: str | None,
        date_from: str | None = None,
        date_to: str | None = None,
        sort_by: str,
        sort_order: str,
    ) -> str:
        q_norm = (q or "").strip()
        status_norm = (status or "").strip().upper()
        sku_norm = (sku or "").strip().upper()
        cust_norm = (customer or "").strip()
        wh_norm = (warehouse or "").strip()
        date_from_norm = (date_from or "").strip()
        date_to_norm = (date_to or "").strip()
        sort_by_norm = (sort_by or "").strip().lower()
        sort_order_norm = (sort_order or "").strip().lower()

        normalized = (
            f"q={q_norm}&status={status_norm}&sku={sku_norm}&cust={cust_norm}&"
            f"wh={wh_norm}&dfrom={date_from_norm}&dto={date_to_norm}&"
            f"sort={sort_by_norm}:{sort_order_norm}"
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    async def search(
        self,
        *,
        tenant_id: str,
        q: str | None = None,
        status: str | None = None,
        sku: str | None = None,
        customer: str | None = None,
        warehouse: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        page: int = 1,
        page_size: int = 20,
        limit: int | None = None,
        cursor: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> SearchPageResult:
        if not tenant_id or not tenant_id.strip():
            raise InvalidRequest("tenant_id is required for order search")

        sort_by_norm = sort_by.strip().lower()
        if sort_by_norm not in ALLOWED_ORDER_SORT_FIELDS:
            raise InvalidRequest(
                f"invalid sort_by field '{sort_by}'",
                details={"allowed": sorted(ALLOWED_ORDER_SORT_FIELDS)},
            )

        sort_order_norm = sort_order.strip().lower()
        if sort_order_norm not in ("asc", "desc"):
            raise InvalidRequest(
                f"invalid sort_order '{sort_order}'", details={"allowed": ["asc", "desc"]}
            )

        if limit is not None:
            if not 1 <= limit <= self._max_page_size:
                raise InvalidRequest(
                    f"limit must be between 1 and {self._max_page_size}",
                    details={"limit": limit},
                )
            effective_page_size = limit
        else:
            if not 1 <= page_size <= self._max_page_size:
                raise InvalidRequest(
                    f"page_size must be between 1 and {self._max_page_size}",
                    details={"page_size": page_size},
                )
            effective_page_size = page_size

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
                    status=status,
                    sku=sku,
                    customer=customer,
                    warehouse=warehouse,
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

        query = SearchQuery(
            tenant_id=tenant_id,
            q=q.strip() if q and q.strip() else None,
            status=status.strip().upper() if status and status.strip() else None,
            sku=sku.strip().upper() if sku and sku.strip() else None,
            customer=customer.strip() if customer and customer.strip() else None,
            warehouse=warehouse.strip() if warehouse and warehouse.strip() else None,
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

        result = await self._indexer.search(query)

        opaque_next_cursor: str | None = None
        if result.next_cursor:
            try:
                sort_tokens = json.loads(result.next_cursor)
                cursor_dict = {
                    "v": sort_tokens,
                    "h": self._compute_base_hash(
                        q=q,
                        status=status,
                        sku=sku,
                        customer=customer,
                        warehouse=warehouse,
                        date_from=date_from,
                        date_to=date_to,
                        sort_by=sort_by_norm,
                        sort_order=sort_order_norm,
                    ),
                    "t": tenant_id,
                }
                encoded = base64.urlsafe_b64encode(json.dumps(cursor_dict).encode("utf-8")).decode(
                    "ascii"
                )
                opaque_next_cursor = encoded
            except Exception as exc:
                logger.warning("Failed to encode next_cursor: %s", exc)
                opaque_next_cursor = None

        return SearchPageResult(
            items=result.items,
            total=result.total,
            page=result.page,
            page_size=result.page_size,
            total_pages=result.total_pages,
            next_cursor=opaque_next_cursor,
        )


__all__ = ["SearchOrdersService"]
