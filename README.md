# Nexus FP&A

[![CI](https://github.com/kartikeyatripathi1254-design/Automated-Financial-Reporting-and-Scenario-Analysis/actions/workflows/ci.yml/badge.svg)](https://github.com/kartikeyatripathi1254-design/Automated-Financial-Reporting-and-Scenario-Analysis/actions/workflows/ci.yml)

**Financial performance, explained.** Upload a company's financials and get a plain-English health check:
a 0–100 health score, the key numbers, what went well, what to watch, recommended next steps,
what-if scenarios and a board-ready report.

Built with Python and Streamlit.

## Features

- **Financial statement PDFs**: reads only the profit & loss and cash flow statements (consolidated first) from
  - Indian annual reports (Ind-AS), e.g. Reliance Industries
  - Indian quarterly / annual results filings (4–6 columns), e.g. Ola Electric, Tata Motors
  - investor-presentation P&L slides with quarter columns (`Q1 FY26 … Q1 FY27`)
  - US 10-K / 10-Q filings and annual reports, e.g. Tesla, Berkshire Hathaway

  Handles Indian number formats (`10,75,675`), bracketed negatives, and scanned PDFs with OCR slips
  (`4B`, `(4281`, `69,60 5`). Compares with the same period last year and checks that totals reconcile.
  Text is read with pdfium (a 150-page annual report takes about 3 seconds), with pypdf as a fallback.
- **Budget vs actual**: upload a CSV or Excel file with *Line Item*, *Budget* (or *Last Year*) and *Actual* columns.
- **Health score and verdict**: Healthy / Watch / At risk, with reasons in everyday language.
- **Charts**: sales-to-profit waterfall, where every 100 of sales went, and actual vs budget/last year.
- **What-if scenarios**: change sales and running costs and see the effect on profit and cash.
- **Exports**: a printable HTML board report (Print → Save as PDF), the line-item table (CSV) and the full result (JSON).

## Project structure

| File | What it does |
|---|---|
| `app.py` | The Streamlit dashboard |
| `ui.py` | Design system: theme, cards, header and other UI components |
| `fpa_engine.py` | Analysis engine: variances, alerts, health score, CFO commentary and plain-English insights |
| `ind_as_extractor.py` | Reads the financial statements from annual reports, results filings and 10-Ks |
| `report.py` | Builds the downloadable board report |
| `.streamlit/config.toml` | Theme and app settings |
| `test_fpa_engine.py`, `test_ind_as_extractor.py` | Unit tests for the analysis engine and the PDF statement parser (pytest) |
| `test_app.py` | Dashboard tests: renders with the sample data, what-if sliders, board report |
| `Dockerfile` | Container image for running the dashboard anywhere |
| `.github/workflows/ci.yml` | Runs linting, the tests and a Docker build on every push |

## Getting started

```bash
git clone https://github.com/kartikeyatripathi1254-design/Automated-Financial-Reporting-and-Scenario-Analysis.git
cd Automated-Financial-Reporting-and-Scenario-Analysis
python -m venv .venv
.venv\Scripts\activate          # Windows   (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
streamlit run app.py
```

The dashboard opens at http://localhost:8501. Choose **Sample data** to look around, or **Upload my file**.

### Run with Docker

```bash
docker build -t nexus-fpa .
docker run -p 8501:8501 nexus-fpa
```

## Tests

```bash
python -m pytest -q
```

The tests that read real filings run only when the PDFs are present: `RIL-Integrated-Annual-Report-2025-26.pdf`
in the project folder, and the Tesla 10-K, Berkshire Hathaway annual report, Tata Motors and Ola Electric results in `test_pdfs/`
(all public, from the companies' investor pages); otherwise they're skipped. PDFs are excluded from the repository by `.gitignore`.

## Notes

- "Operating profit" is revenue minus cost of goods and running costs. For Indian filings it's **before
  depreciation** (close to EBITDA); US filings already include depreciation in costs, so it's EBIT.
- Quarterly filings don't contain a quarter's cash flow, so cash figures are only shown for annual statements.
- Analysis is automated. Review the results before using them externally.
