"""Spec 1.3-1.7: open a New Order and fill its header from the extracted JSON.

The steps read as the spec does, because every mechanism lives elsewhere:
controls come from the catalog via find_control, values go in through
set_value, readiness is a polled UIA state. Nothing here names a widget, waits
a fixed number of seconds, or knows what a CDateTime is.

Every step verifies itself. A step that cannot be verified says so rather than
reporting success - the same posture as the reconciliation gate in the
extraction half, which refuses to write numbers it cannot prove.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import actions, config, ui
from .resolver import Scope, find_control, present
from .ui import UIError


@dataclass
class Step:
    """One spec step and what actually happened."""

    ref: str
    what: str
    ok: bool = False
    detail: str = ""
    verified: bool = True   # False => done, but the UI offers no way to confirm
    layer: str = ""         # which resolver layer found the control

    def __str__(self) -> str:
        mark = "ok " if self.ok else "FAIL"
        if self.ok and not self.verified:
            mark = "set"
        line = f"  [{mark}] {self.ref} {self.what}"
        if self.detail:
            line += f": {self.detail}"
        if self.layer:
            line += f"  [{self.layer}]"
        return line


@dataclass
class Result:
    steps: list[Step] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps)

    @property
    def failures(self) -> list[Step]:
        return [s for s in self.steps if not s.ok]


def _layer_of(resolved) -> str:
    return f"L{int(resolved.layer)} {resolved.layer.name.lower()}"


# --- readiness conditions ----------------------------------------------------


def editor_ready(win) -> object | None:
    """The New Order editor exists *and* its fields are realised.

    The pane appears before its children do; asking for the editor alone would
    hand back a shell whose fields are not there yet.
    """
    editor = ui.order_editor(win)
    if editor is None:
        return None
    scope = Scope(win, editor)
    return editor if present("order.cust_ref", scope) else None


def save_enabled(win) -> bool:
    """Save goes live once the editor has unsaved changes - a real UIA state,
    which is what the flow polls instead of sleeping."""
    resolved = find_control("toolbar.save", Scope(win), required=False)
    return bool(resolved and resolved.control.IsEnabled)


# --- steps -------------------------------------------------------------------


def step_1_3_open_editor(win, result: Result, *, allow_existing: bool = False):
    """1.3 Click Order in the top toolbar and wait for the New Order editor."""
    step = Step("1.3", "open New Order editor")
    existing = ui.order_editor(win)

    if existing is not None and not allow_existing and ui.is_dirty(win):
        step.detail = (
            "a New Order editor is already open with unsaved changes. Clicking "
            "Order reuses it, so the run would write into a half-filled form. "
            "Close it, or pass --allow-existing."
        )
        result.steps.append(step)
        raise UIError(step.detail)

    how = actions.click("toolbar.new_order", Scope(win))
    editor = actions.wait_ready(
        lambda: editor_ready(win), "the New Order editor and its fields",
        timeout=config.EDITOR_TIMEOUT,
    )
    step.ok = True
    step.detail = ("reused the open editor" if existing is not None else "opened") + f" ({how})"
    step.layer = _layer_of(find_control("toolbar.new_order", Scope(win)))
    result.steps.append(step)
    return editor


def step_1_4_keep_number(scope: Scope, result: Result) -> str:
    """1.4 Leave the automatically proposed No. unchanged - read, never write."""
    step = Step("1.4", "leave proposed No. unchanged")
    resolved = find_control("order.number", scope)
    step.layer = _layer_of(resolved)
    number = actions.read_value(resolved).strip()
    if not number:
        step.detail = "the No. field is empty; Fakturama proposes one, so this is wrong"
    else:
        step.ok = True
        step.detail = f"{number!r} (untouched)"
    result.steps.append(step)
    return number


def step_1_5_set_date(scope: Scope, result: Result, order_date: str | None) -> None:
    """1.5 Set Date to the extracted Order Date."""
    step = Step("1.5", "set Date")
    if not order_date:
        step.detail = "the extraction produced no order_date"
        result.steps.append(step)
        return

    step.layer = _layer_of(find_control("order.date", scope))
    target = datetime.strptime(order_date, "%Y-%m-%d").date()
    written = actions.set_value("order.date", target, scope)
    step.ok = written.ok
    step.detail = f"{written.read_back!r} == {order_date}" if written.ok else written.detail
    result.steps.append(step)


def step_1_6_set_custref(scope: Scope, result: Result, reference: str | None) -> None:
    """1.6 Enter the extracted External Reference in Cust.Ref."""
    step = Step("1.6", "set Cust.Ref.")
    if not reference:
        step.detail = "the extraction produced no external_reference"
        result.steps.append(step)
        return

    step.layer = _layer_of(find_control("order.cust_ref", scope))
    written = actions.set_value("order.cust_ref", reference, scope)
    step.ok = written.ok
    # Character-exact matters: this is the customer's own reference, and a
    # reformatted one silently breaks their matching.
    step.detail = repr(written.read_back) if written.ok else written.detail
    result.steps.append(step)


def step_1_7_price_mode_and_vat(scope: Scope, result: Result) -> None:
    """1.7 Set the document price mode to Net and keep VAT as With VAT."""
    price = Step("1.7a", f"price mode -> {config.PRICE_MODE_NET}")
    price.layer = _layer_of(find_control("order.price_mode", scope))
    written = actions.set_value("order.price_mode", config.PRICE_MODE_NET, scope)

    # Two independent confirmations: the combo reports its own selection, and
    # choosing Net renames the totals field from 'Total Gross' to 'Total Net'.
    # The rename is proof of application state rather than of widget state, so
    # it is worth keeping even now that the combo can be read directly.
    renamed = False
    try:
        actions.wait_ready(
            lambda: present("order.total_net", scope) and not present("order.total_gross", scope),
            "the totals field to become 'Total Net'", timeout=5.0,
        )
        renamed = True
    except UIError:
        pass

    price.ok = written.ok and renamed
    if price.ok:
        price.detail = f"combo reads {written.read_back!r}; totals field renamed to 'Total Net'"
    elif not written.ok:
        price.detail = written.detail
    else:
        price.detail = f"combo reads {written.read_back!r} but the totals field was not renamed"
    result.steps.append(price)

    vat = Step("1.7b", f"VAT stays {config.VAT_WITH!r}")
    resolved = find_control("order.vat_mode", scope)
    vat.layer = _layer_of(resolved)
    options = actions.combo_options(resolved)
    if config.VAT_WITH not in options:
        vat.detail = f"no {config.VAT_WITH!r} option; found {options}"
        result.steps.append(vat)
        return
    written = actions.set_value("order.vat_mode", config.VAT_WITH, scope)
    vat.ok = written.ok
    vat.verified = bool(written.read_back)
    vat.detail = (
        f"combo reads {written.read_back!r}" if written.read_back else written.detail
    )
    result.steps.append(vat)


# --- orchestration -----------------------------------------------------------


def fill_header(order: dict, *, allow_existing: bool = False) -> Result:
    """Run spec 1.3-1.7 against the running Fakturama."""
    result = Result()
    win = ui.window()
    ui.activate(win)

    with ui.Clipboard():
        editor = step_1_3_open_editor(win, result, allow_existing=allow_existing)
        scope = Scope(win, editor)
        step_1_4_keep_number(scope, result)
        step_1_5_set_date(scope, result, order.get("order_date"))
        step_1_6_set_custref(scope, result, order.get("external_reference"))
        step_1_7_price_mode_and_vat(scope, result)

    return result
