import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from app import SAMPLE_PAYLOAD, table_to_payload
from fpa_engine import FPAAnalysisEngine
from report import build_board_report


def page_html(at) -> str:
    return " ".join(m.value for m in at.markdown)


def test_sample_dashboard_renders_without_errors():
    at = AppTest.from_file("app.py", default_timeout=60).run()
    assert not at.exception
    html = page_html(at)
    assert "How is the business doing?" in html
    assert "Needs action" in html          # sample has negative cash + overruns
    assert "€1.38M" in html                # sales card
    assert "Health score 41 out of 100" in html
    assert [t.label for t in at.tabs][:4] == ["Overview", "Charts", "Line items", "What if?"]


def test_what_if_slider_updates_profit():
    at = AppTest.from_file("app.py", default_timeout=60).run()
    next(s for s in at.slider if s.label == "Sales change (%)").set_value(10).run()
    assert not at.exception
    assert any("operating profit would rise" in i.value for i in at.info)


def test_upload_mode_without_file_shows_landing_page():
    at = AppTest.from_file("app.py", default_timeout=60).run()
    at.sidebar.radio[0].set_value("Upload my file").run()
    assert not at.exception
    assert "Welcome to Nexus FP&amp;A" in page_html(at) or "Welcome to Nexus FP&A" in page_html(at)


def test_board_report_contains_key_figures_and_escapes_names():
    payload = {**SAMPLE_PAYLOAD, "line_items": {**SAMPLE_PAYLOAD["line_items"],
                                                "<script>x</script>": {"budget": 1, "actual": 2}}}
    html = build_board_report(FPAAnalysisEngine().process_bva(payload))
    assert html.startswith("<!doctype html>")
    assert "Health score 41/100" in html
    assert "€1.38M" in html
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html


def test_table_to_payload_detects_columns_and_brackets():
    df = pd.DataFrame({"Account": ["Revenue", "OpEx", "Notes"],
                       "Last Year": ["1,000", "(200)", "n/a"],
                       "This Year": ["1,200", "250", "x"]})
    payload = table_to_payload(df, "yoy", "INR Cr", "FY26")
    assert payload["line_items"] == {"Revenue": {"budget": 1000, "actual": 1200},
                                     "OpEx": {"budget": -200, "actual": 250}}
    assert payload["source"]["skipped_rows"] == ["Notes"]
    assert payload["analysis_mode"] == "yoy"


def test_table_to_payload_rejects_single_value_column():
    with pytest.raises(ValueError):
        table_to_payload(pd.DataFrame({"Item": ["Revenue"], "Amount": [1]}), "bva", "EUR", "")
