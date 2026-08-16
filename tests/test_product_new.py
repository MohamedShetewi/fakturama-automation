"""Spec 3.7's two arithmetic decisions, tested without Fakturama.

Both exist because the field lies about what it holds. It renders '$297.50'
for a written '297.50', and it asks for gross while the extracted order carries
net - and neither mismatch would be caught downstream, because the
reconciliation gate ran a stage earlier, against the image.
"""

from decimal import Decimal

import pytest

from automation.actions import parse_money
from automation.product_new import price_for


class TestParseMoney:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("$297.50", "297.50"),
            ("297.50", "297.50"),
            ("297,50 EUR", "297.50"),
            ("€ 40.00", "40.00"),
            ("1,234.56", "1234.56"),      # thousands with a comma
            ("1.234,56", "1234.56"),      # ... and the other convention
            ("$0.00", "0.00"),
            ("-12.34", "-12.34"),
        ],
    )
    def test_reads_the_number_through_the_rendering(self, text, expected):
        assert parse_money(text) == Decimal(expected)

    @pytest.mark.parametrize("text", ["", "   ", None, "$", "n/a", "--"])
    def test_no_number_is_none_never_zero(self, text):
        # Zero is a real price. An unreadable field must not become one.
        assert parse_money(text) is None


class TestPriceFor:
    def test_a_net_field_takes_the_net_price_unchanged(self):
        assert price_for("product.price_net", Decimal("250"), Decimal("19")) == Decimal("250.00")

    def test_a_gross_field_takes_the_price_with_vat_added(self):
        # The live workspace prices products gross while the order is net:
        # typing 250 there would book ~16% under what the customer was charged.
        assert price_for("product.price_gross", Decimal("250"), Decimal("19")) == Decimal("297.50")

    def test_the_second_line_item_too(self):
        assert price_for("product.price_gross", Decimal("40"), Decimal("19")) == Decimal("47.60")

    def test_zero_vat_leaves_a_gross_price_equal_to_net(self):
        assert price_for("product.price_gross", Decimal("40"), Decimal("0")) == Decimal("40.00")

    def test_rounds_to_cents_once_at_the_end(self):
        # 33.33 * 1.19 = 39.6627 -> 39.66, not 39.67 via an earlier rounding.
        assert price_for("product.price_gross", Decimal("33.33"), Decimal("19")) == Decimal("39.66")

    def test_no_float_ever_touches_the_arithmetic(self):
        # 0.1 + 0.2 territory: this must be exact, not 118.99999999999999.
        assert price_for("product.price_gross", Decimal("99.99"), Decimal("19")) == Decimal("118.99")
