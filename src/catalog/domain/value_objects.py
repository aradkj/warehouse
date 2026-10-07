"""Catalog value objects: SKU code, product slug, attribute bag, lifecycle states."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from shared.domain.errors import InvalidValue

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SKU_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{1,63}$")
_ATTRIBUTE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

MAX_ATTRIBUTES = 64
MAX_ATTRIBUTE_STRING = 512
MAX_ATTRIBUTE_LIST_ITEMS = 50
MAX_ATTRIBUTE_LIST_ITEM_LENGTH = 200

#: Attributes are intentionally limited to scalar-ish, JSON-safe values. Nested
#: documents belong to the search projection (Phase 3), not to the catalogue of
#: record, because every value here has to survive strict validation forever.
type AttributeValue = str | int | float | bool | list[str]


class ProductStatus(StrEnum):
    """Product lifecycle state.

    ``DRAFT``    - created, not yet sellable.
    ``ACTIVE``   - sellable.
    ``ARCHIVED`` - terminal: no longer sold and never sellable again.
    """

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class SkuStatus(StrEnum):
    """SKU lifecycle state.

    ``ACTIVE``   - purchasable.
    ``INACTIVE`` - temporarily withdrawn (superseded, out of season, ...).
    Reactivating an INACTIVE SKU is an explicit, allowed transition.
    """

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


@dataclass(frozen=True, slots=True)
class ProductSlug:
    """A human-readable, tenant-unique product identifier used in URLs."""

    value: str

    def __post_init__(self) -> None:
        if not (3 <= len(self.value) <= 80) or not _SLUG_RE.match(self.value):
            raise InvalidValue(
                "product slug must be 3-80 characters of lowercase letters, digits and dashes",
                details={"slug": self.value},
            )

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class SkuCode:
    """The stable, human-readable code a warehouse and channel refer to."""

    value: str

    def __post_init__(self) -> None:
        if not _SKU_CODE_RE.match(self.value):
            raise InvalidValue(
                "sku code must be 2-64 uppercase alphanumeric characters "
                "(dot, dash and underscore allowed after the first character)",
                details={"sku_code": self.value},
            )

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, eq=False)
class AttributeSet:
    """Validated, immutable bag of searchable attributes.

    Used by both products and SKUs. Validation happens once, here, so that no
    downstream code (search projections in Phase 3, channel exports, ...) has to
    defend against malformed attributes.
    """

    values: Mapping[str, AttributeValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.values, MappingProxyType):
            object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        self._validate()

    # -- constructors ------------------------------------------------------
    @classmethod
    def empty(cls) -> AttributeSet:
        return cls({})

    # -- behaviour ---------------------------------------------------------
    def get(self, key: str, default: AttributeValue | None = None) -> AttributeValue | None:
        return self.values.get(key, default)

    def as_dict(self) -> dict[str, AttributeValue]:
        return dict(self.values)

    def __len__(self) -> int:
        return len(self.values)

    def __contains__(self, key: object) -> bool:
        return key in self.values

    # -- validation --------------------------------------------------------
    def _validate(self) -> None:
        if len(self.values) > MAX_ATTRIBUTES:
            raise InvalidValue(
                f"at most {MAX_ATTRIBUTES} attributes are allowed",
                details={"count": len(self.values)},
            )
        for key, value in self.values.items():
            if not _ATTRIBUTE_KEY_RE.match(key):
                raise InvalidValue(
                    "attribute keys must be snake_case and start with a letter",
                    details={"key": key},
                )
            _validate_value(key, value)

    def __deepcopy__(self, memo: dict[int, object]) -> AttributeSet:
        """Return ``self``: the set is immutable, so a copy would be pointless.

        This also keeps aggregates copyable at all. ``mappingproxy`` objects cannot
        be deep-copied, and any snapshotting mechanism (test fakes, caches,
        serialisation helpers) that walks a product or SKU would otherwise fail on
        its attribute set.
        """
        return self

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AttributeSet):
            return NotImplemented
        return dict(self.values) == dict(other.values)

    def __hash__(self) -> int:
        return hash(tuple(sorted(self.values.items())))


def _validate_value(key: str, value: AttributeValue) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidValue(
                "attribute floats must be finite", details={"key": key, "value": value}
            )
        return
    if isinstance(value, str):
        if not value or len(value) > MAX_ATTRIBUTE_STRING:
            raise InvalidValue(
                f"attribute strings must be 1-{MAX_ATTRIBUTE_STRING} characters",
                details={"key": key},
            )
        return
    if isinstance(value, list):
        if len(value) > MAX_ATTRIBUTE_LIST_ITEMS:
            raise InvalidValue(
                f"attribute lists may hold at most {MAX_ATTRIBUTE_LIST_ITEMS} items",
                details={"key": key},
            )
        for item in value:
            if not item or len(item) > MAX_ATTRIBUTE_LIST_ITEM_LENGTH:
                raise InvalidValue(
                    "attribute list items must be non-empty strings of at most "
                    f"{MAX_ATTRIBUTE_LIST_ITEM_LENGTH} characters",
                    details={"key": key},
                )
        return
    raise InvalidValue(
        "attribute values must be a string, integer, float, boolean or list of strings",
        details={"key": key, "type": type(value).__name__},
    )


def normalise_attribute_input(raw: Mapping[str, object] | None) -> AttributeSet:
    """Convert an untyped mapping (e.g. parsed JSON) into an ``AttributeSet``."""
    if not raw:
        return AttributeSet.empty()
    return AttributeSet({str(key): _coerce_value(key, value) for key, value in raw.items()})


def _coerce_value(key: str, value: object) -> AttributeValue:
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            if not isinstance(item, (str, int, float, bool)):
                raise InvalidValue(
                    "attribute lists may only contain scalar values",
                    details={"key": key},
                )
            items.append(item if isinstance(item, str) else str(item))
        return items
    raise InvalidValue(
        "attribute values must be a string, integer, float, boolean or list of strings",
        details={"key": key, "type": type(value).__name__},
    )


__all__ = [
    "AttributeSet",
    "AttributeValue",
    "ProductSlug",
    "ProductStatus",
    "SkuCode",
    "SkuStatus",
    "normalise_attribute_input",
]
