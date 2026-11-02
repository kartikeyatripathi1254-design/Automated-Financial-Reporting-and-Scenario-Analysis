import json

import pytest

from fpa_engine import FPAAnalysisEngine, normalize_item


@pytest.fixture
def engine():
    return FPAAnalysisEngine()


def payload(**line_items):
    return {"period": "Oct 2026", "currency": "EUR", "line_items": line_items}


def flag_types(result):
    return {(f["line_item"], f["type"]) for f in result["critical_flags"]}


def test_normalize_item():
    assert normalize_item(" Cost of Goods-Sold ") == "cost_of_goods_sold"
    assert normalize_item("OpEx") == "opex"


def test_long_form_names_feed_kpis(engine):
    # Bug 1: "Operating Expenses" / "Cost of Goods Sold" were flagged but counted as 0 in EBIT.
    result = engine.process_bva(payload(**{
        "Revenue": {"budget": 1000, "actual": 1000},
        "Cost of Goods Sold": {"budget": 400, "actual": 400},
        "Operating Expenses": {"budget": 300, "actual": 300},
    }))
    kpis = result["summary_kpis"]
    assert kpis["gross_margin_pct"]["actual"] == 60.0
    assert kpis["operating_income"]["actual"] == 300.0


def test_cost_of_revenue_is_an_expense(engine):
    # Bug 2: "cost_of_revenue" contained "revenue" and was treated as revenue.
    result = engine.process_bva(payload(
        revenue={"budget": 1000, "actual": 1000},
        cost_of_revenue={"budget": 400, "actual": 500},
    ))
    row = next(r for r in result["line_item_analysis"] if r["line_item"] == "cost_of_revenue")
    assert row["category"] == "cogs"
    assert row["is_favorable"] is False
    assert ("cost_of_revenue", "EXPENSE_OVERRUN") in flag_types(result)
    assert result["summary_kpis"]["gross_margin_pct"]["actual"] == 50.0


def test_unrelated_items_containing_keywords_are_not_categorized(engine):
    result = engine.process_bva(payload(
        revenue={"budget": 1000, "actual": 1000},
        deferred_revenue={"budget": 100, "actual": 50},
    ))
    row = next(r for r in result["line_item_analysis"] if r["line_item"] == "deferred_revenue")
    assert row["category"] is None
    assert result["critical_flags"] == []


def test_forecast_applies_to_long_form_opex(engine):
    # Bug 3: forecast used item.lower() without "_" so "Operating Expenses" was skipped.
    base = payload(**{
        "Revenue": {"budget": 1000, "actual": 1000},
        "Cost of Goods Sold": {"budget": 400, "actual": 400},
        "Operating Expenses": {"budget": 300, "actual": 300},
        "Net Cash Flow": {"budget": 100, "actual": 100},
    })
    result = engine.forecast_scenario(base, revenue_growth_pct=10, opex_adjustment_pct=20)
    actuals = {r["line_item"]: r["actual"] for r in result["line_item_analysis"]}
    assert actuals["Revenue"] == 1100.0
    assert actuals["Cost of Goods Sold"] == 440.0  # scales with revenue by default
    assert actuals["Operating Expenses"] == 360.0
    # EBIT moves 300 -> 300 (+100 rev -40 cogs -60 opex = 0), so cash unchanged
    assert actuals["Net Cash Flow"] == 100.0
    assert result["metadata"]["report_type"] == "Scenario Forecast"
    assert "Scenario Forecast" in result["cfo_executive_summary"]["headline"]
    # base payload must not be mutated
    assert base["line_items"]["Revenue"]["actual"] == 1000


def test_forecast_moves_cash_and_runway(engine):
    base = payload(
        revenue={"budget": 1000, "actual": 1000},
        opex={"budget": 1000, "actual": 1100},
        net_cash_flow={"budget": 0, "actual": -100},
    )
    base["cash_reserves"] = 1200
    before = engine.process_bva(base)["summary_kpis"]["cash_runway_months"]
    after = engine.forecast_scenario(base, revenue_growth_pct=0, opex_adjustment_pct=10)
    assert before == 12.0
    assert after["summary_kpis"]["net_cash_flow"]["actual"] == -210.0
    assert after["summary_kpis"]["cash_runway_months"] == round(1200 / 210, 1)


def test_forecast_cogs_can_follow_opex_factor(engine):
    base = payload(revenue={"budget": 1000, "actual": 1000}, cogs={"budget": 400, "actual": 400})
    result = engine.forecast_scenario(base, 10, -50, cogs_scales_with_revenue=False)
    actuals = {r["line_item"]: r["actual"] for r in result["line_item_analysis"]}
    assert actuals["cogs"] == 200.0


def test_zero_budget_gives_json_safe_output(engine):
    # Bug 4: budget 0 produced inf/NaN.
    result = engine.process_bva(payload(
        revenue={"budget": 1000, "actual": 1000},
        opex={"budget": 0, "actual": 50},
        other_income={"budget": 0, "actual": 0},
    ))
    json.dumps(result, allow_nan=False)
    rows = {r["line_item"]: r for r in result["line_item_analysis"]}
    assert rows["opex"]["variance_pct"] is None
    assert rows["other_income"]["variance_pct"] is None
    opex_flag = next(f for f in result["critical_flags"] if f["line_item"] == "opex")
    assert "Unbudgeted spend" in opex_flag["message"]
    assert "inf" not in json.dumps(result)


def test_zero_revenue_gross_margin_is_none(engine):
    result = engine.process_bva(payload(revenue={"budget": 0, "actual": 0}, cogs={"budget": 0, "actual": 0}))
    json.dumps(result, allow_nan=False)
    assert result["summary_kpis"]["gross_margin_pct"]["actual"] is None


def test_kpi_uses_priority_not_dict_order(engine):
    # Bug 5: first "revenue" match by dict order fed the KPI.
    result = engine.process_bva(payload(
        gross_revenue={"budget": 2000, "actual": 2000},
        net_revenue={"budget": 1000, "actual": 1000},
        cogs={"budget": 500, "actual": 500},
    ))
    assert result["metadata"]["kpi_sources"]["revenue"] == "net_revenue"
    assert result["summary_kpis"]["gross_margin_pct"]["actual"] == 50.0


def test_runway_none_without_reserves(engine):
    result = engine.process_bva(payload(net_cash_flow={"budget": 0, "actual": -100}))
    assert result["summary_kpis"]["cash_runway_months"] is None
    assert "no cash reserves provided" in result["cfo_executive_summary"]["cash_and_liquidity"]


def test_runway_uses_period_months(engine):
    p = payload(net_cash_flow={"budget": 0, "actual": -300})
    p["cash_reserves"] = 1200
    p["period_months"] = 3
    assert engine.process_bva(p)["summary_kpis"]["cash_runway_months"] == 12.0


def test_no_burn_runway_is_none(engine):
    p = payload(net_cash_flow={"budget": 0, "actual": 100})
    p["cash_reserves"] = 1200
    assert engine.process_bva(p)["summary_kpis"]["cash_runway_months"] is None


def test_thresholds_are_configurable():
    engine = FPAAnalysisEngine(overrun_threshold_pct=5, critical_threshold_pct=8)
    result = engine.process_bva(payload(opex={"budget": 100, "actual": 109}))
    assert result["critical_flags"][0]["severity"] == "CRITICAL"
    assert ">5%" in result["cfo_executive_summary"]["recommended_actions"][0]
    with pytest.raises(ValueError):
        FPAAnalysisEngine(overrun_threshold_pct=25, critical_threshold_pct=20)


def test_opex_overrun_with_ebit_still_above_budget(engine):
    result = engine.process_bva(payload(
        revenue={"budget": 1000, "actual": 1500},
        opex={"budget": 500, "actual": 600},
    ))
    text = result["cfo_executive_summary"]["cost_and_margins"]
    assert "depressing" not in text
    assert "still closed" in text


def test_on_budget_wording(engine):
    result = engine.process_bva(payload(revenue={"budget": 1000, "actual": 1000}, opex={"budget": 500, "actual": 500}))
    summary = result["cfo_executive_summary"]
    assert "in line with budget" in summary["topline_performance"]
    assert "landed on budget" in summary["cost_and_margins"]


def test_cash_flag_names_actual_line(engine):
    result = engine.process_bva(payload(free_cash_flow={"budget": 0, "actual": -50}))
    assert result["critical_flags"][0]["message"].startswith("Negative free_cash_flow")


def test_non_numeric_values_raise(engine):
    with pytest.raises(ValueError, match="opex"):
        engine.process_bva(payload(revenue={"budget": 1000, "actual": 1000}, opex={"budget": "abc", "actual": 1}))
    with pytest.raises(ValueError, match="cogs"):
        engine.process_bva(payload(revenue={"budget": 1000, "actual": 1000}, cogs={"budget": 100}))


def test_numeric_strings_accepted(engine):
    result = engine.process_bva(payload(revenue={"budget": "1000", "actual": "900"}))
    assert result["line_item_analysis"][0]["variance_pct"] == -10.0


def test_revenue_shortfall_flag(engine):
    result = engine.process_bva(payload(revenue={"budget": 1000, "actual": 750}))
    flag = result["critical_flags"][0]
    assert flag["type"] == "REVENUE_SHORTFALL"
    assert flag["severity"] == "CRITICAL"
    assert flag["shortfall"] == 250.0


# --- Formatting, year-over-year mode and plain-English insights ---

from fpa_engine import format_compact, format_money


def test_format_money_indian_and_western():
    assert format_money(1075675, "INR Cr") == "₹10,75,675 Cr"
    assert format_money(-10267, "INR Cr") == "-₹10,267 Cr"
    assert format_money(95539, "INR Cr", signed=True) == "+₹95,539 Cr"
    assert format_money(1380000, "EUR") == "€1,380,000"
    assert format_money(50, "USD") == "$50.00"
    assert format_money(1200, "CHF") == "CHF 1,200"


def test_format_compact():
    assert format_compact(1075675, "INR Cr") == "₹10.76 lakh crore"
    assert format_compact(95539, "INR Cr") == "₹95,539 crore"
    assert format_compact(1380000, "EUR") == "€1.38M"
    assert format_compact(-45000, "EUR") == "-€45.0K"
    assert format_compact(270000, "EUR") == "€270K"
    assert format_compact(250000, "INR") == "₹2.50 lakh"


def test_yoy_costs_growing_slower_than_revenue_are_fine(engine):
    result = engine.process_bva({
        "analysis_mode": "yoy",
        "line_items": {
            "revenue": {"budget": 1000, "actual": 1200},   # +20%
            "opex": {"budget": 500, "actual": 575},        # +15%: slower than sales
        },
    })
    assert result["critical_flags"] == []
    assert next(r for r in result["line_item_analysis"] if r["line_item"] == "opex")["is_favorable"] is True
    assert result["metadata"]["baseline"] == "prior year"


def test_yoy_costs_outpacing_revenue_are_flagged(engine):
    result = engine.process_bva({
        "analysis_mode": "yoy",
        "line_items": {
            "revenue": {"budget": 1000, "actual": 1050},   # +5%
            "opex": {"budget": 500, "actual": 600},        # +20%
        },
    })
    assert result["critical_flags"][0]["message"] == "Cost growth of 20.00% vs prior year, ahead of revenue growth of 5.0%"
    assert "faster than sales" in " ".join(result["plain_english"]["watch_out"])


def test_plain_english_verdicts(engine):
    healthy = engine.process_bva(payload(
        revenue={"budget": 1000, "actual": 1100}, cogs={"budget": 400, "actual": 420},
        opex={"budget": 300, "actual": 290}, net_cash_flow={"budget": 50, "actual": 80}))
    assert healthy["plain_english"]["status"] == "healthy"
    assert healthy["plain_english"]["watch_out"] == []
    assert any("Sales grew 10.0%" in g for g in healthy["plain_english"]["good_news"])

    at_risk = engine.process_bva(payload(
        revenue={"budget": 1000, "actual": 700}, opex={"budget": 300, "actual": 900},
        net_cash_flow={"budget": 50, "actual": -100}))
    pe = at_risk["plain_english"]
    assert pe["status"] == "at_risk"
    assert any("operating loss" in w for w in pe["watch_out"])
    assert any("Collect money owed" in s for s in pe["next_steps"])


def test_kpis_include_revenue_and_per_100(engine):
    result = engine.process_bva(payload(
        revenue={"budget": 1000, "actual": 1000}, cogs={"budget": 400, "actual": 400},
        opex={"budget": 300, "actual": 300}))
    kpis = result["summary_kpis"]
    assert kpis["revenue"]["actual"] == 1000
    assert kpis["per_100_of_revenue"] == {"cogs": 40.0, "opex": 30.0, "operating_income": 30.0}
    assert kpis["net_cash_flow"]["available"] is False


def test_health_score(engine):
    clean = engine.process_bva(payload(revenue={"budget": 1000, "actual": 1100}, opex={"budget": 300, "actual": 300}))
    assert clean["plain_english"]["health_score"] == 100

    # Sample payload: 2 cost warnings (-24), negative cash critical (-25), sales down (-5), margin down (-5).
    sample = engine.process_bva(payload(
        Revenue={"budget": 1500000, "actual": 1380000}, COGS={"budget": 450000, "actual": 510000},
        OpEx={"budget": 700000, "actual": 790000}, **{"Net Cash Flow": {"budget": 200000, "actual": -45000}}))
    assert sample["plain_english"]["health_score"] == 41

    wrecked = engine.process_bva(payload(revenue={"budget": 1000, "actual": 100}, opex={"budget": 100, "actual": 900},
                                         net_cash_flow={"budget": 0, "actual": -500}))
    assert wrecked["plain_english"]["health_score"] == 0
