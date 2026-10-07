"""Unit tests: shared value objects.

The value objects are the smallest unit of the domain's correctness. If a
``Quantity`` could be negative or a ``Money`` could mix currencies, every
aggregate built on top of them would need defensive checks - and the platform's
headline invariant (``available >= 0``) would be unprovable.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from shared.domain.errors import InvalidValue, InvariantViolation
from shared.domain.value_objects import Money, Quantity

pytestmark = pytest.mark.unit


class TestQuantity:
    def test_accepts_zero_and_positive_integers(self) -> None:
        assert Quantity(0).value == 0
        assert Quantity(7).value == 7
        assert int(Quantity.positive(3)) == 3

    @pytest.mark.parametrize("value", [-1, -100])
    def test_rejects_negative_amounts(self, value: int) -> None:
        with pytest.raises(InvalidValue):
            Quantity(value)

    @pytest.mark.parametrize("value", [True, False])
    def test_rejects_booleans(self, value: bool) -> None:
        with pytest.raises(InvalidValue):
            Quantity(value)  # type: ignore[arg-type]

    def test_positive_rejects_zero(self) -> None:
        with pytest.raises(InvalidValue):
            Quantity.positive(0)

    def test_subtraction_never_produces_a_negative_quantity(self) -> None:
        with pytest.raises(InvariantViolation):
            Quantity(1) - Quantity(2)
        assert (Quantity(5) - Quantity(2)).value == 3

    def test_arithmetic(self) -> None:
        assert (Quantity(2) + Quantity(3)).value == 5
        assert (Quantity(2) * 3).value == 6

    def test_comparisons_are_total(self) -> None:
        assert Quantity(1) < Quantity(2) <= Quantity(2)
        assert Quantity(3) > Quantity(2) >= Quantity(2)


class TestMoney:
    def test_accepts_minor_units_and_validates_currency(self) -> None:
        money = Money(2599, "GBP")
        assert money.amount_minor == 2599
        assert money.currency == "GBP"

    @pytest.mark.parametrize("currency", ["gbp", "GB", "GBPX", "12A", ""])
    def test_rejects_malformed_currencies(self, currency: str) -> None:
        with pytest.raises(InvalidValue):
            Money(100, currency)

    def test_amounts_are_signed_but_validate_their_currency(self) -> None:
        # ``Money`` is a signed amount (refunds, credit notes, adjustments), so it
        # accepts negatives; aggregates that must not go negative - SKU prices,
        # order line totals - validate that themselves.
        assert Money(-500, "GBP").is_negative()
        with pytest.raises(InvalidValue):
            Money(-500, "gbp")

    def test_of_major_units_converts_exactly(self) -> None:
        assert Money.of_major(Decimal("25.99"), "GBP").amount_minor == 2599
        assert Money.of_major("0.10", "GBP").amount_minor == 10

    def test_arithmetic_requires_the_same_currency(self) -> None:
        assert (Money(100, "GBP") + Money(50, "GBP")).amount_minor == 150
        assert (Money(100, "GBP") * 3).amount_minor == 300
        with pytest.raises(InvariantViolation):
            Money(100, "GBP") + Money(100, "USD")

    def test_zero_is_constructible_for_any_currency(self) -> None:
        assert Money.zero("EUR").is_zero()
