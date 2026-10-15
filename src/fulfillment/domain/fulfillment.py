"""Fulfillment aggregate root."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from fulfillment.domain.errors import InvalidFulfillment, InvalidFulfillmentTransition
from fulfillment.domain.events import (
    FulfillmentAllocated,
    FulfillmentCompensated,
    FulfillmentCreated,
    FulfillmentFailed,
    FulfillmentShipped,
)
from fulfillment.domain.value_objects import FulfillmentStatus, FulfillmentTask
from shared.domain.aggregate import AggregateRoot
from shared.domain.identifiers import (
    FulfillmentId,
    OrderId,
    TenantId,
    WarehouseId,
    new_id,
)

_ALLOWED_TRANSITIONS: dict[FulfillmentStatus, frozenset[FulfillmentStatus]] = {
    FulfillmentStatus.PENDING: frozenset(
        {FulfillmentStatus.ALLOCATED, FulfillmentStatus.FAILED}
    ),
    FulfillmentStatus.ALLOCATED: frozenset(
        {FulfillmentStatus.IN_PROGRESS, FulfillmentStatus.FAILED}
    ),
    FulfillmentStatus.IN_PROGRESS: frozenset(
        {FulfillmentStatus.SHIPPED, FulfillmentStatus.FAILED}
    ),
    FulfillmentStatus.FAILED: frozenset(
        {FulfillmentStatus.IN_PROGRESS, FulfillmentStatus.COMPENSATED}
    ),
    FulfillmentStatus.SHIPPED: frozenset(),
    FulfillmentStatus.COMPENSATED: frozenset(),
}


class Fulfillment(AggregateRoot):
    """Aggregate root managing warehouse fulfillment, external carriers, and compensation."""

    def __init__(
        self,
        *,
        id: FulfillmentId,
        tenant_id: TenantId,
        order_id: OrderId,
        warehouse_id: WarehouseId,
        status: FulfillmentStatus = FulfillmentStatus.PENDING,
        tasks: Sequence[FulfillmentTask] = (),
        tracking_number: str | None = None,
        carrier: str | None = None,
        failure_reason: str | None = None,
        retry_count: int = 0,
        max_retries: int = 3,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
        version: int = 1,
    ) -> None:
        super().__init__(tenant_id=tenant_id, version=version)
        self._id = id
        self._order_id = order_id
        self._warehouse_id = warehouse_id
        self._status = status
        self._tasks: list[FulfillmentTask] = list(tasks)
        self._tracking_number = tracking_number
        self._carrier = carrier
        self._failure_reason = failure_reason
        self._retry_count = retry_count
        self._max_retries = max_retries
        self._created_at = created_at or datetime.now(UTC)
        self._updated_at = updated_at or datetime.now(UTC)

    @property
    def id(self) -> FulfillmentId:
        return self._id

    @property
    def tenant_id(self) -> TenantId:
        return self._tenant_id

    @property
    def order_id(self) -> OrderId:
        return self._order_id

    @property
    def warehouse_id(self) -> WarehouseId:
        return self._warehouse_id

    @property
    def status(self) -> FulfillmentStatus:
        return self._status

    @property
    def tasks(self) -> tuple[FulfillmentTask, ...]:
        return tuple(self._tasks)

    @property
    def tracking_number(self) -> str | None:
        return self._tracking_number

    @property
    def carrier(self) -> str | None:
        return self._carrier

    @property
    def failure_reason(self) -> str | None:
        return self._failure_reason

    @property
    def retry_count(self) -> int:
        return self._retry_count

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    @property
    def version(self) -> int:
        return self._version

    @classmethod
    def create(
        cls,
        *,
        tenant_id: TenantId,
        order_id: OrderId,
        warehouse_id: WarehouseId,
        tasks: Sequence[FulfillmentTask],
        max_retries: int = 3,
        now: datetime | None = None,
    ) -> Fulfillment:
        """Create a new fulfillment aggregate in PENDING state."""
        if not tasks:
            raise InvalidFulfillment("at least one task is required for fulfillment")
        for task in tasks:
            if task.warehouse_id != warehouse_id:
                raise InvalidFulfillment(
                    f"task warehouse {task.warehouse_id} does not match "
                    f"fulfillment warehouse {warehouse_id}"
                )

        current_time = now or datetime.now(UTC)
        fulfillment_id = FulfillmentId(new_id())
        fulfillment = cls(
            id=fulfillment_id,
            tenant_id=tenant_id,
            order_id=order_id,
            warehouse_id=warehouse_id,
            status=FulfillmentStatus.PENDING,
            tasks=tasks,
            max_retries=max_retries,
            created_at=current_time,
            updated_at=current_time,
            version=1,
        )
        fulfillment._record_event(
            FulfillmentCreated(
                tenant_id=tenant_id,
                fulfillment_id=fulfillment_id,
                order_id=order_id,
                warehouse_id=warehouse_id,
                status=FulfillmentStatus.PENDING.value,
            )
        )
        return fulfillment

    def _transition(self, target: FulfillmentStatus, now: datetime | None = None) -> None:
        allowed = _ALLOWED_TRANSITIONS.get(self._status, frozenset())
        if target not in allowed:
            raise InvalidFulfillmentTransition(self._status.value, target.value)
        self._status = target
        self._updated_at = now or datetime.now(UTC)

    def allocate(self, now: datetime | None = None) -> None:
        """Mark tasks allocated to the warehouse."""
        self._transition(FulfillmentStatus.ALLOCATED, now)
        self._record_event(
            FulfillmentAllocated(
                tenant_id=self._tenant_id,
                fulfillment_id=self._id,
                order_id=self._order_id,
                warehouse_id=self._warehouse_id,
            )
        )

    def start_processing(self, now: datetime | None = None) -> None:
        """Transition into IN_PROGRESS when external carrier operation begins."""
        self._transition(FulfillmentStatus.IN_PROGRESS, now)

    def mark_shipped(
        self,
        *,
        tracking_number: str,
        carrier: str,
        now: datetime | None = None,
    ) -> None:
        """Mark fulfillment as successfully shipped."""
        if not tracking_number.strip():
            raise InvalidFulfillment("tracking number cannot be empty")
        if not carrier.strip():
            raise InvalidFulfillment("carrier cannot be empty")

        self._transition(FulfillmentStatus.SHIPPED, now)
        self._tracking_number = tracking_number.strip()
        self._carrier = carrier.strip()
        self._failure_reason = None
        # Complete all tasks
        self._tasks = [task.complete() for task in self._tasks]
        self._record_event(
            FulfillmentShipped(
                tenant_id=self._tenant_id,
                fulfillment_id=self._id,
                order_id=self._order_id,
                tracking_number=self._tracking_number,
                carrier=self._carrier,
            )
        )

    def can_retry(self) -> bool:
        """Check if fulfillment is in FAILED state and has remaining retry attempts."""
        return self._status == FulfillmentStatus.FAILED and self._retry_count < self._max_retries

    def record_failure(
        self,
        reason: str,
        *,
        is_permanent: bool = False,
        now: datetime | None = None,
    ) -> None:
        """Record a failure from carrier operation."""
        current_time = now or datetime.now(UTC)
        self._failure_reason = reason
        self._updated_at = current_time

        if is_permanent or self._retry_count >= self._max_retries:
            self._retry_count = self._max_retries
            self._transition(FulfillmentStatus.FAILED, current_time)
            self._record_event(
                FulfillmentFailed(
                    tenant_id=self._tenant_id,
                    fulfillment_id=self._id,
                    order_id=self._order_id,
                    warehouse_id=self._warehouse_id,
                    reason=reason,
                    is_permanent=True,
                )
            )
        else:
            self._retry_count += 1
            self._transition(FulfillmentStatus.FAILED, current_time)
            self._record_event(
                FulfillmentFailed(
                    tenant_id=self._tenant_id,
                    fulfillment_id=self._id,
                    order_id=self._order_id,
                    warehouse_id=self._warehouse_id,
                    reason=reason,
                    is_permanent=False,
                )
            )

    def retry(self, now: datetime | None = None) -> None:
        """Initiate retry transition from FAILED to IN_PROGRESS."""
        if not self.can_retry():
            raise InvalidFulfillmentTransition(
                self._status.value,
                FulfillmentStatus.IN_PROGRESS.value,
            )
        self._transition(FulfillmentStatus.IN_PROGRESS, now)

    def compensate(self, reason: str, now: datetime | None = None) -> None:
        """Trigger compensation after permanent fulfillment failure."""
        current_time = now or datetime.now(UTC)
        self._transition(FulfillmentStatus.COMPENSATED, current_time)
        self._failure_reason = reason
        self._record_event(
            FulfillmentCompensated(
                tenant_id=self._tenant_id,
                fulfillment_id=self._id,
                order_id=self._order_id,
                warehouse_id=self._warehouse_id,
                reason=reason,
            )
        )


__all__ = ["Fulfillment"]
