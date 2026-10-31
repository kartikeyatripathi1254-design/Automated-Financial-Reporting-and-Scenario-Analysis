"""
Extract a FPAAnalysisEngine payload from an Indian (Ind-AS) annual report PDF.

Reads only the Statement of Profit and Loss and the Statement of Cash Flows
(consolidated by default, standalone as fallback), so segment tables, ratio tables
and statutory disclosures elsewhere in the report are never picked up.

Annual reports have no budget: the current year becomes "actual" and the prior
year becomes the baseline (passed to the engine as "budget", labelled "prior year").
"""
import re
from typing import Optional

from pypdf import PdfReader

from fpa_engine import normalize_item

AMOUNT_RE = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?$|^[-–‑]$")
NOTE_REF_RE = re.compile(r"^\d{1,2}(?:\.\d{1,2})?$")
# The column header: two adjacent fiscal years, e.g. "2025-26 2024-25".
YEAR_PAIR_RE = re.compile(r"(20\d{2})\s*[-–]\s*(\d{2})\s+(20\d{2})\s*[-–]\s*(\d{2})\b")
DECORATION_RE = re.compile(r"^[*#@^†]+$")


class ExtractionError(ValueError):
    pass


def parse_amount(token: str) -> float:
    """'10,75,675' -> 1075675.0, '(10,267)' -> -10267.0, '-' -> 0.0."""
    token = token.strip()
    if token in ("-", "–", "‑"):
        return 0.0
    negative = token.startswith("(") and token.endswith(")")
    value = float(token.strip("()").replace(",", ""))
    return -value if negative else value


def parse_statement_lines(text: str) -> dict:
    """
    Parse '<label> [note] <current> <prior>' lines into {normalized_label: (current, prior)}.

    The first occurrence of a label wins, so repeated sub-totals further down the
    page don't overwrite the main line.
    """
    rows = {}
    pending_label = []
    for raw in text.splitlines():
        tokens = raw.replace("\t", " ").split()
        if not tokens:
            continue

        amounts = []
        while tokens and AMOUNT_RE.match(tokens[-1]) and len(amounts) < 2:
            amounts.insert(0, tokens.pop())
        while tokens and DECORATION_RE.match(tokens[-1]):
            tokens.pop()

        if len(amounts) < 2:
            # Label wrapped onto several lines: keep it until the amounts arrive.
            pending_label = (pending_label + tokens)[-25:] if not amounts else []
            continue
        # A note reference ("25", "26.1") sits between the label and the amounts.
        if tokens and NOTE_REF_RE.match(tokens[-1]):
            tokens.pop()

        label_tokens = tokens or pending_label
        pending_label = []
        key = normalize_item(" ".join(label_tokens))
        if not key or key in rows:
            continue
        try:
            rows[key] = (parse_amount(amounts[0]), parse_amount(amounts[1]))
        except ValueError:
            continue
    return rows


def _find_page(pages, required, prefer_consolidated):
    candidates = [i for i, t in enumerate(pages) if all(r in t.lower() for r in required)]
    if not candidates:
        return None
    if prefer_consolidated:
        consolidated = [i for i in candidates if "consolidated" in pages[i].lower()]
        if consolidated:
            return consolidated[0]
    standalone = [i for i in candidates if "consolidated" not in pages[i].lower()]
    return (standalone or candidates)[0]


def _first(rows, *names, default=None):
    for name in names:
        if name in rows:
            return rows[name]
    return default


def _detect_period(text):
    match = YEAR_PAIR_RE.search(text)
    if match:
        y1, s1, y2, s2 = match.groups()
        current, prior = f"{y1}-{s1}", f"{y2}-{s2}"
        if int(y1) < int(y2):  # columns listed oldest first
            return prior, current, True
        return current, prior, False
    return "Current Year", "Prior Year", False


def _detect_unit(text):
    lowered = text.lower()
    for unit in ("crore", "lakh", "million", "billion"):
        if f"in {unit}" in lowered:
            return {"crore": "Cr", "lakh": "Lakh", "million": "Mn", "billion": "Bn"}[unit]
    return None


def extract_pages(pdf_path) -> list:
    reader = PdfReader(pdf_path)
    # Some PDFs put tabs between words ("Total\tExpenses"); normalise so searches match.
    return [re.sub(r"[ \t]+", " ", p.extract_text() or "") for p in reader.pages]


def build_payload(pages: list, prefer_consolidated: bool = True) -> dict:
    """Build an engine payload from page texts. Raises ExtractionError when statements can't be found."""
    pl_idx = _find_page(pages, ["revenue", "total expenses", "profit"], prefer_consolidated)
    pl_idx = pl_idx if pl_idx is not None and "statement of profit and loss" in pages[pl_idx].lower() else \
        _find_page(pages, ["statement of profit and loss", "revenue", "total expenses"], prefer_consolidated)
    if pl_idx is None:
        raise ExtractionError("Could not find a Statement of Profit and Loss in the PDF.")

    cf_idx = _find_page(pages, ["operating activities", "closing balance of cash"], prefer_consolidated)

    pl_text = pages[pl_idx]
    current_label, prior_label, oldest_first = _detect_period(pl_text)
    pl = parse_statement_lines(pl_text)
    cf = parse_statement_lines(pages[cf_idx]) if cf_idx is not None else {}
    if oldest_first:
        pl = {k: (p, c) for k, (c, p) in pl.items()}
        cf = {k: (p, c) for k, (c, p) in cf.items()}

    revenue = _first(pl, "revenue_from_operations", "revenue_from_operations_net", "total_revenue_from_operations")
    total_expenses = _first(pl, "total_expenses")
    if revenue is None or total_expenses is None:
        raise ExtractionError(
            f"P&L page {pl_idx + 1} found, but 'Revenue from Operations' or 'Total Expenses' could not be read."
        )

    zero = (0.0, 0.0)
    cost_lines = [
        _first(pl, "cost_of_materials_consumed", default=zero),
        _first(pl, "purchase_of_stock_in_trade", "purchases_of_stock_in_trade", default=zero),
        _first(pl, "changes_in_inventories_of_finished_goods_work_in_progress_and_stock_in_trade",
               "changes_in_inventories_of_finished_goods_stock_in_trade_and_work_in_progress",
               "changes_in_inventories", default=zero),
    ]
    cogs = tuple(sum(v[i] for v in cost_lines) for i in (0, 1))
    finance = _first(pl, "finance_costs", default=zero)
    dna = _first(pl, "depreciation_amortisation_and_depletion_expense", "depreciation_and_amortisation_expense",
                 "depreciation_and_amortization_expense", "depreciation_amortization_and_depletion_expense",
                 default=zero)
    # Everything else in Total Expenses (employee, excise, other expenses, network costs...) is OpEx.
    opex = tuple(total_expenses[i] - cogs[i] - finance[i] - dna[i] for i in (0, 1))

    other_income = _first(pl, "other_income", default=zero)
    total_income = _first(pl, "total_income")
    profit = _first(pl, "profit_for_the_year", "profit_for_the_period", "net_profit_for_the_year")

    line_items = {
        "Revenue": revenue,
        "COGS": cogs,
        "OpEx": opex,
    }
    if profit is not None:
        line_items["Net Profit"] = profit

    net_cash = _first(cf, "net_increase_in_cash_and_cash_equivalents",
                      "net_increase_decrease_in_cash_and_cash_equivalents",
                      "net_decrease_in_cash_and_cash_equivalents")
    operating_cash = _first(cf, "net_cash_flow_from_operating_activities", "net_cash_from_operating_activities",
                            "net_cash_generated_from_operating_activities")
    closing_cash = _first(cf, "closing_balance_of_cash_and_cash_equivalents_refer_note_10",
                          "closing_balance_of_cash_and_cash_equivalents", "cash_and_cash_equivalents_at_end_of_the_year")
    if closing_cash is None:
        closing_cash = next((v for k, v in cf.items() if k.startswith("closing_balance_of_cash")), None)
    if net_cash is not None:
        line_items["Net Cash Flow"] = net_cash
    if operating_cash is not None:
        line_items["Operating Cash Flow"] = operating_cash

    # Reconciliation checks so a bad parse is visible instead of silently analysed.
    checks = {}
    if total_income is not None:
        checks["total_income_reconciles"] = abs(revenue[0] + other_income[0] - total_income[0]) < 1
    if net_cash is not None and closing_cash is not None:
        opening = next((v for k, v in cf.items() if k.startswith("opening_balance_of_cash")), None)
        if opening is not None:
            checks["cash_reconciles"] = abs(opening[0] + net_cash[0] - closing_cash[0]) < 1

    unit = _detect_unit(pl_text)
    scope = "Consolidated" if "consolidated" in pl_text.lower() else "Standalone"
    payload = {
        "period": f"FY {current_label} vs FY {prior_label}",
        "currency": f"INR {unit}" if unit in ("Cr", "Lakh") else "INR",
        "report_type": f"Annual Results ({scope})",
        "analysis_mode": "yoy",
        "baseline_label": "prior year",
        "period_months": 12,
        "line_items": {k: {"budget": v[1], "actual": v[0]} for k, v in line_items.items()},
        "source": {
            "profit_and_loss_page": pl_idx + 1,
            "cash_flow_page": cf_idx + 1 if cf_idx is not None else None,
            "scope": scope,
            "other_income": {"actual": other_income[0], "prior": other_income[1]},
            "depreciation": {"actual": dna[0], "prior": dna[1]},
            "finance_costs": {"actual": finance[0], "prior": finance[1]},
            "checks": checks,
        },
    }
    if closing_cash is not None:
        payload["cash_reserves"] = closing_cash[0]
    return payload


def extract_payload_from_pdf(pdf_path, prefer_consolidated: bool = True) -> dict:
    return build_payload(extract_pages(pdf_path), prefer_consolidated=prefer_consolidated)


if __name__ == "__main__":
    import json
    import sys

    from fpa_engine import FPAAnalysisEngine

    payload = extract_payload_from_pdf(sys.argv[1])
    print(json.dumps(payload, indent=2))
    print(json.dumps(FPAAnalysisEngine().process_bva(payload)["cfo_executive_summary"], indent=2, ensure_ascii=False))
