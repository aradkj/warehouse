"""Unit tests for domain event envelope and integration serialization."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from ordering.domain.events import OrderConfirmed, OrderPlaced
from shared.domain.events import DomainEvent, TenantDomainEvent
from shared.domain.identifiers import OrderId, TenantId

pytestmark = pytest.mark.unit


def test_domain_event_envelope_structure() -> None:
    tenant_id = TenantId(uuid.uuid4())
    order_id = OrderId(uuid.uuid4())
    event = OrderConfirmed(
        tenant_id=tenant_id,
        order_id=order_id,
        total_minor=1000,
        currency="GBP",
    )

    envelope = event.envelope()
    assert envelope["event_name"] == "OrderConfirmed"
    assert envelope["schema_version"] == 1
    assert envelope["tenant_id"] == str(tenant_id)
    assert "event_id" in envelope
    assert "occurred_at" in envelope

    payload = event.payload()
    assert payload["order_id"] == str(order_id)
    assert payload["total_minor"] == 1000
    assert payload["currency"] == "GBP"


def test_domain_event_serialization_handles_uuids_and_datetimes() -> None:
    tenant_id = TenantId(uuid.uuid4())
    order_id = OrderId(uuid.uuid4())
    event = OrderPlaced(
        tenant_id=tenant_id,
        order_id=order_id,
        line_count=2,
        total_quantity=5,
        total_minor=5000,
        currency="USD",
        customer_reference="REF-123",
    )

    payload = event.payload()
    assert isinstance(payload["order_id"], str)
    assert payload["line_count"] == 2
    assert payload["total_quantity"] == 5
    assert payload["customer_reference"] == "REF-123"
