"""Spec 3.16's arithmetic: quantity x unit net price x (1 - discount / 100).

Its own tests because it is the one number on the line that nothing else
computes. Quantity, price and discount each have a step that confirms what was
typed; the resulting Price is Fakturama's answer, and this is the independent
one it gets compared against.
"""

from decimal import Decimal

import pytest

from automation.line_items import line_price


def price(quantity, unit, discount):
    return line_price(Decimal(quantity), Decimal(unit), Decimal(discount))


class TestLinePrice:
    def test_the_discounted_line_from_the_sample_order(self):
        assert price("2", "250", "10") == Decimal("450.00")

    def test_the_undiscounted_line_from_the_sample_order(self):
        assert price("3", "40", "0") == Decimal("120.00")

    def test_a_full_discount_is_free_not_an_error(self):
        assert price("2", "250", "100") == Decimal("0.00")

    def test_a_fractional_quantity(self):
        assert price("1.5", "10", "0") == Decimal("15.00")

    def test_a_fractional_discount(self):
        assert price("1", "100", "7.5") == Decimal("92.50")

    def test_rounds_once_at_the_end_not_per_unit(self):
        # 3 x 9.99 x 0.9967 = 29.871... -> 29.87. Rounding the discounted unit
        # price to 9.96 first and multiplying gives 29.88 - a cent adrift, on
        # an ordinary-looking line.
        assert price("3", "9.99", "0.33") == Decimal("29.87")

    def test_no_float_ever_touches_the_arithmetic(self):
        assert price("3", "0.1", "0") == Decimal("0.30")

    @pytest.mark.parametrize("discount", ["0", "0.0", "00"])
    def test_zero_discount_spellings_agree(self, discount):
        assert price("2", "250", discount) == Decimal("500.00")
