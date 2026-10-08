"""Search projection handler and cache invalidation.

Listens to Catalog domain events, projects changes into the Elasticsearch
read model (ProductSearchDocument), and selectively invalidates relevant
Redis cache keys.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from catalog.ports.events import (
    ProductActivated,
    ProductArchived,
    ProductCreated,
    ProductRevised,
    SkuActivated,
    SkuCreated,
    SkuDeactivated,
    SkuPriceChanged,
)
from search.application.models import (
    PRODUCTS_INDEX_ALIAS,
    ProductSearchDocument,
    build_product_document_id,
)
from search.ports.indexer import SearchIndexer
from shared.infrastructure.cache.keys import default_key_builder
from shared.ports.cache import CachePort

logger = logging.getLogger(__name__)


class ProductSearchProjector:
    """Projector that maintains Elasticsearch read models and invalidates caches."""

    def __init__(
        self,
        indexer: SearchIndexer,
        cache: CachePort | None = None,
        index_alias: str = PRODUCTS_INDEX_ALIAS,
    ) -> None:
        self._indexer = indexer
        self._cache = cache
        self._index_alias = index_alias

    async def handle_event(self, event: Any) -> None:
        """Route domain event or serialized event dictionary to specific handler."""
        if isinstance(event, dict):
            await self._handle_event_dict(event)
            return

        match event:
            case ProductCreated():
                await self.on_product_created(event)
            case ProductRevised():
                await self.on_product_revised(event)
            case ProductActivated():
                await self.on_product_activated(event)
            case ProductArchived():
                await self.on_product_archived(event)
            case SkuCreated():
                await self.on_sku_created(event)
            case SkuPriceChanged():
                await self.on_sku_price_changed(event)
            case SkuActivated():
                await self.on_sku_activated(event)
            case SkuDeactivated():
                await self.on_sku_deactivated(event)
            case _:
                logger.debug("Unhandled event type in projector: %s", type(event).__name__)
                return

        await self._bump_search_version(str(event.tenant_id))

    async def _handle_event_dict(self, data: dict[str, Any]) -> None:
        """Handle raw event dictionary from message queue."""
        event_name = data.get("event_name") or data.get("event_type") or data.get("type", "")
        payload = data.get("payload", data)

        tenant_id = str(payload.get("tenant_id") or data.get("tenant_id") or "")
        if not tenant_id:
            logger.warning("Event missing tenant_id: %s", event_name)
            return

        match event_name:
            case "ProductCreated":
                doc = ProductSearchDocument(
                    id=str(payload["product_id"]),
                    tenant_id=tenant_id,
                    slug=str(payload["slug"]),
                    name=str(payload["name"]),
                    status="DRAFT",
                    version=int(payload.get("version", 1)),
                )
                await self._index_document(doc)
                await self._invalidate_cache(
                    default_key_builder.product(tenant_id, payload["product_id"])
                )

            case "ProductRevised":
                product_id = str(payload["product_id"])
                doc = await self._load_or_create_doc(tenant_id, product_id)
                doc.slug = str(payload["slug"])
                if payload.get("name"):
                    doc.name = str(payload["name"])
                doc.version = int(payload.get("version", doc.version + 1))
                doc.updated_at = datetime.now(UTC).isoformat()
                await self._index_document(doc)
                await self._invalidate_cache(default_key_builder.product(tenant_id, product_id))

            case "ProductActivated":
                product_id = str(payload["product_id"])
                doc = await self._load_or_create_doc(tenant_id, product_id)
                doc.status = "ACTIVE"
                doc.version = int(payload.get("version", doc.version + 1))
                doc.updated_at = datetime.now(UTC).isoformat()
                await self._index_document(doc)
                await self._invalidate_cache(default_key_builder.product(tenant_id, product_id))

            case "ProductArchived":
                product_id = str(payload["product_id"])
                doc = await self._load_or_create_doc(tenant_id, product_id)
                doc.status = "ARCHIVED"
                doc.version = int(payload.get("version", doc.version + 1))
                doc.updated_at = datetime.now(UTC).isoformat()
                await self._index_document(doc)
                await self._invalidate_cache(default_key_builder.product(tenant_id, product_id))

            case "SkuCreated":
                product_id = str(payload["product_id"])
                sku_id = str(payload["sku_id"])
                doc = await self._load_or_create_doc(tenant_id, product_id)
                new_sku = {
                    "sku_id": sku_id,
                    "sku_code": str(payload.get("sku_code", "")),
                    "unit_price_minor": int(payload.get("unit_price_minor", 0)),
                    "currency": str(payload.get("currency", "")),
                    "status": "ACTIVE",
                }
                doc.skus = [s for s in doc.skus if str(s.get("sku_id")) != sku_id]
                doc.skus.append(new_sku)
                doc.version = int(payload.get("version", doc.version + 1))
                doc.updated_at = datetime.now(UTC).isoformat()
                await self._index_document(doc)
                await self._invalidate_cache(default_key_builder.product(tenant_id, product_id))
                await self._invalidate_cache(default_key_builder.sku(tenant_id, sku_id))

            case "SkuPriceChanged":
                sku_id = str(payload["sku_id"])
                product_id = payload.get("product_id")
                if product_id:
                    doc = await self._load_or_create_doc(tenant_id, str(product_id))
                    for s in doc.skus:
                        if str(s.get("sku_id")) == sku_id:
                            s["unit_price_minor"] = int(payload.get("new_price_minor", 0))
                            s["currency"] = str(payload.get("currency", s.get("currency", "")))
                    doc.version = int(payload.get("version", doc.version + 1))
                    doc.updated_at = datetime.now(UTC).isoformat()
                    await self._index_document(doc)
                    await self._invalidate_cache(
                        default_key_builder.product(tenant_id, str(product_id))
                    )
                await self._invalidate_cache(default_key_builder.sku(tenant_id, sku_id))

            case "SkuActivated" | "SkuDeactivated":
                sku_id = str(payload["sku_id"])
                product_id = payload.get("product_id")
                new_status = "ACTIVE" if event_name == "SkuActivated" else "INACTIVE"
                if product_id:
                    doc = await self._load_or_create_doc(tenant_id, str(product_id))
                    for s in doc.skus:
                        if str(s.get("sku_id")) == sku_id:
                            s["status"] = new_status
                    doc.version = int(payload.get("version", doc.version + 1))
                    doc.updated_at = datetime.now(UTC).isoformat()
                    await self._index_document(doc)
                    await self._invalidate_cache(
                        default_key_builder.product(tenant_id, str(product_id))
                    )
                await self._invalidate_cache(default_key_builder.sku(tenant_id, sku_id))

            case _:
                logger.debug("Unhandled event name in projector: %s", event_name)
                return

        await self._bump_search_version(tenant_id)

    async def on_product_created(self, event: ProductCreated) -> None:
        """Handle ProductCreated domain event."""
        doc = ProductSearchDocument(
            id=str(event.product_id),
            tenant_id=str(event.tenant_id),
            slug=event.slug,
            name=event.name,
            status="DRAFT",
            version=1,
        )
        await self._index_document(doc)
        await self._invalidate_cache(default_key_builder.product(event.tenant_id, event.product_id))

    async def on_product_revised(self, event: ProductRevised) -> None:
        """Handle ProductRevised domain event."""
        tenant_str = str(event.tenant_id)
        prod_str = str(event.product_id)
        doc = await self._load_or_create_doc(tenant_str, prod_str)
        doc.slug = event.slug
        if event.name is not None:
            doc.name = event.name
        doc.version += 1
        doc.updated_at = datetime.now(UTC).isoformat()
        await self._index_document(doc)
        await self._invalidate_cache(default_key_builder.product(event.tenant_id, event.product_id))

    async def on_product_activated(self, event: ProductActivated) -> None:
        """Handle ProductActivated domain event."""
        tenant_str = str(event.tenant_id)
        prod_str = str(event.product_id)
        doc = await self._load_or_create_doc(tenant_str, prod_str)
        doc.status = "ACTIVE"
        doc.version += 1
        doc.updated_at = datetime.now(UTC).isoformat()
        await self._index_document(doc)
        await self._invalidate_cache(default_key_builder.product(event.tenant_id, event.product_id))

    async def on_product_archived(self, event: ProductArchived) -> None:
        """Handle ProductArchived domain event."""
        tenant_str = str(event.tenant_id)
        prod_str = str(event.product_id)
        doc = await self._load_or_create_doc(tenant_str, prod_str)
        doc.status = "ARCHIVED"
        doc.version += 1
        doc.updated_at = datetime.now(UTC).isoformat()
        await self._index_document(doc)
        await self._invalidate_cache(default_key_builder.product(event.tenant_id, event.product_id))

    async def on_sku_created(self, event: SkuCreated) -> None:
        """Handle SkuCreated domain event."""
        tenant_str = str(event.tenant_id)
        prod_str = str(event.product_id)
        sku_str = str(event.sku_id)

        doc = await self._load_or_create_doc(tenant_str, prod_str)
        new_sku = {
            "sku_id": sku_str,
            "sku_code": event.sku_code,
            "unit_price_minor": event.unit_price_minor,
            "currency": event.currency,
            "status": "ACTIVE",
        }
        doc.skus = [s for s in doc.skus if str(s.get("sku_id")) != sku_str]
        doc.skus.append(new_sku)
        doc.version += 1
        doc.updated_at = datetime.now(UTC).isoformat()

        await self._index_document(doc)
        await self._invalidate_cache(default_key_builder.product(event.tenant_id, event.product_id))
        await self._invalidate_cache(default_key_builder.sku(event.tenant_id, event.sku_id))

    async def on_sku_price_changed(self, event: SkuPriceChanged) -> None:
        """Handle SkuPriceChanged domain event."""
        tenant_str = str(event.tenant_id)
        sku_str = str(event.sku_id)

        if event.product_id is not None:
            prod_str = str(event.product_id)
            doc = await self._load_or_create_doc(tenant_str, prod_str)
            for s in doc.skus:
                if str(s.get("sku_id")) == sku_str:
                    s["unit_price_minor"] = event.new_price_minor
                    s["currency"] = event.currency
            doc.version += 1
            doc.updated_at = datetime.now(UTC).isoformat()
            await self._index_document(doc)
            await self._invalidate_cache(
                default_key_builder.product(event.tenant_id, event.product_id)
            )

        await self._invalidate_cache(default_key_builder.sku(event.tenant_id, event.sku_id))

    async def on_sku_activated(self, event: SkuActivated) -> None:
        """Handle SkuActivated domain event."""
        tenant_str = str(event.tenant_id)
        sku_str = str(event.sku_id)

        if event.product_id is not None:
            prod_str = str(event.product_id)
            doc = await self._load_or_create_doc(tenant_str, prod_str)
            for s in doc.skus:
                if str(s.get("sku_id")) == sku_str:
                    s["status"] = "ACTIVE"
            doc.version += 1
            doc.updated_at = datetime.now(UTC).isoformat()
            await self._index_document(doc)
            await self._invalidate_cache(
                default_key_builder.product(event.tenant_id, event.product_id)
            )

        await self._invalidate_cache(default_key_builder.sku(event.tenant_id, event.sku_id))

    async def on_sku_deactivated(self, event: SkuDeactivated) -> None:
        """Handle SkuDeactivated domain event."""
        tenant_str = str(event.tenant_id)
        sku_str = str(event.sku_id)

        if event.product_id is not None:
            prod_str = str(event.product_id)
            doc = await self._load_or_create_doc(tenant_str, prod_str)
            for s in doc.skus:
                if str(s.get("sku_id")) == sku_str:
                    s["status"] = "INACTIVE"
            doc.version += 1
            doc.updated_at = datetime.now(UTC).isoformat()
            await self._index_document(doc)
            await self._invalidate_cache(
                default_key_builder.product(event.tenant_id, event.product_id)
            )

        await self._invalidate_cache(default_key_builder.sku(event.tenant_id, event.sku_id))

    # --- Internal helpers ---

    async def _load_or_create_doc(self, tenant_id: str, product_id: str) -> ProductSearchDocument:
        """Fetch existing document from Elasticsearch or return an initial stub."""
        doc_id = build_product_document_id(tenant_id, product_id)
        raw = await self._indexer.get_document(index_alias=self._index_alias, document_id=doc_id)
        if raw is not None:
            return ProductSearchDocument.from_dict(raw)
        return ProductSearchDocument(
            id=product_id,
            tenant_id=tenant_id,
            slug="",
            name="",
            version=1,
        )

    async def _index_document(self, doc: ProductSearchDocument) -> None:
        """Index search document using external optimistic versioning."""
        search_doc = doc.to_search_document(alias=self._index_alias)
        await self._indexer.index((search_doc,))

    async def _invalidate_cache(self, key: str) -> None:
        """Invalidate a Redis cache key safely without failing the projection."""
        if self._cache is None:
            return
        try:
            await self._cache.delete(key)
        except Exception:
            logger.warning("Failed to invalidate cache key %s; ignoring error", key, exc_info=True)

    async def _bump_search_version(self, tenant_id: str) -> None:
        """Bump tenant's search cache generation/version so old search keys expire naturally."""
        if self._cache is None:
            return
        try:
            version_key = default_key_builder.search_version(tenant_id)
            await self._cache.increment(version_key)
            logger.debug("Bumped search cache version for tenant %s", tenant_id)
        except Exception:
            logger.warning(
                "Failed to increment search cache version for tenant %s; ignoring error",
                tenant_id,
                exc_info=True,
            )


__all__ = ["ProductSearchProjector"]
