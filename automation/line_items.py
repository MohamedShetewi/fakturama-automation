"""Filling in each order line, and checking the document against the source.

The chooser adds a line at quantity 1 and no discount, because that is all it
knows - it was given a product, not an order row. Everything that makes the
line *this* order's line has to be typed in afterwards: how many, and at what
allowance.

The check at the end is the point of the whole thing. Fakturama computes its
own net, VAT and gross from the lines, and the extraction computed the same
three numbers from the image, through a completely separate path. When they
agree to the cent, the document really does say what the order said. When they
do not, something between the two is wrong and it does not matter which end -
the run stops.
"""

from __future__ import annotations

from decimal import Decimal

from . import actions, order_items, ui, vat_form
from .actions import parse_money
from .order_form import Result, Step, _layer_of
from .resolver import Scope, find_control
from .ui import UIError

CENT = Decimal("0.01")


def _decimal(value) -> Decimal:
    return Decimal(str(value))


def _plain(value: Decimal) -> str:
    """A number the grid will accept: no currency, no percent, no exponent."""
    return f"{value.normalize():f}"


def line_price(quantity: Decimal, unit_net: Decimal, discount_percent: Decimal) -> Decimal:
    """Spec 3.16: quantity x unit net price x (1 - discount / 100).

    Decimal throughout and rounded once, at the end. Rounding the discounted
    unit price first and multiplying after gives a different number on many
    ordinary orders, and it is the kind of difference nobody notices until a
    customer does.
    """
    return (quantity * unit_net * (Decimal(1) - discount_percent / Decimal(100))).quantize(CENT)


def apply_line(editor, result: Result, rows, item: dict,
               vat_rates: dict[str, Decimal]) -> bool:
    """Spec 3.13-3.16 for one line, every step verified on the row itself."""
    sku = item["sku"]
    pos = item["position"]

    row = order_items.index_of(rows, sku)
    if row is None:
        step = Step(f"3.13[{pos}]", f"locate the line for {sku!r}")
        step.detail = (
            f"the order does not hold exactly one line for {sku!r}; "
            "refusing to guess which one to edit"
        )
        result.steps.append(step)
        return False

    ok = True
    quantity = _decimal(item["quantity"])
    step = Step(f"3.13[{pos}]", f"set Qty. for {sku!r}")
    current = order_items.cell(rows[row], "quantity")
    if order_items.same_value(current, _plain(quantity)):
        step.ok = True
        step.detail = f"already {current!r}; untouched"
    else:
        written = order_items.set_cell(editor, row, "quantity", _plain(quantity), current)
        step.ok = written.ok
        step.detail = (f"{current!r} -> {written.read_back!r}" if written.ok
                       else written.detail)
    result.steps.append(step)
    ok &= step.ok

    ok &= _unit_price_and_vat(editor, result, row, item, vat_rates)

    # A missing discount is not a zero discount in the source - it is simply
    # absent - but on the line it means the same thing, and the line already
    # says 0. So leave it rather than writing a number the document did not
    # state.
    discount = item.get("discount_percent")
    step = Step(f"3.15[{pos}]", f"set Discount for {sku!r}")
    if discount is None:
        step.ok = True
        step.detail = "the source states no discount; leaving the line's 0 alone"
        result.steps.append(step)
        return ok and _confirm_line_price(editor, result, row, item)

    rows = ui.item_rows(editor).rows          # the quantity write moved the row's values
    stored = order_items.cell(rows[row], "discount") if row < len(rows) else ""
    # Everything here is in whole percents - the units the cell editor uses and
    # the source states. The grid's own signed fraction is converted on the way
    # in and on the way out, never compared raw.
    current = order_items.discount_as_percent(stored)
    wanted = _decimal(discount)
    if order_items.same_value(current, _plain(wanted)):
        step.ok = True
        step.detail = f"already {current}%; untouched"
    else:
        written = order_items.set_cell(
            editor, row, "discount", _plain(wanted), current,
            as_written=order_items.discount_as_percent)
        step.ok = written.ok
        step.detail = (f"{current}% -> {written.read_back}%" if written.ok
                       else written.detail)
    result.steps.append(step)
    return ok and step.ok and _confirm_line_price(editor, result, row, item)


def _unit_price_and_vat(editor, result: Result, row: int, item: dict,
                        vat_rates: dict[str, Decimal]) -> bool:
    """Spec 3.14: U.Price is the extracted net price, VAT is the extracted rate.

    The line inherits both from the product record, so they are usually right
    already - which is exactly why they are worth checking. A product that
    existed before this run carries whatever price it was created with, and
    nothing else in the flow would ever notice it disagreeing with the
    document being booked.
    """
    sku, pos = item["sku"], item["position"]
    wanted = _decimal(item["unit_net_price"])

    rows = ui.item_rows(editor).rows
    step = Step(f"3.14[{pos}]", f"U.Price and VAT for {sku!r}")
    if row >= len(rows):
        step.detail = "the line disappeared between steps"
        result.steps.append(step)
        return False

    current = order_items.cell(rows[row], "unit_price")
    if order_items.same_value(current, _plain(wanted)):
        priced, detail = True, f"U.Price already {current!r}"
    else:
        written = order_items.set_cell(editor, row, "unit_price", _plain(wanted), current)
        priced = written.ok
        detail = (f"U.Price {current!r} -> {written.read_back!r}" if written.ok
                  else f"U.Price: {written.detail}")

    # VAT is confirmed, never rewritten. The line's rate comes from the product
    # record, and disagreeing with it means the product itself is booked
    # against the wrong tax - which a line-level override would paper over.
    rows = ui.item_rows(editor).rows
    name = order_items.to_item(rows[row]).vat if row < len(rows) else None
    want_percent = _decimal(item["vat_percent"])
    if name is None:
        vat_ok, vat_detail = False, "the line's VAT could not be read"
    elif name not in vat_rates:
        vat_ok, vat_detail = False, f"the line's VAT {name!r} is not in the VATs list"
    elif vat_rates[name] != want_percent:
        vat_ok = False
        vat_detail = (f"the line uses {name!r} at {vat_rates[name]}%, "
                      f"but the source says {want_percent}%")
    else:
        vat_ok, vat_detail = True, f"VAT {name!r} is {want_percent}%"

    step.ok = priced and vat_ok
    step.detail = f"{detail}; {vat_detail}"
    result.steps.append(step)
    return step.ok


def _confirm_line_price(editor, result: Result, row: int, item: dict) -> bool:
    """Spec 3.16: the line's Price is quantity x unit net x (1 - discount/100).

    Computed here and compared with what Fakturama shows, rather than trusting
    either alone. The two are worked out from the same three inputs by
    different code, so agreement is evidence; a mismatch means one of the three
    did not land the way its own step reported.
    """
    sku, pos = item["sku"], item["position"]
    step = Step(f"3.16[{pos}]", f"line Price for {sku!r}")

    rows = ui.item_rows(editor).rows
    if row >= len(rows):
        step.detail = "the line disappeared between steps"
        result.steps.append(step)
        return False

    line = order_items.to_item(rows[row])
    expected = line_price(_decimal(item["quantity"]),
                          _decimal(item["unit_net_price"]),
                          _decimal(item.get("discount_percent") or 0))
    if line.total is None:
        step.detail = "the line's Price could not be read"
    elif line.total.quantize(CENT) != expected:
        step.detail = (f"expected {expected} from {item['quantity']} x "
                       f"{item['unit_net_price']} less {item.get('discount_percent') or 0}%, "
                       f"but the line shows {line.total}")
    else:
        step.ok = True
        step.detail = (f"{expected} = {item['quantity']} x {item['unit_net_price']}"
                       + (f" less {item['discount_percent']}%"
                          if item.get("discount_percent") else ""))
    result.steps.append(step)
    return step.ok


def read_totals(win, editor) -> dict[str, Decimal | None]:
    """Fakturama's own net, VAT and gross for the document."""
    scope = Scope(win, editor)
    out = {}
    for name, key in (("net", "order.total_net"), ("vat", "order.vat_amount"),
                      ("gross", "order.total")):
        try:
            out[name] = parse_money(actions.read_value(find_control(key, scope)))
        except UIError:
            out[name] = None
    return out


def verify_totals(win, editor, result: Result, expected: dict) -> None:
    """Compare the document's totals with the ones reconciled from the image.

    Beyond the numbered steps, and worth the extra read: 3.16 checks each line
    against its own three inputs, but nothing else checks that the lines add up
    to the order the image showed. This is the only place the two halves of the
    project meet.
    """
    step = Step("totals", "the document totals match the source")
    step.layer = _layer_of(find_control("order.total_net", Scope(win, editor)))
    got = read_totals(win, editor)

    missing = [k for k, v in got.items() if v is None]
    if missing:
        step.detail = f"could not read {missing} from the order"
        result.steps.append(step)
        return

    want = {k: _decimal(expected[f"{k}_total"]).quantize(CENT) for k in ("net", "vat", "gross")}
    have = {k: v.quantize(CENT) for k, v in got.items()}
    differences = {k: (want[k], have[k]) for k in want if want[k] != have[k]}

    if not differences:
        step.ok = True
        step.detail = (f"net {have['net']}, VAT {have['vat']}, gross {have['gross']} - "
                       "the same figures the extraction reconciled against the image")
    else:
        step.detail = "; ".join(
            f"{k}: source says {w}, the order says {h}" for k, (w, h) in differences.items()
        )
    result.steps.append(step)


def save_order(win, editor, result: Result) -> None:
    """Write the document, once every step above has verified.

    Guarded by result.ok rather than attempted regardless: this is the only
    step that turns the run into a record, and a half-filled order saved is
    worse than no order at all - it looks finished.
    """
    # Deliberately unnumbered: 3.11 is the *product* save, and the spec does
    # not say when the order itself is written.
    step = Step("save", "save the order")
    if not result.ok:
        step.detail = "skipped: an earlier step did not verify, so nothing was saved"
        result.steps.append(step)
        return

    actions.save_editor(editor)
    try:
        actions.wait_ready(lambda: not ui.editor_is_dirty(win, "New Order"),
                           "the order editor to stop being dirty", timeout=15.0)
        step.ok = True
        step.detail = "saved; the editor is no longer dirty"
    except UIError as exc:
        step.detail = f"saved with Ctrl+S but the editor is still dirty ({exc})"
    result.steps.append(step)


def apply_lines(order: dict, reconciliation: dict, *, save: bool = False) -> Result:
    """Fill every line in, then check the document against the source."""
    result = Result()
    win = ui.window()
    ui.activate(win)

    with ui.Clipboard():
        # Read the rate table first: 3.14 has to confirm a *percentage*, and
        # the order's grid only ever names the rate. Done once, before the
        # order editor is brought forward, because it navigates away from it.
        try:
            vat_rates = vat_form.vat_percentages(win)
        except UIError as exc:
            step = Step("3.14", "read the VAT rates")
            step.detail = f"{exc}; without them a line's VAT cannot be confirmed"
            result.steps.append(step)
            return result

        editor = ui.activate_editor(win, "New Order", ui.order_editor)
        if editor is None:
            step = Step("3.13", "find the order editor")
            step.detail = "the New Order editor is not open"
            result.steps.append(step)
            return result

        got = ui.item_rows(editor)
        if got.how != "read":
            step = Step("3.13", "read the order's lines")
            step.detail = f"the Items grid could not be read ({got.how})"
            result.steps.append(step)
            return result

        # 3.17: every source item, in order.
        for item in sorted(order["items"], key=lambda i: i["position"]):
            if not apply_line(editor, result, ui.item_rows(editor).rows, item, vat_rates):
                return result

        verify_totals(win, editor, result, reconciliation["computed"])
        if save:
            save_order(win, editor, result)
    return result
