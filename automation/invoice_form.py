"""Spec 5.1-5.7: check the linked Invoice over, record payment, save, verify.

Almost nothing here is data entry. The follow-up copied the Order's values
across, so 5.1's job is to confirm it really did rather than to re-type them -
and confirming is the more useful thing, because a follow-up that quietly
dropped a field would otherwise produce an invoice that looks finished.

The one place values are written is 5.3, and only when the source says the
order was paid. When it does not, nothing is entered: a payment date invented
to fill a box is a claim that money changed hands.

5.7 is a boundary, not a step. The invoice editor offers Delivery, Invoice
Correction and Dunning buttons in the same follow-up group that produced it,
and none of them is this flow's to press.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from . import actions, config, order_items, ui
from .actions import parse_money
from .order_complete import documents_list, expected_address_parts, parse_document_date
from .order_form import Result, Step, _layer_of
from .resolver import Scope, find_control
from .ui import UIError

CENT = Decimal("0.01")
INVOICE_EDITOR_TITLE = "New Invoice"


def invoice_editor(win, title: str = INVOICE_EDITOR_TITLE):
    return ui.editor_named(win, title)


def _read(scope: Scope, key: str) -> str:
    return actions.read_value(find_control(key, scope)).strip()


def step_5_1_confirm_copied(win, editor, result: Result, order: dict,
                            reconciliation: dict) -> None:
    """5.1 Everything the follow-up should have carried over, did."""
    scope = Scope(win, invoice=editor)

    proposed = Step("5.1a", "the proposed No., Date and Service date are untouched")
    try:
        values = {k: _read(scope, f"invoice.{k}") for k in ("number", "date", "service_date")}
        proposed.ok = all(values.values())
        proposed.detail = ", ".join(f"{k}={v!r}" for k, v in values.items()) + " - left as proposed"
    except UIError as exc:
        proposed.detail = str(exc)
    result.steps.append(proposed)

    copied = Step("5.1b", "Cust.Ref., Order Date and VAT mode came from the Order")
    checks, ok = [], True
    try:
        ref = _read(scope, "invoice.cust_ref")
        want_ref = order["external_reference"]
        checks.append(f"Cust.Ref.={ref!r}")
        ok &= ref == want_ref
        if ref != want_ref:
            checks[-1] += f" (source says {want_ref!r})"

        shown = _read(scope, "invoice.order_date")
        want_date = datetime.strptime(order["order_date"], "%Y-%m-%d").date()
        got_date = actions.parse_ui_date(shown)
        checks.append(f"Order Date={shown!r}")
        ok &= got_date == want_date
        if got_date != want_date:
            checks[-1] += f" (source says {want_date})"

        mode = _read(scope, "invoice.vat_mode")
        checks.append(f"VAT mode={mode!r}")
        ok &= mode == config.VAT_WITH
    except UIError as exc:
        checks.append(str(exc))
        ok = False
    copied.ok = ok
    copied.detail = "; ".join(checks)
    result.steps.append(copied)

    address = Step("5.1c", "the Invoice address came from the Order")
    try:
        shown = _read(scope, "invoice.address_text")
        wanted = expected_address_parts(order["debtor"])
        missing = [p for p in wanted if p.casefold() not in shown.casefold()]
        address.ok = not missing
        address.detail = (f"all of {wanted} appear" if not missing
                          else f"the invoice address does not mention {missing}")
    except UIError as exc:
        address.detail = str(exc)
    result.steps.append(address)

    delivery = Step("5.1d", "the Delivery address")
    delivery.ok = True
    delivery.verified = False
    delivery.detail = (
        "the invoice carries a single address tab, 'Invoice address'; there is "
        "no separate Delivery address field to confirm, and the source's "
        "different shipping address is still recorded nowhere"
    )
    result.steps.append(delivery)

    lines = Step("5.1e", "the item lines came from the Order")
    got = ui.item_rows(editor)
    if got.how != "read":
        lines.detail = f"the Items grid could not be read ({got.how})"
    else:
        items = [order_items.to_item(r) for r in got.rows]
        problems = []
        for source in sorted(order["items"], key=lambda i: i["position"]):
            matches = order_items.find(items, source["sku"])
            if len(matches) != 1:
                problems.append(f"{source['sku']}: {len(matches)} lines")
            elif matches[0].quantity != Decimal(str(source["quantity"])):
                problems.append(f"{source['sku']}: quantity {matches[0].quantity}")
        lines.ok = not problems and len(items) == len(order["items"])
        lines.detail = ("; ".join(problems) if problems
                        else f"{len(items)} line(s), matching the Order")
    result.steps.append(lines)

    totals = Step("5.1f", "the totals came from the Order")
    want = {k: Decimal(str(reconciliation["computed"][f"{k}_total"])).quantize(CENT)
            for k in ("net", "vat", "gross")}
    try:
        have = {
            "net": parse_money(_read(scope, "invoice.total_net")),
            "vat": parse_money(_read(scope, "invoice.vat_amount")),
            "gross": parse_money(_read(scope, "invoice.total")),
        }
    except UIError as exc:
        totals.detail = str(exc)
        result.steps.append(totals)
        return
    differences = {k: (want[k], have[k]) for k in want
                   if have[k] is None or have[k].quantize(CENT) != want[k]}
    if differences:
        totals.detail = "; ".join(f"{k}: source {w}, invoice {h}"
                                  for k, (w, h) in differences.items())
    else:
        totals.ok = True
        totals.detail = (f"net {have['net']}, VAT {have['vat']}, gross {have['gross']} - "
                         "the same figures as the Order and the image")
    result.steps.append(totals)


def step_5_2_payment_method(win, editor, result: Result, wanted: str) -> bool:
    """5.2 The payment method is the one the image named, or the run stops."""
    scope = Scope(win, invoice=editor)
    step = Step("5.2", f"the Invoice's payment method is {wanted!r}")
    resolved = find_control("invoice.payment_method", scope)
    step.layer = _layer_of(resolved)
    current = actions.read_value(resolved).strip()

    if current == wanted:
        step.ok = True
        step.detail = f"already {current!r}; untouched"
        result.steps.append(step)
        return True

    available = actions.combo_options(resolved)
    if wanted not in available:
        # 5.2's halt. Picking the nearest name would book the order against a
        # payment method the document never mentioned.
        step.detail = (f"{wanted!r} is not offered - the list has {available}; "
                       "stopping for manual review")
        result.steps.append(step)
        return False

    written = actions.set_value("invoice.payment_method", wanted, scope)
    step.ok = written.ok
    step.detail = (f"{current!r} -> {written.read_back!r}" if written.ok else written.detail)
    result.steps.append(step)
    return step.ok


def step_5_3_paid(win, editor, result: Result, payment: dict,
                  total: Decimal) -> bool:
    """5.3 Record payment, but only what the source actually states."""
    scope = Scope(win, invoice=editor)
    is_paid = (payment.get("paid_status") or "").upper() == "PAID"

    mark = Step("5.3a", "the paid box matches the source")
    if not is_paid:
        # Not an omission - a refusal. An unpaid invoice marked paid, with a
        # date and an amount that were never stated, is a fabricated record.
        state = actions.checkbox_state(find_control("invoice.paid", scope))
        mark.ok = state is False
        mark.detail = (f"the source says {payment.get('paid_status')!r}; the box is "
                       f"{'clear' if state is False else state!r}, and no date or "
                       "value has been invented")
        result.steps.append(mark)
        return mark.ok

    written = actions.set_checkbox("invoice.paid", True, scope)
    mark.ok = written.ok
    mark.detail = "ticked" if written.ok else written.detail
    result.steps.append(mark)
    if not written.ok:
        return False

    when = Step("5.3b", "the payment date is the extracted one")
    wanted = datetime.strptime(payment["payment_date"], "%Y-%m-%d").date()
    resolved = find_control("invoice.payment_date", scope)
    when.layer = _layer_of(resolved)
    current = actions.parse_ui_date(actions.read_value(resolved))
    if current == wanted:
        when.ok = True
        when.detail = f"already {wanted}; untouched"
    else:
        got = actions.set_value("invoice.payment_date", wanted, scope)
        when.ok = got.ok
        when.detail = (f"{current} -> {got.read_back!r}" if got.ok else got.detail)
    result.steps.append(when)

    value = Step("5.3c", "the paid Value is the full Invoice Total")
    resolved = find_control("invoice.paid_value", scope)
    value.layer = _layer_of(resolved)
    current = parse_money(actions.read_value(resolved))
    if current is not None and current.quantize(CENT) == total.quantize(CENT):
        value.ok = True
        value.detail = f"already {current}; untouched"
    else:
        got = actions.set_value("invoice.paid_value", total, scope)
        value.ok = got.ok
        value.detail = (f"{current} -> {got.read_back!r}" if got.ok else got.detail)
    result.steps.append(value)
    return when.ok and value.ok


def step_5_4_save(win, editor, result: Result, title: str) -> None:
    """5.4 Click the toolbar Save control once."""
    step = Step("5.4", "save the invoice")
    if not result.ok:
        step.detail = "skipped: an earlier step did not verify, so nothing was saved"
        result.steps.append(step)
        return

    def saved() -> bool:
        return not ui.editor_is_dirty(win, title)

    if saved():
        step.ok = True
        step.detail = "already saved; nothing to write"
        result.steps.append(step)
        return

    actions.click("toolbar.save", Scope(win))
    try:
        actions.wait_ready(saved, "the invoice editor to stop being dirty", timeout=8.0)
        step.ok = True
        step.detail = "saved with the toolbar Save control"
        result.steps.append(step)
        return
    except UIError:
        pass

    actions.save_editor(editor)
    try:
        actions.wait_ready(saved, "the invoice editor to stop being dirty", timeout=12.0)
        step.ok = True
        step.detail = ("the toolbar Save left the editor dirty, so it was saved with "
                       "Ctrl+S, which targets this editor rather than the active one")
    except UIError as exc:
        step.detail = f"neither the toolbar Save nor Ctrl+S wrote the invoice ({exc})"
    result.steps.append(step)


def step_5_5_documents(win, result: Result, invoice_no: str, order_no: str,
                       reference: str, total: Decimal, is_paid: bool) -> None:
    """5.5 The Invoice is listed, and the source Order is still open."""
    step = Step("5.5", "Data > Documents shows the Invoice and the Order")
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

    def only(number):
        rows = [r for r in read.rows if col(r, "number") == number]
        return rows[0] if len(rows) == 1 else None

    problems = []
    invoice_row, order_row = only(invoice_no), only(order_no)
    if invoice_row is None:
        problems.append(f"expected exactly one row numbered {invoice_no!r}")
    if order_row is None:
        problems.append(f"expected exactly one row numbered {order_no!r}")

    if invoice_row is not None:
        got = parse_money(col(invoice_row, "total"))
        if got is None or got.quantize(CENT) != total.quantize(CENT):
            problems.append(f"invoice total is {col(invoice_row, 'total')!r}, expected {total}")
        if col(invoice_row, "reference") != reference:
            problems.append(f"invoice Cust.Ref. is {col(invoice_row, 'reference')!r}")
        state = col(invoice_row, "state")
        if is_paid and state != config.INVOICE_STATE_PAID:
            problems.append(f"the Invoice's state is {state!r}, expected "
                            f"{config.INVOICE_STATE_PAID!r} for a paid invoice")

    if order_row is not None:
        # 5.5 explicitly wants the Order still standing, unchanged.
        if col(order_row, "state") != config.ORDER_STATE_OPEN:
            problems.append(f"the Order's state is {col(order_row, 'state')!r}, "
                            f"expected {config.ORDER_STATE_OPEN!r}")
        if col(order_row, "reference") != reference:
            problems.append(f"the Order's Cust.Ref. is {col(order_row, 'reference')!r}")
        got = parse_money(col(order_row, "total"))
        if got is None or got.quantize(CENT) != total.quantize(CENT):
            problems.append(f"the Order's total is {col(order_row, 'total')!r}")

    if problems:
        step.detail = "; ".join(problems)
    else:
        step.ok = True
        step.detail = (f"Invoice {invoice_no!r} ({col(invoice_row, 'state')}, "
                       f"{parse_money(col(invoice_row, 'total')).quantize(CENT)}) and "
                       f"Order {order_no!r} still {col(order_row, 'state')}, "
                       f"both ref {reference!r}")
    result.steps.append(step)


def step_5_6_reconfirm(win, result: Result, title: str, payment: dict,
                       total: Decimal) -> None:
    """5.6 Re-read the payment fields from the saved document."""
    step = Step("5.6", "the payment details persisted")
    editor = ui.activate_editor(win, title, lambda w: invoice_editor(w, title))
    if editor is None:
        step.detail = f"the saved invoice editor {title!r} could not be reopened"
        result.steps.append(step)
        return

    scope = Scope(win, invoice=editor)
    findings, ok = [], True
    try:
        method = actions.read_value(find_control("invoice.payment_method", scope)).strip()
        findings.append(f"method={method!r}")
        ok &= method == payment["method"]

        state = actions.checkbox_state(find_control("invoice.paid", scope))
        findings.append(f"paid={state}")
        want_paid = (payment.get("paid_status") or "").upper() == "PAID"
        ok &= state is want_paid

        if want_paid:
            when = actions.parse_ui_date(
                actions.read_value(find_control("invoice.payment_date", scope)))
            findings.append(f"date={when}")
            ok &= when == datetime.strptime(payment["payment_date"], "%Y-%m-%d").date()

            value = parse_money(actions.read_value(find_control("invoice.paid_value", scope)))
            findings.append(f"value={value}")
            ok &= value is not None and value.quantize(CENT) == total.quantize(CENT)
    except UIError as exc:
        findings.append(str(exc))
        ok = False

    step.ok = ok
    step.detail = "; ".join(findings)
    result.steps.append(step)


def complete_invoice(doc: dict, order_number: str, *, invoice_title: str = None) -> Result:
    """Run 5.1-5.6. 5.7 is the absence of anything after it."""
    order, reconciliation = doc["order"], doc["reconciliation"]
    payment = order["payment"]
    total = Decimal(str(reconciliation["computed"]["gross_total"]))

    result = Result()
    win = ui.window()
    ui.activate(win)

    with ui.Clipboard():
        title = invoice_title or INVOICE_EDITOR_TITLE
        editor = ui.activate_editor(win, title, lambda w: invoice_editor(w, title))
        if editor is None:
            step = Step("5.1", "find the invoice editor")
            step.detail = f"no invoice editor titled {title!r} is open"
            result.steps.append(step)
            return result

        invoice_no = _read(Scope(win, invoice=editor), "invoice.number")
        reference = _read(Scope(win, invoice=editor), "invoice.cust_ref")

        step_5_1_confirm_copied(win, editor, result, order, reconciliation)
        if not step_5_2_payment_method(win, editor, result, payment["method"]):
            return result
        step_5_3_paid(win, editor, result, payment, total)
        step_5_4_save(win, editor, result, title)
        if not result.ok:
            return result

        saved_title = invoice_no if ui.editor_named(win, invoice_no) else title
        is_paid = (payment.get("paid_status") or "").upper() == "PAID"
        step_5_5_documents(win, result, invoice_no, order_number, reference, total, is_paid)
        if result.ok:
            step_5_6_reconfirm(win, result, saved_title, payment, total)
    return result
