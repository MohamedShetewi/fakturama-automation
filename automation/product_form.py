"""Spec 3.1-3.3: the product selection branch, run per extracted item row.

The chooser's grid publishes no rows to UIA, but it copies tab-separated rows
to the clipboard, so the exact-SKU decision is made on real data - including
the 'more than one match' case, which position alone could never see.

Selecting still needs a coordinate, because the rows are painted rather than
published. That click is immediately proved by copying the selected row back:
a mis-aimed click is caught before OK is pressed, never after.
"""

from __future__ import annotations

import time

from . import actions, config, ui
from .entities import Verdict, classify
from .order_form import Result, Step, _layer_of
from .resolver import Scope, find_control
from .ui import UIError


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


def step_3_3_choose(win, dlg, result: Result, sku: str, position: int) -> Verdict:
    """3.3 Exactly one exact SKU -> select and OK. Conflicting -> halt.
    None -> Cancel and fall through to the creation branch."""
    step = Step(f"3.3[{position}]", f"find SKU {sku!r}")
    dscope = Scope(win, dialog=dlg)

    # A modal chooser does not inherit the shell's activation, and keystrokes
    # aimed at an inactive window are discarded silently.
    dlg.SetActive()
    time.sleep(config.SETTLE)

    written = actions.set_value("product_dialog.search", sku, dscope)
    if not written.ok:
        # Do not read the grid after a failed search: it would still hold the
        # unfiltered list, and 'no exact match' would be a lie.
        actions.click("product_dialog.cancel", dscope)
        step.detail = f"could not type the SKU into the search box ({written.detail})"
        result.steps.append(step)
        return Verdict.CONFLICT
    time.sleep(config.SETTLE * 4)          # let the filter settle

    rows = ui.grid_rows(dlg)
    col = config.PRODUCT_COL["sku"]
    verdict, matches = classify(rows, sku, key=lambda r: r[col] if len(r) > col else "")

    if verdict is Verdict.UNIQUE:
        index = rows.index(matches[0])
        selected = ui.grid_select_row(dlg, index)
        got = selected[col] if len(selected) > col else ""
        if got.strip().casefold() != sku.strip().casefold():
            # The click landed on the wrong row. Do not press OK.
            actions.click("product_dialog.cancel", dscope)
            step.detail = (
                f"selected row reports SKU {got!r}, not {sku!r}; cancelled "
                "rather than adding the wrong product"
            )
            result.steps.append(step)
            return Verdict.CONFLICT
        actions.click("product_dialog.ok", dscope)
        actions.wait_ready(lambda: product_dialog(win) is None, "the chooser to close", timeout=10.0)
        step.ok = True
        step.detail = f"one exact match ({selected[:2]}); selected and OK"
        result.steps.append(step)
        return verdict

    actions.click("product_dialog.cancel", dscope)
    actions.wait_ready(lambda: product_dialog(win) is None, "the chooser to close", timeout=10.0)

    if verdict is Verdict.NONE:
        # Not a failure: it is the documented route into the creation branch.
        step.ok = True
        step.detail = (
            f"no exact SKU among {[r[col] for r in rows if r]}; cancelled - "
            "spec 3.4+ must create the product"
        )
    else:
        step.detail = (
            f"{len(matches)} rows match {sku!r} exactly; stopping for manual review"
        )
    result.steps.append(step)
    return verdict


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

            editor = ui.activate_editor(win, "New Order", ui.order_editor)
            scope = Scope(win, editor)
            dlg = step_3_2_open_selector(win, scope, result, pos)
            verdict = step_3_3_choose(win, dlg, result, sku, pos)
            if verdict is Verdict.NONE:
                to_create.append(item)

    return result, to_create
