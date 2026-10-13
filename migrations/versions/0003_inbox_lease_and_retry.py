"""Add inbox lease, ownership, and failure fields for concurrent processing safety.

Revision ID: 0003_inbox_lease_and_retry
Revises: 0002_phase2_async_processing

Adds lease and ownership columns to ``inbox_messages``:
* ``locked_by`` - identifier of worker currently holding lease
* ``locked_at`` - timestamp when processing lease was acquired
* ``lease_until`` - timestamp when processing lease expires
* ``failed_at`` - timestamp when processing failure occurred
Updates check constraint to permit status ``PROCESSING``.
Adds index on ``(status, lease_until)`` for safe concurrent claim/reclaim queries.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003_inbox_lease_and_retry"
down_revision: str | None = "0002_phase2_async_processing"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 1. Add new columns
    op.add_column("inbox_messages", sa.Column("locked_by", sa.String(length=128), nullable=True))
    op.add_column(
        "inbox_messages",
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "inbox_messages",
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "inbox_messages",
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 2. Update status constraint to include 'PROCESSING'
    op.execute(
        "ALTER TABLE inbox_messages DROP CONSTRAINT IF EXISTS ck_inbox_messages_status_valid"
    )
    op.execute(
        "ALTER TABLE inbox_messages ADD CONSTRAINT ck_inbox_messages_status_valid "
        "CHECK (status IN ('RECEIVED', 'PROCESSING', 'PROCESSED', 'FAILED'))"
    )

    # 3. Add lease index for efficient expired-lease reclamation
    op.create_index(
        "ix_inbox_messages_status_lease_until",
        "inbox_messages",
        ["status", "lease_until"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_inbox_messages_status_lease_until", table_name="inbox_messages")

    op.execute(
        "ALTER TABLE inbox_messages DROP CONSTRAINT IF EXISTS ck_inbox_messages_status_valid"
    )
    op.execute(
        "ALTER TABLE inbox_messages ADD CONSTRAINT ck_inbox_messages_status_valid "
        "CHECK (status IN ('RECEIVED', 'PROCESSED', 'FAILED'))"
    )

    op.drop_column("inbox_messages", "failed_at")
    op.drop_column("inbox_messages", "lease_until")
    op.drop_column("inbox_messages", "locked_at")
    op.drop_column("inbox_messages", "locked_by")
