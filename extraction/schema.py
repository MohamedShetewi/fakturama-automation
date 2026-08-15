"""The shape of an extracted order.

Three jobs:
  1. Generates the JSON Schema handed to the model as ``text_format``, so the
     response comes back with fixed fields and fixed types.
  2. Gives ``reconcile.py`` typed objects, so a field typo is an error at
     import rather than a ``None`` inside a subtraction.
  3. Defines the output file format - the contract with the UIA half.

Fields follow spec 1.2.

Two constraints from OpenAI strict structured outputs shape this file:

  * Every property must be *required* and every object must set
    ``additionalProperties: false``. So no field may carry a default.
  * Genuinely-absent data is therefore modelled as **nullable**, not optional.
    That is the better model anyway: "the document does not state this" becomes
    an explicit ``null`` instead of an absence we could not distinguish from an
    extraction miss. Spec 2.6 depends on this - salutation is left as ``---``
    only when the source supplies none.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, WithJsonSchema

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _to_decimal(v: Any) -> Any:
    """Accept whatever JSON gave us and land on an exact Decimal.

    ``json.loads(..., parse_float=Decimal)`` in extract.py means the common
    path is Decimal already, so no binary float ever touches the arithmetic.
    The float branch is a fallback for callers that parsed normally; going via
    ``str`` keeps 19.99 as Decimal("19.99") rather than the binary expansion.
    """
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool):  # bool is an int subclass; reject it explicitly
        raise ValueError("expected a number, got a boolean")
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, float):
        return Decimal(str(v))
    if isinstance(v, str):
        s = v.strip()
        if not s:
            raise ValueError("expected a number, got an empty string")
        try:
            return Decimal(s)
        except InvalidOperation:
            raise ValueError(f"expected a number, got {v!r}") from None
    return v


def _check_iso_date(v: Any) -> Any:
    """Dates must arrive already normalized; the prompt asks for ISO-8601.

    Catching a non-ISO date here turns an ambiguous 01/02/2026 into a retry
    instead of a silently wrong booking date.
    """
    if v is None:
        return v
    if isinstance(v, str) and _ISO_DATE.match(v.strip()):
        return v.strip()
    raise ValueError(f"expected an ISO-8601 date (YYYY-MM-DD), got {v!r}")


# Decimal renders as an anyOf(number, string) in Pydantic's JSON schema, which
# is noisier than strict mode needs. Pin it to a plain number.
Number = Annotated[Decimal, BeforeValidator(_to_decimal), WithJsonSchema({"type": "number"})]
DateStr = Annotated[str, BeforeValidator(_check_iso_date)]


class Strict(BaseModel):
    """extra='forbid' is what emits ``additionalProperties: false``."""

    model_config = ConfigDict(extra="forbid")


class Address(Strict):
    street: str | None
    zip: str | None
    city: str | None
    country: str | None
    email: str | None
    phone: str | None
    # Spec 2.7: fill these only when the source supplies them.
    additional_name: str | None
    address_specification: str | None
    district: str | None


class Debtor(Strict):
    company: str | None
    first_name: str | None
    last_name: str | None
    salutation: str | None
    alias: str | None
    # Whatever the document prints as a customer identifier. This is NOT
    # Fakturama's Customer ID - spec 2.6 says leave that auto-proposed. It goes
    # in the order's Cust.Ref. field (spec 1.6) if it is the external reference.
    printed_customer_id: str | None
    billing_address: Address
    delivery_address: Address | None


class Payment(Strict):
    # Raw text as printed. Mapping to a Fakturama payment code happens in
    # derive.py, deterministically, so an unrecognized method fails loudly
    # rather than being guessed at by the model.
    method: str | None
    paid_status: str | None
    payment_date: DateStr | None


class LineItem(Strict):
    position: int
    sku: str | None
    description: str | None
    quantity: Number
    unit_net_price: Number
    vat_percent: Number
    discount_percent: Number | None
    # The line total as printed on the document. Never computed - this is the
    # value reconcile.py checks our own arithmetic against.
    source_total: Number


class Totals(Strict):
    net_total: Number
    vat_total: Number
    gross_total: Number


class Order(Strict):
    order_date: DateStr | None
    external_reference: str | None
    currency: str | None
    # Which in-document signal fixed the date/number format. Provenance, so a
    # misread locale is debuggable rather than mysterious.
    locale_evidence: str | None
    debtor: Debtor
    payment: Payment
    items: list[LineItem]
    totals: Totals
