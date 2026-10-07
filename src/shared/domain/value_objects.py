"""Shared value objects.

Value objects are immutable, have no identity and validate themselves on
construction: an instance that exists is always valid, so no downstream code has
to re-check it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Self

from shared.domain.errors import InvalidValue, InvariantViolation

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True, slots=True, order=False)
class Quantity:
    """A non-negative whole amount of units.

    ``Quantity`` is used for stock, reserved stock, movements, reservation
    allocations and order lines. The non-negativity rule is the value-object
    level expression of the platform's headline invariant
    (``available_stock >= 0``): a negative quantity cannot even be represented.
    """

    value: int

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int):
            raise InvalidValue("quantity must be an integer", details={"value": repr(self.value)})
        if self.value < 0:
            raise InvalidValue("quantity must not be negative", details={"value": self.value})

    # -- constructors -----------------------------------------------------
    @classmethod
    def zero(cls) -> Self:
        return cls(0)

    @classmethod
    def of(cls, value: int) -> Self:
        return cls(value)

    @classmethod
    def positive(cls, value: int) -> Self:
        """Build a strictly positive quantity (used by commands that require it)."""
        quantity = cls(value)
        if quantity.value == 0:
            raise InvalidValue("quantity must be greater than zero", details={"value": value})
        return quantity

    # -- behaviour --------------------------------------------------------
    def is_zero(self) -> bool:
        return self.value == 0

    def is_positive(self) -> bool:
        return self.value > 0

    def __add__(self, other: Quantity) -> Quantity:
        return Quantity(self.value + other.value)

    def __sub__(self, other: Quantity) -> Quantity:
        result = self.value - other.value
        if result < 0:
            raise InvariantViolation(
                "quantity subtraction would produce a negative quantity",
                details={"left": self.value, "right": other.value},
            )
        return Quantity(result)

    def __mul__(self, factor: int) -> Quantity:
        if factor < 0:
            raise InvariantViolation("cannot multiply a quantity by a negative factor")
        return Quantity(self.value * factor)

    def __lt__(self, other: Quantity) -> bool:
        return self.value < other.value

    def __le__(self, other: Quantity) -> bool:
        return self.value <= other.value

    def __gt__(self, other: Quantity) -> bool:
        return self.value > other.value

    def __ge__(self, other: Quantity) -> bool:
        return self.value >= other.value

    def __int__(self) -> int:
        return self.value

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True, order=False)
class Money:
    """An amount of money in a single currency.

    Amounts are stored as integer **minor units** (cents). Floating point is
    never used for money: ``10.1 + 20.2 != 30.3`` in binary floating point, and
    pricing snapshots must reconcile exactly.
    """

    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
            raise InvalidValue("money amount must be an integer number of minor units")
        if not _CURRENCY_RE.match(self.currency):
            raise InvalidValue(
                "currency must be a three-letter uppercase ISO-4217 code",
                details={"currency": self.currency},
            )

    # -- constructors -----------------------------------------------------
    @classmethod
    def zero(cls, currency: str) -> Self:
        return cls(0, currency)

    @classmethod
    def of_major(cls, amount: Decimal | str | int, currency: str) -> Self:
        """Build money from a major-unit amount (``"12.34"`` -> ``1234``).

        Only used by adapters translating external representations; the domain
        itself always works in minor units.
        """
        decimal_amount = amount if isinstance(amount, Decimal) else Decimal(amount)
        minor = decimal_amount.scaleb(2)
        if minor != minor.to_integral_value():
            raise InvalidValue(
                "amount has more than two decimal places",
                details={"amount": str(amount), "currency": currency},
            )
        return cls(int(minor), currency)

    # -- behaviour --------------------------------------------------------
    def __add__(self, other: Money) -> Money:
        self._assert_same_currency(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._assert_same_currency(other)
        return Money(self.amount_minor - other.amount_minor, self.currency)

    def __mul__(self, factor: int) -> Money:
        if factor < 0:
            raise InvariantViolation("cannot multiply money by a negative factor")
        return Money(self.amount_minor * factor, self.currency)

    def is_zero(self) -> bool:
        return self.amount_minor == 0

    def is_negative(self) -> bool:
        return self.amount_minor < 0

    def as_decimal(self) -> Decimal:
        return Decimal(self.amount_minor).scaleb(-2)

    def _assert_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise InvariantViolation(
                "cannot mix currencies in a money operation",
                details={"left": self.currency, "right": other.currency},
            )

    def __str__(self) -> str:
        return f"{self.as_decimal()} {self.currency}"


__all__ = ["Money", "Quantity"]
