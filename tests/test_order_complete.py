"""The pure parts of spec 4: the Documents list's date format, and what
counts as the extracted address appearing on the order.
"""

from datetime import date

import pytest

from automation.order_complete import expected_address_parts, parse_document_date

DEBTOR = {
    "company": "Northstar Office GmbH",
    "first_name": "Marta",
    "last_name": "Klein",
    "billing_address": {"street": "Friedrichstrasse 88", "zip": "10117",
                        "city": "Berlin", "country": "Germany"},
    "delivery_address": {"street": "Beusselstrasse 44", "zip": "10553",
                         "city": "Berlin", "country": "Germany"},
}


class TestParseDocumentDate:
    def test_reads_the_java_timestamp_the_list_copies(self):
        assert parse_document_date("Thu Aug 04 00:00:00 AST 2011") == date(2011, 8, 4)

    def test_a_single_digit_day(self):
        assert parse_document_date("Tue Jul 14 00:00:00 AST 2026") == date(2026, 7, 14)

    @pytest.mark.parametrize(
        "zone", ["AST", "GMT", "CEST", "UTC"],
    )
    def test_the_zone_abbreviation_is_ignored_not_parsed(self, zone):
        # strptime's %Z rejects most of these, which is why the day is pulled
        # out by hand instead.
        assert parse_document_date(f"Tue Jul 14 00:00:00 {zone} 2026") == date(2026, 7, 14)

    @pytest.mark.parametrize("text", ["", None, "null", "2026-07-14", "Tue Jul 2026"])
    def test_anything_else_is_none_rather_than_a_guess(self, text):
        assert parse_document_date(text) is None


class TestExpectedAddressParts:
    def test_lists_the_pieces_that_must_appear(self):
        parts = expected_address_parts(DEBTOR)
        assert "Northstar Office GmbH" in parts
        assert "Friedrichstrasse 88" in parts
        assert "10117" in parts
        assert "Berlin" in parts

    def test_uses_the_billing_address_not_the_delivery_one(self):
        # The order carries one address, and it is the billing one. Checking
        # against the delivery street would fail on every order that ships
        # somewhere else - like this one.
        assert "Beusselstrasse 44" not in expected_address_parts(DEBTOR)

    def test_absent_fields_are_dropped_not_rendered_as_none(self):
        debtor = {"company": None, "first_name": "Marta", "last_name": "Klein",
                  "billing_address": {"street": "Friedrichstrasse 88"}}
        parts = expected_address_parts(debtor)
        assert parts == ["Marta", "Klein", "Friedrichstrasse 88"]

    def test_a_debtor_with_no_address_yields_only_the_name(self):
        assert expected_address_parts({"first_name": "Marta", "last_name": "Klein"}) == \
            ["Marta", "Klein"]
