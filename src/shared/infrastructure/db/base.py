"""SQLAlchemy declarative metadata for the whole platform.

The platform uses SQLAlchemy **Core** table objects rather than ORM entities.
The reason is not stylistic:

* inventory correctness depends on explicit ``SELECT ... FOR UPDATE`` and on
  ``UPDATE ... WHERE version = :expected`` with row-count inspection, which Core
  expresses directly;
* idempotency depends on ``INSERT ... ON CONFLICT DO NOTHING``;
* an ORM identity map hiding stale state is a liability when the same aggregate
  is deliberately locked twice in one transaction.

Mappers (``to_domain`` / ``apply`` functions) live next to each repository and
are the only place where database rows and domain objects meet.
"""

from __future__ import annotations

from sqlalchemy import MetaData

#: Deterministic constraint naming. Every generated constraint gets a stable
#: name, which keeps Alembic migrations diff-able and rollbacks possible.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

__all__ = ["NAMING_CONVENTION", "metadata"]
