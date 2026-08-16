"""Spec 3.5: the reuse test, exercised without Fakturama running.

Every case here is one the live database produced. The two notations - the
list's fraction and the editor's percent - are the whole reason this decision
needed its own tests: the string comparison it replaces looked obviously
correct and was wrong on real data.
"""

from decimal import Decimal

import pytest

from automation.entities import Verdict, classify
from automation.vat_form import canonical_rate, parse_rate, _row_rate


# The rows as Ctrl+C hands them over: standard flag, Name, Description, Value
TAX_FREE = ["true", "Tax-free", "Free of Tax", "0.0"]
MWST_19 = ["false", "MwSt. 19%", "null", "0.19"]


class TestParseRate:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("0.19", "0.19"),      # as the list stores it
            ("19%", "0.19"),       # as the editor and the order speak it
            ("19 %", "0.19"),
            ("0.0", "0"),
            ("0%", "0"),
            ("0.190", "0.19"),     # trailing zeros are not a difference
            ("19,0%", "0.19"),     # a comma decimal separator
            ("0.07", "0.07"),
        ],
    )
    def test_both_notations_reduce_to_one_fraction(self, text, expected):
        assert canonical_rate(parse_rate(text)) == expected

    def test_bare_integer_is_a_fraction_not_a_percent(self):
        # The list column has no '%', so '19' there really would mean 1900%.
        # Guessing otherwise is how a rate silently becomes the wrong one.
        assert parse_rate("19") == Decimal("19")

    @pytest.mark.parametrize("text", ["", "   ", None, "null", "n/a", "%"])
    def test_unreadable_values_are_none_never_zero(self, text):
        # Zero is a real VAT rate. An unparseable cell must not become one.
        assert parse_rate(text) is None

    def test_canonical_rate_never_uses_exponent_notation(self):
        assert canonical_rate(parse_rate("100")) == "100"


class TestReuseDecision:
    def _classify(self, rows, percent):
        return classify(rows, canonical_rate(parse_rate(percent)), key=_row_rate)

    def test_matches_across_notations(self):
        # The bug this replaces: '0.19' vs '19' compared as strings -> CONFLICT
        # on the one row that was right.
        verdict, matches = self._classify([TAX_FREE, MWST_19], "19%")
        assert verdict is Verdict.UNIQUE
        assert matches == [MWST_19]

    def test_a_foreign_name_does_not_block_reuse(self):
        # 'MwSt. 19%' is the same rate as a requested 'VAT 19%'. Keying on the
        # name created a duplicate; keying on the rate reuses the record.
        verdict, _ = self._classify([TAX_FREE, MWST_19], "19%")
        assert verdict is Verdict.UNIQUE

    def test_absent_rate_routes_to_creation(self):
        verdict, matches = self._classify([TAX_FREE, MWST_19], "7%")
        assert verdict is Verdict.NONE
        assert matches == []

    def test_zero_is_found_not_treated_as_missing(self):
        verdict, matches = self._classify([TAX_FREE, MWST_19], "0%")
        assert verdict is Verdict.UNIQUE
        assert matches == [TAX_FREE]

    def test_two_rates_at_the_same_percentage_halt(self):
        other = ["false", "Reduced 19%", "null", "0.19"]
        verdict, matches = self._classify([TAX_FREE, MWST_19, other], "19%")
        assert verdict is Verdict.AMBIGUOUS
        assert len(matches) == 2

    def test_unreadable_rows_never_match(self):
        broken = ["false", "Broken", "null", "null"]
        verdict, matches = self._classify([broken], "0%")
        assert verdict is Verdict.NONE

    def test_short_rows_do_not_raise(self):
        verdict, _ = self._classify([["true"], []], "19%")
        assert verdict is Verdict.NONE
