"""plain_number: the spelling of a rate that goes into a field and a name.

Its own tests because the obvious one-liner is right often enough to look
safe. Stripping trailing zeros and then a trailing dot handles '19.0' and even
'20.0' - rstrip stops at the '.'. What it does not handle is a percentage that
arrives as a whole number: '20'.rstrip('0') is '2'.

The schema types these as numbers, so whether 20% reaches here as 20 or 20.0
depends on how the model happened to emit it. Nothing later would catch the
difference: a document booked at 2% is arithmetically consistent, just wrong.
"""

from decimal import Decimal

import pytest

from automation.actions import plain_number


class TestPlainNumber:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (19.0, "19"),
            (19, "19"),
            ("19.0", "19"),
            (Decimal("19.00"), "19"),
            (7.5, "7.5"),
            (0.0, "0"),
            (2.5, "2.5"),
        ],
    )
    def test_drops_trailing_zeros(self, value, expected):
        assert plain_number(value) == expected

    @pytest.mark.parametrize("value", [20.0, 20, "20", "20.0", Decimal("20.0")])
    def test_a_zero_inside_the_number_is_not_a_trailing_zero(self, value):
        # The whole reason this function exists: 20 must not become 2, however
        # the percentage happens to have been typed or emitted.
        assert plain_number(value) == "20"

    @pytest.mark.parametrize(
        "value,expected",
        [(100.0, "100"), (1000, "1000"), (10.0, "10"), (200.0, "200")],
    )
    def test_never_uses_exponent_notation(self, value, expected):
        # Decimal.normalize() alone turns 20 into 2E+1 and 100 into 1E+2.
        assert plain_number(value) == expected

    def test_the_naive_string_approach_would_have_been_wrong(self):
        # Pinning the exact case, so nobody reintroduces the one-liner as a
        # "simplification" after checking it against 19.0 and 20.0 only.
        def naive(text):
            return text.rstrip("0").rstrip(".")

        assert naive("19.0") == "19"      # fine
        assert naive("20.0") == "20"      # also fine - rstrip stops at the '.'
        assert naive("20") == "2"         # not fine
        assert plain_number("20") == "20"
