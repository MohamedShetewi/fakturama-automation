"""Spec 3.4-3.6: reuse or create the VAT rate an item needs.

3.5's reuse test is deliberately strict - Name, Value *and* the E-Invoice code
must all agree - because a VAT record that merely looks right books the wrong
tax. The list is readable via Ctrl+C, so that comparison is made on real rows
rather than inferred.
"""

from __future__ import annotations

import time

from . import actions, config, ui
from .entities import Verdict, classify
from .order_form import Result, Step, _layer_of
from .resolver import Scope, find_control
from .ui import UIError

# The VATs list copies as: standard-flag, Name, Description, Value
VAT_COL = {"standard": 0, "name": 1, "description": 2, "value": 3}


def vat_editor(win):
    panes = ui.find_all(
        win,
        lambda c: c.ControlTypeName == "PaneControl"
        and (c.Name or "").lstrip("*") == config.VAT_EDITOR_TITLE,
    )
    if not panes:
        return None
    return max(panes, key=lambda p: p.BoundingRectangle.width() * p.BoundingRectangle.height())


def vat_list(win):
    panes = ui.find_all(win, lambda c: c.ControlTypeName == "PaneControl" and c.Name == "VATs")
    return max(panes, key=lambda p: p.BoundingRectangle.height()) if panes else None


def step_3_4_open_list(win, result: Result):
    """3.4 Open Data > VATs before creating anything."""
    step = Step("3.4", "open Data > VATs")
    step.layer = _layer_of(find_control("nav.vats", Scope(win)))
    actions.click("nav.vats", Scope(win))
    actions.wait_ready(lambda: vat_list(win), "the VATs list", timeout=config.EDITOR_TIMEOUT)
    step.ok = True
    result.steps.append(step)


def step_3_5_reuse_or_none(win, result: Result, name: str, percent: str) -> Verdict:
    """3.5 Reuse an existing row only if Name, Value and code all agree."""
    step = Step("3.5", f"look for an exact {name!r}")
    rows = ui.grid_rows(vat_list(win))
    verdict, matches = classify(
        rows, name, key=lambda r: r[VAT_COL["name"]] if len(r) > VAT_COL["name"] else ""
    )

    if verdict is Verdict.UNIQUE:
        row = matches[0]
        got = (row[VAT_COL["value"]] if len(row) > VAT_COL["value"] else "").strip()
        # '19.0' and '19' and '19%' all mean the same rate.
        want = percent.rstrip("%")
        same = got.rstrip("%").rstrip("0").rstrip(".") == want.rstrip("0").rstrip(".")
        if same:
            step.ok = True
            step.detail = f"reusing {row[:3]}"
        else:
            step.detail = (
                f"a VAT named {name!r} exists but its Value is {got!r}, not {percent!r}; "
                "stopping for manual review rather than booking the wrong rate"
            )
            verdict = Verdict.CONFLICT
    elif verdict is Verdict.NONE:
        step.ok = True
        step.detail = f"no exact {name!r} among {[r[VAT_COL['name']] for r in rows if r]}; will create"
    else:
        step.detail = f"{len(matches)} rows named {name!r}; stopping for manual review"
    result.steps.append(step)
    return verdict


def step_3_6_create(win, result: Result, name: str, percent: str) -> None:
    """3.6 Create the VAT: Name and Description, code stays S, Value set,
    Standard left alone, saved once."""
    new = Step("3.6a", "open the new tax rate editor")
    new.layer = _layer_of(find_control("vat.list_new", Scope(win)))
    actions.click("vat.list_new", Scope(win))
    editor = actions.wait_ready(lambda: vat_editor(win), "the New TAX Rate editor",
                                timeout=config.EDITOR_TIMEOUT)
    new.ok = True
    result.steps.append(new)

    scope = Scope(win, vat=editor)

    for ref, key, label in (("3.6b", "vat.name", "Name"),
                            ("3.6c", "vat.description", "Description")):
        step = Step(ref, f"set {label}")
        step.layer = _layer_of(find_control(key, scope))
        written = actions.set_value(key, name, scope)
        step.ok = written.ok
        step.detail = repr(written.read_back) if written.ok else written.detail
        result.steps.append(step)

    code = Step("3.6d", f"VAT code stays {config.VAT_CODE_STANDARD!r}")
    resolved = find_control("vat.code", scope)
    code.layer = _layer_of(resolved)
    current = actions.read_value(resolved)
    if current.strip() == config.VAT_CODE_STANDARD:
        # Already correct: 'keep' means leave alone, not re-select.
        code.ok = True
        code.detail = f"already {current.strip()!r}; untouched"
    else:
        written = actions.set_value("vat.code", config.VAT_CODE_STANDARD, scope)
        code.ok = written.ok
        code.detail = f"set to {written.read_back!r}" if written.ok else written.detail
    result.steps.append(code)

    val = Step("3.6e", "set Value")
    val.layer = _layer_of(find_control("vat.value", scope))
    written = actions.set_value("vat.value", percent, scope)
    val.ok = written.ok
    val.detail = repr(written.read_back) if written.ok else written.detail
    result.steps.append(val)

    std = Step("3.6f", "leave the displayed Standard VAT unchanged")
    std.ok = True
    std.detail = "'Set as standard' catalogued but never clicked"
    result.steps.append(std)

    save = Step("3.6g", "save once")
    if not result.ok:
        save.detail = "skipped: an earlier step did not verify, so nothing was written"
        result.steps.append(save)
        return
    actions.click("toolbar.save", Scope(win))
    try:
        actions.wait_ready(
            lambda: not find_control("toolbar.save", Scope(win)).control.IsEnabled,
            "Save to go disabled (tax rate written)", timeout=12.0,
        )
        save.ok = True
        save.detail = "saved once; Save is disabled again"
    except UIError as exc:
        save.detail = f"clicked Save but unsaved changes remain ({exc})"
    result.steps.append(save)


def ensure_vat(name: str, percent: str) -> Result:
    """Run 3.4-3.6 for one required VAT rate."""
    result = Result()
    win = ui.window()
    ui.activate(win)

    with ui.Clipboard():
        step_3_4_open_list(win, result)
        verdict = step_3_5_reuse_or_none(win, result, name, percent)
        if verdict is Verdict.NONE:
            step_3_6_create(win, result, name, percent)
    return result
