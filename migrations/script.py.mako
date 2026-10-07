"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

Migration strategy (see docs/decisions/0005-migration-strategy.md):

* revisions are append-only - never edit a revision that has been applied
  somewhere other than a developer's machine;
* every revision implements ``upgrade`` **and** ``downgrade`` when the rollback
  is lossless; when it is not, ``downgrade`` raises explicitly instead of
  pretending to work;
* destructive steps (dropping a column or a table) must be split across two
  releases: one that stops writing, one that removes.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | None = ${repr(branch_labels)}
depends_on: str | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
