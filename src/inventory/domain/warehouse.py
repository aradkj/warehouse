"""The Warehouse aggregate: a physical or logical stocking location.

Warehouses own stock, and (from Phase 2 on) fulfilment tasks. Phase 1 keeps the
aggregate small: identity, a stable code, a name and an active flag. Address and
capacity data belong to later phases.

Deactivating a warehouse does **not** delete or move stock; it prevents *new*
stock operations from targeting it while existing reservations stay valid. That
is why deactivation is a soft, reversible flag rather than a status machine with
a terminal state.
"""

from __future__ import annotations

import re
from datetime import datetime

from inventory.domain.errors import InvalidWarehouseTransition
from inventory.domain.events import (
    WarehouseActivated,
    WarehouseDeactivated,
    WarehouseRegistered,
    WarehouseRenamed,
)
from shared.domain.aggregate import AggregateRoot
from shared.domain.errors import InvalidValue
from shared.domain.identifiers import TenantId, WarehouseId, new_id

_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{1,31}$")

MAX_WAREHOUSE_NAME_LENGTH = 160


class Warehouse(AggregateRoot):
    """Warehouse aggregate root."""

    __slots__ = ("_code", "_created_at", "_id", "_is_active", "_name", "_updated_at")

    def __init__(
        self,
        *,
        id: WarehouseId,
        tenant_id: TenantId,
        code: str,
        name: str,
        is_active: bool,
        created_at: datetime,
        updated_at: datetime,
        version: int = 0,
    ) -> None:
        super().__init__(tenant_id=tenant_id, version=version)
        self._id = id
        self._code = _validate_code(code)
        self._name = _validate_name(name)
        self._is_active = is_active
        self._created_at = created_at
        self._updated_at = updated_at

    # -- creation ----------------------------------------------------------
    @classmethod
    def register(cls, *, tenant_id: TenantId, code: str, name: str, now: datetime) -> Warehouse:
        warehouse_id = WarehouseId(new_id())
        warehouse = cls(
            id=warehouse_id,
            tenant_id=tenant_id,
            code=code,
            name=name,
            is_active=True,
            created_at=now,
            updated_at=now,
            version=0,
        )
        warehouse._record_event(
            WarehouseRegistered(tenant_id=tenant_id, warehouse_id=warehouse_id, code=warehouse.code)
        )
        return warehouse

    # -- state -------------------------------------------------------------
    @property
    def id(self) -> WarehouseId:
        return self._id

    @property
    def code(self) -> str:
        return self._code

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_active(self) -> bool:
        return self._is_active

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    # -- behaviour ---------------------------------------------------------
    def rename(self, *, name: str, now: datetime) -> None:
        new_name = _validate_name(name)
        if new_name == self._name:
            return
        self._name = new_name
        self._updated_at = now
        self._record_event(
            WarehouseRenamed(tenant_id=self.tenant_id, warehouse_id=self._id, name=new_name)
        )

    def deactivate(self, *, now: datetime) -> None:
        """Stop accepting new stock operations."""
        if not self._is_active:
            raise InvalidWarehouseTransition("INACTIVE", "INACTIVE")
        self._is_active = False
        self._updated_at = now
        self._record_event(WarehouseDeactivated(tenant_id=self.tenant_id, warehouse_id=self._id))

    def activate(self, *, now: datetime) -> None:
        """Re-enable the warehouse (an explicit, allowed transition)."""
        if self._is_active:
            raise InvalidWarehouseTransition("ACTIVE", "ACTIVE")
        self._is_active = True
        self._updated_at = now
        self._record_event(WarehouseActivated(tenant_id=self.tenant_id, warehouse_id=self._id))


def _validate_code(code: str) -> str:
    value = code.strip().upper()
    if not _CODE_RE.match(value):
        raise InvalidValue(
            "warehouse code must be 2-32 uppercase alphanumeric characters "
            "(dot, dash and underscore allowed after the first character)",
            details={"code": code},
        )
    return value


def _validate_name(name: str) -> str:
    stripped = name.strip()
    if not stripped or len(stripped) > MAX_WAREHOUSE_NAME_LENGTH:
        raise InvalidValue(
            f"warehouse name must be 1-{MAX_WAREHOUSE_NAME_LENGTH} characters",
            details={"field": "name"},
        )
    return stripped


__all__ = ["MAX_WAREHOUSE_NAME_LENGTH", "Warehouse"]
