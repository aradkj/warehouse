"""Celery application configuration and beat scheduler setup.

Celery handles asynchronous background processing (e.g., reservation expiration sweep).
"""

from __future__ import annotations

import logging
from celery import Celery

from bootstrap.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

celery_app = Celery(
    "arad_workers",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["bootstrap.workers"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "expire-reservations-sweep": {
            "task": "inventory.expire_reservations_task",
            "schedule": float(settings.reservation_expiry_interval_seconds),
            "args": (),
        },
    },
)

__all__ = ["celery_app"]
