"""Spec 3.4-3.6: reuse or create the VAT rate an item needs.

3.5 keys the reuse test on the *rate*, not on the name. An earlier draft
matched the Name exactly and it was wrong twice over against the live
database, which holds:

    Tax-free    Free of Tax    0.0
    MwSt. 19%   null           0.19

Names are whatever the bookkeeper typed - German here - so searching for
'VAT 19%' finds nothing and creates a duplicate 19% rate. And the list stores
rates as *fractions* while the editor and the extracted order speak percent,
so the old string comparison pitted '0.19' against '19' and reported a
conflict on the one row that was actually correct.

The rate is the only part of a VAT record that changes what gets booked, so
that is what identifies it. Two rows at the same rate is still a halt: they
are genuinely different records and nothing here can tell which was meant.
"""

from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation

from . import actions, config, ui
from .entities import Verdict, classify
from .order_form import Result, Step, _layer_of
from .resolver import Scope, find_control
from .ui import UIError

# The VATs list copies as: standard-flag, Name, Description, Value
VAT_COL = {"standard": 0, "name": 1, "description": 2, "value": 3}


def parse_rate(text: str) -> Decimal | None:
    """Read a VAT rate as a fraction, from either notation.

    '19%' and '0.19' are the same rate; '19' on its own is not, because the
    list column is unambiguously a fraction. Decimal throughout - a rate that
    round-trips through float is exactly the kind of silent drift the
    extraction half's gate exists to catch.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    percent = raw.endswith("%")
    body = raw.rstrip("%").strip().replace(",", ".")
    try:
        value = Decimal(body)
    except InvalidOperation:
        return None
    return value / 100 if percent else value


def canonical_rate(rate: Decimal | None) -> str:
    """A comparable spelling. 'f' rather than str() so 1E+2 never appears."""
    return "" if rate is None else f"{rate.normalize():f}"


def _cell(row, column: str) -> str:
    index = VAT_COL[column]
    return (row[index] if len(row) > index else "").strip()


def _row_rate(row) -> str:
    return canonical_rate(parse_rate(_cell(row, "value")))


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


def vat_percentages(win) -> dict[str, Decimal]:
    """Every rate the database holds, as Name -> percent.

    The order's Items grid names a line's tax rate but never states it, so
    confirming that a line carries 19% means resolving the name against this.
    Returns percentages, not the fractions the list stores, because that is
    what the source document quotes.
    """
    actions.click("nav.vats", Scope(win))
    actions.wait_ready(lambda: vat_list(win), "the VATs list", timeout=config.EDITOR_TIMEOUT)
    read = ui.grid_read(vat_list(win))
    if not read.trustworthy:
        raise UIError(f"could not read the VATs list ({read.how})")
    out = {}
    for row in read.rows:
        rate = parse_rate(_cell(row, "value"))
        name = _cell(row, "name")
        if name and rate is not None:
            out[name] = rate * 100
    return out


def step_3_4_open_list(win, result: Result):
    """3.4 Open Data > VATs before creating anything."""
    step = Step("3.4", "open Data > VATs")
    step.layer = _layer_of(find_control("nav.vats", Scope(win)))
    actions.click("nav.vats", Scope(win))
    actions.wait_ready(lambda: vat_list(win), "the VATs list", timeout=config.EDITOR_TIMEOUT)
    step.ok = True
    result.steps.append(step)


def step_3_5_reuse_or_none(win, result: Result, name: str, percent: str) -> tuple[Verdict, str]:
    """3.5 Reuse the existing rate if there is exactly one at this percentage.

    Returns the verdict and the Name to use downstream - the stored one when
    reusing, the requested one when creating - because the order line has to
    name the rate it books against.
    """
    step = Step("3.5", f"look for an existing {percent} rate")
    want = parse_rate(percent)
    if want is None:
        step.detail = f"{percent!r} is not a readable VAT rate"
        result.steps.append(step)
        return Verdict.CONFLICT, name

    read = ui.grid_read(vat_list(win))
    if not read.trustworthy:
        # An unreadable list is not an empty one. Treating it as empty here
        # would create a second 19% rate alongside the one already there, and
        # from then on every lookup is ambiguous.
        step.detail = (
            f"could not read the VATs list ({read.how}); refusing to conclude "
            "the rate is missing"
        )
        result.steps.append(step)
        return Verdict.CONFLICT, name

    rows = [r for r in read.rows if r]
    verdict, matches = classify(rows, canonical_rate(want), key=_row_rate)
    listed = [(_cell(r, "name"), _cell(r, "value")) for r in rows]
    chosen = name

    if verdict is Verdict.UNIQUE:
        row = matches[0]
        chosen = _cell(row, "name")
        step.ok = True
        step.detail = f"reusing {chosen!r} ({_cell(row, 'value')})"
        if chosen.casefold() != name.casefold():
            # Worth saying out loud: the record is right, its label just is not
            # the one the caller asked for, and that name goes on the invoice.
            step.detail += f" - stored under a different name than the requested {name!r}"
    elif verdict is Verdict.NONE:
        step.ok = True
        step.detail = f"no {percent} rate among {listed}; will create {name!r}"
    else:
        step.detail = (
            f"{len(matches)} rates at {percent} ({[_cell(m, 'name') for m in matches]}); "
            "stopping for manual review rather than picking one"
        )
    result.steps.append(step)
    return verdict, chosen


def step_3_6_create(win, result: Result, name: str, percent: str) -> None:
    """3.6 Create the VAT: Name and Description, code stays S, Value set,
    Standard left alone, saved once."""
    new = Step("3.6a", "open the new tax rate editor")
    editor = vat_editor(win)
    if editor is not None:
        # Clicking again would open a *second* editor, and Eclipse stacks them
        # happily - a previous run left two '*New TAX Rate' tabs behind that
        # way, which then made "did the save land?" unanswerable.
        new.ok = True
        new.detail = "an editor was already open; reusing it instead of stacking another"
    else:
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
    # Ctrl+S on this editor, not the toolbar button. The button applies to
    # whatever Eclipse considers active, which with several dirty editors open
    # is not reliably the one just filled - measured on the product editor, it
    # left the work unsaved while Ctrl+S wrote it at once.
    actions.save_editor(editor)

    # Nor is the toolbar Save button an oracle. It reflects the *active*
    # editor, and a run leaves several dirty ones open, so it can stay enabled
    # long after this tax rate is safely written. The record itself is the
    # proof: go back to the list and look for the row.
    want = canonical_rate(parse_rate(percent))
    actions.click("nav.vats", Scope(win))
    try:
        actions.wait_ready(lambda: vat_list(win), "the VATs list",
                           timeout=config.EDITOR_TIMEOUT)
        row = actions.wait_ready(
            lambda: next((r for r in ui.grid_rows(vat_list(win))
                          if r and _row_rate(r) == want and _cell(r, "name") == name), None),
            f"{name!r} to appear in the VATs list", timeout=12.0,
        )
        save.ok = True
        save.detail = f"written: {[_cell(row, c) for c in ('name', 'description', 'value')]}"
    except UIError as exc:
        save.detail = f"clicked Save but the rate is not in the list ({exc})"
    result.steps.append(save)


def ensure_vat(name: str, percent: str) -> tuple[Result, str | None]:
    """Run 3.4-3.6 for one required VAT rate.

    `name` is the label to use if the rate has to be created; `percent` is what
    identifies it. Returns the step log and the Name the order line should book
    against - None when the run halted and nothing can be booked.
    """
    result = Result()
    win = ui.window()
    ui.activate(win)

    with ui.Clipboard():
        step_3_4_open_list(win, result)
        verdict, chosen = step_3_5_reuse_or_none(win, result, name, percent)
        if verdict is Verdict.NONE:
            step_3_6_create(win, result, name, percent)
    return result, (chosen if result.ok else None)
