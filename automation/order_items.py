"""Reading the order's own Items grid.

This is the only place that can answer "what is actually on the order", and
every later step depends on it: the product chooser accepts a selection by
closing itself, without telling anyone what it added, so the row it created is
the only evidence. Quantity, discount and the row total are verified here too,
for the same reason - the widgets that hold them publish nothing to UIA.

The grid is editable, which makes it select by cell rather than by row, so it
needs the row-header click that ui.item_rows performs. Its VAT column is not a
name but a serialised object, so the name has to be dug out of it.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from decimal import Decimal

import uiautomation as auto

from . import config, ui
from .actions import Written, _set_text, parse_money
from .resolver import Resolved
from .selectors import Input, Layer, Screen, Target

log = logging.getLogger("automation.order_items")

# 'VAT taxValue: [0.19] ... description: [null] name: [MwSt. 19%] dateAdded: ...'
_VAT_NAME = re.compile(r"\bname:\s*\[(?P<name>[^\]]*)\]")


def vat_name(cell: str) -> str | None:
    """The rate's Name out of the serialised VAT the column actually holds.

    Returns None rather than a guess: booking against the wrong tax rate is
    exactly the error the whole select-or-create branch exists to prevent, so
    an unparseable cell must not quietly become a plausible one.
    """
    match = _VAT_NAME.search(cell or "")
    if not match:
        return None
    name = match.group("name").strip()
    return None if not name or name == "null" else name


def cell(row: list[str], column: str) -> str:
    index = config.ITEM_COL[column]
    return (row[index] if len(row) > index else "").strip()


@dataclass(frozen=True)
class Item:
    """One line of the order, as the grid reports it."""

    sku: str
    name: str
    quantity: Decimal | None
    unit_price: Decimal | None
    discount: Decimal | None
    total: Decimal | None
    vat: str | None

    def __str__(self) -> str:
        return (f"{self.quantity} x {self.sku} @ {self.unit_price} "
                f"less {self.discount} = {self.total} [{self.vat}]")


def _number(text: str) -> Decimal | None:
    """A bare number, as opposed to an amount. '0.0' is a real discount."""
    return parse_money(text)


def to_item(row: list[str]) -> Item:
    return Item(
        sku=cell(row, "sku"),
        name=cell(row, "name"),
        quantity=_number(cell(row, "quantity")),
        unit_price=parse_money(cell(row, "unit_price")),
        discount=_number(cell(row, "discount")),
        total=parse_money(cell(row, "total")),
        vat=vat_name(cell(row, "vat")),
    )


def read(editor) -> tuple[list[Item], str]:
    """Every line on the order, and whether the read can be trusted.

    The trust flag is passed straight through rather than folded away: an
    unreadable grid must never be mistaken for an empty order, because the
    step after this one decides what to add based on what is already there.
    """
    got = ui.item_rows(editor)
    return [to_item(r) for r in got.rows if r], got.how


def find(items: list[Item], sku: str) -> list[Item]:
    """Every line booked against `sku`. More than one is not a match."""
    want = (sku or "").strip().casefold()
    return [i for i in items if i.sku.strip().casefold() == want]


def discount_as_percent(stored: str) -> str:
    """The allowance in the units its editor speaks.

    Three spellings of one number, all seen on the same cell: the source says
    10, the cell editor renders '10.00 %', and the grid copies '-0.1' - signed,
    because an allowance is a negative adjustment, and fractional because that
    is how it is stored. Comparing across those without converting reports a
    correct write as a failure, which is how a working discount looked broken.
    """
    value = parse_money(stored)
    return "" if value is None else f"{(-value * 100).normalize():f}"


def index_of(rows: list[list[str]], sku: str) -> int | None:
    """Which row holds `sku`, when exactly one does."""
    hits = [i for i, r in enumerate(rows) if cell(r, "sku").casefold() == sku.casefold()]
    return hits[0] if len(hits) == 1 else None


# --- writing -----------------------------------------------------------------


def _cell_editors(editor, grid_rect) -> list:
    """Every cell editor currently open inside the grid.

    A NatTable cell is painted, not published - until it is being edited, at
    which point SWT creates a real Text over it. That control is the write
    target, so the coordinate only has to be good enough to *open* the editor;
    the value goes in through a published control and is read back from it,
    exactly like every other field.

    Returned as a list rather than "the one", because more than one is a state
    that actually happens and the two cases need different handling: none means
    the cell never opened, several means an earlier edit has not been disposed
    yet and there is no way to tell which box the keystrokes would reach.
    """
    inside = []
    for c in ui.find_all(editor, lambda c: c.ControlTypeName == "EditControl", 14):
        b = c.BoundingRectangle
        if (grid_rect.left <= b.left and b.right <= grid_rect.right
                and grid_rect.top <= b.top and b.bottom <= grid_rect.bottom):
            inside.append(c)
    return inside


def _inline_editor(editor, grid_rect):
    """The one cell editor open inside the grid, or None if that is unclear."""
    open_boxes = _cell_editors(editor, grid_rect)
    return open_boxes[0] if len(open_boxes) == 1 else None


def _close_open_editors(editor, grid_rect, tries: int = 6) -> int:
    """Escape until the grid holds no open cell editor. Returns how many remain.

    Committing with Enter does not dispose the editor synchronously - SWT
    reuses and hides it, and for a short window afterwards the grid reports two
    Text controls. Starting the next cell in that state is what turns a working
    write into "no cell editor opened": the count is not one, so the box cannot
    be identified, and the step gives up on a grid that was about to be fine.
    """
    for _ in range(tries):
        left = _cell_editors(editor, grid_rect)
        if not left:
            return 0
        auto.SendKeys("{Esc}", waitTime=0.1)
        time.sleep(config.SETTLE / 2)
    return len(_cell_editors(editor, grid_rect))


def same_value(got: str, expect: str) -> bool:
    """Do these two spellings name the same cell value?

    Compared as numbers whenever both are numeric, because the grid and its
    editor disagree about formatting: a discount copies as '0.0' and edits as
    '0.00 %'. Those are one cell, and a string comparison would call the
    correct cell the wrong one and refuse to write.
    """
    a, b = parse_money(got), parse_money(expect)
    if a is not None and b is not None:
        return a == b
    return (got or "").strip() == (expect or "").strip()


def open_cell(editor, row: int, column: str, expect: str) -> tuple[object | None, str]:
    """Open one cell for editing, and prove it is the cell that was asked for.

    Only the *first* row's y is measured. Every row after it is reached with
    {Down}, and every column with {Right}, for the same reason in both
    directions: a fixed row height is a guess about a table that re-lays itself
    out. GRID_ROW_HEIGHT was measured on the address grid, and a row that is a
    pixel or two taller here puts row 3's click on row 2 - which the identity
    check below catches, but only as a flat refusal to write. Arrow keys also
    carry the viewport with them, so an order long enough to scroll still
    works; a computed y for row 20 is simply off the bottom of the pane.

    `expect` is what the copied row says the cell already holds. If the opened
    editor disagrees, the navigation landed somewhere else and this refuses
    rather than typing into whatever it found - writing a quantity into the SKU
    column raises nothing at all, it just books a different product.

    Returns the editor control and an empty reason, or None and why not. The
    reason is carried out rather than only logged: "could not open that cell"
    covers four different faults, and a run that stops has to say which.
    """
    # items_grid, not grid_pane: the generic "smallest pane" heuristic picks
    # the Remarks box on a tall order, and a cell editor opened there would
    # take the typed quantity into the wrong widget entirely.
    grid = ui.items_grid(editor)
    r = grid.BoundingRectangle

    # Start from a grid with nothing open. An editor left over from the
    # previous cell makes the one below unidentifiable.
    stale = _close_open_editors(editor, r)
    if stale:
        return None, (f"{stale} cell editor(s) from an earlier edit are still "
                      "open and would not close; refusing to type into a grid "
                      "whose keystrokes could land in either")

    auto.Click(r.left + config.GRID_FIRST_COLUMN_DX,
               r.top + config.GRID_FIRST_ROW_DY)
    time.sleep(config.SETTLE)
    if not ui.focus_is_inside(editor):
        return None, "the Items grid did not take the keyboard when clicked"

    # That click also opens the first column's editor. Close it, so the arrow
    # keys move the *selection* instead of the caret inside a text box.
    _close_open_editors(editor, r)
    for _ in range(row):
        auto.SendKeys("{Down}", waitTime=0.08)
    for _ in range(config.ITEM_COL[column]):
        auto.SendKeys("{Right}", waitTime=0.08)
    time.sleep(config.SETTLE)
    auto.SendKeys("{F2}", waitTime=0.15)
    time.sleep(config.SETTLE)

    open_boxes = _cell_editors(editor, r)
    if len(open_boxes) != 1:
        return None, (f"{len(open_boxes)} cell editors are open on row {row}, "
                      f"column {column!r}; expected exactly one")
    box = open_boxes[0]
    got = (ui.legacy_value(box) or "").strip()
    if not same_value(got, expect):
        # Landed on the wrong cell. Cancel rather than overwrite it.
        auto.SendKeys("{Esc}", waitTime=0.1)
        return None, (f"the cell that opened holds {got!r}, but row {row}'s "
                      f"{column} was copied as {expect!r} - the navigation "
                      "landed somewhere else, so nothing was typed")
    return box, ""


def set_cell(editor, row: int, column: str, value: str, expect: str,
             *, as_written=None) -> Written:
    """Type `value` into one cell, and confirm it on the row afterwards.

    Committed with Enter, not Tab: Tab moves to the next cell and leaves a
    second editor open behind it, which the next read then mistakes for its own.

    The verification deliberately ignores what the editor says on the way out
    and re-reads the grid instead. The editor reformats ('2' becomes '2.00',
    '10' becomes '10.00 %'), and more importantly a value typed but not
    committed still shows in the box - the row is the only place that proves
    the change was taken.

    `as_written` converts the copied cell back into the units `value` was given
    in, for columns where the grid and its editor disagree - see
    discount_as_percent.
    """
    key = f"item[{row}].{column}"
    box, why = open_cell(editor, row, column, expect)
    if box is None:
        log.warning("row %d column %r: %s", row, column, why)
        return Written(key, value, "", False, why)

    target = Target(key=key, screen=Screen.ORDER_EDITOR,
                    control_type="EditControl", input=Input.TEXT,
                    commit_with_tab=False)
    _set_text(Resolved(control=box, target=target, layer=Layer.UIA_PROPERTY,
                       detail="inline cell editor"), value)
    auto.SendKeys("{Enter}", waitTime=0.15)
    time.sleep(config.SETTLE * 2)

    got = ui.item_rows(editor)
    if got.how != "read" or row >= len(got.rows):
        return Written(key, value, "", False,
                       f"wrote it, but the row could not be read back ({got.how})")
    raw = cell(got.rows[row], column)
    back = as_written(raw) if as_written else raw
    if same_value(back, value):
        return Written(key, value, back, True)
    return Written(key, value, back, False,
                   f"wrote {value!r} but the row shows {raw!r}"
                   + (f" ({back!r} in the same units)" if as_written else ""))
