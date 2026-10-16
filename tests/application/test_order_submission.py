"""Application tests: order submission, confirmation, cancellation, idempotency.

These run the real use cases over in-memory ports (``tests.fakes``), so the
orchestration under test is the one the API executes: one transaction per
command, real cross-context calls into inventory, real idempotency claims.

The three behaviours the specification calls out explicitly are asserted here:

* insufficient stock either back-orders or rejects the order (configurable), and
  never oversells;
* a repeated submission with the same idempotency key returns the *same* order
  and reserves no further stock;
* a failed submission leaves nothing behind.
"""

from __future__ import annotations

import pytest
from tests.application.conftest import Shop, _build_shop
from tests.fakes.application import DEFAULT_TTL_SECONDS

from inventory.application.commands import AdjustInventoryCommand
from inventory.domain.errors import InsufficientInventory
from inventory.domain.value_objects import AdjustmentReason, ReservationStatus
from ordering.application.commands import (
    CancelOrderCommand,
    ConfirmOrderCommand,
    CreateOrderCommand,
    GetOrderQuery,
    OrderLineRequest,
)
from ordering.domain.errors import InvalidOrderTransition
from ordering.domain.policies import UnavailableStockPolicy
from ordering.domain.value_objects import OrderStatus
from shared.application.errors import (
    DuplicateIdempotencyKey,
    IdempotencyKeyRequired,
    InvalidRequest,
    NotFound,
)
from shared.domain.identifiers import OrderId, SkuId, new_id

pytestmark = pytest.mark.application


def order_command(
    shop: Shop,
    quantity: int = 3,
    *,
    key: str | None = "order-key-1",
    sku_id: SkuId | None = None,
    reference: str | None = "PO-1",
) -> CreateOrderCommand:
    return CreateOrderCommand(
        tenant_id=shop.tenant_id,
        context=shop.with_context(idempotency_key=key),
        lines=(OrderLineRequest(sku_id=sku_id or shop.sku_id, quantity=quantity),),
        customer_reference=reference,
    )


class TestOrderSubmission:
    async def test_a_fully_covered_order_is_confirmed_immediately(self, stocked_shop: Shop) -> None:
        outcome = await stocked_shop.app.use_cases.create_order(order_command(stocked_shop, 3))

        assert outcome.status_code == 201
        assert outcome.replayed is False
        assert outcome.view.status is OrderStatus.CONFIRMED
        assert outcome.view.total_minor == 3 * 2599
        assert outcome.view.reservation is not None
        assert outcome.view.reservation.allocated_quantity == 3

        on_hand, reserved, available = stocked_shop.stock_of()
        assert (on_hand, reserved, available) == (10, 3, 7)

    async def test_a_partially_covered_order_is_backordered_by_default(
        self, stocked_shop: Shop
    ) -> None:
        outcome = await stocked_shop.app.use_cases.create_order(order_command(stocked_shop, 25))

        assert outcome.view.status is OrderStatus.BACKORDERED
        assert outcome.view.reservation is not None
        assert outcome.view.reservation.allocated_quantity == 10

        on_hand, reserved, available = stocked_shop.stock_of()
        assert (on_hand, reserved, available) == (10, 10, 0)

    async def test_the_reject_policy_refuses_the_order_and_keeps_no_hold(self) -> None:
        shop = await _build_shop(on_hand=2, unavailable_policy="REJECT")
        key = "reject-key"

        with pytest.raises(InsufficientInventory):
            await shop.app.use_cases.create_order(order_command(shop, 5, key=key))

        # Nothing survived the rollback: no order, no reservation, no stock held.
        assert shop.app.stores.orders == {}
        assert shop.app.stores.reservations == {}
        assert shop.stock_of() == (2, 0, 2)
        # And the claim was rolled back with the transaction, so the same key can
        # be retried once stock arrives.
        assert (shop.tenant_id, key) not in shop.app.stores.idempotency

    async def test_an_order_for_an_unknown_sku_is_not_found(self, stocked_shop: Shop) -> None:
        with pytest.raises(NotFound):
            await stocked_shop.app.use_cases.create_order(
                order_command(stocked_shop, 1, sku_id=SkuId(new_id()))
            )

    async def test_an_empty_order_is_refused(self, stocked_shop: Shop) -> None:
        command = CreateOrderCommand(
            tenant_id=stocked_shop.tenant_id,
            context=stocked_shop.with_context(idempotency_key="empty-order"),
            lines=(),
        )
        with pytest.raises(InvalidRequest):
            await stocked_shop.app.use_cases.create_order(command)

    async def test_submission_requires_an_idempotency_key(self, stocked_shop: Shop) -> None:
        with pytest.raises(IdempotencyKeyRequired):
            await stocked_shop.app.use_cases.create_order(order_command(stocked_shop, 1, key=None))


class TestIdempotentSubmission:
    async def test_the_same_key_and_payload_returns_the_same_order(
        self, stocked_shop: Shop
    ) -> None:
        first = await stocked_shop.app.use_cases.create_order(order_command(stocked_shop, 4))
        second = await stocked_shop.app.use_cases.create_order(order_command(stocked_shop, 4))

        assert second.replayed is True
        assert second.view.id == first.view.id
        assert second.view.status is first.view.status
        assert len(stocked_shop.app.stores.orders) == 1
        assert len(stocked_shop.app.stores.reservations) == 1
        # The hold was not taken twice: 4 units are held, not 8.
        assert stocked_shop.stock_of() == (10, 4, 6)

    async def test_the_same_key_with_a_different_payload_is_a_conflict(
        self, stocked_shop: Shop
    ) -> None:
        await stocked_shop.app.use_cases.create_order(order_command(stocked_shop, 2))

        with pytest.raises(DuplicateIdempotencyKey):
            await stocked_shop.app.use_cases.create_order(order_command(stocked_shop, 3))

    async def test_idempotency_keys_are_scoped_per_tenant(self) -> None:
        first_shop = await _build_shop(on_hand=5)
        second_shop = await _build_shop(on_hand=5)
        shared_key = "same-key-different-tenant"

        first = await first_shop.app.use_cases.create_order(
            order_command(first_shop, 1, key=shared_key)
        )
        second = await first_shop.app.use_cases.create_order(
            order_command(first_shop, 1, key=shared_key)
        )
        assert second.replayed is True

        # Keys are per tenant: the exact same key on another tenant is a fresh
        # operation, not a replay of somebody else's order.
        assert (second_shop.tenant_id, shared_key) not in second_shop.app.stores.idempotency
        other = await second_shop.app.use_cases.create_order(
            order_command(second_shop, 1, key=shared_key)
        )
        assert other.replayed is False
        assert other.view.id != first.view.id

    async def test_a_replay_does_not_duplicate_domain_events(self, stocked_shop: Shop) -> None:
        dispatcher = stocked_shop.app.events
        first = await stocked_shop.app.use_cases.create_order(order_command(stocked_shop, 2))
        events_after_first = len(getattr(dispatcher, "events", []))
        second = await stocked_shop.app.use_cases.create_order(order_command(stocked_shop, 2))

        assert second.replayed is True
        assert len(getattr(dispatcher, "events", [])) == events_after_first
        assert first.view.id == second.view.id


class TestOrderLifecycle:
    async def test_a_backordered_order_can_be_confirmed_once_stock_arrives(self) -> None:
        shop = await _build_shop(on_hand=3)
        submitted = await shop.app.use_cases.create_order(order_command(shop, 5))
        assert submitted.view.status is OrderStatus.BACKORDERED
        assert shop.stock_of() == (3, 3, 0)

        # More stock arrives, then the order is confirmed through the normal flow.
        await shop.app.use_cases.adjust_inventory(
            AdjustInventoryCommand(
                tenant_id=shop.tenant_id,
                context=shop.context,
                sku_id=shop.sku_id,
                warehouse_id=shop.warehouse_id,
                quantity_delta=5,
                reason=AdjustmentReason.GOODS_RECEIPT,
            )
        )

        confirmed = await shop.app.use_cases.confirm_order(
            ConfirmOrderCommand(
                tenant_id=shop.tenant_id, context=shop.context, order_id=submitted.view.id
            )
        )
        assert confirmed.view.status is OrderStatus.CONFIRMED
        assert confirmed.view.reservation is not None
        assert confirmed.view.reservation.allocated_quantity == 5
        # Confirmation keeps the hold ACTIVE: the units are still physically in the
        # warehouse, and they leave it on fulfilment (Phase 2). 3 were received, 5
        # more arrived, all 5 are now held for the order.
        assert shop.stock_of() == (8, 5, 3)
        assert confirmed.view.reservation is not None
        assert confirmed.view.reservation.status == "ACTIVE"

    async def test_confirming_a_backordered_order_without_stock_is_refused(self) -> None:
        shop = await _build_shop(on_hand=1)
        submitted = await shop.app.use_cases.create_order(order_command(shop, 4))
        assert submitted.view.status is OrderStatus.BACKORDERED

        with pytest.raises(InsufficientInventory):
            await shop.app.use_cases.confirm_order(
                ConfirmOrderCommand(
                    tenant_id=shop.tenant_id, context=shop.context, order_id=submitted.view.id
                )
            )

    async def test_a_confirmed_order_cannot_be_cancelled(self) -> None:
        shop = await _build_shop(on_hand=4)
        submitted = await shop.app.use_cases.create_order(order_command(shop, 4))
        assert submitted.view.status is OrderStatus.CONFIRMED

        # A confirmed order cannot be cancelled in Phase 1 (that needs a
        # compensation flow, which is Phase 2+).
        with pytest.raises(InvalidOrderTransition):
            await shop.app.use_cases.cancel_order(
                CancelOrderCommand(
                    tenant_id=shop.tenant_id,
                    context=shop.with_context(idempotency_key="cancel-1"),
                    order_id=submitted.view.id,
                    reason="customer changed their mind",
                )
            )

    async def test_cancelling_a_backordered_order_releases_its_partial_hold(self) -> None:
        shop = await _build_shop(on_hand=2)
        submitted = await shop.app.use_cases.create_order(order_command(shop, 6))
        assert submitted.view.status is OrderStatus.BACKORDERED

        cancelled = await shop.app.use_cases.cancel_order(
            CancelOrderCommand(
                tenant_id=shop.tenant_id,
                context=shop.with_context(idempotency_key="cancel-backorder"),
                order_id=submitted.view.id,
                reason="out of stock",
            )
        )
        assert cancelled.view.status is OrderStatus.CANCELLED
        assert shop.stock_of() == (2, 0, 2)

    async def test_an_expired_reservation_is_not_treated_as_active_when_cancelling(
        self,
    ) -> None:
        """Ordering must respect reservation expiry, not only ACTIVE status.

        ``ReservationView.is_active`` is true whenever status is ACTIVE. An
        expired hold (past ``expires_at`` but not yet swept) must not be
        released by Ordering; stock stays reserved until the Phase 2 sweeper.
        """
        shop = await _build_shop(on_hand=2)
        submitted = await shop.app.use_cases.create_order(order_command(shop, 6))
        assert submitted.view.status is OrderStatus.BACKORDERED
        assert shop.stock_of() == (2, 2, 0)

        reservation = shop.reservations_of()[0]
        assert reservation.is_active(now=shop.app.clock.now())

        shop.app.clock.advance(DEFAULT_TTL_SECONDS + 1)
        assert not reservation.is_active(now=shop.app.clock.now())

        cancelled = await shop.app.use_cases.cancel_order(
            CancelOrderCommand(
                tenant_id=shop.tenant_id,
                context=shop.with_context(idempotency_key="cancel-expired"),
                order_id=submitted.view.id,
                reason="abandoned after hold expired",
            )
        )
        assert cancelled.view.status is OrderStatus.CANCELLED
        assert shop.stock_of() == (2, 2, 0)
        assert reservation.status is ReservationStatus.ACTIVE

    async def test_confirming_order_with_expired_reservation_is_refused(self) -> None:
        """ConfirmOrder must reject confirmation if the reservation has expired."""
        from inventory.domain.errors import ReservationExpired

        shop = await _build_shop(on_hand=2)
        submitted = await shop.app.use_cases.create_order(order_command(shop, 4))
        assert submitted.view.status is OrderStatus.BACKORDERED

        order = shop.app.stores.orders[submitted.view.id]
        res = shop.reservations_of()[0]
        order.mark_inventory_reserved(reservation_id=res.id, now=shop.app.clock.now())
        shop.app.stores.versions[order.id] = order.version

        # Advance clock past reservation expiry
        shop.app.clock.advance(DEFAULT_TTL_SECONDS + 10)

        # Attempt confirmation against expired reservation must fail
        with pytest.raises(ReservationExpired, match="expired and can no longer confirm order"):
            await shop.app.use_cases.confirm_order(
                ConfirmOrderCommand(
                    tenant_id=shop.tenant_id,
                    context=shop.with_context(idempotency_key="confirm-expired"),
                    order_id=submitted.view.id,
                )
            )

    async def test_confirming_order_with_released_reservation_is_refused(self) -> None:
        """ConfirmOrder must reject confirmation if the reservation was released."""
        from inventory.domain.errors import InvalidReservationTransition

        shop = await _build_shop(on_hand=2)
        submitted = await shop.app.use_cases.create_order(order_command(shop, 4))
        assert submitted.view.status is OrderStatus.BACKORDERED

        order = shop.app.stores.orders[submitted.view.id]
        res = shop.reservations_of()[0]
        order.mark_inventory_reserved(reservation_id=res.id, now=shop.app.clock.now())
        shop.app.stores.versions[order.id] = order.version

        # Release the reservation
        res.release(reason="cancelled", now=shop.app.clock.now())

        # Attempt confirmation against released reservation must fail
        with pytest.raises(InvalidReservationTransition, match="cannot move to CONFIRMED"):
            await shop.app.use_cases.confirm_order(
                ConfirmOrderCommand(
                    tenant_id=shop.tenant_id,
                    context=shop.with_context(idempotency_key="confirm-released"),
                    order_id=submitted.view.id,
                )
            )

    async def test_cancellation_is_idempotent(self) -> None:
        shop = await _build_shop(on_hand=1)
        submitted = await shop.app.use_cases.create_order(order_command(shop, 4))
        key = "cancel-twice"
        command = CancelOrderCommand(
            tenant_id=shop.tenant_id,
            context=shop.with_context(idempotency_key=key),
            order_id=submitted.view.id,
            reason="no longer needed",
        )
        first = await shop.app.use_cases.cancel_order(command)
        second = await shop.app.use_cases.cancel_order(command)

        assert first.view.status is OrderStatus.CANCELLED
        assert second.replayed is True
        assert second.view.id == first.view.id

    async def test_read_back_returns_the_stored_order(self, stocked_shop: Shop) -> None:
        submitted = await stocked_shop.app.use_cases.create_order(order_command(stocked_shop, 2))
        view = await stocked_shop.app.use_cases.get_order(
            GetOrderQuery(
                tenant_id=stocked_shop.tenant_id,
                context=stocked_shop.context,
                order_id=submitted.view.id,
            )
        )
        assert view.id == submitted.view.id
        assert view.total_minor == submitted.view.total_minor
        assert view.reservation is not None


class TestTenantIsolation:
    async def test_another_tenant_cannot_see_an_order(self) -> None:
        first = await _build_shop(on_hand=2)
        second = await _build_shop(on_hand=2)
        submitted = await first.app.use_cases.create_order(order_command(first, 1))

        with pytest.raises(NotFound):
            await second.app.use_cases.get_order(
                GetOrderQuery(
                    tenant_id=second.tenant_id,
                    context=second.context,
                    order_id=submitted.view.id,
                )
            )

    async def test_stock_never_crosses_a_tenant_boundary(self) -> None:
        first = await _build_shop(on_hand=5)
        second = await _build_shop(on_hand=0)

        # The second tenant tries to order the first tenant's SKU. From inside
        # tenant two that SKU simply does not exist.
        foreign_sku = SkuId(first.sku_id)
        with pytest.raises(NotFound):
            await second.app.use_cases.create_order(order_command(second, 1, sku_id=foreign_sku))

        assert first.stock_of() == (5, 0, 5)

    async def test_ordering_another_tenants_order_id_is_not_found(self) -> None:
        first = await _build_shop(on_hand=5)
        second = await _build_shop(on_hand=5)
        submitted = await first.app.use_cases.create_order(order_command(first, 1))

        with pytest.raises(NotFound):
            await second.app.use_cases.confirm_order(
                ConfirmOrderCommand(
                    tenant_id=second.tenant_id,
                    context=second.context,
                    order_id=OrderId(submitted.view.id),
                )
            )


class TestUnavailableStockPolicyConfiguration:
    async def test_the_policy_is_configuration_not_code(self) -> None:
        reject = await _build_shop(on_hand=1, unavailable_policy=UnavailableStockPolicy.REJECT.name)
        backorder = await _build_shop(
            on_hand=1, unavailable_policy=UnavailableStockPolicy.BACKORDER.name
        )

        with pytest.raises(InsufficientInventory):
            await reject.app.use_cases.create_order(order_command(reject, 9))

        accepted = await backorder.app.use_cases.create_order(order_command(backorder, 9))
        assert accepted.view.status is OrderStatus.BACKORDERED
