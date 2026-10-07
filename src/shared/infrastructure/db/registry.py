"""Single import point for every Core table definition.

Alembic and the test suite need ``metadata`` to know all tables. Importing them
in one module (and nowhere else) keeps the dependency direction obvious: this is
infrastructure importing infrastructure - the domain never participates.
"""

from __future__ import annotations

from catalog.adapters.persistence import tables as catalog_tables
from identity.adapters.persistence import tables as identity_tables
from inventory.adapters.persistence import tables as inventory_tables
from ordering.adapters.persistence import tables as ordering_tables
from shared.infrastructure.db import tables as shared_tables

__all__ = [
    "catalog_tables",
    "identity_tables",
    "inventory_tables",
    "ordering_tables",
    "shared_tables",
]
