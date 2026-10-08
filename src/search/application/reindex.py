"""Reindexing application service.

Rebuilds the Elasticsearch read model from PostgreSQL (the authoritative system of record).
Implements a safe versioned index / atomic alias swap pattern with automatic rollback
on failure to ensure zero search downtime and no partial-index activation.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Final

from search.application.models import (
    PRODUCTS_INDEX_ALIAS,
    PRODUCTS_INDEX_MAPPINGS,
    PRODUCTS_INDEX_SCHEMA_VERSION,
)
from search.ports.indexer import ReindexResult, SearchIndexer
from search.ports.reader import CatalogSearchReader
from shared.ports.cache import CachePort

logger = logging.getLogger(__name__)

DEFAULT_REINDEX_BATCH_SIZE: Final[int] = 200


class ReindexingService:
    """Orchestrates zero-downtime, authoritative reindexing from PostgreSQL to Elasticsearch."""

    def __init__(
        self,
        indexer: SearchIndexer,
        reader: CatalogSearchReader,
        cache: CachePort | None = None,
        *,
        batch_size: int = DEFAULT_REINDEX_BATCH_SIZE,
        alias: str = PRODUCTS_INDEX_ALIAS,
    ) -> None:
        self._indexer = indexer
        self._reader = reader
        self._cache = cache
        self._batch_size = batch_size
        self._alias = alias

    async def reindex(
        self,
        *,
        tenant_id: str | None = None,
        retain_old_indices: bool = False,
    ) -> ReindexResult:
        """Execute full rebuild of the search index from PostgreSQL.

        1. Creates a new versioned index: e.g. products-<timestamp>.
        2. Applies mappings and settings.
        3. Streams all records from PostgreSQL in memory-bounded batches.
        4. Bulk indexes each batch with external_gte optimistic versioning.
        5. Validates indexed document count against PostgreSQL source of truth.
        6. Atomically updates alias to point to the new index.
        7. Cleans up old index only after successful swap (unless retained).
        """
        timestamp = int(time.time())
        suffix = uuid.uuid4().hex[:8]
        new_index_name = f"{self._alias}-v{PRODUCTS_INDEX_SCHEMA_VERSION}-{timestamp}-{suffix}"

        logger.info(
            "Starting reindex workflow: target index '%s', alias '%s', tenant '%s'",
            new_index_name,
            self._alias,
            tenant_id or "ALL",
        )

        lock_token: str | None = None
        lock_key = f"lock:reindex:{self._alias}"
        if self._cache is not None:
            lock_token = await self._cache.acquire_lock(
                lock_key,
                ttl=600,
            )
            if lock_token is None:
                from shared.application.errors import ConcurrencyConflict

                raise ConcurrencyConflict(
                    f"Reindexing currently in progress for alias '{self._alias}'"
                )

        # 1. Create new index with deterministic mappings
        await self._indexer.ensure_index(
            index_name=new_index_name,
            mappings=PRODUCTS_INDEX_MAPPINGS,
        )

        total_indexed = 0
        try:
            # 2. Stream from PostgreSQL and bulk index in batches
            async for batch in self._reader.stream_search_documents(
                tenant_id=tenant_id,
                target_alias=new_index_name,
                batch_size=self._batch_size,
            ):
                if batch:
                    bulk_res = await self._indexer.bulk_index(batch, refresh=False)
                    if bulk_res.failed_count > 0:
                        raise RuntimeError(
                            f"Reindex bulk indexing failed for {bulk_res.failed_count} documents "
                            f"({bulk_res.permanent_failure_count} permanent failures). "
                            "Incomplete index will not be promoted to production alias."
                        )
                    total_indexed += bulk_res.successful_count

            # 3. Explicitly refresh new index so all bulk-indexed documents are searchable
            await self._indexer.refresh_index(index_name=new_index_name)

            # 4. Validate indexing count
            expected_count = await self._reader.count_authoritative_products(tenant_id=tenant_id)
            actual_count = await self._indexer.count_documents(
                index_or_alias=new_index_name,
                tenant_id=tenant_id,
            )

            if actual_count < expected_count:
                raise RuntimeError(
                    f"Reindex verification failed: expected {expected_count} documents from "
                    f"PostgreSQL, but only {actual_count} were indexed in {new_index_name}"
                )

            logger.info(
                "Reindex verified successfully: %d documents indexed in %s",
                total_indexed,
                new_index_name,
            )

            # 5. Atomic alias switch
            old_indices = await self._indexer.list_indices_for_alias(alias=self._alias)
            await self._indexer.switch_alias(
                alias=self._alias,
                new_index=new_index_name,
                old_indices=old_indices,
            )

            # 6. Invalidate search cache version for tenant if scoped
            if self._cache is not None and tenant_id is not None:
                try:
                    await self._cache.increment(f"v1:{tenant_id}:search:_version")
                except Exception as cache_err:
                    logger.warning("Failed to bump search version after reindex: %s", cache_err)

            # 7. Clean up old physical indices unless retention is requested
            if not retain_old_indices:
                for old in old_indices:
                    if old != new_index_name:
                        await self._indexer.delete_index(index_name=old)

            return ReindexResult(
                index_name=new_index_name,
                alias=self._alias,
                total_indexed=total_indexed,
                status="COMPLETED",
            )

        except Exception as exc:
            # SAFETY INVARIANT: If rebuild fails, destroy the incomplete index and
            # never switch alias. The active healthy index remains untouched.
            logger.error(
                "Reindex failed for %s (%s). Rolling back incomplete index.",
                new_index_name,
                exc,
                exc_info=True,
            )
            try:
                await self._indexer.delete_index(index_name=new_index_name)
            except Exception as cleanup_err:
                logger.warning(
                    "Failed to clean up incomplete index %s: %s", new_index_name, cleanup_err
                )
            raise
        finally:
            if self._cache is not None and lock_token is not None:
                try:
                    await self._cache.release_lock(lock_key, token=lock_token)
                except Exception as lock_err:
                    logger.warning("Failed to release reindex lock %s: %s", lock_key, lock_err)


__all__ = ["DEFAULT_REINDEX_BATCH_SIZE", "ReindexingService"]
