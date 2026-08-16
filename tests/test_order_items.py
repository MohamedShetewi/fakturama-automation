"""Parsing the order's Items grid, against rows copied from the live app.

The VAT column is the reason this needs tests: it is not a name but a
serialised object, and the name has to be dug out of it. Getting that wrong
books the line against the wrong tax rate - silently, because the total does
not change until the document is printed.
"""

from decimal import Decimal

import pytest

import automation.order_items as mod
from automation.order_items import (
    Item, discount_as_percent, find, index_of, open_cell, read, same_value,
    to_item, vat_name,
)

VAT_19 = ("VAT taxValue: [0.19] salesEqualizationTax: [null] description: [null] "
          "name: [MwSt. 19%] dateAdded: [Sun Aug 16 00:00:00 AST 2026] "
          "modifiedBy: [Shetewi] modified: [null] id: [2] deleted: [false] "
          "validFrom: [Sun Aug 16 00:00:00 AST 2026] validTo: [null]")
VAT_FREE = ("VAT taxValue: [0.0] salesEqualizationTax: [null] "
            "description: [Free of Tax] name: [Tax-free] id: [1] deleted: [false]")

CHAIR = ["1.00", "CHR-ERG-01", "null", "Ergonomic Desk Chair", "", VAT_19,
         "USD 250", "0.0", "USD 250"]
JUICE = ["1.00", "12", "null", "juice", "", VAT_FREE, "USD 12", "0.0", "USD 12"]


class TestVatName:
    def test_digs_the_name_out_of_the_serialised_object(self):
        assert vat_name(VAT_19) == "MwSt. 19%"

    def test_does_not_confuse_the_description_for_the_name(self):
        # 'Free of Tax' is the description; 'Tax-free' is the name, and the
        # name is what the product editor's combo lists.
        assert vat_name(VAT_FREE) == "Tax-free"

    @pytest.mark.parametrize("cell", ["", None, "VAT taxValue: [0.19]", "name: [null]", "name: []"])
    def test_an_unreadable_cell_is_none_never_a_guess(self, cell):
        assert vat_name(cell) is None


class TestToItem:
    def test_reads_a_line_the_chooser_created(self):
        item = to_item(CHAIR)
        assert item.sku == "CHR-ERG-01"
        assert item.name == "Ergonomic Desk Chair"
        assert item.quantity == Decimal("1.00")
        assert item.unit_price == Decimal("250")
        assert item.discount == Decimal("0.0")
        assert item.total == Decimal("250")
        assert item.vat == "MwSt. 19%"

    def test_the_currency_prefix_is_not_part_of_the_amount(self):
        assert to_item(CHAIR).unit_price == Decimal("250")

    def test_a_zero_discount_is_zero_not_missing(self):
        # None would mean "could not read", and that has to stay distinct.
        assert to_item(CHAIR).discount == Decimal(0)

    def test_a_short_row_does_not_raise(self):
        item = to_item(["1.00", "CHR-ERG-01"])
        assert item.sku == "CHR-ERG-01"
        assert item.total is None

    def test_an_empty_row_yields_nothing_readable(self):
        item = to_item([])
        assert item.sku == ""
        assert item.quantity is None


class TestFind:
    def test_matches_a_sku_exactly_and_case_insensitively(self):
        items = [to_item(CHAIR), to_item(JUICE)]
        assert [i.sku for i in find(items, "chr-erg-01")] == ["CHR-ERG-01"]

    def test_a_substring_is_not_a_match(self):
        items = [to_item(CHAIR)]
        assert find(items, "CHR-ERG-0") == []

    def test_duplicates_are_all_returned_so_the_caller_can_halt(self):
        items = [to_item(CHAIR), to_item(CHAIR)]
        assert len(find(items, "CHR-ERG-01")) == 2


class TestDiscountAsPercent:
    """One number, three spellings, all seen on the same cell."""

    @pytest.mark.parametrize(
        "stored,percent",
        [
            ("-0.1", "10"),        # how the grid copies a 10% allowance
            ("0.0", "0"),
            ("-0.05", "5"),
            ("-0.075", "7.5"),
            ("-0.19", "19"),
        ],
    )
    def test_converts_the_stored_fraction_to_whole_percent(self, stored, percent):
        assert discount_as_percent(stored) == percent

    def test_an_unreadable_cell_yields_nothing(self):
        assert discount_as_percent("null") == ""

    def test_a_written_percent_round_trips(self):
        # The regression: '10' written, '-0.1' copied back. Comparing those
        # without converting called a correct write a failure.
        assert same_value(discount_as_percent("-0.1"), "10")


class TestSameValue:
    def test_formats_that_differ_but_mean_the_same_number_match(self):
        assert same_value("0.00 %", "0.0")
        assert same_value("2.00", "2")
        assert same_value("USD 250", "250.00")

    def test_different_numbers_do_not(self):
        assert not same_value("2.00", "3")

    def test_non_numeric_text_falls_back_to_an_exact_comparison(self):
        assert same_value("CHR-ERG-01", "CHR-ERG-01")
        assert not same_value("CHR-ERG-01", "CHR-ERG-011")


class TestIndexOf:
    def test_finds_the_only_row_for_a_sku(self):
        assert index_of([JUICE, CHAIR], "CHR-ERG-01") == 1

    def test_refuses_to_choose_between_duplicates(self):
        # Editing "the" row when there are two would silently pick one.
        assert index_of([CHAIR, CHAIR], "CHR-ERG-01") is None

    def test_absent_sku_is_none(self):
        assert index_of([JUICE], "CHR-ERG-01") is None


class Rect:
    left, top, right, bottom = 100, 200, 1400, 500

    def width(self):
        return self.right - self.left

    def height(self):
        return self.bottom - self.top


class FakeBox:
    """A stand-in for the Text control SWT opens over a cell being edited."""

    ControlTypeName = "EditControl"

    def __init__(self, value):
        self.value = value
        self.BoundingRectangle = Rect()


@pytest.fixture
def grid(monkeypatch):
    """open_cell with the UI replaced by a keystroke recorder.

    Records what was sent and hands back whichever cell editor the test says
    is open, so the navigation can be checked without Fakturama.
    """

    class Grid:
        def __init__(self):
            self.keys = []
            self.clicks = []
            self.boxes = []          # what _cell_editors reports, after F2
            self.opened = []         # what it reports before F2

        def press(self, keys, waitTime=0):
            self.keys.append(keys)
            if keys == "{Esc}":
                self.opened = []

        def click(self, x, y):
            self.clicks.append((x, y))

        @property
        def downs(self):
            return self.keys.count("{Down}")

        @property
        def rights(self):
            return self.keys.count("{Right}")

    g = Grid()

    class FakePane:
        BoundingRectangle = Rect()

    monkeypatch.setattr(mod.ui, "items_grid", lambda editor: FakePane())
    monkeypatch.setattr(mod.ui, "focus_is_inside", lambda ctrl: True)
    monkeypatch.setattr(mod.ui, "legacy_value", lambda box: box.value)
    monkeypatch.setattr(mod.auto, "SendKeys", g.press)
    monkeypatch.setattr(mod.auto, "Click", g.click)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    def editors(editor, rect):
        return g.boxes if "{F2}" in g.keys else g.opened

    monkeypatch.setattr(mod, "_cell_editors", editors)
    return g


class TestOpenCell:
    """Reaching a row without trusting a row height.

    The regression: row 0 opened fine and row 1 reported "could not open that
    cell". GRID_ROW_HEIGHT was measured on a different grid, so every row past
    the first was aimed at by arithmetic that had never been checked here.
    """

    def test_a_later_row_is_reached_with_arrow_keys(self, grid):
        grid.boxes = [FakeBox("1.00")]
        box, why = open_cell(object(), 3, "quantity", "1.00")
        assert why == "" and box is not None
        assert grid.downs == 3

    def test_the_first_row_needs_no_arrows_at_all(self, grid):
        grid.boxes = [FakeBox("1.00")]
        open_cell(object(), 0, "quantity", "1.00")
        assert grid.downs == 0

    def test_every_row_is_clicked_at_the_same_place(self, grid):
        # The point of the change: the click no longer encodes which row.
        seen = set()
        for row in (0, 1, 5, 30):
            grid.keys.clear()
            grid.clicks.clear()
            grid.boxes = [FakeBox("1.00")]
            open_cell(object(), row, "quantity", "1.00")
            seen.update(grid.clicks)
        assert len(seen) == 1

    def test_the_column_is_reached_with_arrow_keys_too(self, grid):
        grid.boxes = [FakeBox("USD 40")]
        open_cell(object(), 1, "unit_price", "USD 40")
        assert grid.rights == 6          # ITEM_COL['unit_price']

    def test_a_cell_holding_something_else_is_not_typed_into(self, grid):
        # Landing on the SKU column while aiming at the quantity would book a
        # different product, and nothing downstream would notice.
        grid.boxes = [FakeBox("MAT-DESK-02")]
        box, why = open_cell(object(), 1, "quantity", "1.00")
        assert box is None
        assert "MAT-DESK-02" in why and "1.00" in why
        assert grid.keys[-1] == "{Esc}"          # cancelled, not left open

    def test_a_cell_that_never_opened_says_so(self, grid):
        grid.boxes = []
        box, why = open_cell(object(), 1, "quantity", "1.00")
        assert box is None
        assert "0 cell editors are open" in why

    def test_a_leftover_editor_is_closed_before_starting(self, grid):
        # Enter does not dispose the editor synchronously; for a moment the
        # grid reports two, and the next cell was unidentifiable.
        grid.opened = [FakeBox("stale")]
        grid.boxes = [FakeBox("1.00")]
        box, why = open_cell(object(), 1, "quantity", "1.00")
        assert why == "" and box is not None
        assert grid.keys[0] == "{Esc}"

    def test_an_editor_that_refuses_to_close_stops_the_write(self, grid, monkeypatch):
        monkeypatch.setattr(mod, "_cell_editors", lambda e, r: [FakeBox("stuck")])
        box, why = open_cell(object(), 1, "quantity", "1.00")
        assert box is None
        assert "still" in why and "open" in why
        assert grid.clicks == []          # never even reached for

    def test_a_grid_that_does_not_take_focus_stops_the_write(self, grid, monkeypatch):
        monkeypatch.setattr(mod.ui, "focus_is_inside", lambda ctrl: False)
        grid.boxes = [FakeBox("1.00")]
        box, why = open_cell(object(), 1, "quantity", "1.00")
        assert box is None
        assert "did not take the keyboard" in why


class TestRead:
    def test_the_trust_flag_is_passed_through_untouched(self, monkeypatch):
        # An unreadable grid must never look like an order with no lines: the
        # next step decides what to add from what is already there.
        import automation.order_items as mod
        monkeypatch.setattr(mod.ui, "item_rows",
                            lambda editor: mod.ui.GridRead([], "no-focus"))
        items, how = read(object())
        assert items == []
        assert how == "no-focus"
