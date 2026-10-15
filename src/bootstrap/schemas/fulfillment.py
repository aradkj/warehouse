"""Fulfillment request and response schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fulfillment.application.views import FulfillmentTaskView, FulfillmentView


class TaskRequirementSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku_id: UUID
    quantity: int = Field(gt=0, le=1_000_000)


class RequestFulfillmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: UUID
    warehouse_id: UUID
    requirements: list[TaskRequirementSchema] = Field(min_length=1, max_length=100)
    max_retries: int = Field(default=3, ge=0, le=10)


class FulfillmentTaskResponse(BaseModel):
    task_id: UUID
    warehouse_id: UUID
    sku_id: UUID
    quantity: int
    is_completed: bool

    @classmethod
    def from_view(cls, view: FulfillmentTaskView) -> FulfillmentTaskResponse:
        return cls(
            task_id=view.task_id,
            warehouse_id=view.warehouse_id,
            sku_id=view.sku_id,
            quantity=view.quantity,
            is_completed=view.is_completed,
        )


class FulfillmentResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    order_id: UUID
    warehouse_id: UUID
    status: str
    tasks: list[FulfillmentTaskResponse]
    tracking_number: str | None = None
    carrier: str | None = None
    failure_reason: str | None = None
    retry_count: int
    max_retries: int
    created_at: datetime
    updated_at: datetime
    version: int

    @classmethod
    def from_view(cls, view: FulfillmentView) -> FulfillmentResponse:
        return cls(
            id=view.id,
            tenant_id=view.tenant_id,
            order_id=view.order_id,
            warehouse_id=view.warehouse_id,
            status=view.status,
            tasks=[FulfillmentTaskResponse.from_view(t) for t in view.tasks],
            tracking_number=view.tracking_number,
            carrier=view.carrier,
            failure_reason=view.failure_reason,
            retry_count=view.retry_count,
            max_retries=view.max_retries,
            created_at=view.created_at,
            updated_at=view.updated_at,
            version=view.version,
        )


__all__ = [
    "FulfillmentResponse",
    "FulfillmentTaskResponse",
    "RequestFulfillmentRequest",
    "TaskRequirementSchema",
]
