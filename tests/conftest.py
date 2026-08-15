"""Fixture builders.

Orders are built from strings and converted through the real schema, so the
tests exercise the same validation path production does.
"""

from __future__ import annotations

from decimal import Decimal

from extraction.schema import Address, Debtor, LineItem, Order, Payment, Totals


def D(value: str | int) -> Decimal:
    return Decimal(str(value))


def make_address(**overrides) -> Address:
    base = dict(
        street="1 Example Way",
        zip="10115",
        city="Berlin",
        country="DE",
        email="ap@example.com",
        phone="+49 30 123456",
        additional_name=None,
        address_specification=None,
        district=None,
    )
    base.update(overrides)
    return Address(**base)


def make_item(
    position: int = 1,
    sku: str | None = "SKU-1",
    description: str | None = "Widget",
    quantity: str = "2",
    unit_net_price: str = "10.00",
    vat_percent: str = "19",
    discount_percent: str | None = None,
    source_total: str | None = None,
) -> LineItem:
    """source_total defaults to the arithmetically correct value, so a test
    only states it when it means to make it wrong."""
    if source_total is None:
        discount = D(discount_percent) if discount_percent is not None else D(0)
        computed = D(quantity) * D(unit_net_price) * (D(1) - discount / D(100))
        source_total = str(computed.quantize(D("0.01")))
    return LineItem(
        position=position,
        sku=sku,
        description=description,
        quantity=D(quantity),
        unit_net_price=D(unit_net_price),
        vat_percent=D(vat_percent),
        discount_percent=D(discount_percent) if discount_percent is not None else None,
        source_total=D(source_total),
    )


def make_order(
    items: list[LineItem] | None = None,
    net_total: str | None = None,
    vat_total: str | None = None,
    gross_total: str | None = None,
    payment_method: str | None = "Bank Transfer",
    paid_status: str | None = "PAID",
    payment_date: str | None = "2026-03-04",
) -> Order:
    """Totals default to the arithmetically correct values."""
    items = items if items is not None else [make_item()]

    if net_total is None:
        net = sum(
            (
                (
                    it.quantity
                    * it.unit_net_price
                    * (D(1) - (it.discount_percent or D(0)) / D(100))
                ).quantize(D("0.01"))
                for it in items
            ),
            D(0),
        )
        net_total = str(net)
    if vat_total is None:
        vat = sum(
            (
                (
                    (
                        it.quantity
                        * it.unit_net_price
                        * (D(1) - (it.discount_percent or D(0)) / D(100))
                    ).quantize(D("0.01"))
                    * it.vat_percent
                    / D(100)
                ).quantize(D("0.01"))
                for it in items
            ),
            D(0),
        )
        vat_total = str(vat)
    if gross_total is None:
        gross_total = str(D(net_total) + D(vat_total))

    return Order(
        order_date="2026-03-01",
        external_reference="PO-2026-0042",
        currency="EUR",
        locale_evidence="EUR symbol and DD.MM.YYYY dates",
        debtor=Debtor(
            company="Example GmbH",
            first_name="Anna",
            last_name="Schmidt",
            salutation=None,
            alias="EXGMBH",
            printed_customer_id="CUST-1007",
            billing_address=make_address(),
            delivery_address=None,
        ),
        payment=Payment(
            method=payment_method,
            paid_status=paid_status,
            payment_date=payment_date,
        ),
        items=items,
        totals=Totals(
            net_total=D(net_total),
            vat_total=D(vat_total),
            gross_total=D(gross_total),
        ),
    )
