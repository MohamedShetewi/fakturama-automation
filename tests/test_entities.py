"""The tri-state resolve_or_create decision table.

This is the whole risk surface of spec 2.x-3.x expressed as pure logic, so it
is tested exhaustively here rather than discovered against a live database.
The middle state is the point: 'found / not found' is what books an order
against the wrong customer.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from automation.entities import (
    Outcome,
    Verdict,
    classify,
    normalize,
    resolve_or_create,
)


@dataclass
class Row:
    name: str
    city: str = "Berlin"


def name_of(r):
    return r.name


class Spy:
    """Records whether the UI action would have been taken."""

    def __init__(self):
        self.selected = []
        self.created = []

    def select(self, row):
        self.selected.append(row)

    def create(self, wanted):
        self.created.append(wanted)


# --- classify ----------------------------------------------------------------


def test_exactly_one_exact_match_is_unique():
    rows = [Row("Northstar Office GmbH"), Row("Southstar GmbH")]
    verdict, matches = classify(rows, "Northstar Office GmbH", key=name_of)
    assert verdict is Verdict.UNIQUE
    assert matches[0].name == "Northstar Office GmbH"


def test_no_match_is_none():
    verdict, matches = classify([Row("Someone Else")], "Northstar Office GmbH", key=name_of)
    assert verdict is Verdict.NONE
    assert matches == []


def test_two_exact_matches_are_ambiguous():
    rows = [Row("Northstar Office GmbH", "Berlin"), Row("Northstar Office GmbH", "Hamburg")]
    verdict, matches = classify(rows, "Northstar Office GmbH", key=name_of)
    assert verdict is Verdict.AMBIGUOUS
    assert len(matches) == 2


def test_a_substring_hit_is_not_a_match():
    # Fakturama's search returns prefixes: 'CHR-ERG-01' also finds
    # 'CHR-ERG-011'. Selecting that is a silent wrong booking.
    rows = [Row("CHR-ERG-011"), Row("CHR-ERG-010")]
    verdict, matches = classify(rows, "CHR-ERG-01", key=name_of)
    assert verdict is Verdict.NONE
    assert matches == []


def test_matching_ignores_case_and_surrounding_whitespace():
    rows = [Row("  northstar   office  GmbH ")]
    verdict, _ = classify(rows, "Northstar Office GmbH", key=name_of)
    assert verdict is Verdict.UNIQUE


def test_a_single_match_that_conflicts_is_not_unique():
    rows = [Row("Northstar Office GmbH", city="Munich")]
    verdict, matches = classify(
        rows, "Northstar Office GmbH", key=name_of,
        conflicts=lambda r: f"stored city {r.city!r} != 'Berlin'" if r.city != "Berlin" else None,
    )
    assert verdict is Verdict.CONFLICT
    assert matches[0].city == "Munich"


@pytest.mark.parametrize("text,expected", [
    ("  A  B ", "a b"), ("ÄB", "äb"), ("", ""), (None, ""),
])
def test_normalize(text, expected):
    assert normalize(text) == expected


# --- resolve_or_create -------------------------------------------------------


def test_unique_selects_and_never_creates():
    spy = Spy()
    res = resolve_or_create(
        "Debtor", "Northstar Office GmbH", [Row("Northstar Office GmbH")],
        select=spy.select, create=spy.create, key=name_of,
    )
    assert res.outcome is Outcome.SELECTED and res.ok
    assert len(spy.selected) == 1 and spy.created == []


def test_none_takes_the_create_branch():
    spy = Spy()
    res = resolve_or_create(
        "Debtor", "New Customer GmbH", [], select=spy.select, create=spy.create, key=name_of,
    )
    assert res.outcome is Outcome.CREATED and res.ok
    assert spy.created == ["New Customer GmbH"] and spy.selected == []


def test_ambiguous_halts_and_touches_nothing():
    spy = Spy()
    rows = [Row("Acme GmbH", "Berlin"), Row("Acme GmbH", "Hamburg")]
    res = resolve_or_create(
        "Debtor", "Acme GmbH", rows, select=spy.select, create=spy.create, key=name_of,
    )
    assert res.outcome is Outcome.HALT and not res.ok
    assert spy.selected == [] and spy.created == []
    assert "2 exact matches" in res.detail


def test_conflict_halts_rather_than_selecting_the_only_match():
    spy = Spy()
    res = resolve_or_create(
        "Debtor", "Acme GmbH", [Row("Acme GmbH", city="Munich")],
        select=spy.select, create=spy.create, key=name_of,
        conflicts=lambda r: "address differs from the document",
    )
    assert res.outcome is Outcome.HALT
    assert spy.selected == [] and spy.created == []
    assert "conflicts" in res.detail


def test_no_match_halts_when_creation_is_not_permitted():
    # VAT records and payment methods are looked up by exact name; inventing
    # one would book against a rate the document never stated.
    spy = Spy()
    res = resolve_or_create(
        "VAT", "VAT 19%", [], select=spy.select, create=spy.create,
        key=name_of, allow_create=False,
    )
    assert res.outcome is Outcome.HALT
    assert spy.created == []


def test_halt_carries_the_matches_so_a_human_can_choose():
    rows = [Row("Acme GmbH", "Berlin"), Row("Acme GmbH", "Hamburg")]
    res = resolve_or_create(
        "Debtor", "Acme GmbH", rows, select=lambda r: None, create=lambda w: None, key=name_of,
    )
    assert [m.city for m in res.matches] == ["Berlin", "Hamburg"]


def test_str_is_reportable():
    res = resolve_or_create(
        "Debtor", "Acme GmbH", [Row("Acme GmbH")],
        select=lambda r: None, create=lambda w: None, key=name_of,
    )
    assert "Debtor" in str(res) and "selected" in str(res)
