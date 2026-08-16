"""Spec 3.7: create the product the chooser did not have.

This is the branch 3.3 falls into when no row matches the SKU exactly. It runs
after the VAT branch, not before, because the editor's VAT combo lists rates by
Name - so the rate has to exist and be known by name before a product can be
booked against it.

The one judgement call here is the price. The editor asks for whichever of net
or gross the workspace is configured for, and says which in its label; the
extracted order always carries a *net* unit price. So the label is read, not
assumed, and the conversion - when there is one - is done in Decimal and stated
in the step log. Typing a net figure into a field labelled gross would book
roughly 16% less than the customer was charged, and nothing downstream would
catch it: the reconciliation gate ran a stage earlier, against the image.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal

import uiautomation as auto

from . import actions, config, ui
from .order_form import Result, Step, _layer_of
from .resolver import Scope, find_control, present
from .ui import UIError

log = logging.getLogger("automation.product_new")


def product_editor(win):
    """The New product editor's content Pane, or None."""
    panes = ui.find_all(
        win,
        lambda c: c.ControlTypeName == "PaneControl"
        and (c.Name or "").lstrip("*") == config.PRODUCT_EDITOR_TITLE,
    )
    if not panes:
        return None
    return max(panes, key=lambda p: p.BoundingRectangle.width() * p.BoundingRectangle.height())


def editor_is_dirty(win) -> bool:
    """Does an unsaved New product editor exist?

    The editor's own tab star, not the toolbar Save button. Save reflects
    whichever editor is active, and a run leaves several dirty ones open, so it
    answers a different question than the one being asked.
    """
    return ui.editor_is_dirty(win, config.PRODUCT_EDITOR_TITLE)


def close_saved_editor(win) -> bool:
    """Close a saved New product editor so the next item gets a blank one.

    Fakturama does not rename this editor after a save - it stays 'New
    product' - so a second item filled into it would overwrite the record just
    written rather than create a new one. Closing is the only way back to a
    blank form.

    Refuses outright if the editor is dirty. Ctrl+W on unsaved work either
    loses it or raises a prompt, and neither is this function's decision.
    """
    if product_editor(win) is None:
        return True
    if editor_is_dirty(win):
        log.warning("the product editor still has unsaved changes; not closing it")
        return False
    ui.activate_editor(win, config.PRODUCT_EDITOR_TITLE, product_editor)
    auto.SendKeys("{Ctrl}w", waitTime=0.2)
    try:
        actions.wait_ready(lambda: product_editor(win) is None,
                           "the saved product editor to close", timeout=8.0)
        return True
    except UIError as exc:
        log.warning("could not close the saved product editor: %s", exc)
        return False


def price_mode(scope: Scope) -> tuple[str, str]:
    """Which price the editor wants: the catalog key, and the label proving it.

    Read rather than assumed. If neither label is there the editor is not
    realised - or has been relaid out - and guessing would silently write the
    net price into a gross field.
    """
    if present("product.price_gross", scope):
        return "product.price_gross", config.PRICE_LABEL_GROSS
    if present("product.price_net", scope):
        return "product.price_net", config.PRICE_LABEL_NET
    raise UIError(
        "the product editor shows neither a "
        f"{config.PRICE_LABEL_GROSS!r} nor a {config.PRICE_LABEL_NET!r} field, "
        "so which price it wants cannot be established"
    )


def price_for(mode_key: str, net: Decimal, vat_percent: Decimal) -> Decimal:
    """The figure to type, given what the field is asking for.

    Decimal end to end. Quantised to cents only at the last step, because the
    field stores cents and rounding earlier would drift the total.
    """
    if mode_key == "product.price_net":
        return net.quantize(Decimal("0.01"))
    gross = net * (Decimal(1) + vat_percent / Decimal(100))
    return gross.quantize(Decimal("0.01"))


def step_3_7a_open(win, result: Result):
    """Open the New product editor, or reuse the one already open."""
    step = Step("3.7a", "open the New product editor")
    # Activate before deciding: SWT realises only the active editor's controls,
    # so an existing New product tab that is not in front looks exactly like no
    # editor at all - and clicking the link again would stack a second one.
    editor = ui.activate_editor(win, config.PRODUCT_EDITOR_TITLE, product_editor)
    if editor is not None:
        # Clicking the link again stacks a second editor, and then "did the
        # save land?" has two possible subjects and no answer.
        step.ok = True
        step.detail = "an editor was already open; reusing it"
        result.steps.append(step)
        return editor

    step.layer = _layer_of(find_control("product.list_new", Scope(win)))
    actions.click("product.list_new", Scope(win))
    editor = actions.wait_ready(lambda: product_editor(win), "the New product editor",
                                timeout=config.EDITOR_TIMEOUT)
    step.ok = True
    result.steps.append(step)
    return editor


def _write(result: Result, ref: str, what: str, key: str, value, scope: Scope) -> bool:
    step = Step(ref, what)
    step.layer = _layer_of(find_control(key, scope))
    written = actions.set_value(key, value, scope)
    step.ok = written.ok
    step.detail = repr(written.read_back) if written.ok else written.detail
    result.steps.append(step)
    return written.ok


def create_product(win, result: Result, item: dict, vat_name: str) -> bool:
    """3.7 Fill and save one product. Returns whether it is safely stored."""
    sku = item["sku"]
    editor = step_3_7a_open(win, result)
    scope = Scope(win, product=editor)

    _write(result, "3.7b", f"set Item Number for {sku!r}",
           "product.item_number", sku, scope)
    _write(result, "3.7c", "set Name", "product.name", item["description"], scope)

    # VAT before price: in a gross-priced workspace the amount depends on the
    # rate, and a rate changed afterwards would not retype the price.
    _write(result, "3.7d", f"set VAT to {vat_name!r}", "product.vat", vat_name, scope)

    net = Decimal(str(item["unit_net_price"]))
    vat_percent = Decimal(str(item["vat_percent"]))
    mode_key, label = price_mode(scope)
    amount = price_for(mode_key, net, vat_percent)

    step = Step("3.7e", f"set the price, which this editor asks for as {label!r}")
    step.layer = _layer_of(find_control(mode_key, scope))
    written = actions.set_value(mode_key, amount, scope)
    step.ok = written.ok
    if mode_key == "product.price_net":
        how = f"net {net} written as-is"
    else:
        how = f"net {net} at {vat_percent}% VAT -> gross {amount}"
    step.detail = f"{how}; field holds {written.read_back!r}" if written.ok else written.detail
    result.steps.append(step)

    save = Step("3.7f", f"save {sku!r}")
    if not result.ok:
        save.detail = "skipped: an earlier step did not verify, so nothing was saved"
        result.steps.append(save)
        return False

    actions.save_editor(editor)
    try:
        actions.wait_ready(lambda: not editor_is_dirty(win),
                           "the product editor to stop being dirty", timeout=12.0)
        save.ok = True
        save.detail = "saved; the editor is no longer dirty"
    except UIError as exc:
        save.detail = f"clicked Save but the editor is still dirty ({exc})"
    result.steps.append(save)
    return save.ok


def vat_names_for(items: list[dict]) -> tuple[Result, dict[str, str]]:
    """Resolve every distinct rate the items need, once each.

    Returns the step log and a percent -> VAT Name map. The map is keyed on
    the rate because that is what identifies a VAT record; the Name is
    whatever the database calls it, which is not predictable - the live one is
    'MwSt. 19%'.
    """
    from .vat_form import ensure_vat          # here, to keep the import graph flat

    log_result = Result()
    names: dict[str, str] = {}
    for percent in sorted({actions.plain_number(i["vat_percent"]) for i in items}):
        result, name = ensure_vat(f"VAT {percent}%", f"{percent}%")
        log_result.steps.extend(result.steps)
        if name:
            names[percent] = name
    return log_result, names


def create_products(items: list[dict], vat_names: dict[str, str]) -> Result:
    """3.7 for every item the chooser could not find, in source order."""
    result = Result()
    win = ui.window()
    ui.activate(win)
    for item in sorted(items, key=lambda i: i["position"]):
        vat_name = vat_names.get(actions.plain_number(item["vat_percent"]))
        if vat_name is None:
            missing = Step("3.7d", f"set VAT for {item['sku']!r}")
            missing.detail = (
                f"no VAT record resolved for {item['vat_percent']}%; refusing to "
                "create a product that would book against the wrong rate"
            )
            result.steps.append(missing)
            break
        if not create_product(win, result, item, vat_name):
            break          # do not pile a second product onto a failed editor
        time.sleep(config.SETTLE)

        # The saved editor keeps its 'New product' title, so it has to be
        # closed: filling the next item into it would overwrite the record
        # just written instead of creating a new one.
        fresh = Step("3.7g", f"close the saved editor for {item['sku']!r}")
        fresh.ok = close_saved_editor(win)
        fresh.detail = ("closed; the next product starts from a blank form"
                        if fresh.ok else
                        "still open - refusing to create the next product in it")
        result.steps.append(fresh)
        if not fresh.ok:
            break
    return result


def ensure_products(order: dict) -> Result:
    """3.1-3.7 as one pass: select what exists, create what does not.

    Composed in this order for a reason. Creation is driven by what the
    *chooser* could not find, never by the order data alone - asking "which
    items are there?" and answering from the extraction would create a second
    copy of every product that already exists, which is exactly what a hand-run
    of these steps did: two identical 'CHR-ERG-01' rows, after which every
    lookup for that SKU is ambiguous and the flow can only halt.
    """
    from .product_form import select_products

    result = Result()
    selected, to_create = select_products(order)
    result.steps.extend(selected.steps)
    if not selected.ok or not to_create:
        return result

    vat_log, vat_names = vat_names_for(to_create)
    result.steps.extend(vat_log.steps)
    if not vat_log.ok:
        return result

    result.steps.extend(create_products(to_create, vat_names).steps)
    if not result.ok:
        return result

    # Now that they exist, run the selection branch again for those items only.
    again, still_missing = select_products({"items": to_create})
    result.steps.extend(again.steps)
    if still_missing:
        step = Step("3.7h", "confirm every item resolves")
        step.detail = (
            "still not found after creating: "
            f"{[i['sku'] for i in still_missing]}"
        )
        result.steps.append(step)
    return result
