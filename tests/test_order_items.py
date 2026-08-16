"""Parsing the order's Items grid, against rows copied from the live app.

The VAT column is the reason this needs tests: it is not a name but a
serialised object, and the name has to be dug out of it. Getting that wrong
books the line against the wrong tax rate - silently, because the total does
not change until the document is printed.
"""

from decimal import Decimal

import pytest

from automation.order_items import (
    Item, discount_as_percent, find, index_of, read, same_value, to_item, vat_name,
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
