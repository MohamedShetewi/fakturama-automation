"""Spec 3.1-3.3: the product selection branch, run per extracted item row.

The chooser's grid publishes no rows to UIA, but it copies tab-separated rows
to the clipboard, so the exact-SKU decision is made on real data - including
the 'more than one match' case, which position alone could never see.

Selecting still needs a coordinate, because the rows are painted rather than
published. That click is immediately proved by copying the selected row back:
a mis-aimed click is caught before OK is pressed, never after.
"""

from __future__ import annotations

import logging
import time

from . import actions, config, order_items, ui
from .entities import Verdict, classify
from .order_form import Result, Step, _layer_of
from .resolver import Scope, find_control
from .ui import UIError

log = logging.getLogger("automation.product_form")


def product_dialog(win):
    for w in ui.find_all(win, lambda c: c.ControlTypeName == "WindowControl", 6):
        if (w.Name or "").startswith(config.PRODUCT_DIALOG_TITLE):
            return w
    return None


def step_3_2_open_selector(win, scope: Scope, result: Result, position: int):
    """3.2 Click the upper Product-selection icon, never the green +."""
    step = Step(f"3.2[{position}]", "open Select a product")
    resolved = find_control("order.item_pick_product", scope)
    step.layer = _layer_of(resolved)
    if resolved.layer.name != "TOOLTIP":
        # 'Add a new item with default name and quantity 1' sits right below.
        step.detail = (
            "the product icon could not be confirmed by tooltip; refusing to "
            "click, because the adjacent icon adds a blank item instead"
        )
        result.steps.append(step)
        raise UIError(step.detail)

    actions.click("order.item_pick_product", scope)
    dlg = actions.wait_ready(lambda: product_dialog(win), "the product chooser", timeout=12.0)
    step.ok = True
    result.steps.append(step)
    return dlg


def _settled_dialog(win, timeout: float = 4.0):
    """The chooser, once its rebuilt widget tree is readable again.

    Filtering disposes and re-creates the chooser's children, and during that
    window the dialog is not merely stale - it is not findable at all. Polling
    rather than looking once is the difference between "the SKU is not there"
    and "we looked mid-redraw", and only one of those should lead to creating
    a product.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        dlg = product_dialog(win)
        if dlg is not None:
            try:
                if ui.grid_pane(dlg) is not None:
                    return dlg
            except UIError:
                pass          # window back, children not yet
        time.sleep(0.25)
    return product_dialog(win)


def dismiss(win, dscope: Scope) -> None:
    """Close the chooser if it is still there, and never fail trying.

    The dialog can already be gone by the time we want to cancel it, and an
    unresolvable 'Cancel' button then masks the real reason the step ended.
    """
    if product_dialog(win) is None:
        return
    try:
        actions.click("product_dialog.cancel", dscope)
        actions.wait_ready(lambda: product_dialog(win) is None,
                           "the chooser to close", timeout=10.0)
    except UIError as exc:
        log.warning("could not cancel the product chooser: %s", exc)


def _confirm_accepted(win, result: Result, step: Step, sku: str, before: int) -> Verdict:
    """The chooser closed by itself. Find out what it did, on the order.

    Fakturama's product chooser accepts a unique filter match without being
    asked: it selects the row, adds the line and disposes itself, so there is
    no grid left to read and no OK to press. The dialog vanishing is therefore
    not evidence of anything on its own - measured, it also vanishes when a
    keystroke lands mid-rebuild. The order's own Items grid is the only place
    that records what happened, so that is what gets asked.
    """
    editor = ui.activate_editor(win, "New Order", ui.order_editor)
    if editor is None:
        step.detail = "the chooser closed but the order editor is not available to check"
        result.steps.append(step)
        return Verdict.CONFLICT

    items, how = order_items.read(editor)
    if how != "read":
        step.detail = (
            f"the chooser closed but the order's items could not be read ({how}); "
            "refusing to assume the line was added"
        )
        result.steps.append(step)
        return Verdict.CONFLICT

    after = len(order_items.find(items, sku))
    if after == before + 1:
        line = order_items.find(items, sku)[-1]
        step.ok = True
        step.detail = f"the chooser accepted it on its own; the order now shows {line}"
        result.steps.append(step)
        return Verdict.UNIQUE
    if after == before:
        step.detail = (
            f"the chooser closed without adding a line for {sku!r}; it is neither "
            "selected nor proven absent, so this needs a human"
        )
    else:
        step.detail = (
            f"the chooser added {after - before} lines for {sku!r} instead of one; "
            "stopping rather than booking duplicates"
        )
    result.steps.append(step)
    return Verdict.CONFLICT


def step_3_3_choose(win, dlg, result: Result, sku: str, position: int,
                    before: int = 0) -> Verdict:
    """3.3 Exactly one exact SKU -> selected. Conflicting -> halt.
    Nothing found, or a grid that offers nothing to read -> Cancel and fall
    through to the creation branch.

    `before` is how many lines the order already had for this SKU, counted
    before the chooser opened - the baseline the auto-accept is measured
    against.
    """
    step = Step(f"3.3[{position}]", f"find SKU {sku!r}")
    dscope = Scope(win, dialog=dlg)
    col = config.PRODUCT_COL["sku"]

    # A modal chooser does not inherit the shell's activation, and keystrokes
    # aimed at an inactive window are discarded silently.
    dlg.SetActive()
    time.sleep(config.SETTLE)

    written = actions.set_value("product_dialog.search", sku, dscope)
    time.sleep(config.SETTLE * 4)          # let the filter settle

    # Re-fetch the dialog. Filtering rebuilds the chooser's widget tree, and
    # the element we are holding goes stale with it - measured: every pane
    # inside it disappears, so the grid reads as 'no pane' and an SKU that is
    # sitting right there reports as absent. That false absence is the worst
    # possible one: it routes straight into creating a duplicate.
    # Whether the chooser is still there is asked before whether the write
    # verified, and the order matters. A search that matches uniquely makes the
    # chooser accept and dispose itself *during* the write, so reading the box
    # back finds a dead control and reports an empty field - a write that
    # actually worked, called a failure. The order's line settles it either way.
    fresh = _settled_dialog(win)
    if fresh is None:
        return _confirm_accepted(win, result, step, sku, before)

    if not written.ok:
        # Still open, and the term never landed. Do not read the grid now: it
        # holds the unfiltered list, and 'no exact match' would be a lie.
        dismiss(win, dscope)
        step.detail = f"could not type the SKU into the search box ({written.detail})"
        result.steps.append(step)
        return Verdict.CONFLICT

    dlg, dscope = fresh, Scope(win, dialog=fresh)

    try:
        rows = ui.grid_rows(dlg)
        verdict, matches = classify(rows, sku, key=lambda r: r[col] if len(r) > col else "")

        if verdict is Verdict.UNIQUE:
            index = rows.index(matches[0])
            selected = ui.grid_select_row(dlg, index)
            got = selected[col] if len(selected) > col else ""
            if got.strip().casefold() != sku.strip().casefold():
                # The click landed on the wrong row. Do not press OK.
                dismiss(win, dscope)
                step.detail = (
                    f"selected row reports SKU {got!r}, not {sku!r}; cancelled "
                    "rather than adding the wrong product"
                )
                result.steps.append(step)
                return Verdict.CONFLICT
            actions.click("product_dialog.ok", dscope)
            actions.wait_ready(lambda: product_dialog(win) is None,
                               "the chooser to close", timeout=10.0)
            step.ok = True
            step.detail = f"one exact match ({selected[:2]}); selected and OK"
            result.steps.append(step)
            return verdict
    except UIError as exc:
        # The chooser could not be read or selected from - an empty filter
        # result looks exactly like this, because there is no row to take
        # focus. Treat it as "not there", which is the route into creation,
        # rather than aborting a run over a product that simply does not exist
        # yet. A wrong *selection* is still a halt; this is a failed lookup.
        dismiss(win, dscope)
        step.ok = True
        step.detail = f"the chooser offered no row for {sku!r} ({exc}); will create it"
        result.steps.append(step)
        return Verdict.NONE

    dismiss(win, dscope)

    if verdict is Verdict.NONE:
        # Not a failure: it is the documented route into the creation branch.
        step.ok = True
        step.detail = (
            f"no exact SKU among {[r[col] for r in rows if r]}; cancelled - "
            "the product has to be created"
        )
    else:
        step.detail = (
            f"{len(matches)} rows match {sku!r} exactly; stopping for manual review"
        )
    result.steps.append(step)
    return verdict


def _select_one(win, result: Result, item: dict, sku: str, pos: int) -> Verdict:
    """3.2-3.3 for one item, retried while the chooser misbehaves.

    Each attempt re-reads how many lines the order already has for this SKU,
    which is what makes retrying safe: an attempt that did add the line is seen
    by the next one, which then skips instead of adding a second.

    Only the chooser's own flakiness is retried. A CONFLICT that means
    something - two products matching one SKU, a selection that came back with
    the wrong SKU - is returned immediately, because trying again would just
    reach the same wrong answer more slowly.
    """
    attempts = []
    for attempt in range(config.CHOOSER_ATTEMPTS):
        editor = ui.activate_editor(win, "New Order", ui.order_editor)

        # Count what the order already holds for this SKU *before* opening the
        # chooser. Once it is open the order is behind a modal and cannot be
        # read, and after it closes there is nothing to compare against - so
        # the baseline has to be taken now or not at all.
        existing, how = order_items.read(editor)
        if how != "read":
            step = Step(f"3.3[{pos}]", f"find SKU {sku!r}")
            step.detail = (
                f"could not read the order's existing lines ({how}); without a "
                "baseline there is no way to tell whether the chooser added one"
            )
            result.steps.append(step)
            return Verdict.CONFLICT

        before = len(order_items.find(existing, sku))
        if before:
            # Already on the order. Selecting it again would add a second line
            # for the same product rather than replace the first - measured, a
            # re-run turned two lines into four.
            step = Step(f"3.3[{pos}]", f"find SKU {sku!r}")
            step.ok = True
            step.detail = (f"the order already has {before} line(s) for it; "
                           "not adding another")
            if attempts:
                step.detail += f" (after {len(attempts)} unusable attempt(s))"
            result.steps.append(step)
            return Verdict.UNIQUE

        attempt_steps = Result()
        try:
            dlg = step_3_2_open_selector(win, Scope(win, editor), attempt_steps, pos)
            verdict = step_3_3_choose(win, dlg, attempt_steps, sku, pos, before)
        except UIError as exc:
            verdict = Verdict.CONFLICT
            failed = Step(f"3.2[{pos}]", "open Select a product")
            failed.detail = str(exc)
            attempt_steps.steps.append(failed)

        if verdict is not Verdict.CONFLICT or attempt == config.CHOOSER_ATTEMPTS - 1:
            result.steps.extend(attempt_steps.steps)
            return verdict

        # Worth another go: the chooser closed without doing anything, which is
        # a UI misfire rather than an answer about this SKU.
        attempts.append(attempt_steps)
        log.warning("chooser attempt %d for %r was unusable; retrying", attempt + 1, sku)
        dismiss(win, Scope(win, dialog=product_dialog(win)))
    return Verdict.CONFLICT


def select_products(order: dict) -> tuple[Result, list[dict]]:
    """3.1 Run the selection branch for every item, in source order.

    Returns the step log and the items that still need creating.
    """
    result = Result()
    win = ui.window()
    ui.activate(win)
    to_create: list[dict] = []

    with ui.Clipboard():
        for item in sorted(order["items"], key=lambda i: i["position"]):
            sku = item.get("sku")
            pos = item["position"]
            if not sku:
                step = Step(f"3.3[{pos}]", "find SKU")
                step.detail = "the item has no SKU; the product cannot be resolved"
                result.steps.append(step)
                continue

            verdict = _select_one(win, result, item, sku, pos)
            if verdict is Verdict.NONE:
                to_create.append(item)

    return result, to_create
