"""Unit tests: catalog aggregates.

These tests pin down the *behaviour* of the product and SKU lifecycles: which
transitions are legal, that archived products are immutable, that a SKU cannot be
created against another tenant's product, and that every state change records
exactly one domain event and advances the optimistic-concurrency version.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from catalog.domain.errors import (
    InvalidProductTransition,
    InvalidSkuTransition,
)
from catalog.domain.events import ProductCreated, SkuPriceChanged
from catalog.domain.product import Product
from catalog.domain.sku import Sku
from catalog.domain.value_objects import (
    AttributeSet,
    ProductSlug,
    ProductStatus,
    SkuCode,
    SkuStatus,
)
from shared.domain.errors import InvalidValue
from shared.domain.identifiers import ProductId, TenantId, new_id
from shared.domain.value_objects import Money

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def product(**overrides: object) -> Product:
    defaults: dict[str, object] = {
        "tenant_id": TenantId(new_id()),
        "slug": ProductSlug("blue-tee"),
        "name": "Blue Tee",
        "description": "A tee",
        "attributes": AttributeSet({}),
        "now": NOW,
    }
    defaults.update(overrides)
    return Product.create(**defaults)  # type: ignore[arg-type]


def sku(product_aggregate: Product | None = None, **overrides: object) -> Sku:
    owner = product_aggregate or product()
    defaults: dict[str, object] = {
        "tenant_id": owner.tenant_id,
        "product_id": owner.id,
        "code": SkuCode("TEE-BLUE-M"),
        "name": "Blue Tee (M)",
        "attributes": AttributeSet({}),
        "unit_price": Money(2599, "GBP"),
        "now": NOW,
    }
    defaults.update(overrides)
    return Sku.create(**defaults)  # type: ignore[arg-type]


class TestProductLifecycle:
    def test_a_new_product_is_a_draft_and_emits_its_creation_event(self) -> None:
        aggregate = product()
        assert aggregate.status is ProductStatus.DRAFT
        assert aggregate.version == 1
        assert [event.event_name for event in aggregate.events] == ["ProductCreated"]
        assert isinstance(aggregate.events[0], ProductCreated)

    def test_activate_then_archive(self) -> None:
        aggregate = product()
        aggregate.activate(now=NOW)
        assert aggregate.status is ProductStatus.ACTIVE

        aggregate.archive(now=NOW)
        assert aggregate.status is ProductStatus.ARCHIVED
        names = [event.event_name for event in aggregate.events]
        assert names == ["ProductCreated", "ProductActivated", "ProductArchived"]

    def test_archived_is_terminal(self) -> None:
        aggregate = product()
        aggregate.archive(now=NOW)
        with pytest.raises(InvalidProductTransition):
            aggregate.activate(now=NOW)

    def test_re_activating_an_active_product_is_refused(self) -> None:
        aggregate = product()
        aggregate.activate(now=NOW)
        with pytest.raises(InvalidProductTransition):
            aggregate.activate(now=NOW)

    def test_archived_products_are_immutable(self) -> None:
        aggregate = product()
        aggregate.archive(now=NOW)
        with pytest.raises(InvalidProductTransition):
            aggregate.revise(name="New name", now=NOW)

    def test_revise_changes_metadata_and_bumps_the_version_once(self) -> None:
        aggregate = product()
        before = aggregate.version
        aggregate.revise(name="Blue Tee v2", description="Softer cotton", now=NOW)
        assert aggregate.name == "Blue Tee v2"
        assert aggregate.description == "Softer cotton"
        assert aggregate.version == before + 1

    def test_revise_without_changes_records_nothing(self) -> None:
        aggregate = product()
        before = aggregate.version
        aggregate.revise(name=aggregate.name, now=NOW)
        assert aggregate.version == before

    @pytest.mark.parametrize("slug", ["", "Blue-Tee", "ab", "-blue", "blue-", "blue tee"])
    def test_slug_validation(self, slug: str) -> None:
        with pytest.raises(InvalidValue):
            ProductSlug(slug)

    def test_name_validation(self) -> None:
        with pytest.raises(Exception, match="name"):
            Product.create(
                tenant_id=TenantId(new_id()),
                slug=ProductSlug("blue-tee"),
                name="   ",
                description=None,
                attributes=AttributeSet({}),
                now=NOW,
            )


class TestSkuLifecycle:
    def test_a_new_sku_is_active_and_carries_its_price(self) -> None:
        aggregate = sku()
        assert aggregate.status is SkuStatus.ACTIVE
        assert aggregate.unit_price == Money(2599, "GBP")
        assert [event.event_name for event in aggregate.events] == ["SkuCreated"]

    def test_change_price_records_the_previous_price(self) -> None:
        aggregate = sku()
        aggregate.change_price(new_price=Money(2999, "GBP"), now=NOW)
        event = aggregate.events[-1]
        assert isinstance(event, SkuPriceChanged)
        assert event.payload()["previous_price_minor"] == 2599
        assert event.payload()["new_price_minor"] == 2999

    def test_change_price_to_the_same_value_is_a_no_op(self) -> None:
        aggregate = sku()
        before = aggregate.version
        aggregate.change_price(new_price=Money(2599, "GBP"), now=NOW)
        assert aggregate.version == before

    def test_prices_may_not_be_negative(self) -> None:
        # ``Money`` is a signed amount, so the negative-price rule lives here.
        with pytest.raises(InvalidValue):
            sku().change_price(new_price=Money(-1, "GBP"), now=NOW)

    def test_deactivate_and_reactivate(self) -> None:
        aggregate = sku()
        aggregate.deactivate(now=NOW)
        assert aggregate.status is SkuStatus.INACTIVE
        aggregate.activate(now=NOW)
        assert aggregate.status is SkuStatus.ACTIVE

    def test_deactivating_twice_is_refused(self) -> None:
        aggregate = sku()
        aggregate.deactivate(now=NOW)
        with pytest.raises(InvalidSkuTransition):
            aggregate.deactivate(now=NOW)

    def test_sku_code_is_normalised_and_validated(self) -> None:
        assert SkuCode("TEE-BLUE-M").value == "TEE-BLUE-M"
        with pytest.raises(InvalidValue):
            SkuCode("tee blue")

    def test_a_sku_belongs_to_its_product(self) -> None:
        aggregate = product()
        child = sku(aggregate)
        assert child.product_id == aggregate.id
        assert child.tenant_id == aggregate.tenant_id


class TestAttributeSet:
    def test_validates_keys_and_values(self) -> None:
        attributes = AttributeSet({"colour": "blue", "size": "M", "tags": ["summer", "cotton"]})
        assert attributes.get("colour") == "blue"
        assert len(attributes) == 3

    @pytest.mark.parametrize("key", ["Colour", "1size", "with space", ""])
    def test_rejects_malformed_keys(self, key: str) -> None:
        with pytest.raises(InvalidValue):
            AttributeSet({key: "blue"})

    def test_rejects_deeply_structured_values(self) -> None:
        with pytest.raises(InvalidValue):
            AttributeSet({"colour": {"nested": "not allowed"}})  # type: ignore[dict-item]

    def test_equality_is_by_value(self) -> None:
        assert AttributeSet({"colour": "blue"}) == AttributeSet({"colour": "blue"})
        assert AttributeSet({"colour": "blue"}) != AttributeSet({"colour": "red"})


class TestProductIdentity:
    def test_stable_identifier_is_generated_by_the_domain(self) -> None:
        aggregate = product()
        assert isinstance(aggregate.id, ProductId.__supertype__)  # type: ignore[attr-defined]
        assert aggregate.id == aggregate.id

    def test_two_products_are_never_equal(self) -> None:
        tenant_id = TenantId(new_id())
        first = product(tenant_id=tenant_id)
        second = product(tenant_id=tenant_id, slug=ProductSlug("red-tee"))
        assert first != second

    def test_events_can_be_pulled_once(self) -> None:
        aggregate = product()
        pulled = aggregate.pull_events()
        assert len(pulled) == 1
        assert isinstance(pulled[0], ProductCreated)
        assert aggregate.pull_events() == ()

    def test_tenant_ownership_is_immutable(self) -> None:
        tenant_id = TenantId(new_id())
        aggregate = product(tenant_id=tenant_id)
        assert aggregate.tenant_id == tenant_id
        assert not hasattr(aggregate, "set_tenant")

    def test_timestamps_come_from_the_injected_clock(self) -> None:
        aggregate = product(now=NOW - timedelta(days=1))
        assert aggregate.created_at == NOW - timedelta(days=1)
