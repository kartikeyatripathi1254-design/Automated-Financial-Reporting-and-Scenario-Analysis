import json
from pathlib import Path

import pytest

from fpa_engine import FPAAnalysisEngine
from ind_as_extractor import ExtractionError, build_payload, extract_pages, extract_payload_from_pdf, parse_amount, parse_statement_lines

RIL_PDF = Path(__file__).parent / "RIL-Integrated-Annual-Report-2025-26.pdf"

PL_PAGE = """Consolidated Statement of Profit and Loss
Reliance Industries Limited Integrated Annual Report 2025-26
(₹ in crore)
Notes 2025-26 2024-25
Revenue from Operations 25 10,75,675 9,80,136
Other Income 26 28,962 17,978
Total Income 11,04,637 9,98,114
Cost of Materials Consumed 4,43,101 4,22,127
Purchase of Stock-in-Trade 2,55,829 2,22,686
Changes in Inventories of Finished Goods, Work-in-Progress and Stock-in-Trade 27 (10,267) (15,124)
Excise Duty 18,456 15,443
Employee Benefits Expense 28 30,318 28,559
Finance Costs 29 27,061 24,269
Depreciation / Amortisation and Depletion Expense 1 57,688 53,136
Other Expenses 30 1,59,289 1,41,001
Total Expenses 9,81,475 8,92,097
Profit for the year 95,754 81,309
"""

CF_PAGE = """Consolidated Statement of Cash Flows
A. Cash Flow from Operating Activities
Net Cash Flow from Operating Activities * 1,92,113 1,78,703
Net Increase in Cash and Cash Equivalents 39,475 9,277
Opening Balance of Cash and Cash Equivalents 1,06,502 97,225
Closing Balance of Cash and Cash Equivalents (Refer Note 10) 1,45,977 1,06,502
"""


def test_parse_amount():
    assert parse_amount("10,75,675") == 1075675
    assert parse_amount("(10,267)") == -10267
    assert parse_amount("-") == 0
    assert parse_amount("12.5") == 12.5


def test_parse_lines_strips_note_refs_and_decorations():
    rows = parse_statement_lines(PL_PAGE + CF_PAGE)
    assert rows["revenue_from_operations"] == (1075675, 980136)
    assert rows["depreciation_amortisation_and_depletion_expense"] == (57688, 53136)
    assert rows["changes_in_inventories_of_finished_goods_work_in_progress_and_stock_in_trade"] == (-10267, -15124)
    assert rows["net_cash_flow_from_operating_activities"] == (192113, 178703)


def test_parse_lines_joins_wrapped_labels():
    rows = parse_statement_lines("Net Profit Before Tax as per\nStatement of Profit and Loss\n1,23,162 1,06,017\n")
    assert rows["net_profit_before_tax_as_per_statement_of_profit_and_loss"] == (123162, 106017)


def test_build_payload_from_statement_pages():
    filler = ["Retail segment Revenue from Operations 3,28,202 2,91,043"] * 3
    payload = build_payload(filler + [PL_PAGE, CF_PAGE])
    items = payload["line_items"]
    assert items["Revenue"] == {"budget": 980136, "actual": 1075675}
    assert items["COGS"] == {"budget": 629689, "actual": 688663}
    assert items["OpEx"] == {"budget": 185003, "actual": 208063}
    assert items["Net Cash Flow"] == {"budget": 9277, "actual": 39475}
    assert payload["cash_reserves"] == 145977
    assert payload["period"] == "FY 2025-26 vs FY 2024-25"
    assert payload["currency"] == "INR Cr"
    assert payload["baseline_label"] == "prior year"
    assert payload["source"]["checks"] == {"total_income_reconciles": True, "cash_reconciles": True}
    assert payload["source"]["profit_and_loss_page"] == 4


def test_oldest_first_columns_are_flipped():
    page = PL_PAGE.replace("Notes 2025-26 2024-25", "Notes 2024-25 2025-26").replace(
        "Revenue from Operations 25 10,75,675 9,80,136", "Revenue from Operations 25 9,80,136 10,75,675")
    payload = build_payload([page])
    assert payload["line_items"]["Revenue"] == {"budget": 980136, "actual": 1075675}
    assert payload["period"] == "FY 2025-26 vs FY 2024-25"


def test_missing_statement_raises():
    with pytest.raises(ExtractionError):
        build_payload(["Chairman's letter. Revenue grew strongly."])


def test_engine_uses_prior_year_wording():
    result = FPAAnalysisEngine().process_bva(build_payload([PL_PAGE, CF_PAGE]))
    summary = result["cfo_executive_summary"]
    text = json.dumps(summary)
    assert "budget" not in text.lower() and "plan" not in text.lower()
    assert "up 9.7% on the prior year" in summary["topline_performance"]
    assert result["critical_flags"][0]["message"] == "Cost growth of 12.46% vs prior year, ahead of revenue growth of 9.7%"
    assert result["metadata"]["analysis_mode"] == "yoy"
    assert result["plain_english"]["status"] == "watch"


@pytest.mark.skipif(not RIL_PDF.exists(), reason="RIL annual report PDF not present")
class TestRilAnnualReport:
    @pytest.fixture(scope="class")
    def payload(self):
        return extract_payload_from_pdf(RIL_PDF)

    def test_reads_consolidated_statements(self, payload):
        assert payload["source"]["scope"] == "Consolidated"
        assert payload["source"]["profit_and_loss_page"] == 101
        assert payload["source"]["cash_flow_page"] == 103
        assert all(payload["source"]["checks"].values())

    def test_figures_match_audited_statements(self, payload):
        items = payload["line_items"]
        assert items["Revenue"] == {"budget": 980136, "actual": 1075675}
        assert items["COGS"] == {"budget": 629689, "actual": 688663}
        assert items["OpEx"] == {"budget": 185003, "actual": 208063}
        assert items["Net Profit"] == {"budget": 81309, "actual": 95754}
        assert items["Net Cash Flow"] == {"budget": 9277, "actual": 39475}
        assert items["Operating Cash Flow"] == {"budget": 178703, "actual": 192113}
        assert payload["cash_reserves"] == 145977

    def test_analysis_matches_reported_ebitda(self, payload):
        result = FPAAnalysisEngine().process_bva(payload)
        ebitda = result["summary_kpis"]["operating_income"]["actual"] + payload["source"]["other_income"]["actual"]
        assert ebitda == 207911  # reported consolidated EBITDA
        assert result["summary_kpis"]["gross_margin_pct"]["actual"] == 35.98
        assert [f["line_item"] for f in result["critical_flags"]] == ["OpEx"]

    def test_standalone_fallback(self):
        payload = extract_payload_from_pdf(RIL_PDF, prefer_consolidated=False)
        assert payload["source"]["scope"] == "Standalone"
        assert payload["line_items"]["Revenue"] == {"budget": 532792, "actual": 524105}


# ----------------------------------------------------------------------------
# Other layouts: quarterly results, investor decks, US 10-K, scanned (OCR) PDFs
# ----------------------------------------------------------------------------
QUARTERLY_PAGE = """Statement of unaudited consolidated financial results for the quarter ended June 30, 2026
(INR in Crores)
Quarter ended Year ended
Particulars 30, 2026 31, 2026 30, 2025 31, 2026
I. Revenue from operations 455 265 828 2.253
II. Other income 29 39 66 207
III. Total Income (I+II) 484 304 896 2,460
Cost of materials consumed 381 124 441 1,302
Purchase of Stock-in-trade 10 1 20 50
Change in inventories of finished goods, stock-in-trade and work-in-progress (74) 38 153 211
Employee benefits expense 4B 58 89 294
Other expenses (refer note 12) 255 325 362 1.388
Total Expenses (IV) 620 546 1,065 3,245
Finance costs 73 77 94 360
Depreciation and amortization expense 127 177 165 684
VIII. Loss for the period / year (VI-VII) (336) (500) (4281 (1,833)
"""

DECK_PAGE = """P&L Details
Tata Motors Limited Consolidated| INDAS| ₹ Cr
Particulars Q1 FY26 Q2 FY26 Q3 FY26 Q4 FY26 Q1 FY27 Q1 vs Q1YoY Q1 vs Q4QoQ
Revenue from operations 17,324 18,585 21,847 26,095 20,667 3,343 (5,428)
Cost of materials consumed (11,368) (12,507) (14,942) (17,914) (14,236) (2,868) 3,678
Employee benefit expenses (1,450) (1,447) (1,450) (1,457) (1,590) (140) (133)
EBITDA 2,053 2,113 2,729 3,417 2,257 204 (1,160)
Depreciation and amortization (480) (472) (483) (510) (508) (28) 2
PAT 1,397 (868) 704 1,794 2,556 1,159 762
"""

US_PAGE = """Tesla, Inc.
Consolidated\tStatements\tof\tOperations
(in millions, except per share data)
Year Ended December 31,
2025
2024
2023
Total revenues
94,827
\t
97,690
\t
96,773
Total cost of revenues
77,733
80,240
79,113
Total operating expenses
12,739
10,374
8,769
Income from operations
4,355
7,076
8,891
Interest expense
(
338
)
(
350
)
(
156
)
Net income attributable to common stockholders
$
3,794
$
7,091
$
14,997
"""


def test_ocr_amount_repairs():
    from ind_as_extractor import clean_amount_token
    assert clean_amount_token("4B") == "48"
    assert clean_amount_token("(4281") == "(428)"
    assert clean_amount_token("(496J") == "(496)"
    assert clean_amount_token("{3,157") == "(3,157)"
    assert clean_amount_token("1500)") == "(500)"
    assert clean_amount_token("2.253") == "2,253"
    assert clean_amount_token("12.5") == "12.5"
    assert clean_amount_token("(IV)") is None


def test_ocr_split_numbers_are_joined():
    rows = parse_statement_lines("Total Income (I+ II) 107,080 71,575 99,683 341,369 371, 531\n"
                                 "Other expenses 1 69,60 5 2 3 (29, 612)\n")
    assert rows["total_income"][-1] == 371531


def test_quarterly_results_compare_same_quarter_last_year():
    payload = build_payload([QUARTERLY_PAGE])
    items = payload["line_items"]
    assert payload["period"] == "Quarter ended June 30, 2026 vs same quarter 2025"
    assert payload["period_months"] == 3 and payload["currency"] == "INR Cr"
    assert items["Revenue"] == {"budget": 828, "actual": 455}
    assert items["COGS"] == {"budget": 614, "actual": 317}
    # Finance costs and depreciation sit below Total Expenses here, so they aren't subtracted again.
    assert items["OpEx"] == {"budget": 451, "actual": 303}
    assert items["Net Profit"] == {"budget": -428, "actual": -336}
    assert payload["source"]["checks"] == {"total_income_reconciles": True}


def test_investor_deck_picks_latest_quarter_and_flips_bracketed_costs():
    payload = build_payload([DECK_PAGE])
    items = payload["line_items"]
    assert payload["period"] == "Q1 FY27 vs Q1 FY26"
    assert items["Revenue"] == {"budget": 17324, "actual": 20667}
    assert items["COGS"] == {"budget": 11368, "actual": 14236}
    assert items["OpEx"] == {"budget": 17324 - 11368 - 2053, "actual": 20667 - 14236 - 2257}
    assert items["Net Profit"] == {"budget": 1397, "actual": 2556}


def test_us_10k_layout():
    payload = build_payload([US_PAGE])
    items = payload["line_items"]
    assert payload["period"] == "FY2025 vs FY2024"
    assert payload["currency"] == "USD Mn"
    assert items["Revenue"] == {"budget": 97690, "actual": 94827}
    assert items["COGS"] == {"budget": 80240, "actual": 77733}
    assert items["OpEx"] == {"budget": 10374, "actual": 12739}
    assert items["Net Profit"] == {"budget": 7091, "actual": 3794}
    assert payload["source"]["operating_income_basis"] == "ebit"
    assert payload["source"]["checks"] == {"operating_income_reconciles": True}


TEST_PDFS = Path(__file__).parent / "test_pdfs"


@pytest.mark.parametrize("name, period, revenue", [
    ("tesla_10k_2025.pdf", "FY2025 vs FY2024", {"budget": 97690, "actual": 94827}),
    ("tata_tmpv_q4fy26_results.pdf", "Year ended March 31, 2026 vs 2025", {"budget": 366094, "actual": 335582}),
    ("tata_cv_q1fy27_results.pdf", "Q1 FY27 vs Q1 FY26", {"budget": 17324, "actual": 20667}),
    ("ola_q1fy27_board_outcome.pdf", "Quarter ended June 30, 2026 vs same quarter 2025", {"budget": 828, "actual": 455}),
    ("ola_fy26_results.pdf", "Year ended March 31, 2026 vs 2025", {"budget": 4514, "actual": 2253}),
    ("berkshire_2025_ar.pdf", "FY2025 vs FY2024", {"budget": 371433, "actual": 371444}),
])
def test_real_filings(name, period, revenue):
    path = TEST_PDFS / name
    if not path.exists():
        pytest.skip(f"{name} not present")
    payload = extract_payload_from_pdf(path)
    assert payload["period"] == period
    assert payload["line_items"]["Revenue"] == revenue
    assert payload["source"]["scope"] == "Consolidated"
    assert all(payload["source"]["checks"].values())


def test_berkshire_statements_found_without_word_spacing():
    """pypdf drops the spaces in this report's headings ("CONSOLIDATEDSTATEMENTSOFEARNINGS"); both readers must work."""
    path = TEST_PDFS / "berkshire_2025_ar.pdf"
    if not path.exists():
        pytest.skip("berkshire_2025_ar.pdf not present")
    from pypdf import PdfReader
    reader = PdfReader(path)  # pypdf on the statement pages only; the whole report takes ~40s
    pypdf_pages = [""] * 93 + [reader.pages[i].extract_text() for i in range(93, 97)]
    for pages in (extract_pages(path), pypdf_pages):
        payload = build_payload(pages)
        items = payload["line_items"]
        assert payload["source"]["profit_and_loss_page"] == 94
        assert items["COGS"] == {"budget": 163642, "actual": 160062}
        assert items["OpEx"] == {"budget": 315697 - 163642, "actual": 318473 - 160062}
        assert items["Net Profit"] == {"budget": 88995, "actual": 66968}
        assert payload["cash_reserves"] == 52569
        assert payload["source"]["checks"] == {"cash_reconciles": True}
