"""Wire the real use cases onto in-memory fake ports.

Application-layer tests must exercise the *actual* orchestration - idempotency
claims, transaction boundaries, cross-context calls - without PostgreSQL. This
module therefore mirrors ``bootstrap.dependencies.build_use_cases`` one for one,
substituting only the adapters, and reuses the production
:class:`~inventory.adapters.in_process.InProcessInventoryOperations` translator so
that ordering's cross-context path is the same code the API runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from catalog.application.queries import GetProduct, GetSku, ListProducts
from catalog.application.use_cases import (
    ActivateProduct,
    ActivateSku,
    ArchiveProduct,
    ChangeSkuPrice,
    CreateProduct,
    CreateSku,
    DeactivateSku,
    ReviseProduct,
)
from identity.application.use_cases import GetTenant, RegisterTenant
from inventory.adapters.in_process import InProcessInventoryOperations
from inventory.application.queries import (
    GetAvailability,
    GetReservation,
    GetWarehouse,
    ListWarehouses,
)
from inventory.application.use_cases import (
    AdjustInventory,
    ConfirmReservation,
    ExpireReservations,
    RegisterWarehouse,
    ReleaseReservation,
    ReserveInventory,
)
from ordering.application.queries import GetOrder, ListOrders
from ordering.application.use_cases import CancelOrder, ConfirmOrder, CreateOrder
from ordering.domain.policies import UnavailableStockPolicy
from shared.application.events import EventDispatcher, NullEventDispatcher
from shared.application.use_case import DEFAULT_IDEMPOTENCY_RETENTION
from shared.domain.clock import FixedClock
from shared.domain.value_objects import Quantity
from tests.fakes.store import FakeStores, FakeTransactionScope
from tests.fakes.unit_of_work import (
    FakeCatalogUnitOfWork,
    FakeIdentityUnitOfWork,
    FakeInventoryUnitOfWork,
    FakeOrderingUnitOfWork,
)

#: Default reservation lifetime used by the fake wiring (mirrors ARAD settings).
DEFAULT_TTL_SECONDS = 1800
MAX_TTL_SECONDS = 86_400


@dataclass(slots=True)
class FakeApplication:
    """A fully wired application plus direct access to its in-memory state."""

    stores: FakeStores
    scope: FakeTransactionScope
    use_cases: object
    clock: FixedClock
    events: EventDispatcher
    dispatched: list[object] = field(default_factory=list)


def build_fake_application(
    *,
    stores: FakeStores | None = None,
    clock: FixedClock | None = None,
    events: EventDispatcher | None = None,
    idempotency_retention: timedelta = DEFAULT_IDEMPOTENCY_RETENTION,
    unavailable_policy: UnavailableStockPolicy = UnavailableStockPolicy.BACKORDER,
    default_ttl_seconds: int = DEFAULT_TTL_SECONDS,
    max_ttl_seconds: int = MAX_TTL_SECONDS,
) -> FakeApplication:
    """Build every use case over one shared fake transaction scope."""
    from bootstrap.dependencies import UseCases

    fake_stores = stores if stores is not None else FakeStores()
    fake_clock = clock if clock is not None else FixedClock()
    dispatcher = events or NullEventDispatcher()
    scope = FakeTransactionScope(fake_stores)

    catalog_uow = lambda: FakeCatalogUnitOfWork(fake_stores, scope)  # noqa: E731
    identity_uow = lambda: FakeIdentityUnitOfWork(fake_stores, scope)  # noqa: E731
    inventory_uow = lambda: FakeInventoryUnitOfWork(fake_stores, scope)  # noqa: E731

    def ordering_uow() -> FakeOrderingUnitOfWork:
        return FakeOrderingUnitOfWork(
            fake_stores,
            scope,
            inventory=InProcessInventoryOperations(
                uow_factory=inventory_uow,
                clock=fake_clock,
                default_ttl_seconds=default_ttl_seconds,
                max_ttl_seconds=max_ttl_seconds,
            ),
            clock=fake_clock,
        )

    common = {
        "clock": fake_clock,
        "events": dispatcher,
        "idempotency_retention": idempotency_retention,
    }

    use_cases = UseCases(
        create_product=CreateProduct(uow_factory=catalog_uow, **common),
        revise_product=ReviseProduct(uow_factory=catalog_uow, **common),
        activate_product=ActivateProduct(uow_factory=catalog_uow, **common),
        archive_product=ArchiveProduct(uow_factory=catalog_uow, **common),
        create_sku=CreateSku(uow_factory=catalog_uow, **common),
        change_sku_price=ChangeSkuPrice(uow_factory=catalog_uow, **common),
        deactivate_sku=DeactivateSku(uow_factory=catalog_uow, **common),
        activate_sku=ActivateSku(uow_factory=catalog_uow, **common),
        get_product=GetProduct(uow_factory=catalog_uow, clock=fake_clock),
        get_sku=GetSku(uow_factory=catalog_uow, clock=fake_clock),
        list_products=ListProducts(uow_factory=catalog_uow, clock=fake_clock),
        register_warehouse=RegisterWarehouse(uow_factory=inventory_uow, **common),
        adjust_inventory=AdjustInventory(uow_factory=inventory_uow, **common),
        reserve_inventory=ReserveInventory(
            uow_factory=inventory_uow,
            default_ttl_seconds=default_ttl_seconds,
            max_ttl_seconds=max_ttl_seconds,
            **common,
        ),
        release_reservation=ReleaseReservation(uow_factory=inventory_uow, **common),
        confirm_reservation=ConfirmReservation(uow_factory=inventory_uow, **common),
        expire_reservations=ExpireReservations(uow_factory=inventory_uow, **common),
        get_availability=GetAvailability(uow_factory=inventory_uow, clock=fake_clock),
        get_reservation=GetReservation(uow_factory=inventory_uow, clock=fake_clock),
        get_warehouse=GetWarehouse(uow_factory=inventory_uow, clock=fake_clock),
        list_warehouses=ListWarehouses(uow_factory=inventory_uow, clock=fake_clock),
        create_order=CreateOrder(
            uow_factory=ordering_uow,
            unavailable_policy=unavailable_policy,
            **common,
        ),
        confirm_order=ConfirmOrder(uow_factory=ordering_uow, **common),
        cancel_order=CancelOrder(uow_factory=ordering_uow, **common),
        get_order=GetOrder(uow_factory=ordering_uow, clock=fake_clock),
        list_orders=ListOrders(uow_factory=ordering_uow, clock=fake_clock),
        register_tenant=RegisterTenant(uow_factory=identity_uow, **common),
        get_tenant=GetTenant(uow_factory=identity_uow, clock=fake_clock),
    )
    return FakeApplication(
        stores=fake_stores,
        scope=scope,
        use_cases=use_cases,
        clock=fake_clock,
        events=dispatcher,
    )


def quantity(value: int) -> Quantity:
    """Readability helper for command construction in tests."""
    return Quantity(value)


__all__ = ["DEFAULT_TTL_SECONDS", "FakeApplication", "build_fake_application", "quantity"]
