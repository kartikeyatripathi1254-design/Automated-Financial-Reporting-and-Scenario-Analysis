from pathlib import Path

import pytest

from ind_as_extractor import parse_amount, parse_statement_lines

RIL_PDF = Path(__file__).parent / "RIL-Integrated-Annual-Report-2025-26.pdf"


@pytest.mark.parametrize("token,expected", [
    ("10,75,675", 1_075_675.0),
    ("(10,267)", -10_267.0),
    ("1,234.50", 1_234.5),
    ("-", 0.0),
])
def test_parse_amount(token, expected):
    assert parse_amount(token) == expected


def test_parse_statement_lines_skips_note_refs_and_handles_brackets():
    text = (
        "Revenue from Operations 25 10,75,675 9,90,000\n"
        "Total Expenses 9,00,000 (8,00,000)\n"
    )
    rows = parse_statement_lines(text)
    assert rows["revenue_from_operations"] == (1_075_675.0, 990_000.0)
    assert rows["total_expenses"] == (900_000.0, -800_000.0)


def test_parse_statement_lines_joins_wrapped_labels():
    text = "Changes in inventories of finished goods\nand stock-in-trade (2,500) 1,200\n"
    rows = parse_statement_lines(text)
    assert list(rows.values()) == [(-2_500.0, 1_200.0)]


def test_first_occurrence_wins():
    rows = parse_statement_lines("Total Income 500 400\nTotal Income 1 2\n")
    assert rows["total_income"] == (500.0, 400.0)


@pytest.mark.skipif(not RIL_PDF.exists(), reason="Reliance annual report PDF not present (not stored in the repo)")
def test_real_annual_report_reconciles():
    from fpa_engine import FPAAnalysisEngine
    from ind_as_extractor import extract_payload_from_pdf

    payload = extract_payload_from_pdf(RIL_PDF)
    assert payload["line_items"]["Revenue"]["actual"] > 0
    FPAAnalysisEngine().process_bva(payload)
