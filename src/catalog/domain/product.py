"""The Product aggregate.

A product is the sellable *concept* (name, slug, attributes, lifecycle). The
commercial units it can be sold in are SKUs, which are a **separate aggregate**
(see :mod:`catalog.domain.sku` and ADR-0003): inventory and ordering reference
SKUs directly and must not contend with product-metadata edits.
"""

from __future__ import annotations

from datetime import datetime

from catalog.domain.errors import InvalidProduct, InvalidProductTransition
from catalog.domain.events import (
    ProductActivated,
    ProductArchived,
    ProductCreated,
    ProductRevised,
)
from catalog.domain.value_objects import AttributeSet, ProductSlug, ProductStatus
from shared.domain.aggregate import AggregateRoot
from shared.domain.identifiers import ProductId, TenantId, new_id

MAX_NAME_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 4000

#: Allowed lifecycle transitions. Anything not listed is rejected, which is what
#: makes accidental "backwards" transitions impossible by construction.
_ALLOWED_TRANSITIONS: dict[ProductStatus, frozenset[ProductStatus]] = {
    ProductStatus.DRAFT: frozenset({ProductStatus.ACTIVE, ProductStatus.ARCHIVED}),
    ProductStatus.ACTIVE: frozenset({ProductStatus.ARCHIVED}),
    # ARCHIVED is terminal in Phase 1: reintroducing an archived product is a
    # merchandising decision that needs its own lifecycle concept, not a
    # silent state flip.
    ProductStatus.ARCHIVED: frozenset(),
}


class Product(AggregateRoot):
    """Catalog product aggregate root."""

    __slots__ = (
        "_attributes",
        "_created_at",
        "_description",
        "_id",
        "_name",
        "_slug",
        "_status",
        "_updated_at",
    )

    def __init__(
        self,
        *,
        id: ProductId,
        tenant_id: TenantId,
        slug: ProductSlug,
        name: str,
        description: str | None,
        attributes: AttributeSet,
        status: ProductStatus,
        created_at: datetime,
        updated_at: datetime,
        version: int = 0,
    ) -> None:
        super().__init__(tenant_id=tenant_id, version=version)
        self._id = id
        self._slug = slug
        self._name = _validate_name(name)
        self._description = _validate_description(description)
        self._attributes = attributes
        self._status = status
        self._created_at = created_at
        self._updated_at = updated_at

    # -- creation ----------------------------------------------------------
    @classmethod
    def create(
        cls,
        *,
        tenant_id: TenantId,
        slug: ProductSlug,
        name: str,
        description: str | None,
        attributes: AttributeSet,
        now: datetime,
    ) -> Product:
        """Create a product in the ``DRAFT`` state.

        Products are not sellable until explicitly activated: a draft lets
        merchandising prepare attributes before channels can order stock.
        """
        product_id = ProductId(new_id())
        product = cls(
            id=product_id,
            tenant_id=tenant_id,
            slug=slug,
            name=name,
            description=description,
            attributes=attributes,
            status=ProductStatus.DRAFT,
            created_at=now,
            updated_at=now,
            version=0,
        )
        product._record_event(
            ProductCreated(
                tenant_id=tenant_id,
                product_id=product_id,
                slug=slug.value,
                name=product.name,
            )
        )
        return product

    # -- state -------------------------------------------------------------
    @property
    def id(self) -> ProductId:
        return self._id

    @property
    def slug(self) -> ProductSlug:
        return self._slug

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str | None:
        return self._description

    @property
    def attributes(self) -> AttributeSet:
        return self._attributes

    @property
    def status(self) -> ProductStatus:
        return self._status

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    @property
    def is_active(self) -> bool:
        return self._status is ProductStatus.ACTIVE

    @property
    def is_archived(self) -> bool:
        return self._status is ProductStatus.ARCHIVED

    # -- behaviour ---------------------------------------------------------
    def revise(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        attributes: AttributeSet | None = None,
        now: datetime,
    ) -> None:
        """Change descriptive metadata. Lifecycle state is *not* touched here.

        Archived products are immutable: their attributes are part of the
        historical record that orders were priced and fulfilled against.
        """
        if self._status is ProductStatus.ARCHIVED:
            raise InvalidProductTransition(self._status.value, "REVISE")
        changed = False
        if name is not None:
            new_name = _validate_name(name)
            changed = changed or new_name != self._name
            self._name = new_name
        if description is not None:
            new_description = _validate_description(description)
            changed = changed or new_description != self._description
            self._description = new_description
        if attributes is not None:
            changed = changed or attributes != self._attributes
            self._attributes = attributes
        if not changed:
            return
        self._updated_at = now
        self._record_event(
            ProductRevised(tenant_id=self.tenant_id, product_id=self._id, slug=self._slug.value)
        )

    def activate(self, *, now: datetime) -> None:
        """Make the product sellable."""
        self._transition_to(ProductStatus.ACTIVE, now=now)
        self._record_event(ProductActivated(tenant_id=self.tenant_id, product_id=self._id))

    def archive(self, *, now: datetime) -> None:
        """Retire the product permanently."""
        self._transition_to(ProductStatus.ARCHIVED, now=now)
        self._record_event(ProductArchived(tenant_id=self.tenant_id, product_id=self._id))

    # -- internals ---------------------------------------------------------
    def _transition_to(self, target: ProductStatus, *, now: datetime) -> None:
        allowed = _ALLOWED_TRANSITIONS[self._status]
        if target not in allowed:
            raise InvalidProductTransition(self._status.value, target.value)
        if target is self._status:  # pragma: no cover - guarded by the table above
            raise InvalidProductTransition(self._status.value, target.value)
        self._status = target
        self._updated_at = now


def _validate_name(name: str) -> str:
    if not isinstance(name, str):
        raise InvalidProduct("product name must be a string", details={"field": "name"})
    stripped = name.strip()
    if not stripped or len(stripped) > MAX_NAME_LENGTH:
        raise InvalidProduct(
            f"product name must be 1-{MAX_NAME_LENGTH} characters",
            details={"field": "name"},
        )
    return stripped


def _validate_description(description: str | None) -> str | None:
    if description is None:
        return None
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise InvalidProduct(
            f"product description must be at most {MAX_DESCRIPTION_LENGTH} characters",
            details={"field": "description"},
        )
    stripped = description.strip()
    return stripped or None


__all__ = ["MAX_DESCRIPTION_LENGTH", "MAX_NAME_LENGTH", "Product"]
