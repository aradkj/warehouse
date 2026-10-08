"""Search application layer."""

from search.application.models import (
    PRODUCTS_INDEX_ALIAS,
    PRODUCTS_INDEX_MAPPINGS,
    PRODUCTS_INDEX_V1,
    ProductSearchDocument,
    build_product_document_id,
)
from search.application.projector import ProductSearchProjector

__all__ = [
    "PRODUCTS_INDEX_ALIAS",
    "PRODUCTS_INDEX_MAPPINGS",
    "PRODUCTS_INDEX_V1",
    "ProductSearchDocument",
    "ProductSearchProjector",
    "build_product_document_id",
]
