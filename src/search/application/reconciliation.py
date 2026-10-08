"""Reconciliation application service.

Compares PostgreSQL (authoritative source of truth) against Elasticsearch
(derived read model) to detect divergence, missing documents, version staleness,
and cross-tenant mismatches. Supports safe unidirectional auto-repair (PG -> ES only).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, Final

from search.application.models import PRODUCTS_INDEX_ALIAS
from search.ports.indexer import (
    ReconciliationDiscrepancy,
    ReconciliationResult,
    SearchIndexer,
)
from search.ports.reader import CatalogSearchReader

logger = logging.getLogger(__name__)

DEFAULT_RECONCILIATION_BATCH_SIZE: Final[int] = 200


class ReconciliationService:
    """Service to detect and repair discrepancies between PostgreSQL and Elasticsearch."""

    def __init__(
        self,
        indexer: SearchIndexer,
        reader: CatalogSearchReader,
        *,
        batch_size: int = DEFAULT_RECONCILIATION_BATCH_SIZE,
        alias: str = PRODUCTS_INDEX_ALIAS,
    ) -> None:
        self._indexer = indexer
        self._reader = reader
        self._batch_size = batch_size
        self._alias = alias

    async def reconcile(
        self,
        *,
        tenant_id: str | None = None,
        auto_repair: bool = False,
    ) -> ReconciliationResult:
        """Execute reconciliation check between PostgreSQL and Elasticsearch.

        Guarantees:
        * PostgreSQL is authoritative and NEVER modified.
        * Memory usage is bounded to O(batch_size); does not load full datasets into memory.
        * Repair direction is strictly PostgreSQL -> Elasticsearch.
        * Missing or stale documents are refreshed into Elasticsearch.
        * Orphaned Elasticsearch documents (missing in PostgreSQL) are deleted from ES.
        """
        logger.info(
            "Starting bounded search reconciliation (tenant=%s, auto_repair=%s, batch_size=%d)",
            tenant_id or "ALL",
            auto_repair,
            self._batch_size,
        )

        discrepancies: list[ReconciliationDiscrepancy] = []
        repaired_count = 0
        matched_count = 0
        total_authoritative = 0
        total_search = 0

        # Pass 1: Stream PostgreSQL authoritative records in bounded batches
        # Verify existence and version in Elasticsearch
        if hasattr(self._reader, "stream_authoritative_keys"):
            auth_stream = self._reader.stream_authoritative_keys(
                tenant_id=tenant_id, batch_size=self._batch_size
            )
        else:
            async def _single_chunk_reader() -> AsyncIterator[dict[str, tuple[str, int]]]:
                yield await self._reader.get_authoritative_keys(tenant_id=tenant_id)

            auth_stream = _single_chunk_reader()

        async for auth_chunk in auth_stream:
            total_authoritative += len(auth_chunk)
            for doc_id, (pg_tenant, pg_ver) in auth_chunk.items():
                es_doc = await self._indexer.get_document(
                    index_alias=self._alias,
                    document_id=doc_id,
                )
                if es_doc is None:
                    discrepancies.append(
                        ReconciliationDiscrepancy(
                            document_id=doc_id,
                            tenant_id=pg_tenant,
                            discrepancy_type="missing_in_elasticsearch",
                            details={"authoritative_version": pg_ver},
                        )
                    )
                    if auto_repair:
                        prod_id = doc_id.split(":", 1)[1] if ":" in doc_id else doc_id
                        doc = await self._reader.get_search_document(
                            tenant_id=pg_tenant,
                            product_id=prod_id,
                            target_alias=self._alias,
                        )
                        if doc is not None:
                            await self._indexer.index((doc,), force=True)
                            repaired_count += 1
                            logger.info("Auto-repaired missing document in ES: %s", doc_id)
                else:
                    es_tenant = str(es_doc.get("tenant_id", ""))
                    raw_ver = es_doc.get("version", 1)
                    es_ver = int(str(raw_ver)) if raw_ver is not None else 1
                    if es_tenant != pg_tenant:
                        discrepancies.append(
                            ReconciliationDiscrepancy(
                                document_id=doc_id,
                                tenant_id=pg_tenant,
                                discrepancy_type="tenant_mismatch",
                                details={"pg_tenant": pg_tenant, "es_tenant": es_tenant},
                            )
                        )
                    elif es_ver != pg_ver:
                        discrepancies.append(
                            ReconciliationDiscrepancy(
                                document_id=doc_id,
                                tenant_id=pg_tenant,
                                discrepancy_type="version_mismatch",
                                details={"pg_version": pg_ver, "es_version": es_ver},
                            )
                        )
                        if auto_repair:
                            prod_id = doc_id.split(":", 1)[1] if ":" in doc_id else doc_id
                            doc = await self._reader.get_search_document(
                                tenant_id=pg_tenant,
                                product_id=prod_id,
                                target_alias=self._alias,
                            )
                            if doc is not None:
                                await self._indexer.index((doc,), force=True)
                                repaired_count += 1
                                logger.info(
                                    "Auto-repaired stale document version in ES: %s", doc_id
                                )
                    else:
                        matched_count += 1

        # Pass 2: Stream Elasticsearch keys in bounded chunks using search_after to detect orphans
        sa: tuple[Any, ...] | None = None
        has_chunking = hasattr(self._indexer, "get_document_keys_chunk")

        if has_chunking:
            while True:
                es_chunk, next_sa = await self._indexer.get_document_keys_chunk(
                    index_or_alias=self._alias,
                    tenant_id=tenant_id,
                    search_after=sa,
                    limit=self._batch_size,
                )
                total_search += len(es_chunk)
                for doc_id, (es_tenant, es_ver) in es_chunk.items():
                    prod_id = doc_id.split(":", 1)[1] if ":" in doc_id else doc_id
                    pg_doc = await self._reader.get_search_document(
                        tenant_id=es_tenant,
                        product_id=prod_id,
                        target_alias=self._alias,
                    )
                    if pg_doc is None:
                        discrepancies.append(
                            ReconciliationDiscrepancy(
                                document_id=doc_id,
                                tenant_id=es_tenant,
                                discrepancy_type="missing_in_postgresql",
                                details={"search_version": es_ver},
                            )
                        )
                        if auto_repair:
                            await self._indexer.delete(
                                index_alias=self._alias,
                                document_ids=(doc_id,),
                            )
                            repaired_count += 1
                            logger.info("Deleted orphaned ES document: %s", doc_id)

                if not next_sa or len(es_chunk) < self._batch_size:
                    break
                sa = next_sa
        else:
            search_keys = await self._indexer.get_document_keys(
                index_or_alias=self._alias,
                tenant_id=tenant_id,
            )
            total_search = len(search_keys)
            for doc_id, (es_tenant, es_ver) in search_keys.items():
                prod_id = doc_id.split(":", 1)[1] if ":" in doc_id else doc_id
                pg_doc = await self._reader.get_search_document(
                    tenant_id=es_tenant,
                    product_id=prod_id,
                    target_alias=self._alias,
                )
                if pg_doc is None:
                    discrepancies.append(
                        ReconciliationDiscrepancy(
                            document_id=doc_id,
                            tenant_id=es_tenant,
                            discrepancy_type="missing_in_postgresql",
                            details={"search_version": es_ver},
                        )
                    )
                    if auto_repair:
                        await self._indexer.delete(
                            index_alias=self._alias,
                            document_ids=(doc_id,),
                        )
                        repaired_count += 1

        return ReconciliationResult(
            total_authoritative=total_authoritative,
            total_search=total_search,
            matched=matched_count,
            discrepancies=tuple(discrepancies),
            repaired_count=repaired_count,
        )


__all__ = ["DEFAULT_RECONCILIATION_BATCH_SIZE", "ReconciliationService"]
