"""The gate. If anything in this repo deserves exhaustive tests, it is this."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from conftest import D, make_item, make_order

from extraction.reconcile import line_net, line_vat, money, reconcile
from extraction.schema import Order


# --- the happy path ----------------------------------------------------------


def test_clean_order_reconciles():
    report = reconcile(make_order())
    assert report.ok, [str(f) for f in report.failures]
    assert report.computed_net == D("20.00")
    assert report.computed_vat == D("3.80")
    assert report.computed_gross == D("23.80")


def test_multi_line_order_reconciles():
    items = [
        make_item(position=1, sku="A-1", quantity="3", unit_net_price="19.99"),
        make_item(position=2, sku="B-2", quantity="1", unit_net_price="250.00", vat_percent="7"),
        make_item(position=3, sku="C-3", quantity="10", unit_net_price="4.95", discount_percent="10"),
    ]
    report = reconcile(make_order(items=items))
    assert report.ok, [str(f) for f in report.failures]


def test_discount_is_applied_to_the_line():
    item = make_item(quantity="10", unit_net_price="4.95", discount_percent="10")
    # 10 x 4.95 = 49.50, less 10% = 44.55
    assert line_net(item) == D("44.55")
    assert item.source_total == D("44.55")


def test_null_discount_is_treated_as_zero():
    assert line_net(make_item(quantity="2", unit_net_price="10.00")) == D("20.00")


# --- the failures we exist to catch ------------------------------------------


def test_corrupted_line_total_is_caught_and_names_the_row():
    items = [
        make_item(position=1, sku="A-1"),
        make_item(position=2, sku="B-2", quantity="4", unit_net_price="25.00", source_total="90.00"),
        make_item(position=3, sku="C-3"),
    ]
    # Totals are left consistent with the *correct* line so only the line check trips.
    report = reconcile(make_order(items=items, net_total="140.00", vat_total="26.60", gross_total="166.60"))

    assert not report.ok
    line_failures = [f for f in report.failures if f.check == "line_total"]
    assert len(line_failures) == 1
    failure = line_failures[0]
    # The point of the gate is that it says *which* row.
    assert failure.location == "items[1].source_total"
    assert failure.expected == D("100.00")
    assert failure.found == D("90.00")
    assert failure.delta == D("10.00")


def test_net_total_mismatch_is_caught():
    report = reconcile(make_order(net_total="25.00"))
    assert not report.ok
    assert {f.check for f in report.failures} >= {"net_total"}
    assert any(f.location == "totals.net_total" for f in report.failures)


def test_vat_total_mismatch_is_caught():
    report = reconcile(make_order(vat_total="5.00"))
    assert not report.ok
    assert any(f.location == "totals.vat_total" for f in report.failures)


def test_gross_total_mismatch_is_caught():
    report = reconcile(make_order(gross_total="99.99"))
    assert not report.ok
    assert any(f.location == "totals.gross_total" for f in report.failures)


# --- the tolerance boundary --------------------------------------------------


@pytest.mark.parametrize(
    "printed_total, should_pass",
    [
        ("20.00", True),   # exact
        ("20.01", True),   # +1 cent: rounding, absorbed
        ("19.99", True),   # -1 cent: rounding, absorbed
        ("20.02", False),  # +2 cents: a real disagreement
        ("19.98", False),  # -2 cents
    ],
)
def test_tolerance_boundary(printed_total, should_pass):
    item = make_item(quantity="2", unit_net_price="10.00", source_total=printed_total)
    report = reconcile(
        make_order(items=[item], net_total=printed_total, vat_total="3.80",
                   gross_total=str(D(printed_total) + D("3.80")))
    )
    line_failures = [f for f in report.failures if f.check == "line_total"]
    assert (not line_failures) is should_pass, [str(f) for f in report.failures]


# --- structural blockers -----------------------------------------------------


def test_empty_item_list_is_structural():
    report = reconcile(make_order(items=[], net_total="0.00", vat_total="0.00", gross_total="0.00"))
    assert not report.ok
    assert report.failures[0].check == "structural"
    assert report.failures[0].location == "items"


def test_missing_sku_is_structural_because_the_product_cannot_be_resolved():
    report = reconcile(make_order(items=[make_item(sku=None)]))
    assert not report.ok
    assert any(f.location == "items[0].sku" for f in report.failures)


def test_structural_failures_short_circuit_the_arithmetic_checks():
    # A blank SKU alongside deliberately wrong totals: we should hear about the
    # blocker, not a pile of downstream arithmetic noise.
    report = reconcile(make_order(items=[make_item(sku="  ")], net_total="999.99"))
    assert not report.ok
    assert {f.check for f in report.failures} == {"structural"}


def test_zero_quantity_is_structural():
    report = reconcile(make_order(items=[make_item(quantity="0", source_total="0.00")]))
    assert not report.ok
    assert any(f.location == "items[0].quantity" for f in report.failures)


# --- decimal hygiene ---------------------------------------------------------


def test_no_binary_float_drift_on_a_classic_case():
    # 0.1 + 0.2 in binary float is 0.30000000000000004. In Decimal it is 0.30,
    # and the whole gate depends on that.
    items = [
        make_item(position=1, sku="A", quantity="1", unit_net_price="0.10", vat_percent="0"),
        make_item(position=2, sku="B", quantity="1", unit_net_price="0.20", vat_percent="0"),
    ]
    report = reconcile(make_order(items=items, net_total="0.30", vat_total="0.00", gross_total="0.30"))
    assert report.ok, [str(f) for f in report.failures]
    assert report.computed_net == D("0.30")


def test_raw_json_is_parsed_as_decimal_not_float():
    """The production path parses with parse_float=Decimal; confirm that keeps
    values exact through schema validation."""
    payload = {
        "order_date": "2026-03-01",
        "external_reference": "PO-1",
        "currency": "EUR",
        "locale_evidence": "EUR symbol",
        "debtor": {
            "company": "X", "first_name": None, "last_name": None, "salutation": None,
            "alias": None, "printed_customer_id": None,
            "billing_address": {
                "street": None, "zip": None, "city": None, "country": None,
                "email": None, "phone": None, "additional_name": None,
                "address_specification": None, "district": None,
            },
            "delivery_address": None,
        },
        "payment": {"method": "Bank Transfer", "paid_status": "PAID", "payment_date": None},
        "items": [{
            "position": 1, "sku": "A", "description": "d", "quantity": 3,
            "unit_net_price": 19.99, "vat_percent": 19, "discount_percent": None,
            "source_total": 59.97,
        }],
        "totals": {"net_total": 59.97, "vat_total": 11.39, "gross_total": 71.36},
    }
    order = Order.model_validate(json.loads(json.dumps(payload), parse_float=Decimal))
    assert order.items[0].unit_net_price == Decimal("19.99")
    assert reconcile(order).ok


# --- rounding policy ---------------------------------------------------------


def test_money_rounds_half_up_not_bankers():
    # Python's round() would give 0.02 for both; invoices round half away from zero.
    assert money(Decimal("0.025")) == Decimal("0.03")
    assert money(Decimal("0.035")) == Decimal("0.04")


def test_line_vat_uses_the_discounted_net():
    item = make_item(quantity="10", unit_net_price="4.95", discount_percent="10")
    assert line_net(item) == D("44.55")
    assert line_vat(item) == D("8.46")  # 44.55 x 19% = 8.4645 -> 8.46
