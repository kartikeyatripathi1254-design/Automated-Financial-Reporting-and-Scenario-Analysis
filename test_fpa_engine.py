import pytest

from fpa_engine import FPAAnalysisEngine, format_compact, format_money, normalize_item

SAMPLE = {
    "period": "October 2026",
    "currency": "EUR",
    "analysis_mode": "bva",
    "cash_reserves": 1_200_000.0,
    "line_items": {
        "Revenue": {"budget": 1_500_000.0, "actual": 1_380_000.0},
        "COGS": {"budget": 450_000.0, "actual": 510_000.0},
        "OpEx": {"budget": 700_000.0, "actual": 790_000.0},
        "Net Cash Flow": {"budget": 200_000.0, "actual": -45_000.0},
    },
}


@pytest.fixture
def engine():
    return FPAAnalysisEngine()


def test_normalize_item():
    assert normalize_item("Cost of Goods Sold") == "cost_of_goods_sold"
    assert normalize_item("  cost-of-goods sold ") == "cost_of_goods_sold"


def test_categorize_aliases(engine):
    assert engine.categorize("Revenue from Operations") == "revenue"
    assert engine.categorize("Cost of Sales") == "cogs"
    assert engine.categorize("Operating Expenses") == "opex"
    assert engine.categorize("Free Cash Flow") == "cash"
    assert engine.categorize("Office plants") is None


def test_money_formatting():
    assert format_money(1_075_675, "INR Cr") == "₹10,75,675 Cr"
    assert format_money(-45_000, "EUR") == "-€45,000"
    assert format_compact(1_380_000, "EUR") == "€1.38M"


def test_kpis_for_sample(engine):
    k = engine.process_bva(SAMPLE)["summary_kpis"]
    assert k["revenue"]["variance_abs"] == pytest.approx(-120_000)
    assert k["revenue"]["variance_pct"] == pytest.approx(-8.0)
    assert k["gross_margin_pct"]["actual"] == pytest.approx(63.04, abs=0.01)
    assert k["operating_income"]["actual"] == pytest.approx(80_000)
    assert k["net_cash_flow"]["is_negative"] is True
    assert k["cash_runway_months"] == pytest.approx(26.7, abs=0.1)


def test_flags_for_sample(engine):
    flags = {(f["line_item"], f["type"], f["severity"]) for f in engine.process_bva(SAMPLE)["critical_flags"]}
    assert ("COGS", "EXPENSE_OVERRUN", "WARNING") in flags
    assert ("OpEx", "EXPENSE_OVERRUN", "WARNING") in flags
    assert ("Net Cash Flow", "NEGATIVE_CASH_FLOW", "CRITICAL") in flags
    # Revenue is 8% under budget, inside the 10% tolerance: no shortfall flag.
    assert not any(f[1] == "REVENUE_SHORTFALL" for f in flags)


def test_on_budget_is_healthy(engine):
    payload = {"line_items": {
        "Revenue": {"budget": 100.0, "actual": 100.0},
        "COGS": {"budget": 30.0, "actual": 30.0},
        "OpEx": {"budget": 40.0, "actual": 40.0},
    }}
    result = engine.process_bva(payload)
    assert result["critical_flags"] == []
    assert result["plain_english"]["health_score"] == 100


def test_threshold_controls_flags():
    strict = FPAAnalysisEngine(overrun_threshold_pct=5, critical_threshold_pct=10)
    sev = {f["line_item"]: f["severity"] for f in strict.process_bva(SAMPLE)["critical_flags"]}
    assert sev["COGS"] == "CRITICAL"  # 13.3% over budget > 10%


def test_scenario_scales_revenue_and_cash(engine):
    base = engine.process_bva(SAMPLE)["summary_kpis"]
    sim = engine.forecast_scenario(SAMPLE, revenue_growth_pct=10, opex_adjustment_pct=0)["summary_kpis"]
    assert sim["revenue"]["actual"] == pytest.approx(1_518_000)
    # COGS scales with revenue, so only the gross profit on the extra sales reaches cash.
    delta = sim["operating_income"]["actual"] - base["operating_income"]["actual"]
    assert delta == pytest.approx(138_000 - 51_000)
    assert sim["net_cash_flow"]["actual"] == pytest.approx(-45_000 + delta)
    assert SAMPLE["line_items"]["Revenue"]["actual"] == 1_380_000.0  # input not mutated


@pytest.mark.parametrize("payload", [{}, {"line_items": {}}, {"line_items": {"Revenue": {"actual": 1.0}}}])
def test_invalid_payloads_raise(engine, payload):
    with pytest.raises(ValueError):
        engine.process_bva(payload)


def test_non_numeric_values_raise(engine):
    with pytest.raises(ValueError, match="Revenue"):
        engine.process_bva({"line_items": {"Revenue": {"budget": "abc", "actual": 1.0}}})


def test_critical_below_overrun_rejected():
    with pytest.raises(ValueError):
        FPAAnalysisEngine(overrun_threshold_pct=20, critical_threshold_pct=10)
