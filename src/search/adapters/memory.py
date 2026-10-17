"""In-memory implementation of the SearchIndexer port for unit tests."""

from __future__ import annotations

import copy
import json
import math
from typing import Any

from search.ports.indexer import (
    BulkIndexResult,
    SearchDocument,
    SearchIndexer,
    SearchPageResult,
    SearchQuery,
    SearchResultItem,
)


class InMemorySearchIndexer(SearchIndexer):
    """In-memory search indexer for fast, isolated unit tests."""

    def __init__(self) -> None:
        # storage: {index_name_or_alias: {doc_id: (doc_body, version)}}
        self._indices: dict[str, dict[str, tuple[dict[str, Any], int]]] = {}
        # alias map: alias -> physical_index_name
        self._aliases: dict[str, set[str]] = {}
        self._connected: bool = True

    def _resolve_index_storage(self, index_or_alias: str) -> dict[str, tuple[dict[str, Any], int]]:
        """Resolve storage dict for index or alias."""
        if index_or_alias in self._aliases:
            # Union of all indices behind alias
            target_indices = self._aliases[index_or_alias]
            combined: dict[str, tuple[dict[str, Any], int]] = {}
            for idx in target_indices:
                if idx in self._indices:
                    combined.update(self._indices[idx])
            return combined
        return self._indices.setdefault(index_or_alias, {})

    async def index(
        self,
        documents: tuple[SearchDocument, ...],
        *,
        force: bool = False,
        refresh: bool | str = False,
    ) -> None:
        """Store or update documents in memory with optimistic version check."""
        if not self._connected:
            raise RuntimeError("InMemorySearchIndexer is closed")

        for doc in documents:
            alias = doc.index_alias
            target_names = self._aliases.get(alias, {alias})
            for target_name in target_names:
                target = self._indices.setdefault(target_name, {})
                current = target.get(doc.document_id)
                if current is not None and not force:
                    _, current_ver = current
                    if doc.version < current_ver:
                        continue

                target[doc.document_id] = (copy.deepcopy(doc.body), doc.version)

    async def bulk_index(
        self,
        documents: tuple[SearchDocument, ...],
        *,
        force: bool = False,
        refresh: bool | str = False,
    ) -> BulkIndexResult:
        """Bulk index documents in memory."""
        await self.index(documents, force=force, refresh=refresh)
        return BulkIndexResult(successful_count=len(documents))

    async def delete(
        self,
        *,
        index_alias: str,
        document_ids: tuple[str, ...],
        refresh: bool | str = False,
    ) -> None:
        """Idempotently delete documents by ID from the in-memory index."""
        if not self._connected:
            raise RuntimeError("InMemorySearchIndexer is closed")

        target_names = self._aliases.get(index_alias, {index_alias})
        for target_name in target_names:
            target = self._indices.get(target_name, {})
            for doc_id in document_ids:
                target.pop(doc_id, None)

    async def get_document(
        self,
        *,
        index_alias: str,
        document_id: str,
    ) -> dict[str, object] | None:
        """Retrieve a stored document by id."""
        if not self._connected:
            raise RuntimeError("InMemorySearchIndexer is closed")

        target = self._resolve_index_storage(index_alias)
        entry = target.get(document_id)
        if entry is None:
            return None
        return copy.deepcopy(entry[0])

    async def search(self, query: SearchQuery) -> SearchPageResult:
        """Execute a tenant-safe search in memory."""
        if not self._connected:
            raise RuntimeError("InMemorySearchIndexer is closed")

        target = self._resolve_index_storage(query.index_alias)
        matched_items: list[SearchResultItem] = []

        q_lower = query.q.strip().lower() if query.q and query.q.strip() else None

        for _doc_id, (body, ver) in target.items():
            # 1. Mandatory tenant filter
            if str(body.get("tenant_id")) != query.tenant_id:
                continue

            # 2. Index-specific filters
            is_audit = query.index_alias == "audit_logs" or "audit" in query.index_alias
            if is_audit:
                effective_action = query.action or query.status
                if effective_action and str(body.get("action") or "") != effective_action:
                    continue

                effective_actor = query.actor_id or query.customer
                if effective_actor and str(body.get("actor_id") or "") != effective_actor:
                    continue

                if (
                    query.resource_type
                    and str(body.get("resource_type") or "") != query.resource_type
                ):
                    continue

                if query.resource_id and str(body.get("resource_id") or "") != query.resource_id:
                    continue

                occurred_at_str = str(body.get("occurred_at") or "")
                if query.date_from and occurred_at_str < query.date_from:
                    continue
                if query.date_to and occurred_at_str > query.date_to:
                    continue
            else:
                if query.status and str(body.get("status") or "") != query.status:
                    continue

                if query.customer:
                    cust_match = (
                        str(body.get("customer_id") or "") == query.customer
                        or str(body.get("customer_reference") or "") == query.customer
                    )
                    if not cust_match:
                        continue

                if query.warehouse:
                    wh_ids = [str(w) for w in body.get("warehouse_ids", [])]
                    single_wh = str(body.get("warehouse_id") or "")
                    if query.warehouse not in wh_ids and query.warehouse != single_wh:
                        continue

                created_at_str = str(body.get("created_at") or "")
                if query.date_from and created_at_str < query.date_from:
                    continue
                if query.date_to and created_at_str > query.date_to:
                    continue

                skus_list = list(body.get("skus", []))
                if query.sku:
                    has_sku = any(
                        str(s.get("sku_code")) == query.sku or str(s.get("sku_id")) == query.sku
                        for s in skus_list
                    )
                    if not has_sku:
                        continue

            skus_list = list(body.get("skus", []))

            # 7. Text search
            if q_lower:
                name_match = q_lower in str(body.get("name", "")).lower()
                desc_match = q_lower in str(body.get("description", "") or "").lower()
                slug_match = q_lower in str(body.get("slug", "")).lower()
                sku_match = any(q_lower in str(s.get("sku_code", "")).lower() for s in skus_list)
                cust_ref_match = q_lower in str(body.get("customer_reference", "") or "").lower()
                order_id_match = q_lower in str(body.get("order_id", "") or "").lower()
                reason_match = q_lower in str(body.get("reason", "") or "").lower()
                actor_match = q_lower in str(body.get("actor_id", "") or "").lower()
                action_match = q_lower in str(body.get("action", "") or "").lower()
                dec_match = q_lower in str(body.get("decision_reason", "") or "").lower()
                if not (
                    name_match
                    or desc_match
                    or slug_match
                    or sku_match
                    or cust_ref_match
                    or order_id_match
                    or reason_match
                    or actor_match
                    or action_match
                    or dec_match
                ):
                    continue

            matched_items.append(
                SearchResultItem(
                    id=str(body.get("id", "")),
                    tenant_id=str(body.get("tenant_id", "")),
                    slug=str(body.get("slug", "")),
                    name=str(body.get("name", "")),
                    description=body.get("description"),
                    status=str(body.get("status", "DRAFT")),
                    attributes=dict(body.get("attributes", {})),
                    skus=tuple(dict(s) for s in skus_list),
                    created_at=str(body.get("created_at", "") or body.get("occurred_at", "")),
                    updated_at=str(body.get("updated_at", "")),
                    version=int(body.get("version", ver)),
                    score=1.0 if q_lower else None,
                    raw_source=dict(body),
                )
            )

        # 5. Sorting
        reverse = query.sort_order.lower() == "desc"
        default_sort = "occurred_at" if query.index_alias == "audit_logs" else "created_at"
        sort_field = query.sort_by if query.sort_by else default_sort

        def _extract_sort_val(item: SearchResultItem) -> Any:
            if sort_field in ("created_at", "updated_at", "status", "slug", "name"):
                return getattr(item, sort_field) or ""
            if item.raw_source and sort_field in item.raw_source:
                return str(item.raw_source[sort_field] or "")
            if sort_field == "occurred_at":
                return str(item.raw_source.get("occurred_at") or item.created_at or "")
            return ""

        matched_items.sort(
            key=lambda item: (_extract_sort_val(item), item.id),
            reverse=reverse,
        )

        # 6. Pagination & search_after
        total = len(matched_items)
        safe_page = max(1, query.page)
        safe_page_size = max(1, query.page_size)

        if query.search_after:
            target_val, target_id = query.search_after[0], query.search_after[1]
            filtered_items: list[SearchResultItem] = []
            for item in matched_items:
                cur_val = _extract_sort_val(item)
                if reverse:
                    is_after = (cur_val, item.id) < (target_val, target_id)
                else:
                    is_after = (cur_val, item.id) > (target_val, target_id)
                if is_after:
                    filtered_items.append(item)
            page_items = tuple(filtered_items[:safe_page_size])
        else:
            start = (safe_page - 1) * safe_page_size
            end = start + safe_page_size
            page_items = tuple(matched_items[start:end])

        next_cursor = None
        if len(page_items) == safe_page_size:
            last_item = page_items[-1]
            last_val = _extract_sort_val(last_item)
            next_cursor = json.dumps([last_val, last_item.id])

        total_pages = max(1, math.ceil(total / safe_page_size)) if total > 0 else 1

        return SearchPageResult(
            items=page_items,
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
        """Ensure an in-memory index and aliases exist."""
        if not self._connected:
            raise RuntimeError("InMemorySearchIndexer is closed")

        created = index_name not in self._indices
        if created:
            self._indices[index_name] = {}
        for alias in aliases:
            self._aliases.setdefault(alias, set()).add(index_name)
        return created

    async def refresh_index(self, *, index_name: str) -> None:
        """Explicitly refresh an in-memory index (no-op)."""
        pass

    async def switch_alias(
        self,
        *,
        alias: str,
        new_index: str,
        old_indices: tuple[str, ...] = (),
    ) -> None:
        """Atomically switch alias to point exclusively to new_index."""
        if not self._connected:
            raise RuntimeError("InMemorySearchIndexer is closed")
        self._aliases[alias] = {new_index}

    async def list_indices_for_alias(self, *, alias: str) -> tuple[str, ...]:
        """Return indices currently bound to alias."""
        return tuple(self._aliases.get(alias, set()))

    async def delete_index(self, *, index_name: str) -> bool:
        """Delete an in-memory index."""
        if not self._connected:
            raise RuntimeError("InMemorySearchIndexer is closed")
        existed = index_name in self._indices
        self._indices.pop(index_name, None)
        for _alias, targets in list(self._aliases.items()):
            targets.discard(index_name)
        return existed

    async def count_documents(self, *, index_or_alias: str, tenant_id: str | None = None) -> int:
        """Count total documents."""
        target = self._resolve_index_storage(index_or_alias)
        if tenant_id:
            return sum(1 for body, _ in target.values() if str(body.get("tenant_id")) == tenant_id)
        return len(target)

    async def get_document_keys(
        self, *, index_or_alias: str, tenant_id: str | None = None
    ) -> dict[str, tuple[str, int]]:
        """Return mapping of document_id -> (tenant_id, version)."""
        target = self._resolve_index_storage(index_or_alias)
        res: dict[str, tuple[str, int]] = {}
        for doc_id, (body, ver) in target.items():
            t_id = str(body.get("tenant_id", ""))
            if tenant_id and t_id != tenant_id:
                continue
            res[doc_id] = (t_id, ver)
        return res

    async def get_document_keys_chunk(
        self,
        *,
        index_or_alias: str,
        tenant_id: str | None = None,
        search_after: tuple[Any, ...] | None = None,
        limit: int = 500,
    ) -> tuple[dict[str, tuple[str, int]], tuple[Any, ...] | None]:
        """Fetch a chunk of document keys."""
        all_keys = await self.get_document_keys(index_or_alias=index_or_alias, tenant_id=tenant_id)
        sorted_keys = sorted(all_keys.items(), key=lambda x: x[0])
        if search_after:
            last_k = str(search_after[0])
            sorted_keys = [k for k in sorted_keys if k[0] > last_k]
        chunk = dict(sorted_keys[:limit])
        next_sa = (sorted_keys[limit - 1][0],) if len(sorted_keys) > limit else None
        return chunk, next_sa

    async def ping(self) -> bool:
        """Health check."""
        return self._connected

    async def close(self) -> None:
        """Clean up resources."""
        self._connected = False

    def clear(self) -> None:
        """Helper for test cleanup."""
        self._indices.clear()
        self._aliases.clear()


__all__ = ["InMemorySearchIndexer"]
