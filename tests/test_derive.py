"""Derived values. These feed the UI automation directly, so a wrong one here
becomes a wrong master record in Fakturama."""

from __future__ import annotations

import pytest
from conftest import D, make_item, make_order

from extraction.derive import (
    derive,
    format_percentage,
    is_paid,
    payment_code,
    product_gross_price,
    vat_name,
)


# --- product master price (spec 3.9) -----------------------------------------


@pytest.mark.parametrize(
    "unit_net, vat, expected",
    [
        ("10.00", "19", "11.90"),
        ("19.99", "19", "23.79"),   # 23.7881 -> 23.79
        ("250.00", "7", "267.50"),
        ("4.95", "0", "4.95"),
        ("0.10", "19", "0.12"),     # 0.119 -> 0.12, half-up
    ],
)
def test_product_gross_price(unit_net, vat, expected):
    assert product_gross_price(D(unit_net), D(vat)) == D(expected)


def test_product_gross_price_ignores_the_line_discount():
    """Spec 3.9 is explicit: do not apply the transaction-line discount to the
    product master price. The discount belongs to this order line; the master
    record outlives it."""
    item = make_item(quantity="10", unit_net_price="4.95", discount_percent="10")
    derived = derive(make_order(items=[item]))

    # 4.95 x 1.19 = 5.8905 -> 5.89. NOT 4.455 x 1.19 = 5.30.
    assert derived.items[0].product_gross_price == D("5.89")


# --- VAT record naming (spec 3.5-3.6) ----------------------------------------


@pytest.mark.parametrize(
    "percent, expected",
    [
        ("19", "19"),
        ("19.00", "19"),    # a trailing .00 would fail Fakturama's exact-name match
        ("7", "7"),
        ("19.5", "19.5"),
        ("0", "0"),
    ],
)
def test_format_percentage(percent, expected):
    assert format_percentage(D(percent)) == expected


def test_vat_name():
    assert vat_name(D("19")) == "VAT 19%"
    assert vat_name(D("19.00")) == "VAT 19%"
    assert vat_name(D("7.5")) == "VAT 7.5%"


# --- payment code mapping (spec 2.10.4) --------------------------------------


@pytest.mark.parametrize(
    "method, expected",
    [
        ("Bank Transfer", "Credit transfer"),
        ("bank transfer", "Credit transfer"),
        ("  Bank   Transfer  ", "Credit transfer"),  # whitespace from OCR/layout
        ("Credit Card", "Credit card"),
        ("SEPA Direct Debit", "SEPA direct debit"),
    ],
)
def test_payment_code_mapping(method, expected):
    assert payment_code(method) == expected


@pytest.mark.parametrize("method", [None, "", "PayPal", "Cash on delivery", "Bank"])
def test_unknown_payment_method_returns_none_rather_than_guessing(method):
    """A near-miss like 'Bank' must not silently resolve to Credit transfer.
    The automation stops for manual review instead."""
    assert payment_code(method) is None


def test_unmapped_payment_method_is_flagged():
    derived = derive(make_order(payment_method="PayPal"))
    assert derived.payment_code is None
    assert derived.unmapped_payment_method is True


def test_absent_payment_method_is_not_flagged_as_unmapped():
    # Nothing printed is a different situation from something unrecognized.
    derived = derive(make_order(payment_method=None))
    assert derived.unmapped_payment_method is False


# --- paid status (spec 5.3) --------------------------------------------------


@pytest.mark.parametrize("status", ["PAID", "paid", " Paid "])
def test_is_paid_true(status):
    assert is_paid(status) is True


@pytest.mark.parametrize("status", [None, "", "UNPAID", "PARTIALLY PAID", "OPEN", "NOT PAID"])
def test_is_paid_false(status):
    """Anything that is not exactly PAID leaves the invoice unpaid - spec 5.3
    forbids inventing a date or value."""
    assert is_paid(status) is False


# --- shape -------------------------------------------------------------------


def test_derive_preserves_item_order_and_positions():
    items = [
        make_item(position=1, sku="A-1", unit_net_price="10.00", vat_percent="19"),
        make_item(position=2, sku="B-2", unit_net_price="250.00", vat_percent="7"),
    ]
    derived = derive(make_order(items=items))
    assert [i.position for i in derived.items] == [1, 2]
    assert [i.sku for i in derived.items] == ["A-1", "B-2"]
    assert [i.vat_name for i in derived.items] == ["VAT 19%", "VAT 7%"]
