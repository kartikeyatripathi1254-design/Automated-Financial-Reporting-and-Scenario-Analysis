"""
SaaS FP&A Platform: Automated Financial Reporting & Variance Analysis
"""

from __main__ import *
import json
from typing import Any, Dict, List
import numpy as np
import pandas as pd
import streamlit as st

# ----------------------------------------------------------------------------
# 1. Core FP&A Engine Class
# ----------------------------------------------------------------------------
class FPAAnalysisEngine:
    EXPENSE_ITEMS = {"cogs", "opex", "operating_expenses", "cost_of_goods_sold"}
    REVENUE_ITEMS = {"revenue", "gross_revenue", "net_revenue"}
    CASH_ITEMS = {"net_cash_flow", "operating_cash_flow", "free_cash_flow"}

    def __init__(self, overrun_threshold_pct: float = 10.0):
        self.overrun_threshold_pct = overrun_threshold_pct

    def process_bva(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        period = payload.get("period", "Current Period")
        currency = payload.get("currency", "USD")
        line_items_data = payload.get("line_items", {})

        df = pd.DataFrame.from_dict(line_items_data, orient="index")
        df.index.name = "line_item"
        df.reset_index(inplace=True)

        df["normalized_item"] = df["line_item"].str.lower().str.replace(" ", "_")
        df["variance_abs"] = df["actual"] - df["budget"]
        df["variance_pct"] = (df["variance_abs"] / df["budget"].abs()) * 100.0

        flags: List[Dict[str, Any]] = []
        is_favorable_list: List[bool] = []
        flag_notes: List[Any] = []

        for _, row in df.iterrows():
            item = row["normalized_item"]
            b = float(row["budget"])
            a = float(row["actual"])
            var_pct = float(row["variance_pct"])

            if any(exp in item for exp in self.EXPENSE_ITEMS):
                is_fav = bool(a <= b)
                flag_str = None
                if not is_fav and var_pct > self.overrun_threshold_pct:
                    flag_str = f"Budget overrun of {var_pct:.2f}% (> {self.overrun_threshold_pct}% threshold)"
                    flags.append({
                        "line_item": str(row["line_item"]),
                        "type": "EXPENSE_OVERRUN",
                        "severity": "CRITICAL" if var_pct > 20 else "WARNING",
                        "message": flag_str,
                        "excess_spend": round(a - b, 2)
                    })
                is_favorable_list.append(is_fav)
                flag_notes.append(flag_str)

            elif any(rev in item for rev in self.REVENUE_ITEMS):
                is_fav = bool(a >= b)
                flag_str = None
                if not is_fav and abs(var_pct) > self.overrun_threshold_pct:
                    flag_str = f"Revenue shortfall of {abs(var_pct):.2f}% vs budget"
                    flags.append({
                        "line_item": str(row["line_item"]),
                        "type": "REVENUE_SHORTFALL",
                        "severity": "CRITICAL" if abs(var_pct) > 20 else "WARNING",
                        "message": flag_str,
                        "shortfall": round(b - a, 2)
                    })
                is_favorable_list.append(is_fav)
                flag_notes.append(flag_str)

            elif any(cash in item for cash in self.CASH_ITEMS):
                is_fav = bool(a >= b)
                flag_str = None
                if a < 0:
                    flag_str = f"Negative net cash flow of {currency} {a:,.2f}"
                    flags.append({
                        "line_item": str(row["line_item"]),
                        "type": "NEGATIVE_CASH_FLOW",
                        "severity": "CRITICAL",
                        "message": flag_str,
                        "cash_burn": round(abs(a), 2)
                    })
                is_favorable_list.append(is_fav)
                flag_notes.append(flag_str)
            else:
                is_favorable_list.append(bool(a >= b))
                flag_notes.append(None)

        df["is_favorable"] = is_favorable_list
        df["flag"] = flag_notes

        rev_match = df[df["normalized_item"].str.contains("revenue")]
        cogs_match = df[df["normalized_item"].str.contains("cogs")]
        opex_match = df[df["normalized_item"].str.contains("opex")]
        cash_match = df[df["normalized_item"].str.contains("cash")]

        rev_row = rev_match.iloc[0] if len(rev_match) > 0 else None
        cogs_row = cogs_match.iloc[0] if len(cogs_match) > 0 else None
        opex_row = opex_match.iloc[0] if len(opex_match) > 0 else None
        cash_row = cash_match.iloc[0] if len(cash_match) > 0 else None

        actual_rev = float(rev_row["actual"]) if rev_row is not None else 0.0
        budget_rev = float(rev_row["budget"]) if rev_row is not None else 0.0
        actual_cogs = float(cogs_row["actual"]) if cogs_row is not None else 0.0
        budget_cogs = float(cogs_row["budget"]) if cogs_row is not None else 0.0
        actual_opex = float(opex_row["actual"]) if opex_row is not None else 0.0
        budget_opex = float(opex_row["budget"]) if opex_row is not None else 0.0
        actual_cash = float(cash_row["actual"]) if cash_row is not None else 0.0

        actual_gp = actual_rev - actual_cogs
        budget_gp = budget_rev - budget_cogs

        actual_gm_pct = (actual_gp / actual_rev * 100.0) if actual_rev != 0 else 0.0
        budget_gm_pct = (budget_gp / budget_rev * 100.0) if budget_rev != 0 else 0.0

        actual_ebit = actual_gp - actual_opex
        budget_ebit = budget_gp - budget_opex

        commentary = self._generate_cfo_commentary(
            period=period, currency=currency, rev_row=rev_row, opex_row=opex_row,
            cash_row=cash_row, actual_gm_pct=actual_gm_pct, budget_gm_pct=budget_gm_pct,
            actual_ebit=actual_ebit, budget_ebit=budget_ebit, flags=flags
        )

        variance_details = []
        for _, row in df.iterrows():
            variance_details.append({
                "line_item": str(row["line_item"]),
                "budget": round(float(row["budget"]), 2),
                "actual": round(float(row["actual"]), 2),
                "variance_abs": round(float(row["variance_abs"]), 2),
                "variance_pct": round(float(row["variance_pct"]), 2),
                "is_favorable": bool(row["is_favorable"]),
                "flag": row["flag"]
            })

        return {
            "metadata": {"period": str(period), "currency": str(currency), "overrun_threshold_pct": float(self.overrun_threshold_pct)},
            "summary_kpis": {
                "gross_margin_pct": {"budget": round(budget_gm_pct, 2), "actual": round(actual_gm_pct, 2), "variance_bps": round((actual_gm_pct - budget_gm_pct) * 100, 1)},
                "operating_income": {"budget": round(budget_ebit, 2), "actual": round(actual_ebit, 2), "variance_abs": round(actual_ebit - budget_ebit, 2)},
                "net_cash_flow": {"actual": round(actual_cash, 2), "is_negative": bool(actual_cash < 0)}
            },
            "line_item_analysis": variance_details,
            "critical_flags": flags,
            "cfo_executive_summary": commentary
        }

    def _generate_cfo_commentary(self, period, currency, rev_row, opex_row, cash_row, actual_gm_pct, budget_gm_pct, actual_ebit, budget_ebit, flags):
        if rev_row is not None:
            rev_pct = float(rev_row["variance_pct"])
            rev_abs = float(rev_row["variance_abs"])
            topline_narrative = (f"Revenue reached {currency} {float(rev_row['actual']):,.2f}, outperforming budget by {rev_pct:.1f}% (+{currency} {rev_abs:,.2f})." if rev_pct >= 0 else f"Revenue fell short of plan by {abs(rev_pct):.1f}% (-{currency} {abs(rev_abs):,.2f}), closing at {currency} {float(rev_row['actual']):,.2f}.")
        else:
            topline_narrative = "Revenue data not provided."

        gm_variance_bps = (actual_gm_pct - budget_gm_pct) * 100
        cost_narrative = f"Gross Margin was {actual_gm_pct:.1f}% vs. {budget_gm_pct:.1f}% budgeted ({gm_variance_bps:+.0f} bps). "
        if opex_row is not None:
            opex_pct = float(opex_row["variance_pct"])
            opex_abs = float(opex_row["variance_abs"])
            cost_narrative += (f"OpEx exceeded plan by {opex_pct:.1f}% (+{currency} {opex_abs:,.2f}), depressing Operating Income to {currency} {actual_ebit:,.2f} vs. {currency} {budget_ebit:,.2f} expected." if opex_pct > 0 else f"OpEx came in under budget by {abs(opex_pct):.1f}% (-{currency} {abs(opex_abs):,.2f}).")

        cash_val = float(cash_row["actual"]) if cash_row is not None else 0.0
        liquidity_narrative = f"Net Cash Flow dropped into negative territory at -{currency} {abs(cash_val):,.2f}. Immediate working capital control required." if cash_val < 0 else f"Net Cash Flow closed positive at {currency} {cash_val:,.2f}, maintaining reserve safety buffers."

        recommendations = []
        if any(f["type"] == "EXPENSE_OVERRUN" for f in flags): recommendations.append("Implement discretionary spend controls and audit departmental cost overruns >10%.")
        if any(f["type"] == "NEGATIVE_CASH_FLOW" for f in flags): recommendations.append("Accelerate receivables collection (DSO sprint) and delay non-critical vendor disbursements.")
        if any(f["type"] == "REVENUE_SHORTFALL" for f in flags): recommendations.append("Audit pricing realization and review conversion velocity with sales leadership.")
        if not recommendations: recommendations.append("Operating within plan tolerances. Maintain current operational cadence.")

        status = "⚠️ ATTENTION REQUIRED" if flags else "🟢 WITHIN TOLERANCES"
        return {"headline": f"{status} | Monthly Financial Close - {period}", "topline_performance": topline_narrative, "cost_and_margins": cost_narrative, "cash_and_liquidity": liquidity_narrative, "recommended_actions": recommendations}


# ----------------------------------------------------------------------------
# 2. Streamlit UI Dashboard Setup
# ----------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Nexus FP&A SaaS", page_icon="📊", layout="wide")

    st.title("📊 Automated Financial Reporting & Variance Analysis")
    st.markdown("Commercial FP&A SaaS engine for automated Budget vs. Actuals (BvA) diagnostics, risk flagging, and CFO executive commentary.")

    # Sidebar configuration
    st.sidebar.header("1. Model Configuration")
    threshold = st.sidebar.slider("Budget Overrun Alert Threshold (%)", min_value=1.0, max_value=25.0, value=10.0, step=0.5)

    st.sidebar.header("2. Input Data Source")
    input_mode = st.sidebar.radio("Choose input method:", ["Default Sample Payload", "Upload Custom JSON Payload"])

    # Default payload
    default_payload = {
        "period": "October 2026",
        "currency": "EUR",
        "line_items": {
            "Revenue": {"budget": 1200000.0, "actual": 1120000.0},
            "COGS": {"budget": 360000.0, "actual": 415000.0},
            "OpEx": {"budget": 650000.0, "actual": 730000.0},
            "Net Cash Flow": {"budget": 150000.0, "actual": -85000.0}
        }
    }

    payload = default_payload
    if input_mode == "Upload Custom JSON Payload":
        uploaded_file = st.sidebar.file_uploader("Upload BvA JSON file", type=["json"])
        if uploaded_file is not None:
            try:
                payload = json.load(uploaded_file)
                st.sidebar.success("Custom payload loaded successfully!")
            except Exception as e:
                st.sidebar.error(f"Invalid JSON file format: {e}")

    # Run Analysis Engine
    engine = FPAAnalysisEngine(overrun_threshold_pct=threshold)
    result = engine.process_bva(payload)
    summary = result["summary_kpis"]
    exec_summary = result["cfo_executive_summary"]

    # Main Dashboard Display
    st.subheader(f"📌 {exec_summary['headline']} ({result['metadata']['currency']})")

    # KPI Metric Cards
    col1, col2, col3 = st.columns(3)
    col1.metric(
        label="Gross Margin %",
        value=f"{summary['gross_margin_pct']['actual']:.1f}%",
        delta=f"{summary['gross_margin_pct']['variance_bps']:+.0f} bps vs budget"
    )
    col2.metric(
        label="Operating Income (EBIT)",
        value=f"{result['metadata']['currency']} {summary['operating_income']['actual']:,.0f}",
        delta=f"{result['metadata']['currency']} {summary['operating_income']['variance_abs']:+,.0f}"
    )
    col3.metric(
        label="Net Cash Flow",
        value=f"{result['metadata']['currency']} {summary['net_cash_flow']['actual']:,.0f}",
        delta="Negative Burn" if summary['net_cash_flow']['is_negative'] else "Positive Flow",
        delta_color="inverse" if summary['net_cash_flow']['is_negative'] else "normal"
    )

    st.markdown("---")

    # CFO Executive Commentary Section
    st.subheader("📝 CFO Executive Summary & Narrative")
    st.info(f"**Topline Performance:** {exec_summary['topline_performance']}\n\n"
            f"**Cost & Margins:** {exec_summary['cost_and_margins']}\n\n"
            f"**Liquidity & Cash:** {exec_summary['cash_and_liquidity']}")

    # Recommended Actions
    st.markdown("### 🛠️ Recommended Action Items")
    for action in exec_summary["recommended_actions"]:
        st.markdown(f"- {action}")

    st.markdown("---")

    # Detailed Line Item Breakdown Table
    st.subheader("📋 Detailed Variance Table")
    df_details = pd.DataFrame(result["line_item_analysis"])
    st.dataframe(df_details, use_container_width=True)

    # Download Export JSON
    st.download_button(
        label="📥 Download CFO Report (JSON)",
        data=json.dumps(result, indent=2),
        file_name=f"CFO_Report_{result['metadata']['period'].replace(' ', '_')}.json",
        mime="application/json"
    )


if __name__ == "__main__":
    main()