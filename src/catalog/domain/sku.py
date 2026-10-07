"""The SKU aggregate: the unit that stock, orders and prices are expressed in.

A SKU belongs to exactly one product (by identity reference, not by object
reference) and carries the *list price* that becomes the pricing snapshot on an
order line at the moment the order is placed.
"""

from __future__ import annotations

from datetime import datetime

from catalog.domain.errors import InvalidSkuTransition
from catalog.domain.events import SkuActivated, SkuCreated, SkuDeactivated, SkuPriceChanged
from catalog.domain.value_objects import AttributeSet, SkuCode, SkuStatus
from shared.domain.aggregate import AggregateRoot
from shared.domain.errors import InvalidValue
from shared.domain.identifiers import ProductId, SkuId, TenantId, new_id
from shared.domain.value_objects import Money

MAX_SKU_NAME_LENGTH = 200


class Sku(AggregateRoot):
    """Catalog SKU aggregate root (the commercial unit of a product)."""

    __slots__ = (
        "_attributes",
        "_code",
        "_created_at",
        "_id",
        "_name",
        "_product_id",
        "_status",
        "_unit_price",
        "_updated_at",
    )

    def __init__(
        self,
        *,
        id: SkuId,
        tenant_id: TenantId,
        product_id: ProductId,
        code: SkuCode,
        name: str,
        attributes: AttributeSet,
        unit_price: Money,
        status: SkuStatus,
        created_at: datetime,
        updated_at: datetime,
        version: int = 0,
    ) -> None:
        super().__init__(tenant_id=tenant_id, version=version)
        self._id = id
        self._product_id = product_id
        self._code = code
        self._name = _validate_name(name)
        self._attributes = attributes
        self._unit_price = _validate_price(unit_price)
        self._status = status
        self._created_at = created_at
        self._updated_at = updated_at

    # -- creation ----------------------------------------------------------
    @classmethod
    def create(
        cls,
        *,
        tenant_id: TenantId,
        product_id: ProductId,
        code: SkuCode,
        name: str,
        attributes: AttributeSet,
        unit_price: Money,
        now: datetime,
    ) -> Sku:
        sku_id = SkuId(new_id())
        sku = cls(
            id=sku_id,
            tenant_id=tenant_id,
            product_id=product_id,
            code=code,
            name=name,
            attributes=attributes,
            unit_price=unit_price,
            status=SkuStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            version=0,
        )
        sku._record_event(
            SkuCreated(
                tenant_id=tenant_id,
                product_id=product_id,
                sku_id=sku_id,
                sku_code=code.value,
                unit_price_minor=unit_price.amount_minor,
                currency=unit_price.currency,
            )
        )
        return sku

    # -- state -------------------------------------------------------------
    @property
    def id(self) -> SkuId:
        return self._id

    @property
    def product_id(self) -> ProductId:
        return self._product_id

    @property
    def code(self) -> SkuCode:
        return self._code

    @property
    def name(self) -> str:
        return self._name

    @property
    def attributes(self) -> AttributeSet:
        return self._attributes

    @property
    def unit_price(self) -> Money:
        return self._unit_price

    @property
    def status(self) -> SkuStatus:
        return self._status

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    @property
    def is_active(self) -> bool:
        return self._status is SkuStatus.ACTIVE

    # -- behaviour ---------------------------------------------------------
    def change_price(self, *, new_price: Money, now: datetime) -> None:
        """Set a new list price.

        Only future orders are affected: existing order lines keep the snapshot
        taken when they were placed.
        """
        validated = _validate_price(new_price)
        if validated == self._unit_price:
            return
        previous = self._unit_price
        self._unit_price = validated
        self._updated_at = now
        self._record_event(
            SkuPriceChanged(
                tenant_id=self.tenant_id,
                sku_id=self._id,
                previous_price_minor=previous.amount_minor,
                new_price_minor=validated.amount_minor,
                currency=validated.currency,
            )
        )

    def deactivate(self, *, now: datetime) -> None:
        """Withdraw the SKU from sale."""
        if self._status is SkuStatus.INACTIVE:
            raise InvalidSkuTransition(self._status.value, SkuStatus.INACTIVE.value)
        self._status = SkuStatus.INACTIVE
        self._updated_at = now
        self._record_event(SkuDeactivated(tenant_id=self.tenant_id, sku_id=self._id))

    def activate(self, *, now: datetime) -> None:
        """Return a withdrawn SKU to sale (an explicit, allowed transition)."""
        if self._status is SkuStatus.ACTIVE:
            raise InvalidSkuTransition(self._status.value, SkuStatus.ACTIVE.value)
        self._status = SkuStatus.ACTIVE
        self._updated_at = now
        self._record_event(SkuActivated(tenant_id=self.tenant_id, sku_id=self._id))


def _validate_name(name: str) -> str:
    if not isinstance(name, str):
        raise InvalidValue("sku name must be a string", details={"field": "name"})
    stripped = name.strip()
    if not stripped or len(stripped) > MAX_SKU_NAME_LENGTH:
        raise InvalidValue(
            f"sku name must be 1-{MAX_SKU_NAME_LENGTH} characters",
            details={"field": "name"},
        )
    return stripped


def _validate_price(price: Money) -> Money:
    if price.amount_minor < 0:
        raise InvalidValue(
            "sku price must not be negative",
            details={"amount_minor": price.amount_minor, "currency": price.currency},
        )
    return price


__all__ = ["MAX_SKU_NAME_LENGTH", "Sku"]
