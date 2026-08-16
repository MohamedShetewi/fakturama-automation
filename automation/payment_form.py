"""Spec 2.10.1-2.10.6: create the Payment Method the document names.

Reached only when the Debtor editor's Payment combo does not already offer it.

2.10.4's mapping (Bank Transfer -> Credit transfer, ...) is not repeated here:
the extraction half already computed it in derive.py and published it as
`derived.payment_code`, tested there. Re-deriving it in the UI layer would be
a second copy of a rule that must not drift.

2.10.6 is the first step in this project that writes to the database.
"""

from __future__ import annotations

import time

from . import actions, config, ui
from .order_form import Result, Step, _layer_of
from .resolver import Scope, find_control
from .ui import UIError


def payment_editor(win):
    """The 'New Term of Payment' editor pane, or None."""
    panes = ui.find_all(
        win,
        lambda c: c.ControlTypeName == "PaneControl"
        and (c.Name or "").lstrip("*") == config.PAYMENT_EDITOR_TITLE,
    )
    if not panes:
        return None
    return max(panes, key=lambda p: p.BoundingRectangle.width() * p.BoundingRectangle.height())


def step_2_10_1_open_list(win, result: Result) -> None:
    """2.10.1 Open Data > terms of payment."""
    step = Step("2.10.1", "open Data > terms of payment")
    step.layer = _layer_of(find_control("nav.terms_of_payment", Scope(win)))
    actions.click("nav.terms_of_payment", Scope(win))
    actions.wait_ready(
        lambda: find_control("payment.list_new", Scope(win), required=False),
        "the terms-of-payment list", timeout=config.EDITOR_TIMEOUT,
    )
    step.ok = True
    result.steps.append(step)


def step_2_10_2_new(win, result: Result):
    """2.10.2 No exact row exists, so click the green '+'.

    The existence question was already answered against the Debtor editor's
    Payment combo, which is readable - the list itself is an opaque NatTable
    whose rows publish nothing to UIA.
    """
    step = Step("2.10.2", "click the green + (no exact row exists)")
    existing = payment_editor(win)
    if existing is not None:
        step.ok = True
        step.detail = "reused the open Term of Payment editor"
        result.steps.append(step)
        return existing

    step.layer = _layer_of(find_control("payment.list_new", Scope(win)))
    actions.click("payment.list_new", Scope(win))
    editor = actions.wait_ready(
        lambda: payment_editor(win), "the New Term of Payment editor",
        timeout=config.EDITOR_TIMEOUT,
    )
    step.ok = True
    step.detail = "opened"
    result.steps.append(step)
    return editor


def step_2_10_3_identity(scope: Scope, result: Result, method: str) -> None:
    """2.10.3 Name and Description both the exact method; Account left blank."""
    for ref, key, label in (("2.10.3a", "payment.name", "Name"),
                            ("2.10.3b", "payment.description", "Description")):
        step = Step(ref, f"set {label}")
        step.layer = _layer_of(find_control(key, scope))
        written = actions.set_value(key, method, scope)
        step.ok = written.ok
        step.detail = repr(written.read_back) if written.ok else written.detail
        result.steps.append(step)

    acct = Step("2.10.3c", "leave Account blank")
    resolved = find_control("payment.account", scope)
    acct.layer = _layer_of(resolved)
    value = actions.read_value(resolved)
    acct.ok = not (value or "").strip()
    acct.detail = "blank (untouched)" if acct.ok else f"expected blank, holds {value!r}"
    result.steps.append(acct)


def step_2_10_4_code(scope: Scope, result: Result, code: str | None) -> None:
    """2.10.4 Set the payment-code dropdown from the derived mapping."""
    step = Step("2.10.4", "set payment code")
    if not code:
        step.detail = (
            "the extraction produced no payment_code for this method, so the "
            "spec's mapping does not cover it - stopping rather than guessing"
        )
        result.steps.append(step)
        return
    step.layer = _layer_of(find_control("payment.code", scope))
    written = actions.set_value("payment.code", code, scope)
    step.ok = written.ok
    step.detail = f"combo reads {written.read_back!r}" if written.ok else written.detail
    result.steps.append(step)


def step_2_10_5_terms(scope: Scope, result: Result) -> None:
    """2.10.5 Cash discount / Discount Days / Net Days = 0; texts left blank;
    'Set as standard' not clicked."""
    for ref, key, label, value in (
        ("2.10.5a", "payment.cash_discount", "Cash discount", config.PAYMENT_ZERO_DISCOUNT),
        ("2.10.5b", "payment.discount_days", "Discount Days", config.PAYMENT_ZERO_DAYS),
        ("2.10.5c", "payment.net_days", "Net Days", config.PAYMENT_ZERO_DAYS),
    ):
        step = Step(ref, f"set {label} to {value}")
        step.layer = _layer_of(find_control(key, scope))
        written = actions.set_value(key, value, scope)
        step.ok = written.ok
        step.detail = repr(written.read_back) if written.ok else written.detail
        result.steps.append(step)

    # The three Text fields are deliberately not written: 'leave blank' means
    # leave alone, and typing an empty string into a field is a different act.
    untouched = Step("2.10.5d", "leave the three Text fields blank and untouched")
    untouched.ok = True
    untouched.detail = "Text 'unpaid' / 'deposit' / 'paid' never written"
    result.steps.append(untouched)

    std = Step("2.10.5e", "do not click 'Set as standard'")
    std.ok = True
    std.detail = "catalogued but never invoked"
    result.steps.append(std)


def step_2_10_6_save(win, result: Result) -> None:
    """2.10.6 Click the toolbar Save once. This writes to the database."""
    step = Step("2.10.6", "save the new Term of Payment")
    resolved = find_control("toolbar.save", Scope(win))
    step.layer = _layer_of(resolved)
    if not resolved.control.IsEnabled:
        step.detail = "the Save control is disabled - nothing to save?"
        result.steps.append(step)
        return

    actions.click("toolbar.save", Scope(win))
    # Save is the readable oracle: it goes back to disabled once the editor is
    # clean, which is the application telling us the write completed.
    try:
        actions.wait_ready(
            lambda: not find_control("toolbar.save", Scope(win)).control.IsEnabled,
            "the Save control to go disabled (editor clean)", timeout=10.0,
        )
        step.ok = True
        step.detail = "saved once; Save is disabled again"
    except UIError as exc:
        step.detail = f"clicked Save but the editor still reports unsaved changes ({exc})"
    result.steps.append(step)


def create_payment_method(method: str, code: str | None) -> Result:
    """Run 2.10.1-2.10.6. Writes one Term of Payment to the database."""
    result = Result()
    win = ui.window()
    ui.activate(win)

    with ui.Clipboard():
        step_2_10_1_open_list(win, result)
        editor = step_2_10_2_new(win, result)
        scope = Scope(win)
        scope.payment = editor
        # PAYMENT_EDITOR rows resolve against the editor pane.
        scope = _payment_scope(win, editor)
        step_2_10_3_identity(scope, result, method)
        step_2_10_4_code(scope, result, code)
        step_2_10_5_terms(scope, result)
        if result.ok:
            step_2_10_6_save(win, result)
        else:
            skipped = Step("2.10.6", "save the new Term of Payment")
            skipped.detail = "skipped: an earlier step did not verify, so nothing was written"
            result.steps.append(skipped)

    return result


def _payment_scope(win, editor) -> Scope:
    scope = Scope(win)
    scope.payment = editor
    return scope
