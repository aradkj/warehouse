"""Search application layer."""

from search.application.audit_service import SearchAuditService
from search.application.models import (
    AUDIT_INDEX_ALIAS,
    AUDIT_INDEX_MAPPINGS,
    AUDIT_INDEX_V1,
    FULFILLMENT_EXCEPTIONS_INDEX_ALIAS,
    FULFILLMENT_EXCEPTIONS_INDEX_MAPPINGS,
    FULFILLMENT_EXCEPTIONS_INDEX_V1,
    ORDERS_INDEX_ALIAS,
    ORDERS_INDEX_MAPPINGS,
    ORDERS_INDEX_V1,
    PRODUCTS_INDEX_ALIAS,
    PRODUCTS_INDEX_MAPPINGS,
    PRODUCTS_INDEX_V1,
    AuditSearchDocument,
    FulfillmentExceptionSearchDocument,
    OrderSearchDocument,
    ProductSearchDocument,
    build_audit_document_id,
    build_fulfillment_exception_document_id,
    build_order_document_id,
    build_product_document_id,
)
from search.application.order_service import SearchOrdersService
from search.application.projector import ProductSearchProjector
from search.application.reconciliation import ReconciliationService
from search.application.reindex import ReindexingService
from search.application.service import SearchProductsService

__all__ = [
    "AUDIT_INDEX_ALIAS",
    "AUDIT_INDEX_MAPPINGS",
    "AUDIT_INDEX_V1",
    "FULFILLMENT_EXCEPTIONS_INDEX_ALIAS",
    "FULFILLMENT_EXCEPTIONS_INDEX_MAPPINGS",
    "FULFILLMENT_EXCEPTIONS_INDEX_V1",
    "ORDERS_INDEX_ALIAS",
    "ORDERS_INDEX_MAPPINGS",
    "ORDERS_INDEX_V1",
    "PRODUCTS_INDEX_ALIAS",
    "PRODUCTS_INDEX_MAPPINGS",
    "PRODUCTS_INDEX_V1",
    "AuditSearchDocument",
    "FulfillmentExceptionSearchDocument",
    "OrderSearchDocument",
    "ProductSearchDocument",
    "ProductSearchProjector",
    "ReconciliationService",
    "ReindexingService",
    "SearchAuditService",
    "SearchOrdersService",
    "SearchProductsService",
    "build_audit_document_id",
    "build_fulfillment_exception_document_id",
    "build_order_document_id",
    "build_product_document_id",
]
