"""
Extract a FPAAnalysisEngine payload from a company's financial statements PDF.

Supported layouts:
- Indian (Ind-AS) annual reports: Statement of Profit and Loss + Statement of Cash Flows.
- Indian quarterly / annual results filed with the exchanges ("Statement of ... financial
  results"), with 3-6 columns (this quarter, last quarter, same quarter last year, year to date...).
- Investor-presentation P&L slides with quarter columns ("Q1 FY26 ... Q1 FY27").
- US filings (10-K / 10-Q): Consolidated Statements of Operations and of Cash Flows.

Only the statement pages are read, so segment tables and ratio tables elsewhere are never
picked up. Scanned PDFs with an OCR text layer are supported: common OCR slips in numbers
("4B", "(4281", "2.253") are repaired, and reconciliation checks expose anything that isn't.

Reports have no budget: the current period becomes "actual" and the comparison period
becomes the baseline (passed to the engine as "budget", labelled "prior year").
"""
import io
import re
from collections import Counter

from pypdf import PdfReader

try:  # much faster text extraction (C library); pypdf is the fallback
    import pypdfium2 as pdfium
except ImportError:  # pragma: no cover
    pdfium = None

from fpa_engine import normalize_item

AMOUNT_RE = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?$|^[-–‑—.]$")
NOTE_REF_RE = re.compile(r"^\d{1,2}(?:\.\d{1,2})?$")
# Fiscal-year column header, e.g. "2025-26 2024-25".
YEAR_PAIR_RE = re.compile(r"(20\d{2})\s*[-–]\s*(\d{2})\s+(20\d{2})\s*[-–]\s*(\d{2})\b")
QUARTER_LABEL_RE = re.compile(r"\bQ([1-4])\s*FY\s*'?(\d{2})\b", re.I)
PERIOD_END_RE = re.compile(r"ended\s*,?\s*([A-Za-z]{3,9})\s+(\d{1,2})\s*[,.]\s*(20\d{2})", re.I)
DECORATION_RE = re.compile(r"^[*#@^†]+$")
# Row markers in front of labels: "I.", "IV.", "Ill." (OCR of III.), "11" (OCR of II), "(a)", "A.".
LEADING_MARKER_RE = re.compile(r"^[(\[{]?(?:[ivxlIVXL1]{1,5}|[a-zA-Z]|\d{1,2})[.)\]}:]+$|^[ivxlIVXL]{1,4}$|^\d{1,2}$")
OCR_DIGITS = str.maketrans({"B": "8", "O": "0", "o": "0", "l": "1", "I": "1", "S": "5"})
DASHES = ("-", "–", "‑", "—", ".")

STATEMENT_TITLES = ("statement of profit and loss", "financial results", "statements of operations",
                    "statement of operations", "income statement", "statements of income", "p&l details",
                    "statements of earnings", "statement of earnings")
PL_ANCHOR_RE = re.compile(r"t\s?ota[lt]\s*expen|cost\s*of\s*revenues?|cost\s*of\s*sales|ebitda|total\s*costs\s*and\s*expenses")


class ExtractionError(ValueError):
    pass


def parse_amount(token: str) -> float:
    """'10,75,675' -> 1075675.0, '(10,267)' -> -10267.0, '-' -> 0.0."""
    token = token.strip()
    if token in DASHES:
        return 0.0
    negative = token.startswith("(") and token.endswith(")")
    value = float(token.strip("()").replace(",", ""))
    return -value if negative else value


def clean_amount_token(token: str):
    """Return the token as a clean amount string, repairing common OCR slips, or None if it isn't one."""
    token = token.rstrip("*")
    if not token:
        return None
    if token in DASHES:
        return token
    if not any(c.isdigit() for c in token):
        return None
    if token[0] in "{[!":
        token = "(" + token[1:]
    token = token[0] + token[1:].translate(OCR_DIGITS)
    if token.startswith("(") and not token.endswith(")"):
        last_group = token[1:].split(",")[-1]
        if not token[-1].isdigit() or len(last_group.split(".")[0]) > 3:
            token = token[:-1] + ")"  # "(496J", "(4281" -> "(496)", "(428)"
        else:
            token += ")"
    elif token.endswith(")") and not token.startswith("("):
        if token[0] not in "1l" or len(token) < 3:
            return None
        token = "(" + token[1:]  # "1500)" -> "(500)": the opening bracket was read as a 1
    # OCR read the thousands comma as a dot: "2.253" -> "2,253".
    if re.fullmatch(r"\(?[1-9]\d{0,2}(\.\d{3})+\)?", token):
        token = token.replace(".", ",")
    return token if AMOUNT_RE.match(token) else None


def _is_numeric_line(tokens) -> bool:
    return bool(tokens) and all(t in ("$", "(", ")") or clean_amount_token(t) for t in tokens)


def _join_split_numbers(tokens):
    """OCR breaks numbers at spaces: '69,60 5' -> '69,605', '371, 531' -> '371,531', '(3,305 )' -> '(3,305)'."""
    out = []
    for tok in tokens:
        prev = out[-1] if out else ""
        last_group = prev.split(",")[-1].lstrip("(") if "," in prev else None
        if last_group is not None and len(last_group) < 3 and last_group.isdigit() | (last_group == "") and \
                re.fullmatch(rf"\d{{{3 - len(last_group)}}}\)?", tok):
            out[-1] = prev + tok
        elif tok == ")" and re.fullmatch(r"\([\d,.]+", prev):
            out[-1] = prev + tok
        else:
            out.append(tok)
    return out


def _join_split_parens(tokens):
    """US filings split negatives over tokens: '(', '73', ')' -> '(73)'. Also drops '$'."""
    out, i = [], 0
    tokens = _join_split_numbers([t for t in tokens if t != "$"])
    while i < len(tokens):
        if tokens[i] == "(" and i + 2 < len(tokens) and tokens[i + 2] == ")":
            out.append(f"({tokens[i + 1]})")
            i += 3
        elif tokens[i] == "(" and i + 1 < len(tokens) and tokens[i + 1].endswith(")"):
            out.append(f"({tokens[i + 1]}")
            i += 2
        else:
            out.append(tokens[i])
            i += 1
    return out


def _clean_label(tokens) -> str:
    label = " ".join(tokens)
    label = re.sub(r"[(\[{][^)\]}]*[)\]}]", " ", label)  # "(refer note 4)", "(a+b)", "(loss)"
    label = re.sub(r"[(\[{][^)\]}]*$", " ", label)
    words = label.split()
    while len(words) > 1 and LEADING_MARKER_RE.match(words[0]):
        words.pop(0)
    return normalize_item(" ".join(words))


def parse_statement_rows(text: str):
    """
    Parse statement lines into ({normalized_label: (col1, col2, ...)}, column_count).

    The column count is the most common number of amounts per row. Rows with fewer amounts
    are skipped (a missing figure can't be placed in the right column), and the first
    occurrence of a label wins so repeated sub-totals don't overwrite the main line.
    """
    # Pass 1: tokenize, and merge runs of numbers-only lines (US filings put each number on its own line).
    lines = []
    for raw in text.splitlines():
        tokens = raw.replace("\t", " ").split()
        if not tokens:
            continue
        if _is_numeric_line(tokens) and lines and lines[-1][1]:
            lines[-1][0].extend(tokens)
        else:
            lines.append([tokens, _is_numeric_line(tokens)])

    # Pass 2: peel trailing amounts off each line.
    parsed, pending_label = [], []
    for tokens, _ in lines:
        tokens = _join_split_parens(tokens)
        amounts = []
        while tokens:
            cleaned = clean_amount_token(tokens[-1])
            if cleaned is None:
                break
            amounts.insert(0, cleaned)
            tokens.pop()
        while tokens and DECORATION_RE.match(tokens[-1]):
            tokens.pop()
        if len(amounts) < 2:
            # Label wrapped onto several lines: keep it until the amounts arrive.
            # A single "amount" is part of the label ("Year Ended December 31,").
            pending_label = (pending_label + tokens + amounts)[-25:]
            continue
        parsed.append((tokens or pending_label, amounts))
        pending_label = []

    def unambiguous(amounts):  # a leading small integer may be a note reference, not a figure
        return not NOTE_REF_RE.match(amounts[0])

    counts = Counter(len(a) for _, a in parsed if unambiguous(a))
    if not counts:
        counts = Counter(len(a) for _, a in parsed)
    if not counts:
        return {}, 0
    n_cols = counts.most_common(1)[0][0]

    rows = {}
    for label_tokens, amounts in parsed:
        if len(amounts) == n_cols + 1 and NOTE_REF_RE.match(amounts[0]):
            amounts = amounts[1:]  # "Revenue from Operations 25 10,75,675 9,80,136"
        elif len(amounts) > n_cols:
            amounts = amounts[-n_cols:]
        if len(amounts) != n_cols:
            continue
        key = _clean_label(label_tokens)
        if not key or key in rows:
            continue
        try:
            rows[key] = tuple(parse_amount(a) for a in amounts)
        except ValueError:
            continue
    return rows, n_cols


def parse_statement_lines(text: str) -> dict:
    """{normalized_label: (col1, col2, ...)} for a statement page."""
    return parse_statement_rows(text)[0]


# ----------------------------------------------------------------------------
# Column choice: which column is "now" and which is the comparison
# ----------------------------------------------------------------------------
def _period_end(text):
    match = PERIOD_END_RE.search(text)
    return (match.group(1).title(), int(match.group(2)), int(match.group(3))) if match else None


def choose_columns(text: str, rows: dict, n_cols: int) -> dict:
    """Return {current, prior, label, months} describing the comparison to make."""
    lowered = text.lower()

    match = YEAR_PAIR_RE.search(text)  # Indian annual report: "2025-26 2024-25"
    if match and n_cols == 2:
        y1, s1, y2, s2 = match.groups()
        a, b = f"{y1}-{s1}", f"{y2}-{s2}"
        if int(y1) < int(y2):  # columns listed oldest first
            return {"current": 1, "prior": 0, "label": f"FY {b} vs FY {a}", "months": 12}
        return {"current": 0, "prior": 1, "label": f"FY {a} vs FY {b}", "months": 12}

    for line in text.splitlines():  # investor deck: "Q1 FY26 Q2 FY26 ... Q1 FY27"
        labels = [(int(fy), int(q)) for q, fy in QUARTER_LABEL_RE.findall(line)]
        if len(labels) >= 3 and len(labels) <= n_cols:
            cur = max(range(len(labels)), key=lambda i: labels[i])
            fy, q = labels[cur]
            prior = labels.index((fy - 1, q)) if (fy - 1, q) in labels else max(cur - 1, 0)
            return {"current": cur, "prior": prior, "months": 3,
                    "label": f"Q{q} FY{fy:02d} vs Q{labels[prior][1]} FY{labels[prior][0]:02d}"}

    for values in rows.values():  # US filing: a header row of years, e.g. 2025 2024 2023
        if len(values) >= 2 and all(v.is_integer() and 1990 <= v <= 2100 for v in values) and len(set(values)) == len(values):
            years = [int(v) for v in values]
            cur = years.index(max(years))
            prior = years.index(max(years) - 1) if max(years) - 1 in years else (1 if cur == 0 else cur - 1)
            months = 3 if "three months ended" in lowered else 12
            return {"current": cur, "prior": prior, "months": months,
                    "label": f"{'Q ' if months == 3 else 'FY'}{years[cur]} vs {'Q ' if months == 3 else 'FY'}{years[prior]}"}

    end = _period_end(text)
    if n_cols >= 3:
        # Indian results: [quarter, previous quarter, same quarter last year, (year to date, ...)].
        full_year = n_cols == 5 and "year ended" in lowered and not re.search(r"nine months|six months|half year", lowered)
        if full_year:
            label = f"Year ended {end[0]} {end[1]}, {end[2]} vs {end[2] - 1}" if end else "This year vs last year"
            return {"current": 3, "prior": 4, "label": label, "months": 12}
        label = (f"Quarter ended {end[0]} {end[1]}, {end[2]} vs same quarter {end[2] - 1}" if end
                 else "This quarter vs same quarter last year")
        return {"current": 0, "prior": 2, "label": label, "months": 3}

    months = 3 if "quarter ended" in lowered else 12
    return {"current": 0, "prior": 1, "label": "Current period vs prior period", "months": months}


# ----------------------------------------------------------------------------
# Row lookup
# ----------------------------------------------------------------------------
def _find(rows, *patterns, exclude=None):
    """
    First row whose key matches the earliest pattern in the list. Keys are also compared with
    underscores removed, so OCR'd labels split mid-word ("cost_of_mater_ials") still match.
    """
    for pattern in patterns:
        compact = pattern.replace("_", "")
        for key in rows:
            squeezed = key.replace("_", "")
            if (re.search(pattern, key) or re.search(compact, squeezed)) and not (
                    exclude and (re.search(exclude, key) or re.search(exclude.replace("_", ""), squeezed))):
                return key
    return None


REVENUE = (r"^total_revenue_from_operations", r"^revenue_from_operations$", r"^revenue_from_operations(?!_including)",
           r"^total_revenues?$", r"^net_revenues?$", r"^revenues?$", r"^net_sales$", r"^revenue_from_operations")
US_COGS = (r"^total_cost_of_revenues?$", r"^cost_of_revenues?$", r"^cost_of_sales$", r"^cost_of_goods_sold$",
           r"^cost_of_sales_and_services$")
MATERIALS = (r"cost_of_materials_consumed", r"materials_consumed")
PURCHASES = (r"^purchases?_of_(stock|products|traded|goods)",)
INVENTORY_CHANGE = (r"in_inventor", r"inventor\w*_change")
FINANCE = (r"^finance_costs?$", r"^finance_cost", r"^fina\w*_costs?$")
DNA = (r"^deprecia",)
TOTAL_EXPENSES = (r"^total_expenses", r"^total_costs_and_expenses$", r"^t_?ota[lt]_expen")
US_OPEX = (r"^total_operating_expenses$",)
OPERATING_INCOME = (r"^income_from_operations$", r"^operating_income$", r"^loss_from_operations$",
                    r"^income_loss_from_operations$")
OTHER_INCOME = (r"^other_income", r"^interest_and_other_income")
TOTAL_INCOME = (r"^total_income$", r"^total_income_")
PROFIT = (r"^net_income_attributable_to_common_stockholders$", r"^net_income$", r"^net_income_loss$", r"^net_loss$",
          r"^net_earnings_attributable_to_\w*shareholders$", r"^net_earnings$",
          r"^(net_)?(profit|loss)\w*_for_the_(period|year|quarter)", r"^pat$",
          r"^owners_of_the_company$")  # last resort: "Profit attributable to: Owners of the Company"
PROFIT_EXCLUDE = r"comprehensive|attributable_to_(owners|non)|continuing|discontinued|before|owners"
EBITDA = (r"^ebitda$",)

CF_NET = (r"^net_(increase|decrease|change)\w*_in_cash", r"^(increase|decrease)\w*_in_cash")
CF_OPERATING = (r"^net_cash\w*_(from|by|in)_operating_activities",)
CF_CLOSING = (r"^closing_balance_of_cash", r"^cash\w*_(at_)?(the_)?end_of_(the_)?(period|year)",
              r"^cash\w*_as_at_(march|december|june|september)_3[01]$")
CF_OPENING = (r"^opening_balance_of_cash", r"^cash\w*_(at_)?(the_)?begi\w*_of_(the_)?(period|year)",
              r"^cash\w*_as_at_(april|january|july|october)_1$")
CF_FX = (r"^eff\w*_of\w*exchange",)


# ----------------------------------------------------------------------------
# Page finding
# ----------------------------------------------------------------------------
def _is_consolidated(text):
    return "consolidated" in text.lower()


def _order(candidates, pages, prefer_consolidated):
    consolidated = [i for i in candidates if _is_consolidated(pages[i])]
    standalone = [i for i in candidates if not _is_consolidated(pages[i])]
    return consolidated + standalone if prefer_consolidated else standalone + consolidated


def _has_title(low, titles):
    """Title search that also works when the PDF's text layer lost its spaces ("CONSOLIDATEDSTATEMENTSOFEARNINGS")."""
    squeezed = low.replace(" ", "")
    return any(t in low or t.replace(" ", "") in squeezed for t in titles)


def _pl_candidates(pages, prefer_consolidated):
    hits = []
    for i, text in enumerate(pages):
        low = text.lower()
        if "revenue" in low and PL_ANCHOR_RE.search(low) and _has_title(low, STATEMENT_TITLES):
            hits.append(i)
    return _order(hits, pages, prefer_consolidated)


def _cf_candidates(pages, prefer_consolidated):
    hits = []
    for i, text in enumerate(pages):
        low = text.lower()
        if "operating activities" in low and _has_title(low, ("cash flow",)) and re.search(
                r"closing balance of cash|end of (the )?(period|year)|net (increase|decrease|change)", low):
            hits.append(i)
    return _order(hits, pages, prefer_consolidated)


# ----------------------------------------------------------------------------
# Units
# ----------------------------------------------------------------------------
def _detect_currency(text):
    low = text.lower()
    indian = "₹" in text or re.search(r"\binr\b|\brs\.?\s|crore|lakh|ind-?as|indas", low)
    if re.search(r"cr[o0]res?|cr[o0][,.]?[o0]s\b|cmres|\bcr\b", low):
        unit = "Cr"
    elif re.search(r"lakhs?|lacs|lsaas|laks", low):
        unit = "Lakh"
    elif re.search(r"in millions|\(millions|in mn\b", low):
        unit = "Mn"
    elif re.search(r"in billions", low):
        unit = "Bn"
    elif re.search(r"in thousands", low):
        unit = "K"
    else:
        unit = ""
    if indian or unit in ("Cr", "Lakh"):
        return f"INR {unit}".strip()
    code = "USD" if "$" in text or "dollar" in low else "EUR" if "€" in text or "euro" in low else "USD"
    return f"{code} {unit}".strip()


# ----------------------------------------------------------------------------
# Payload
# ----------------------------------------------------------------------------
def _pdfium_pages(data: bytes) -> list:
    doc = pdfium.PdfDocument(data)
    try:
        return [doc[i].get_textpage().get_text_range() for i in range(len(doc))]
    finally:
        doc.close()


def _pypdf_pages(data: bytes) -> list:
    return [p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages]


def _read_bytes(pdf_path) -> bytes:
    if hasattr(pdf_path, "read"):
        return pdf_path.read()
    with open(pdf_path, "rb") as fh:
        return fh.read()


def extract_pages(pdf_path, engine: str = "auto") -> list:
    """
    Text of every page. pdfium is 3-10x faster than pypdf on long annual reports (154 pages: ~3s vs ~40s)
    and keeps word spacing that pypdf loses on some PDFs; pypdf is used when pdfium isn't installed.
    """
    data = _read_bytes(pdf_path)
    texts = None
    if engine in ("auto", "pdfium") and pdfium is not None:
        try:
            texts = _pdfium_pages(data)
        except Exception:  # damaged or unusual PDF; let pypdf try
            texts = None
    if texts is None:
        texts = _pypdf_pages(data)
    # Some PDFs put tabs between words ("Total\tExpenses"); normalise so searches match.
    return [re.sub(r"[ \t]+", " ", t) for t in texts]


def _read_pl(rows, n_cols, text):
    """Pull the P&L figures out of parsed rows (all columns). Returns a dict or None if incomplete."""
    revenue_key = _find(rows, *REVENUE)
    if revenue_key is None:
        return None
    zero = (0.0,) * n_cols
    get = lambda key: rows[key] if key else zero  # noqa: E731
    order = list(rows)

    us_cogs, us_opex = _find(rows, *US_COGS), _find(rows, *US_OPEX)
    total_key = _find(rows, *TOTAL_EXPENSES)
    ebitda_key = _find(rows, *EBITDA)
    finance_key, dna_key = _find(rows, *FINANCE), _find(rows, *DNA)
    revenue = get(revenue_key)

    if us_cogs and (us_opex or total_key):
        cogs, basis = get(us_cogs), "ebit"
        if us_opex:
            opex = get(us_opex)
        else:  # only "Total costs and expenses" is given (e.g. Berkshire Hathaway)
            total = get(total_key)
            opex = tuple(total[i] - cogs[i] for i in range(n_cols))
        finance, dna = tuple(abs(v) for v in get(_find(rows, r"^interest_expense$"))), zero
    else:
        parts = [_find(rows, *MATERIALS), _find(rows, *PURCHASES), _find(rows, *INVENTORY_CHANGE)]
        finance, dna = get(finance_key), get(dna_key)
        flip = parts[0] is not None and get(parts[0])[0] < 0  # investor decks show costs in brackets
        sign = -1 if flip else 1
        cogs = tuple(sign * sum(get(p)[i] for p in parts) for i in range(n_cols))
        finance, dna = tuple(sign * v for v in finance), tuple(sign * v for v in dna)
        basis = "ebitda"
        if total_key:
            # Some results put finance costs and depreciation below Total Expenses; only subtract them if included.
            pos = order.index(total_key)
            in_total = lambda key: key is not None and order.index(key) < pos  # noqa: E731
            fin = finance if in_total(finance_key) else zero
            dep = dna if in_total(dna_key) else zero
            total = get(total_key)
            opex = tuple(total[i] - cogs[i] - fin[i] - dep[i] for i in range(n_cols))
        elif ebitda_key:
            ebitda = get(ebitda_key)
            opex = tuple(revenue[i] - cogs[i] - ebitda[i] for i in range(n_cols))
        else:
            return None

    profit_key = _find(rows, *PROFIT, exclude=PROFIT_EXCLUDE)
    return {
        "revenue": revenue, "cogs": cogs, "opex": opex, "finance": finance, "dna": dna, "basis": basis,
        "other_income": get(_find(rows, *OTHER_INCOME)),
        "total_income": rows.get(_find(rows, *TOTAL_INCOME)) if _find(rows, *TOTAL_INCOME) else None,
        "operating_income": rows.get(_find(rows, *OPERATING_INCOME)) if _find(rows, *OPERATING_INCOME) else None,
        "profit": rows[profit_key] if profit_key else None,
    }


def _has_other_cash_movements(text):
    """True when lines other than exchange-rate effects sit between opening and closing cash (demergers, disposals...)."""
    lines = [l.lower() for l in text.splitlines() if l.strip()]
    start = next((i for i, l in enumerate(lines) if re.search(
        r"opening\s*balance\s*of\s*cash|begi\w*ng\s*of\s*(the\s*)?(period|year)|as at (april|january|july|october) 1\b",
        l)), None)
    if start is None:
        return False
    end = next((i for i in range(start + 1, len(lines)) if re.search(
        r"closing\s*balance\s*of\s*cash|end\s*of\s*(the\s*)?(period|year)|as at (march|december|june|september) 3[01]",
        lines[i])), None)
    if end is None:
        return False
    # Numbers-only lines are the opening amounts themselves; a labelled line is another movement.
    return any(re.search(r"[a-z]{3}", l) and re.search(r"\d", l) and "exchange" not in l for l in lines[start + 1:end])


def _read_cf(text):
    rows, n_cols = parse_statement_rows(text)
    if n_cols < 2:
        return None
    cols = choose_columns(text, rows, n_cols)
    pick = lambda key: (rows[key][cols["current"]], rows[key][cols["prior"]]) if key else None  # noqa: E731
    cf = {
        "net": pick(_find(rows, *CF_NET)),
        "operating": pick(_find(rows, *CF_OPERATING)),
        "closing": pick(_find(rows, *CF_CLOSING)),
        "opening": pick(_find(rows, *CF_OPENING)),
        "fx": pick(_find(rows, *CF_FX)),
        "other_movements": _has_other_cash_movements(text),
    }
    return cf if cf["net"] or cf["closing"] else None


def build_payload(pages: list, prefer_consolidated: bool = True) -> dict:
    """Build an engine payload from page texts. Raises ExtractionError when statements can't be found."""
    pages = [re.sub(r"[ \t]+", " ", p) for p in pages]
    if sum(len(p.strip()) for p in pages) < 50 * max(len(pages), 1) and len(pages) > 0 and \
            not any(t in " ".join(pages).lower() for t in STATEMENT_TITLES):
        raise ExtractionError("This PDF has no readable text (it looks like a scanned image). "
                              "Download the text version of the report, or run it through OCR first.")

    pl, pl_idx, pl_text, cols = None, None, "", None
    for i in _pl_candidates(pages, prefer_consolidated):
        rows, n_cols = parse_statement_rows(pages[i])
        if n_cols < 2:
            continue
        found = _read_pl(rows, n_cols, pages[i])
        if found:
            pl, pl_idx, pl_text, cols = found, i, pages[i], choose_columns(pages[i], rows, n_cols)
            break
    if pl is None:
        raise ExtractionError(
            "Could not find a profit and loss statement in the PDF. We look for a Statement of Profit and Loss, "
            "a 'Statement of financial results', or a US 'Statement of Operations' with revenue and expense lines."
        )

    c, p = cols["current"], cols["prior"]
    pair = lambda values: (values[c], values[p])  # noqa: E731
    revenue, cogs, opex = pair(pl["revenue"]), pair(pl["cogs"]), pair(pl["opex"])
    other_income, finance, dna = pair(pl["other_income"]), pair(pl["finance"]), pair(pl["dna"])

    line_items = {"Revenue": revenue, "COGS": cogs, "OpEx": opex}
    if pl["profit"] is not None:
        line_items["Net Profit"] = pair(pl["profit"])

    # Cash flow: only when the P&L covers a full year (quarterly filings' cash flows cover a different period).
    cf, cf_idx = None, None
    if cols["months"] == 12:
        for i in _cf_candidates(pages, prefer_consolidated):
            if _is_consolidated(pages[i]) != _is_consolidated(pl_text):
                continue
            cf = _read_cf(pages[i])
            if cf and not cf["closing"] and i + 1 < len(pages):
                cf = _read_cf(pages[i] + "\n" + pages[i + 1]) or cf  # statement continues on the next page
            if cf:
                cf_idx = i
                break
    if cf and cf["net"]:
        line_items["Net Cash Flow"] = cf["net"]
    if cf and cf["operating"]:
        line_items["Operating Cash Flow"] = cf["operating"]

    # Reconciliation checks so a bad parse is visible instead of silently analysed.
    checks = {}
    if pl["total_income"] is not None:
        checks["total_income_reconciles"] = abs(revenue[0] + other_income[0] - pl["total_income"][c]) < 1
    if pl["operating_income"] is not None:
        checks["operating_income_reconciles"] = abs(revenue[0] - cogs[0] - opex[0] - pl["operating_income"][c]) < 1
    if cf and cf["net"] and cf["closing"] and cf["opening"] and not cf["other_movements"]:
        fx = cf["fx"][0] if cf["fx"] else 0.0  # some statements add exchange-rate effects after the net change
        checks["cash_reconciles"] = min(abs(cf["opening"][0] + cf["net"][0] + extra - cf["closing"][0])
                                        for extra in (0.0, fx)) < 1

    scope = "Consolidated" if _is_consolidated(pl_text) else "Standalone"
    kind = "Quarterly results" if cols["months"] == 3 else "Annual results"
    payload = {
        "period": cols["label"],
        "currency": _detect_currency(pl_text),
        "report_type": f"{kind} ({scope})",
        "analysis_mode": "yoy",
        "baseline_label": "prior year",
        "period_months": cols["months"],
        "line_items": {k: {"budget": v[1], "actual": v[0]} for k, v in line_items.items()},
        "source": {
            "profit_and_loss_page": pl_idx + 1,
            "cash_flow_page": cf_idx + 1 if cf_idx is not None else None,
            "scope": scope,
            "operating_income_basis": pl["basis"],
            "other_income": {"actual": other_income[0], "prior": other_income[1]},
            "depreciation": {"actual": dna[0], "prior": dna[1]},
            "finance_costs": {"actual": finance[0], "prior": finance[1]},
            "checks": checks,
        },
    }
    if cf and cf["closing"]:
        payload["cash_reserves"] = cf["closing"][0]
    return payload


def extract_payload_from_pdf(pdf_path, prefer_consolidated: bool = True) -> dict:
    data = _read_bytes(pdf_path)
    try:
        return build_payload(extract_pages(io.BytesIO(data)), prefer_consolidated=prefer_consolidated)
    except ExtractionError:
        if pdfium is None:
            raise
        # The two libraries lay text out differently; give the statements a second chance with pypdf.
        return build_payload(extract_pages(io.BytesIO(data), engine="pypdf"), prefer_consolidated=prefer_consolidated)


if __name__ == "__main__":
    import json
    import sys

    from fpa_engine import FPAAnalysisEngine

    payload = extract_payload_from_pdf(sys.argv[1])
    print(json.dumps(payload, indent=2))
    print(json.dumps(FPAAnalysisEngine().process_bva(payload)["cfo_executive_summary"], indent=2, ensure_ascii=False))
