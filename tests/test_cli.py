"""End-to-end wiring, no API key required.

--from-raw replays a saved response through stages 2 and 3, which is the whole
pipeline minus the vision call. That makes the exit-code contract - the thing
any wrapper depends on - testable in CI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from extraction import config
from extraction.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "sample_raw.json"


def _run(tmp_path: Path, raw_text: str, name: str = "raw.json") -> tuple[int, Path]:
    raw = tmp_path / name
    raw.write_text(raw_text, encoding="utf-8")
    out = tmp_path / "order.json"
    code = main(["ignored.png", "--from-raw", str(raw), "-o", str(out)])
    return code, out


@pytest.fixture
def clean_raw() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_clean_order_exits_zero_and_writes_output(tmp_path, clean_raw):
    code, out = _run(tmp_path, clean_raw)
    assert code == config.EXIT_OK
    assert out.is_file()

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["reconciliation"]["status"] == "passed"
    assert payload["order"]["external_reference"] == "PO-2026-0042"
    # The printed customer id is captured, but stays out of Fakturama's own
    # Customer ID field - spec 2.6 leaves that auto-proposed.
    assert payload["order"]["debtor"]["printed_customer_id"] == "CUST-1007"
    assert payload["derived"]["payment_code"] == "Credit transfer"
    assert payload["derived"]["is_paid"] is True


def test_corrupted_line_total_exits_two_and_writes_nothing(tmp_path, clean_raw):
    corrupted = clean_raw.replace('"source_total": 250.00', '"source_total": 205.00')
    code, out = _run(tmp_path, corrupted)
    assert code == config.EXIT_RECONCILIATION_FAILED
    assert not out.exists(), "a failed reconciliation must not produce an output file"


def test_reconciliation_failure_names_the_offending_row(tmp_path, clean_raw, capsys):
    corrupted = clean_raw.replace('"source_total": 250.00', '"source_total": 205.00')
    _run(tmp_path, corrupted)
    stderr = capsys.readouterr().err
    assert "items[1].source_total" in stderr
    assert "250.00" in stderr and "205.00" in stderr


def test_totals_mismatch_exits_two(tmp_path, clean_raw):
    corrupted = clean_raw.replace('"net_total": 354.52', '"net_total": 344.52')
    code, out = _run(tmp_path, corrupted)
    assert code == config.EXIT_RECONCILIATION_FAILED
    assert not out.exists()


def test_unknown_payment_method_still_succeeds_but_warns(tmp_path, clean_raw, capsys):
    """The data is sound, so the JSON is worth having. The automation will stop
    at spec 2.10, so say so now rather than at the UI."""
    modified = clean_raw.replace('"method": "Bank Transfer"', '"method": "PayPal"')
    code, out = _run(tmp_path, modified)
    assert code == config.EXIT_OK
    assert json.loads(out.read_text(encoding="utf-8"))["derived"]["payment_code"] is None
    assert "PayPal" in capsys.readouterr().err


def test_malformed_json_exits_three(tmp_path):
    code, out = _run(tmp_path, "{not json")
    assert code == config.EXIT_EXTRACTION_FAILED
    assert not out.exists()


def test_schema_violation_exits_three(tmp_path, clean_raw):
    # A non-ISO date is a schema violation, not a reconciliation failure: we
    # cannot trust a booking date we could not parse.
    broken = clean_raw.replace('"order_date": "2026-03-01"', '"order_date": "01/03/2026"')
    code, _ = _run(tmp_path, broken)
    assert code == config.EXIT_EXTRACTION_FAILED


def test_utf8_bom_is_tolerated(tmp_path, clean_raw):
    """Notepad and PowerShell both write UTF-8 with a BOM; hand-edited raw
    files must still load."""
    raw = tmp_path / "bom.json"
    raw.write_text(clean_raw, encoding="utf-8-sig")
    out = tmp_path / "order.json"
    assert main(["ignored.png", "--from-raw", str(raw), "-o", str(out)]) == config.EXIT_OK


def test_missing_raw_file_exits_three(tmp_path):
    code = main(["ignored.png", "--from-raw", str(tmp_path / "nope.json"), "-o", str(tmp_path / "o.json")])
    assert code == config.EXIT_EXTRACTION_FAILED
