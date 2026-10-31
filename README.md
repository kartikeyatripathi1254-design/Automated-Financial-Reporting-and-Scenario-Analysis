# Nexus FP&A

[![CI](https://github.com/kartikeyatripathi1254-design/Automated-Financial-Reporting-and-Scenario-Analysis/actions/workflows/ci.yml/badge.svg)](https://github.com/kartikeyatripathi1254-design/Automated-Financial-Reporting-and-Scenario-Analysis/actions/workflows/ci.yml)

**Financial performance, explained.** Upload a company's financials and get a plain-English health check:
a 0–100 health score, the key numbers, what went well, what to watch, recommended next steps,
what-if scenarios and a board-ready report.

Built with Python and Streamlit.

## Features

- **Annual report PDFs (Ind-AS)**: reads only the consolidated Statement of Profit and Loss and
  Cash Flow Statement, handles Indian number formats (`10,75,675`) and bracketed negatives, and checks
  that totals reconcile. Compares this year with last year.
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
| `ind_as_extractor.py` | Reads the financial statements from Indian (Ind-AS) annual report PDFs |
| `report.py` | Builds the downloadable board report |
| `.streamlit/config.toml` | Theme and app settings |
| `test_fpa_engine.py`, `test_ind_as_extractor.py` | Unit tests for the analysis engine and the PDF statement parser (pytest) |
| `test_app.py` | Smoke test that renders the dashboard with the sample data |
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

The tests that read a real annual report run only when `RIL-Integrated-Annual-Report-2025-26.pdf`
(Reliance Industries' FY2025-26 Integrated Annual Report, from ril.com) is in the project folder;
otherwise they're skipped. PDFs are excluded from the repository by `.gitignore`.

## Notes

- "Operating profit" is revenue minus cost of goods and running costs, **before depreciation**
  (close to EBITDA). For annual reports, the *Data & glossary* tab also shows EBITDA and EBIT.
- Analysis is automated. Review the results before using them externally.
