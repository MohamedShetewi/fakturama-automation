"""Stage 2 - the correctness gate.

Pure arithmetic. No API calls, no I/O, no globals: every function here is a
plain input -> output, which is what makes the whole risk surface of this
pipeline unit-testable without a key.

A vision model will occasionally misread a digit. A misread digit that reaches
an accounting system is worse than a failed run, so nothing is written until
the extracted arithmetic agrees with the totals the document itself prints.

Every value is a Decimal. Never float: binary floating point produces spurious
one-cent failures on precisely the sums being checked here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from . import config
from .schema import LineItem, Order

HUNDRED = Decimal(100)


def money(value: Decimal) -> Decimal:
    """Round to the cent, the way a printed invoice does."""
    return value.quantize(config.CENTS, rounding=ROUND_HALF_UP)


def line_net(item: LineItem, *, round_line: bool | None = None) -> Decimal:
    """qty x unit net x (1 - discount/100)   [spec 3.16]

    A null discount means the document showed none, which is arithmetically 0.
    """
    if round_line is None:
        round_line = config.ROUND_LINES_BEFORE_SUM
    discount = item.discount_percent if item.discount_percent is not None else Decimal(0)
    raw = item.quantity * item.unit_net_price * (Decimal(1) - discount / HUNDRED)
    return money(raw) if round_line else raw


def line_vat(item: LineItem, *, round_line: bool | None = None) -> Decimal:
    if round_line is None:
        round_line = config.ROUND_LINES_BEFORE_SUM
    raw = line_net(item, round_line=round_line) * item.vat_percent / HUNDRED
    return money(raw) if round_line else raw


@dataclass(frozen=True)
class Failure:
    """One thing that did not add up, located precisely enough to act on."""

    check: str
    location: str
    message: str
    expected: Decimal | None = None
    found: Decimal | None = None
    delta: Decimal | None = None

    def __str__(self) -> str:
        if self.expected is None:
            return f"[{self.check}] {self.location}: {self.message}"
        return (
            f"[{self.check}] {self.location}: {self.message} "
            f"(computed {self.expected}, document says {self.found}, off by {self.delta})"
        )


@dataclass(frozen=True)
class ReconciliationReport:
    failures: tuple[Failure, ...] = ()
    line_nets: tuple[Decimal, ...] = ()
    line_vats: tuple[Decimal, ...] = ()
    computed_net: Decimal = field(default_factory=lambda: Decimal("0.00"))
    computed_vat: Decimal = field(default_factory=lambda: Decimal("0.00"))
    computed_gross: Decimal = field(default_factory=lambda: Decimal("0.00"))

    @property
    def ok(self) -> bool:
        return not self.failures


def _compare(
    check: str,
    location: str,
    message: str,
    computed: Decimal,
    printed: Decimal,
) -> Failure | None:
    delta = computed - printed
    if abs(delta) <= config.TOLERANCE:
        return None
    return Failure(
        check=check,
        location=location,
        message=message,
        expected=computed,
        found=printed,
        delta=delta,
    )


def structural_failures(order: Order) -> list[Failure]:
    """Blockers that make the arithmetic checks meaningless or make the
    downstream UI automation impossible."""
    out: list[Failure] = []

    if not order.items:
        out.append(
            Failure("structural", "items", "the document produced no line items")
        )

    for i, item in enumerate(order.items):
        where = f"items[{i}]"
        # Spec 3.3 searches the product selector by exact SKU. Without one,
        # the automation cannot resolve or create the product at all.
        if not (item.sku or "").strip():
            out.append(
                Failure("structural", f"{where}.sku", "no SKU; product cannot be resolved")
            )
        if item.quantity <= 0:
            out.append(
                Failure(
                    "structural",
                    f"{where}.quantity",
                    f"quantity must be positive, got {item.quantity}",
                )
            )
        if item.vat_percent < 0:
            out.append(
                Failure(
                    "structural",
                    f"{where}.vat_percent",
                    f"VAT percentage must not be negative, got {item.vat_percent}",
                )
            )

    return out


def reconcile(order: Order) -> ReconciliationReport:
    """Check the extracted numbers against the document's own printed totals.

    Structural problems short-circuit: if there are no items, or an item has no
    quantity to multiply, the checksum failures that follow would be noise on
    top of the real problem.
    """
    structural = structural_failures(order)
    if structural:
        return ReconciliationReport(failures=tuple(structural))

    failures: list[Failure] = []

    nets: list[Decimal] = []
    vats: list[Decimal] = []
    for i, item in enumerate(order.items):
        net = line_net(item)
        nets.append(net)
        vats.append(line_vat(item))
        failure = _compare(
            "line_total",
            f"items[{i}].source_total",
            "qty x unit net x (1 - discount/100) does not match the printed line total",
            net,
            item.source_total,
        )
        if failure:
            failures.append(failure)

    computed_net = money(sum(nets, Decimal(0)))
    computed_vat = money(sum(vats, Decimal(0)))
    # Gross is checked against the document's *printed* net and VAT, so this
    # tests the document's internal consistency rather than re-deriving from
    # our own line reads and comparing our arithmetic to itself.
    computed_gross = money(order.totals.net_total + order.totals.vat_total)

    for failure in (
        _compare(
            "net_total",
            "totals.net_total",
            "line nets do not sum to the printed net total",
            computed_net,
            order.totals.net_total,
        ),
        _compare(
            "vat_total",
            "totals.vat_total",
            "per-line VAT does not sum to the printed VAT total",
            computed_vat,
            order.totals.vat_total,
        ),
        _compare(
            "gross_total",
            "totals.gross_total",
            "net + VAT does not match the printed gross total",
            computed_gross,
            order.totals.gross_total,
        ),
    ):
        if failure:
            failures.append(failure)

    return ReconciliationReport(
        failures=tuple(failures),
        line_nets=tuple(nets),
        line_vats=tuple(vats),
        computed_net=computed_net,
        computed_vat=computed_vat,
        computed_gross=computed_gross,
    )
