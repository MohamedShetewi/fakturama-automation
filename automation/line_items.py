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

from . import actions, order_items, ui
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


def apply_line(editor, result: Result, rows, item: dict) -> bool:
    """Set one line's quantity and discount, each verified on the row."""
    sku = item["sku"]
    pos = item["position"]

    row = order_items.index_of(rows, sku)
    if row is None:
        step = Step(f"3.8[{pos}]", f"locate the line for {sku!r}")
        step.detail = (
            f"the order does not hold exactly one line for {sku!r}; "
            "refusing to guess which one to edit"
        )
        result.steps.append(step)
        return False

    ok = True
    quantity = _decimal(item["quantity"])
    step = Step(f"3.8[{pos}]", f"set quantity for {sku!r}")
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

    # A missing discount is not a zero discount in the source - it is simply
    # absent - but on the line it means the same thing, and the line already
    # says 0. So leave it rather than writing a number the document did not
    # state.
    discount = item.get("discount_percent")
    step = Step(f"3.9[{pos}]", f"set discount for {sku!r}")
    if discount is None:
        step.ok = True
        step.detail = "the source states no discount; leaving the line's 0 alone"
        result.steps.append(step)
        return ok

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
    return ok and step.ok


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
    """Compare the document's totals with the ones reconciled from the image."""
    step = Step("3.10", "the document totals match the source")
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
    step = Step("3.11", "save the order")
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
    editor = ui.activate_editor(win, "New Order", ui.order_editor)
    if editor is None:
        step = Step("3.8", "find the order editor")
        step.detail = "the New Order editor is not open"
        result.steps.append(step)
        return result

    with ui.Clipboard():
        got = ui.item_rows(editor)
        if got.how != "read":
            step = Step("3.8", "read the order's lines")
            step.detail = f"the Items grid could not be read ({got.how})"
            result.steps.append(step)
            return result

        for item in sorted(order["items"], key=lambda i: i["position"]):
            if not apply_line(editor, result, ui.item_rows(editor).rows, item):
                return result

        verify_totals(win, editor, result, reconciliation["computed"])
        if save:
            save_order(win, editor, result)
    return result
