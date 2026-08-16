"""Spec 4.1-4.7: check the order over, write it, and start the Invoice from it.

Everything before 4.4 is confirmation rather than entry. That is the point of
the stage: each earlier step verified its own field as it went, but nothing has
yet asked whether the *document as a whole* says what the image said. So the
address, the lines, the order-level values and the three totals are all read
back and compared against the extraction one last time, and the save only
happens if they agree.

4.6 uses the follow-up Invoice button inside the order rather than the toolbar
one, because only the follow-up carries the Order relationship forward. The two
are easy to confuse on screen and impossible to confuse here: the toolbar's is
named 'Create: New Invoice', the follow-up's is named 'Invoice'.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime
from decimal import Decimal

from . import actions, config, line_items, order_items, ui
from .actions import parse_money
from .order_form import Result, Step, _layer_of
from .resolver import Scope, find_control
from .ui import UIError

CENT = Decimal("0.01")

# 'Thu Aug 04 00:00:00 AST 2011' - a Java Date, whose zone abbreviation no
# strptime on this machine will parse. Only the day matters here.
_JAVA_DATE = re.compile(r"^\w{3}\s+(?P<month>\w{3})\s+(?P<day>\d{1,2})\s+.*\s(?P<year>\d{4})$")


def parse_document_date(text: str) -> date | None:
    """The day out of the Documents list's Java-style timestamp."""
    match = _JAVA_DATE.match((text or "").strip())
    if not match:
        return None
    try:
        return datetime.strptime(
            f"{match['month']} {match['day']} {match['year']}", "%b %d %Y").date()
    except ValueError:
        return None


def _address_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def expected_address_parts(debtor: dict) -> list[str]:
    """The pieces of the extracted billing address that must appear on the order.

    Compared as parts rather than as a formatted block: Fakturama composes the
    address its own way - it writes 'DE-10117 Berlin' where the source has a
    country, a zip and a city as separate fields - and reformatting the source
    to match would be asserting the formatter against itself.
    """
    billing = debtor.get("billing_address") or {}
    parts = [debtor.get("company"), debtor.get("first_name"), debtor.get("last_name"),
             billing.get("street"), billing.get("zip"), billing.get("city")]
    return [str(p).strip() for p in parts if p]


def step_4_1_confirm(win, editor, result: Result, order: dict) -> bool:
    """4.1 The Debtor address and every product line match the extraction."""
    step = Step("4.1a", "the order's address matches the extracted debtor")
    resolved = find_control("order.address_text", Scope(win, editor))
    step.layer = _layer_of(resolved)
    shown = actions.read_value(resolved)
    wanted = expected_address_parts(order["debtor"])
    missing = [p for p in wanted if p.casefold() not in shown.casefold()]
    if missing:
        step.detail = f"the order's address does not mention {missing}; it shows {_address_lines(shown)}"
    else:
        step.ok = True
        step.detail = f"all of {wanted} appear in {_address_lines(shown)}"
    result.steps.append(step)

    delivery = (order["debtor"].get("delivery_address") or {}).get("street")
    billing = (order["debtor"].get("billing_address") or {}).get("street")
    if delivery and delivery != billing:
        note = Step("4.1b", "the source's separate delivery address")
        note.ok = True
        note.verified = False
        note.detail = (
            f"the image ships to {delivery!r}, which differs from the billing "
            f"address {billing!r}. The order carries one address and nothing in "
            "the spec places the delivery one, so it is not recorded anywhere"
        )
        result.steps.append(note)

    lines = Step("4.1c", "every product line matches the extraction")
    got = ui.item_rows(editor)
    if got.how != "read":
        lines.detail = f"the Items grid could not be read ({got.how})"
        result.steps.append(lines)
        return False

    items = [order_items.to_item(r) for r in got.rows]
    problems = []
    for source in sorted(order["items"], key=lambda i: i["position"]):
        matches = order_items.find(items, source["sku"])
        if len(matches) != 1:
            problems.append(f"{source['sku']}: {len(matches)} lines")
            continue
        line = matches[0]
        want_total = line_items.line_price(
            Decimal(str(source["quantity"])), Decimal(str(source["unit_net_price"])),
            Decimal(str(source.get("discount_percent") or 0)))
        if line.quantity is None or line.quantity != Decimal(str(source["quantity"])):
            problems.append(f"{source['sku']}: quantity {line.quantity} != {source['quantity']}")
        if line.unit_price is None or line.unit_price != Decimal(str(source["unit_net_price"])):
            problems.append(f"{source['sku']}: U.Price {line.unit_price} != {source['unit_net_price']}")
        if line.total is None or line.total.quantize(CENT) != want_total:
            problems.append(f"{source['sku']}: Price {line.total} != {want_total}")

    extra = [i.sku for i in items
             if not any(i.sku.casefold() == s["sku"].casefold() for s in order["items"])]
    if extra:
        problems.append(f"lines the source does not have: {extra}")

    if problems:
        lines.detail = "; ".join(problems)
    else:
        lines.ok = True
        lines.detail = f"{len(items)} line(s), each matching the source"
    result.steps.append(lines)
    return result.ok


def step_4_2_order_level(win, editor, result: Result) -> None:
    """4.2 Discount stays 0%, Shipping stays free - the source supplies neither."""
    scope = Scope(win, editor)
    step = Step("4.2", "order-level Discount and Shipping are untouched")
    findings, ok = [], True

    for key, want in (("order.discount", config.ORDER_DISCOUNT_NONE),
                      ("order.shipping", config.SHIPPING_FREE)):
        try:
            got = actions.read_value(find_control(key, scope)).strip()
        except UIError as exc:
            findings.append(f"{key}: {exc}")
            ok = False
            continue
        findings.append(f"{key.split('.')[1]}={got!r}")
        if got != want:
            ok = False
            findings[-1] += f" (expected {want!r})"

    try:
        amount = parse_money(actions.read_value(find_control("order.shipping_amount", scope)))
        findings.append(f"shipping amount={amount}")
        if amount is None or amount != Decimal(0):
            ok = False
    except UIError as exc:
        findings.append(f"shipping amount: {exc}")
        ok = False

    step.ok = ok
    step.detail = ("; ".join(findings) +
                   "; the extraction carries no order-level discount or shipping, "
                   "so these are confirmed rather than set")
    result.steps.append(step)


def step_4_4_save(win, editor, result: Result, title: str) -> None:
    """4.4 Click the toolbar Save control once.

    The toolbar control is what the spec names, so it is what gets clicked. It
    has been measured failing to save when several editors are dirty - it acts
    on whatever Eclipse considers active - so if the editor is still dirty
    afterwards this falls back to Ctrl+S, which names its subject, and says
    which one actually wrote the document.
    """
    step = Step("4.4", "save the order")
    if not result.ok:
        step.detail = "skipped: an earlier check did not pass, so nothing was saved"
        result.steps.append(step)
        return

    def saved() -> bool:
        return not ui.editor_is_dirty(win, title)

    if saved():
        # Re-running the stage against an order that is already written. Saying
        # so beats clicking Save and reporting a success that proves nothing.
        step.ok = True
        step.detail = "already saved; nothing to write"
        result.steps.append(step)
        return

    actions.click("toolbar.save", Scope(win))
    try:
        actions.wait_ready(saved, "the order editor to stop being dirty", timeout=8.0)
        step.ok = True
        step.detail = "saved with the toolbar Save control"
        result.steps.append(step)
        return
    except UIError:
        pass

    actions.save_editor(editor)
    try:
        actions.wait_ready(saved, "the order editor to stop being dirty", timeout=12.0)
        step.ok = True
        step.detail = ("the toolbar Save left the editor dirty, so it was saved with "
                       "Ctrl+S, which targets this editor rather than the active one")
    except UIError as exc:
        step.detail = f"neither the toolbar Save nor Ctrl+S wrote the order ({exc})"
    result.steps.append(step)


def documents_list(win):
    panes = ui.find_all(win, lambda c: c.ControlTypeName == "PaneControl"
                        and c.Name == "Documents")
    return max(panes, key=lambda p: p.BoundingRectangle.height()) if panes else None


def step_4_5_confirm_saved(win, result: Result, number: str, when: date,
                           reference: str, total: Decimal) -> None:
    """4.5 Exactly one Order row in Data > Documents, saying what it should."""
    step = Step("4.5", "the saved Order appears in Data > Documents")
    step.layer = _layer_of(find_control("nav.documents", Scope(win)))
    actions.click("nav.documents", Scope(win))
    try:
        actions.wait_ready(lambda: documents_list(win), "the Documents list",
                           timeout=config.EDITOR_TIMEOUT)
    except UIError as exc:
        step.detail = str(exc)
        result.steps.append(step)
        return

    read = ui.grid_read(documents_list(win))
    if not read.trustworthy:
        step.detail = f"could not read the Documents list ({read.how})"
        result.steps.append(step)
        return

    def col(row, name):
        i = config.DOCUMENT_COL[name]
        return (row[i] if len(row) > i else "").strip()

    rows = [r for r in read.rows if col(r, "number") == number]
    if len(rows) != 1:
        step.detail = (f"{len(rows)} rows numbered {number!r} among "
                       f"{[col(r, 'number') for r in read.rows]}")
        result.steps.append(step)
        return

    row = rows[0]
    problems = []
    if col(row, "type") != config.DOCUMENT_TYPE_ORDER:
        problems.append(f"type is {col(row, 'type')!r}, not an Order")
    got_date = parse_document_date(col(row, "date"))
    if got_date != when:
        problems.append(f"date is {col(row, 'date')!r}, expected {when}")
    if col(row, "reference") != reference:
        problems.append(f"Cust.Ref. is {col(row, 'reference')!r}, expected {reference!r}")
    if col(row, "state") != config.ORDER_STATE_OPEN:
        problems.append(f"state is {col(row, 'state')!r}, expected {config.ORDER_STATE_OPEN!r}")
    got_total = parse_money(col(row, "total"))
    if got_total is None or got_total.quantize(CENT) != total.quantize(CENT):
        problems.append(f"total is {col(row, 'total')!r}, expected {total}")

    if problems:
        step.detail = "; ".join(problems)
    else:
        step.ok = True
        step.detail = (f"one Order {number!r} dated {got_date}, ref {reference!r}, "
                       f"{config.ORDER_STATE_OPEN}, total {got_total.quantize(CENT)}")
    result.steps.append(step)


def invoice_editor(win):
    panes = ui.find_all(
        win, lambda c: c.ControlTypeName == "PaneControl"
        and (c.Name or "").lstrip("*") == "New Invoice")
    if not panes:
        return None
    return max(panes, key=lambda p: p.BoundingRectangle.width() * p.BoundingRectangle.height())


def step_4_6_followup_invoice(win, result: Result, number: str):
    """4.6-4.7 Start the Invoice from the order, and wait for its editor.

    Looked up by the order's number, not by 'New Order': saving renamed the
    editor to PO000001, and the follow-up button lives inside *that* editor.
    """
    step = Step("4.6", "create the follow-up Invoice from the Order")
    editor = ui.activate_editor(win, number, lambda w: ui.editor_named(w, number))
    if editor is None:
        step.detail = f"the saved Order editor {number!r} is not available"
        result.steps.append(step)
        return None

    resolved = find_control("order.followup_invoice", Scope(win, editor))
    step.layer = _layer_of(resolved)
    actions.click("order.followup_invoice", Scope(win, editor))
    step.ok = True
    step.detail = "used the follow-up button, not the toolbar's 'Create: New Invoice'"
    result.steps.append(step)

    wait = Step("4.7", "the linked New Invoice editor opens")
    try:
        pane = actions.wait_ready(lambda: invoice_editor(win), "the New Invoice editor",
                                  timeout=config.EDITOR_TIMEOUT)
        wait.ok = True
        wait.detail = "open and ready for the final stage"
    except UIError as exc:
        wait.detail = str(exc)
        pane = None
    result.steps.append(wait)
    return pane


def find_order_editor(win, title: str | None):
    """The order editor, whether or not it has been saved.

    An unsaved order is 'New Order'; the moment it is written it becomes its
    own number. Re-running this stage therefore has to look for both, and the
    caller can name the saved one when it knows it.
    """
    if title:
        return ui.activate_editor(win, title, lambda w: ui.editor_named(w, title)), title
    editor = ui.activate_editor(win, "New Order", ui.order_editor)
    return editor, "New Order"


def complete_order(doc: dict, *, follow_up: bool = True, order_title: str = None) -> Result:
    """Run 4.1-4.7 against the Order, saved or not."""
    order, reconciliation = doc["order"], doc["reconciliation"]
    result = Result()
    win = ui.window()
    ui.activate(win)

    with ui.Clipboard():
        editor, title = find_order_editor(win, order_title)
        if editor is None:
            step = Step("4.1", "find the order editor")
            step.detail = f"no order editor titled {order_title or 'New Order'!r} is open"
            result.steps.append(step)
            return result

        number = actions.read_value(find_control("order.number", Scope(win, editor))).strip()
        reference = actions.read_value(find_control("order.cust_ref", Scope(win, editor))).strip()
        # Stage 5 needs this and cannot work it out for itself: the number is
        # Fakturama's to assign, and after saving it is also the editor's title.
        result.context["order_number"] = number

        step_4_1_confirm(win, editor, result, order)
        step_4_2_order_level(win, editor, result)
        line_items.verify_totals(win, editor, result, reconciliation["computed"])
        step_4_4_save(win, editor, result, title)

        if not result.ok:
            return result

        when = datetime.strptime(order["order_date"], "%Y-%m-%d").date()
        total = Decimal(str(reconciliation["computed"]["gross_total"]))
        step_4_5_confirm_saved(win, result, number, when, reference, total)

        if result.ok and follow_up:
            step_4_6_followup_invoice(win, result, number)
    return result
