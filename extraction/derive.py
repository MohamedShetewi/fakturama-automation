"""Stage 3a - values the UIA half needs, computed here rather than there.

All deterministic, all in Python. The model reads the document; it does not do
arithmetic and it does not guess at Fakturama's vocabulary. Anything it cannot
be trusted to get exactly right belongs in this file, where it is testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from . import config
from .schema import LineItem, Order

HUNDRED = Decimal(100)

# Spec 2.10.4. Exact mapping, deliberately small. Anything not on this list
# returns None so the automation stops for manual review rather than picking a
# plausible-looking code and silently booking against the wrong one.
PAYMENT_CODES: dict[str, str] = {
    "bank transfer": "Credit transfer",
    "credit card": "Credit card",
    "sepa direct debit": "SEPA direct debit",
}


def _normalize(text: str) -> str:
    return " ".join(text.split()).strip().lower()


def payment_code(method: str | None) -> str | None:
    """Map the printed payment method onto Fakturama's payment code."""
    if not method:
        return None
    return PAYMENT_CODES.get(_normalize(method))


def is_paid(paid_status: str | None) -> bool:
    """Spec 5.3: act only on an explicit PAID. Anything else leaves the
    invoice unpaid, with no invented date or value."""
    return bool(paid_status) and _normalize(paid_status) == "paid"


def format_percentage(percent: Decimal) -> str:
    """19.00 -> '19', 19.50 -> '19.5'. Fakturama's VAT records are named after
    the percentage, so a trailing '.00' would fail the exact-name match in
    spec 3.5."""
    value = percent.normalize()
    if value == value.to_integral_value():
        value = value.to_integral_value()
    return format(value, "f")


def vat_name(percent: Decimal) -> str:
    """Spec 3.5-3.6: the VAT record is named 'VAT' followed by the percentage."""
    return f"VAT {format_percentage(percent)}%"


def product_gross_price(unit_net_price: Decimal, vat_percent: Decimal) -> Decimal:
    """Spec 3.9: Price (gross) = Unit net price x (1 + VAT/100), 2 dp.

    The transaction-line discount is deliberately NOT applied. The discount is
    a property of this order line; the product master price is not, and
    baking it in would corrupt the master record for every future order.
    """
    raw = unit_net_price * (Decimal(1) + vat_percent / HUNDRED)
    return raw.quantize(config.CENTS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class DerivedItem:
    position: int
    sku: str | None
    vat_name: str
    vat_percent: Decimal
    product_gross_price: Decimal


@dataclass(frozen=True)
class Derived:
    is_paid: bool
    payment_method: str | None
    payment_code: str | None
    items: tuple[DerivedItem, ...]

    @property
    def unmapped_payment_method(self) -> bool:
        """A method was printed but is not one we know how to create."""
        return self.payment_method is not None and self.payment_code is None


def derive_item(item: LineItem) -> DerivedItem:
    return DerivedItem(
        position=item.position,
        sku=item.sku,
        vat_name=vat_name(item.vat_percent),
        vat_percent=item.vat_percent,
        product_gross_price=product_gross_price(item.unit_net_price, item.vat_percent),
    )


def derive(order: Order) -> Derived:
    return Derived(
        is_paid=is_paid(order.payment.paid_status),
        payment_method=order.payment.method,
        payment_code=payment_code(order.payment.method),
        items=tuple(derive_item(item) for item in order.items),
    )
