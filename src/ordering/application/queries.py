"""Ordering read use cases."""

from __future__ import annotations

from ordering.application.commands import GetOrderQuery, ListOrdersQuery, OrderView
from ordering.ports.unit_of_work import OrderingUnitOfWork
from shared.application.errors import NotFound
from shared.application.pagination import Page, normalise_limit
from shared.application.use_case import QueryUseCase


class GetOrder(QueryUseCase[GetOrderQuery, OrderView, OrderingUnitOfWork]):
    """Fetch one order, including the summary of its inventory hold."""

    async def _execute(self, uow: OrderingUnitOfWork, command: GetOrderQuery) -> OrderView:
        order = await uow.orders.get(tenant_id=command.tenant_id, order_id=command.order_id)
        if order is None:
            raise NotFound("order", command.order_id)
        reservation = await uow.inventory.reservation_for_order(
            tenant_id=command.tenant_id, order_id=order.id, context=command.context
        )
        return OrderView.of(order, reservation=reservation)


class ListOrders(QueryUseCase[ListOrdersQuery, Page[OrderView], OrderingUnitOfWork]):
    """Keyset-paginated order list, optionally filtered by status.

    List rows deliberately omit the reservation summary: including it would turn
    one page into N+1 cross-context reads. Fetch a single order for the detail.
    """

    async def _execute(self, uow: OrderingUnitOfWork, command: ListOrdersQuery) -> Page[OrderView]:
        limit = normalise_limit(command.limit)
        page = await uow.orders.page(
            tenant_id=command.tenant_id,
            limit=limit,
            cursor=command.cursor,
            status=command.status,
        )
        return Page(
            items=tuple(OrderView.of(order) for order in page.items),
            next_cursor=page.next_cursor,
        )


__all__ = ["GetOrder", "ListOrders"]
