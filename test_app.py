"""Smoke test: the dashboard renders with the sample data without raising."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parent / "app.py")


def test_app_renders_sample_data():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
