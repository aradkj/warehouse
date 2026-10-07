"""Application tests: catalog use cases.

The catalog is where an order's prices come from, so the tests focus on the two
things ordering depends on: a SKU is only purchasable while both it and its
product are active, and a price change never rewrites history (orders keep their
snapshot).
"""

from __future__ import annotations

import pytest
from tests.application.conftest import Shop

from catalog.application.commands import (
    ChangeProductStateCommand,
    ChangeSkuPriceCommand,
    ChangeSkuStateCommand,
    CreateProductCommand,
    CreateSkuCommand,
    GetProductQuery,
    ListProductsQuery,
    ReviseProductCommand,
)
from catalog.domain.value_objects import ProductStatus, SkuStatus
from shared.application.errors import DuplicateResource, NotFound
from shared.domain.errors import InvalidValue
from shared.domain.identifiers import ProductId, SkuId, new_id

pytestmark = pytest.mark.application


class TestProductLifecycle:
    async def test_a_new_product_starts_as_a_draft(self, shop: Shop) -> None:
        outcome = await shop.app.use_cases.create_product(
            CreateProductCommand(
                tenant_id=shop.tenant_id,
                context=shop.context,
                slug="red-hoodie",
                name="Red Hoodie",
                description=None,
            )
        )

        assert outcome.status_code == 201
        assert outcome.view.status is ProductStatus.DRAFT
        assert outcome.view.slug == "red-hoodie"
        assert outcome.view.version == 1

    async def test_slugs_are_unique_per_tenant(self, shop: Shop) -> None:
        with pytest.raises(DuplicateResource):
            await shop.app.use_cases.create_product(
                CreateProductCommand(
                    tenant_id=shop.tenant_id,
                    context=shop.context,
                    slug="blue-tee",
                    name="Blue Tee (again)",
                )
            )

    async def test_the_same_slug_is_free_on_another_tenant(self) -> None:
        from tests.application.conftest import _build_shop

        first = await _build_shop()
        second = await _build_shop()

        def command(shop: Shop) -> CreateProductCommand:
            return CreateProductCommand(
                tenant_id=shop.tenant_id,
                context=shop.context,
                slug="green-tee",
                name="Green Tee",
            )

        mine = await first.app.use_cases.create_product(command(first))
        theirs = await second.app.use_cases.create_product(command(second))

        assert mine.view.slug == theirs.view.slug == "green-tee"
        assert mine.view.id != theirs.view.id

    async def test_archiving_is_irreversible_without_a_new_product(self, shop: Shop) -> None:
        await shop.app.use_cases.archive_product(
            ChangeProductStateCommand(
                tenant_id=shop.tenant_id, context=shop.context, product_id=shop.product_id
            )
        )
        archived = await shop.app.use_cases.get_product(
            GetProductQuery(
                tenant_id=shop.tenant_id, context=shop.context, product_id=shop.product_id
            )
        )
        assert archived.status is ProductStatus.ARCHIVED

        from catalog.domain.errors import InvalidProductTransition

        with pytest.raises(InvalidProductTransition):
            await shop.app.use_cases.activate_product(
                ChangeProductStateCommand(
                    tenant_id=shop.tenant_id, context=shop.context, product_id=shop.product_id
                )
            )

    async def test_revision_uses_optimistic_concurrency(self, shop: Shop) -> None:
        from shared.application.errors import ConcurrencyConflict

        # The fixture activates the product, so it is already at version 2.
        loaded_version = shop.app.stores.products[shop.product_id].version

        revised = await shop.app.use_cases.revise_product(
            ReviseProductCommand(
                tenant_id=shop.tenant_id,
                context=shop.context,
                product_id=shop.product_id,
                name="Blue Tee v2",
                expected_version=loaded_version,
            )
        )
        assert revised.view.name == "Blue Tee v2"
        assert revised.view.version == loaded_version + 1

        # A second writer holding the version it read before the first write
        # committed is rejected instead of silently overwriting it.
        with pytest.raises(ConcurrencyConflict):
            await shop.app.use_cases.revise_product(
                ReviseProductCommand(
                    tenant_id=shop.tenant_id,
                    context=shop.context,
                    product_id=shop.product_id,
                    name="Stale write",
                    expected_version=loaded_version,
                )
            )

    async def test_listing_is_tenant_scoped_and_paginated(self, shop: Shop) -> None:
        page = await shop.app.use_cases.list_products(
            ListProductsQuery(tenant_id=shop.tenant_id, context=shop.context, limit=10)
        )
        assert [product.id for product in page.items] == [shop.product_id]

        empty = await shop.app.use_cases.list_products(
            ListProductsQuery(
                tenant_id=shop.tenant_id,
                context=shop.context,
                limit=10,
                status=ProductStatus.ARCHIVED,
            )
        )
        assert empty.items == ()

    async def test_reading_another_tenants_product_is_not_found(self) -> None:
        from tests.application.conftest import _build_shop

        first = await _build_shop()
        second = await _build_shop()

        with pytest.raises(NotFound):
            await second.app.use_cases.get_product(
                GetProductQuery(
                    tenant_id=second.tenant_id,
                    context=second.context,
                    product_id=ProductId(first.product_id),
                )
            )


class TestSkuLifecycle:
    async def test_a_new_sku_inherits_purchasability_from_its_product(self, shop: Shop) -> None:
        sku_id = await shop.add_sku("TEE-BLUE-L", unit_price_minor=2799)

        assert sku_id in shop.app.stores.skus
        sku = shop.app.stores.skus[sku_id]
        assert sku.status is SkuStatus.ACTIVE
        assert sku.unit_price.amount_minor == 2799

    async def test_a_sku_code_is_unique_per_tenant(self, shop: Shop) -> None:
        with pytest.raises(DuplicateResource):
            await shop.app.use_cases.create_sku(
                CreateSkuCommand(
                    tenant_id=shop.tenant_id,
                    context=shop.context,
                    product_id=shop.product_id,
                    code="TEE-BLUE-M",
                    name="Duplicate",
                    unit_price_minor=100,
                    currency="GBP",
                )
            )

    async def test_prices_can_be_changed_but_never_to_a_negative_amount(self, shop: Shop) -> None:
        changed = await shop.app.use_cases.change_sku_price(
            ChangeSkuPriceCommand(
                tenant_id=shop.tenant_id,
                context=shop.context,
                sku_id=shop.sku_id,
                unit_price_minor=1999,
                currency="GBP",
            )
        )
        assert changed.view.unit_price_minor == 1999

        with pytest.raises(InvalidValue):
            await shop.app.use_cases.change_sku_price(
                ChangeSkuPriceCommand(
                    tenant_id=shop.tenant_id,
                    context=shop.context,
                    sku_id=shop.sku_id,
                    unit_price_minor=-1,
                    currency="GBP",
                )
            )

    async def test_a_deactivated_sku_is_not_purchasable(self, shop: Shop) -> None:
        await shop.app.use_cases.deactivate_sku(
            ChangeSkuStateCommand(
                tenant_id=shop.tenant_id, context=shop.context, sku_id=shop.sku_id
            )
        )

        from catalog.domain.errors import ProductNotPurchasable
        from ordering.application.commands import CreateOrderCommand, OrderLineRequest

        with pytest.raises(ProductNotPurchasable):
            await shop.app.use_cases.create_order(
                CreateOrderCommand(
                    tenant_id=shop.tenant_id,
                    context=shop.with_context(idempotency_key="inactive-sku"),
                    lines=(OrderLineRequest(sku_id=shop.sku_id, quantity=1),),
                )
            )

    async def test_a_sku_on_a_draft_product_is_not_purchasable(self, shop: Shop) -> None:
        from catalog.domain.errors import ProductNotPurchasable
        from ordering.application.commands import CreateOrderCommand, OrderLineRequest

        await shop.app.use_cases.archive_product(
            ChangeProductStateCommand(
                tenant_id=shop.tenant_id, context=shop.context, product_id=shop.product_id
            )
        )

        with pytest.raises(ProductNotPurchasable):
            await shop.app.use_cases.create_order(
                CreateOrderCommand(
                    tenant_id=shop.tenant_id,
                    context=shop.with_context(idempotency_key="archived-product"),
                    lines=(OrderLineRequest(sku_id=shop.sku_id, quantity=1),),
                )
            )

    async def test_reading_an_unknown_sku_is_not_found(self, shop: Shop) -> None:
        from catalog.application.commands import GetSkuQuery

        with pytest.raises(NotFound):
            await shop.app.use_cases.get_sku(
                GetSkuQuery(tenant_id=shop.tenant_id, context=shop.context, sku_id=SkuId(new_id()))
            )


class TestCatalogIdempotency:
    async def test_creating_a_product_twice_with_one_key_creates_one_product(
        self, shop: Shop
    ) -> None:
        command = CreateProductCommand(
            tenant_id=shop.tenant_id,
            context=shop.with_context(idempotency_key="create-green-tee"),
            slug="green-tee",
            name="Green Tee",
        )

        first = await shop.app.use_cases.create_product(command)
        second = await shop.app.use_cases.create_product(command)

        assert second.replayed is True
        assert second.view.id == first.view.id
        assert (
            sum(
                1
                for product in shop.app.stores.products.values()
                if product.slug.value == "green-tee"
            )
            == 1
        )

    async def test_reusing_a_key_for_a_different_payload_conflicts(self, shop: Shop) -> None:
        from shared.application.errors import DuplicateIdempotencyKey

        await shop.app.use_cases.create_product(
            CreateProductCommand(
                tenant_id=shop.tenant_id,
                context=shop.with_context(idempotency_key="reused"),
                slug="purple-tee",
                name="Purple Tee",
            )
        )

        with pytest.raises(DuplicateIdempotencyKey):
            await shop.app.use_cases.create_product(
                CreateProductCommand(
                    tenant_id=shop.tenant_id,
                    context=shop.with_context(idempotency_key="reused"),
                    slug="purple-tee",
                    name="Purple Tee (different payload)",
                )
            )
