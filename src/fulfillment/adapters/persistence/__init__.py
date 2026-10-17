"""Fulfillment persistence adapters."""

from __future__ import annotations

from fulfillment.adapters.persistence.tables import fulfillment_tasks, fulfillments

__all__ = [
    "fulfillment_tasks",
    "fulfillments",
]
