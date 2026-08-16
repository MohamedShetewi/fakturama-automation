"""The whole run, in order: image JSON in, saved Order and Invoice out.

Each stage is a function already proven on its own; this composes them and
enforces the one rule that matters between them - the chain stops at the first
stage that does not verify. Half a booking is worse than none, because it looks
finished.

Stages are re-runnable. Every step that writes now checks what is already there
first: an order that already holds a line for a SKU does not get a second one,
a product that already exists is selected rather than created again, and a
document already saved reports "nothing to write". So a run interrupted at
stage 4 can be started again from the top without duplicating what stages 1-3
did - which matters, because interruptions are the normal case here.

The debtor is the exception worth naming. Spec 2 is written as "create the
Debtor", but a second run must not create a second one, so it is treated the
same way as a product: try to select the existing contact first, and only fall
through to creation when the Order's address stays empty.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import (
    debtor_form, invoice_form, line_items, order_complete, order_form,
    payment_form, product_new, ui,
)
from .entities import Outcome
from .order_form import Result, Step
from .ui import UIError

log = logging.getLogger("automation.flow")


@dataclass
class Stage:
    """One numbered block of the spec, and what happened when it ran."""

    ref: str
    what: str
    result: Result = field(default_factory=Result)
    skipped: bool = False

    @property
    def ok(self) -> bool:
        return self.skipped or self.result.ok

    def __str__(self) -> str:
        if self.skipped:
            return f"[skip] {self.ref} {self.what}"
        mark = "ok  " if self.result.ok else "FAIL"
        return f"[{mark}] {self.ref} {self.what}"


def ensure_debtor(order: dict) -> Result:
    """Spec 2: select the Debtor if it exists, create it if it does not.

    Selection is tried first and its success is judged by 2.13 - the address
    the Order actually shows. That is a real oracle: the chooser's grid gives
    up no rows, so an empty address after a search is the only evidence that
    the contact is not there.
    """
    result = Result()
    debtor_form.steps_2_12_2_13_select_in_order(order, result)
    if result.ok:
        return result

    # Not there. Everything above becomes context rather than failure, so the
    # log still shows the search that came up empty.
    for step in result.steps:
        step.ref = step.ref + " (first attempt)"
        step.ok = True
        step.verified = False
    note = Step("2.1", "no existing Debtor matched; creating one")
    note.ok = True
    result.steps.append(note)

    created = debtor_form.create_debtor(order)
    result.steps.extend(created.steps)
    if not created.ok:
        return result

    # 2.10 may have found the payment method missing rather than failing.
    method = (order.get("payment") or {}).get("method")
    if method and _payment_missing(created):
        made = payment_form.create_payment_method(
            method, (order.get("derived") or {}).get("payment_code"))
        result.steps.extend(made.steps)
        if not made.ok:
            return result
        picked = debtor_form.select_payment_after_creation(order)
        result.steps.extend(picked.steps)
        if not picked.ok:
            return result
    else:
        win = ui.window()
        debtor_form.step_2_11_save(win, result)
        if not result.ok:
            return result

    debtor_form.steps_2_12_2_13_select_in_order(order, result)
    return result


def _payment_missing(result: Result) -> bool:
    """Did 2.10 report the method absent rather than selecting it?"""
    return any(s.ref.startswith("2.10") and not s.ok for s in result.steps)


def run(doc: dict, *, allow_existing: bool = False, follow_up: bool = True,
        stop_after: str = None) -> list[Stage]:
    """Run the spec end to end, stopping at the first stage that fails."""
    order = doc["order"]
    stages = [
        Stage("1.3-1.7", "order header"),
        Stage("2.1-2.13", "debtor and payment method"),
        Stage("3.1-3.7", "products and VAT rates"),
        Stage("3.13-3.17", "line quantities, prices and discounts"),
        Stage("4.1-4.7", "complete and save the order"),
        Stage("5.1-5.6", "complete and verify the invoice"),
    ]
    by_ref = {s.ref: s for s in stages}

    def should_stop(stage: Stage) -> bool:
        return stop_after is not None and stage.ref.startswith(stop_after)

    runners = {
        "1.3-1.7": lambda: order_form.fill_header(order, allow_existing=allow_existing),
        "2.1-2.13": lambda: ensure_debtor(order),
        "3.1-3.7": lambda: product_new.ensure_products(order),
        "3.13-3.17": lambda: line_items.apply_lines(order, doc["reconciliation"]),
        "4.1-4.7": lambda: order_complete.complete_order(doc, follow_up=follow_up),
        "5.1-5.6": lambda: invoice_form.complete_invoice(
            doc, by_ref["4.1-4.7"].result.context.get("order_number", "")),
    }

    stop = False
    for stage in stages:
        if stop:
            stage.skipped = True
            continue
        log.info("stage %s: %s", stage.ref, stage.what)
        stage.result = runners[stage.ref]()
        if not stage.result.ok or should_stop(stage):
            stop = True
    return stages


def report(stages: list[Stage]) -> str:
    """The whole run as text, steps nested under their stage."""
    lines = []
    for stage in stages:
        lines.append(str(stage))
        for step in stage.result.steps:
            lines.append("  " + str(step))
    return "\n".join(lines)
