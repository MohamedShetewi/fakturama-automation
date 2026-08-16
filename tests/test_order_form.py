"""The parts of the automation half that are testable without Fakturama.

Date handling is where a silent wrong booking date would come from: the
extraction speaks ISO-8601, the widget renders a locale format, and the two
must agree as *dates* rather than as strings.
"""

from __future__ import annotations

from datetime import date

import pytest

from automation.actions import format_ui_date, parse_ui_date
from automation.ui import segment_order


def test_writes_the_format_the_widget_renders():
    # Observed live in the Date field: 'Aug 15, 2026'.
    assert format_ui_date(date(2026, 8, 15)) == "Aug 15, 2026"
    assert format_ui_date(date(2026, 7, 14)) == "Jul 14, 2026"


def test_round_trips_its_own_output():
    for d in (date(2026, 7, 14), date(2026, 1, 1), date(2026, 12, 31)):
        assert parse_ui_date(format_ui_date(d)) == d


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Aug 15, 2026", date(2026, 8, 15)),
        ("August 15, 2026", date(2026, 8, 15)),
        ("15.08.2026", date(2026, 8, 15)),
        ("2026-08-15", date(2026, 8, 15)),
        ("08/15/2026", date(2026, 8, 15)),
        ("  Aug 15, 2026  ", date(2026, 8, 15)),
    ],
)
def test_reads_the_locale_formats_the_widget_might_render(text, expected):
    assert parse_ui_date(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "not a date", "Aug 2026", None])
def test_unparseable_is_none_not_an_exception(text):
    # A failed parse must become a reported step failure, not a crash that
    # leaves the form half-filled.
    assert parse_ui_date(text) is None


@pytest.mark.parametrize(
    "fmt,expected",
    [
        ("%b %d, %Y", ["month", "day", "year"]),   # 'Aug 15, 2026' - observed
        ("%d.%m.%Y", ["day", "month", "year"]),    # a German workspace
        ("%Y-%m-%d", ["year", "month", "day"]),
        ("%m/%d/%Y", ["month", "day", "year"]),
    ],
)
def test_segment_order_follows_the_rendered_format(fmt, expected):
    # Typing month-first into a day-first widget sets a wrong date that still
    # looks plausible, so the order is derived, never assumed.
    assert segment_order(fmt) == expected


def test_verification_compares_dates_not_strings():
    assert parse_ui_date("Jul 04, 2026") == parse_ui_date("Jul 4, 2026") == date(2026, 7, 4)
