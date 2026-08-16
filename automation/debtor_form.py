"""Spec 2.5-2.7: create a Debtor from the extracted JSON.

Reached when the address chooser produced no exact match. Same posture as the
order header: every field is written through real input and read back, and a
field that will not verify is reported rather than assumed.

Nothing here saves. Spec 2.11 is the only step that writes to the database,
and it is deliberately not part of this module.
"""

from __future__ import annotations

import time

import uiautomation as auto

from dataclasses import dataclass, field

from . import actions, config, ui
from .entities import Outcome, Resolution, resolve_or_create
from .order_form import Result, Step, _layer_of
from .resolver import Scope, find_control
from .ui import UIError


def debtor_ready(win):
    """The New Debtor editor exists and its fields are realised."""
    editor = ui.debtor_editor(win)
    if editor is None:
        return None
    scope = Scope(win, debtor=editor)
    try:
        find_control("debtor.company", scope)
    except UIError:
        return None
    return editor


def step_2_5_open_debtor_editor(win, result: Result):
    """2.5 Keep the Order tab open, click New Contact, wait for the editor."""
    step = Step("2.5", "open New Debtor editor")
    existing = ui.debtor_editor(win)
    if existing is not None:
        step.ok = True
        step.detail = "reused the open Debtor editor"
        result.steps.append(step)
        return existing

    step.layer = _layer_of(find_control("nav.new_contact", Scope(win)))
    actions.click("nav.new_contact", Scope(win))
    editor = actions.wait_ready(
        lambda: debtor_ready(win), "the New Debtor editor and its fields",
        timeout=config.EDITOR_TIMEOUT,
    )
    step.ok = True
    step.detail = "opened"
    result.steps.append(step)
    return editor


def _write(scope: Scope, key: str, value, step: Step) -> bool:
    """Set one field and fold the outcome into `step`."""
    step.layer = _layer_of(find_control(key, scope))
    written = actions.set_value(key, value, scope)
    if not written.ok:
        step.detail = written.detail
    return written.ok


def step_2_6_identity(scope: Scope, result: Result, debtor: dict) -> None:
    """2.6 Leave Customer ID; enter Company, First Name, Last Name; leave
    Salutation at '---' when the document supplies none."""
    cid = Step("2.6a", "leave proposed Customer ID unchanged")
    cid.layer = _layer_of(find_control("debtor.customer_id", scope))
    proposed = actions.read_value(find_control("debtor.customer_id", scope)).strip()
    cid.ok = bool(proposed)
    cid.detail = f"{proposed!r} (untouched)" if proposed else "Customer ID is empty"
    result.steps.append(cid)

    for ref, key, value, label in (
        ("2.6b", "debtor.company", debtor.get("company"), "Company"),
        ("2.6c", "debtor.first_name", debtor.get("first_name"), "First Name"),
        ("2.6d", "debtor.last_name", debtor.get("last_name"), "Last Name"),
    ):
        step = Step(ref, f"set {label}")
        if not value:
            # Absent is a legitimate reading of the document, not a failure.
            step.ok = True
            step.detail = "not supplied by the document; left blank"
            result.steps.append(step)
            continue
        step.ok = _write(scope, key, value, step)
        if step.ok:
            step.detail = repr(value)
        result.steps.append(step)

    sal = Step("2.6e", "Salutation")
    sal.layer = _layer_of(find_control("debtor.salutation", scope))
    current = actions.read_value(find_control("debtor.salutation", scope))
    wanted = debtor.get("salutation")
    if not wanted:
        # Spec 2.6: leave it as '---' rather than inventing one.
        sal.ok = current == config.SALUTATION_NONE
        sal.detail = (
            f"none supplied; left as {current!r}" if sal.ok
            else f"none supplied but the field reads {current!r}, not {config.SALUTATION_NONE!r}"
        )
    else:
        sal.ok = _write(scope, "debtor.salutation", wanted, sal)
        if sal.ok:
            sal.detail = repr(wanted)
    result.steps.append(sal)


def step_2_7_main_address(scope: Scope, result: Result, address: dict) -> None:
    """2.7 Main address: Street, ZIP, City, Country, E-Mail, Telephone.

    additional name / Address specification / district are filled only when the
    source supplies them - writing a blank into an optional field is not the
    same as leaving it alone, and the spec asks for the latter.
    """
    # SWT realises only the visible tab's controls, so a field on a hidden tab
    # is not merely off-screen - it does not exist to UIA.
    _open_tab(scope, "debtor.tab_addresses")

    required = (
        ("2.7a", "debtor.street", "street", "Street"),
        ("2.7b", "debtor.zip", "zip", "ZIP"),
        ("2.7c", "debtor.city", "city", "City"),
        ("2.7d", "debtor.country", "country", "Country"),
        ("2.7e", "debtor.email", "email", "E-Mail"),
        ("2.7f", "debtor.telephone", "phone", "Telephone"),
    )
    for ref, key, source_key, label in required:
        step = Step(ref, f"set {label}")
        value = address.get(source_key)
        if not value:
            step.ok = True
            step.detail = "not supplied by the document; left blank"
            result.steps.append(step)
            continue
        step.ok = _write(scope, key, value, step)
        if step.ok:
            step.detail = repr(value)
        result.steps.append(step)

    optional = (
        ("2.7g", "debtor.additional_name", "additional_name", "additional name"),
        ("2.7h", "debtor.address_specification", "address_specification", "Address specification"),
        ("2.7i", "debtor.district", "district", "district"),
    )
    for ref, key, source_key, label in optional:
        value = address.get(source_key)
        step = Step(ref, f"optional {label}")
        if not value:
            step.ok = True
            step.verified = True
            step.detail = "absent in the document; deliberately not touched"
            result.steps.append(step)
            continue
        step.ok = _write(scope, key, value, step)
        if step.ok:
            step.detail = repr(value)
        result.steps.append(step)


def _same_address(a: dict, b: dict | None) -> bool:
    """Do billing and delivery describe the same place? (spec 2.8)"""
    if not b:
        return False
    keys = ("street", "zip", "city", "country", "additional_name")
    norm = lambda d, k: " ".join((d.get(k) or "").split()).casefold()
    return all(norm(a, k) == norm(b, k) for k in keys)


def _open_tab(scope: Scope, key: str) -> None:
    actions.click(key, scope)
    import time
    time.sleep(config.SETTLE * 2)


def step_2_8_address_roles(win, scope: Scope, result: Result, debtor: dict) -> None:
    """2.8 Give the Main address the Invoice address role, and the Delivery
    role too when billing and delivery are the same place."""
    _open_tab(scope, "debtor.tab_addresses")
    actions.click("debtor.address_type_open", scope)
    import time
    time.sleep(config.SETTLE * 2)

    # The role popup lives outside the editor pane, so it resolves in MAIN.
    main = Scope(win)

    inv = Step("2.8a", "Main address -> Invoice address role")
    inv.layer = _layer_of(find_control("debtor.role_invoice", main))
    written = actions.set_checkbox("debtor.role_invoice", True, main)
    inv.ok = written.ok
    inv.detail = "ticked" if written.ok else written.detail
    result.steps.append(inv)

    identical = _same_address(debtor["billing_address"], debtor.get("delivery_address"))
    del_step = Step("2.8b", "Delivery address role")
    del_step.layer = _layer_of(find_control("debtor.role_delivery", main))
    written = actions.set_checkbox("debtor.role_delivery", identical, main)
    del_step.ok = written.ok
    if not written.ok:
        del_step.detail = written.detail
    elif identical:
        del_step.detail = "billing and delivery are identical; role also assigned"
    else:
        # Not a failure: the spec only says to double up when they match. A
        # differing delivery address needs its own address record, which is
        # outside 2.8 - flag it rather than silently dropping it.
        dl = debtor.get("delivery_address") or {}
        del_step.detail = (
            "left unticked - delivery differs from billing "
            f"({dl.get('street')!r} vs {debtor['billing_address'].get('street')!r}); "
            "it needs a second address record, which spec 2.8 does not cover"
        )
    result.steps.append(del_step)


def step_2_9_miscellaneous(scope: Scope, result: Result, debtor: dict) -> None:
    """2.9 Alias name, Discount 0%, Net or Gross -> Net."""
    _open_tab(scope, "debtor.tab_miscellaneous")

    alias = Step("2.9a", "set Alias name")
    value = debtor.get("alias")
    if not value:
        alias.ok = True
        alias.detail = "not supplied by the document; left blank"
    else:
        alias.ok = _write(scope, "debtor.alias", value, alias)
        if alias.ok:
            alias.detail = repr(value)
    result.steps.append(alias)

    disc = Step("2.9b", "set Discount to 0%")
    disc.layer = _layer_of(find_control("debtor.discount", scope))
    written = actions.set_value("debtor.discount", config.DEBTOR_DISCOUNT, scope)
    disc.ok = written.ok
    disc.detail = repr(written.read_back) if written.ok else written.detail
    result.steps.append(disc)

    ng = Step("2.9c", f"set Net or Gross to {config.PRICE_MODE_NET}")
    ng.layer = _layer_of(find_control("debtor.net_or_gross", scope))
    written = actions.set_value("debtor.net_or_gross", config.PRICE_MODE_NET, scope)
    ng.ok = written.ok
    ng.detail = f"combo reads {written.read_back!r}" if written.ok else written.detail
    result.steps.append(ng)


def step_2_10_payment(scope: Scope, result: Result, method: str | None) -> Resolution | None:
    """2.10 Select the exact Payment Method, or report that it must be created.

    The terms-of-payment list is another opaque NatTable, but this combo's
    option list answers the same question and is readable - so the tri-state
    decision is made on real data rather than on pixels.
    """
    step = Step("2.10", "select Payment Method")
    if not method:
        step.detail = "the document states no payment method"
        result.steps.append(step)
        return None

    resolved = find_control("debtor.payment", scope)
    step.layer = _layer_of(resolved)
    options = actions.combo_options(resolved)

    selected: list[str] = []
    resolution = resolve_or_create(
        "Payment Method", method, options,
        select=lambda opt: selected.append(opt),
        create=None,              # creation is 2.10.1-2.10.5, a separate screen
        allow_create=False,
    )

    if resolution.outcome is Outcome.SELECTED:
        written = actions.set_value("debtor.payment", selected[0], scope)
        step.ok = written.ok
        step.detail = (f"combo reads {written.read_back!r}" if written.ok else written.detail)
    else:
        step.detail = (
            f"{method!r} is not among the existing methods {options}; "
            "spec 2.10.1-2.10.5 must create it first"
        )
    result.steps.append(step)
    return resolution


def step_2_11_save(win, result: Result) -> None:
    """2.11 Save the Debtor once. Writes to the database."""
    step = Step("2.11", "save the Debtor once")
    resolved = find_control("toolbar.save", Scope(win))
    step.layer = _layer_of(resolved)
    if not resolved.control.IsEnabled:
        step.detail = "Save is disabled - the editor reports no unsaved changes"
        result.steps.append(step)
        return

    actions.click("toolbar.save", Scope(win))
    try:
        actions.wait_ready(
            lambda: not find_control("toolbar.save", Scope(win)).control.IsEnabled,
            "Save to go disabled (Debtor written)", timeout=12.0,
        )
        step.ok = True
        step.detail = "saved once; Save is disabled again"
    except UIError as exc:
        step.detail = f"clicked Save but unsaved changes remain ({exc})"
    result.steps.append(step)


def select_payment_after_creation(order: dict) -> Result:
    """2.10.6 tail + 2.11: return to the Debtor, pick the new method, save."""
    result = Result()
    win = ui.window()
    ui.activate(win)

    editor = ui.activate_editor(win, "New Debtor", ui.debtor_editor)
    if editor is None:
        raise UIError("the New Debtor editor is not open")
    scope = Scope(win, debtor=editor)

    with ui.Clipboard():
        step_2_10_payment(scope, result, order.get("payment", {}).get("method"))
        if result.ok:
            step_2_11_save(win, result)
        else:
            skipped = Step("2.11", "save the Debtor once")
            skipped.detail = "skipped: the Payment Method did not verify, so nothing was written"
            result.steps.append(skipped)
    return result


def _order_address_text(win) -> str:
    """The address box on the Order, which is where a selection shows up."""
    editor = ui.activate_editor(win, "New Order", ui.order_editor)
    if editor is None:
        return ""
    for c in ui.find_all(editor, lambda c: c.ControlTypeName == "EditControl"):
        r = c.BoundingRectangle
        if r.height() > 30 and r.top < 500:
            return ui.legacy_value(c) or ""
    return ""


def steps_2_12_2_13_select_in_order(order: dict, result: Result) -> None:
    """2.12 Reopen Select the address, pick the Debtor, OK. 2.13 Confirm.

    The grid is a canvas-drawn NatTable: no rows in UIA, nothing on the
    clipboard. So the row is clicked at an offset inside the grid's *own*
    runtime rectangle, and - because that is a guess - the selection is proved
    afterwards by reading the address the Order now shows. The verification,
    not the click, is what makes this safe.
    """
    win = ui.window()
    ui.activate(win)
    debtor = order["debtor"]
    editor = ui.activate_editor(win, "New Order", ui.order_editor)
    scope = Scope(win, editor)

    step = Step("2.12", "reopen Select the address and pick the Debtor")
    resolved = find_control("order.address_pick_contact", scope)
    step.layer = _layer_of(resolved)
    if resolved.layer.name != "TOOLTIP":
        # The lower icon starts a new Debtor. Position must not decide this.
        step.detail = (
            "the contact icon could not be confirmed by tooltip; refusing to "
            "click it, because the neighbouring icon creates a new Debtor"
        )
        result.steps.append(step)
        return

    actions.click("order.address_pick_contact", scope)
    dlg = actions.wait_ready(lambda: ui.address_dialog(win), "the address chooser", timeout=12.0)
    dscope = Scope(win, editor, dialog=dlg)

    actions.set_value("addr_dialog.search", debtor["company"], dscope)
    time.sleep(config.SETTLE * 4)      # let the filter settle

    grid = min(
        (p for p in ui.find_all(dlg, lambda c: c.ControlTypeName == "PaneControl", 16)
         if p.BoundingRectangle.height() > 300),
        key=lambda p: p.BoundingRectangle.height(),
    )
    g = grid.BoundingRectangle
    auto.Click(g.left + 120, g.top + config.GRID_FIRST_ROW_DY)
    time.sleep(config.SETTLE * 2)
    actions.click("addr_dialog.ok", dscope)
    actions.wait_ready(lambda: ui.address_dialog(win) is None, "the chooser to close", timeout=10.0)
    step.ok = True
    step.detail = f"searched {debtor['company']!r}, selected row 1, clicked OK"
    result.steps.append(step)

    check = Step("2.13", "confirm the Order's address populated correctly")
    text = _order_address_text(win)
    if not text.strip():
        check.detail = (
            "the Order's address is still empty - no row was selected. The grid "
            "publishes no rows, so this is the only signal available."
        )
        result.steps.append(check)
        return

    want = [v for v in (debtor.get("company"), debtor["billing_address"].get("street"),
                        debtor["billing_address"].get("zip"),
                        debtor["billing_address"].get("city")) if v]
    missing = [v for v in want if v.casefold() not in text.casefold()]
    check.ok = not missing
    check.detail = (
        f"address shows {text.strip()!r}" if check.ok
        else f"address {text.strip()!r} is missing {missing}"
    )
    result.steps.append(check)


def create_debtor(order: dict) -> Result:
    """Run spec 2.5-2.7. Saves nothing - 2.11 is out of scope here."""
    result = Result()
    win = ui.window()
    ui.activate(win)
    debtor = order["debtor"]

    with ui.Clipboard():
        editor = step_2_5_open_debtor_editor(win, result)
        scope = Scope(win, debtor=editor)
        step_2_6_identity(scope, result, debtor)
        step_2_7_main_address(scope, result, debtor["billing_address"])
        step_2_8_address_roles(win, scope, result, debtor)
        step_2_9_miscellaneous(scope, result, debtor)
        step_2_10_payment(scope, result, order.get("payment", {}).get("method"))

    return result


