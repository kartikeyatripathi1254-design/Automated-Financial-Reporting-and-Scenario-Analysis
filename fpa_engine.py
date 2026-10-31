"""
FP&A Analysis Engine.

Compares a period's actuals against a baseline (a budget, or the prior year for
annual reports), flags risks, and writes commentary in both CFO and plain English.
"""
import copy
import math
import re
from typing import Optional

import pandas as pd


def normalize_item(name) -> str:
    """'Cost of Goods Sold' / 'cost-of-goods sold' -> 'cost_of_goods_sold'."""
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


# ----------------------------------------------------------------------------
# Money formatting
# ----------------------------------------------------------------------------
CURRENCY_SYMBOLS = {"INR": "₹", "EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥"}


def _split_currency(currency: str):
    """'INR Cr' -> ('INR', 'Cr'); 'EUR' -> ('EUR', '')."""
    parts = str(currency or "").split()
    code = parts[0].upper() if parts else ""
    return code, " ".join(parts[1:])


def _indian_grouping(n: int) -> str:
    """1075675 -> '10,75,675'."""
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = re.sub(r"(\d)(?=(\d{2})+$)", r"\1,", head)
        s = f"{head},{tail}"
    return ("-" if n < 0 else "") + s


def format_money(value: float, currency: str, signed: bool = False) -> str:
    """Full amount: format_money(1075675, 'INR Cr') -> '₹10,75,675 Cr'."""
    code, unit = _split_currency(currency)
    symbol = CURRENCY_SYMBOLS.get(code, f"{code} " if code else "")
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    v = abs(value)
    if code == "INR":
        body = _indian_grouping(round(v)) if v >= 100 else f"{v:,.2f}"
    else:
        body = f"{v:,.0f}" if v >= 100 else f"{v:,.2f}"
    return f"{sign}{symbol}{body}{' ' + unit if unit else ''}"


def format_compact(value: float, currency: str, signed: bool = False) -> str:
    """Short, human scale: '₹10.76 lakh crore', '€1.38M', '$950K'."""
    code, unit = _split_currency(currency)
    symbol = CURRENCY_SYMBOLS.get(code, f"{code} " if code else "")
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    v = abs(value)
    if code == "INR":
        # Convert to rupees, then express in crore / lakh crore.
        rupees = v * {"cr": 1e7, "crore": 1e7, "crores": 1e7, "lakh": 1e5, "mn": 1e6, "bn": 1e9}.get(unit.lower(), 1)
        if rupees >= 1e12:
            return f"{sign}{symbol}{rupees / 1e12:,.2f} lakh crore"
        if rupees >= 1e7:
            return f"{sign}{symbol}{_indian_grouping(round(rupees / 1e7))} crore"
        if rupees >= 1e5:
            return f"{sign}{symbol}{rupees / 1e5:,.2f} lakh"
        return f"{sign}{symbol}{_indian_grouping(round(rupees))}"
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= limit:
            scaled = v / limit
            digits = 0 if scaled >= 100 else 1 if scaled >= 10 else 2  # €80K, €45.5K, €1.38M
            return f"{sign}{symbol}{scaled:,.{digits}f}{suffix}{' ' + unit if unit else ''}"
    return f"{sign}{symbol}{v:,.0f}{' ' + unit if unit else ''}"


# ----------------------------------------------------------------------------
# Engine
# ----------------------------------------------------------------------------
class FPAAnalysisEngine:
    # Exact normalized names per category. Order = priority when several are present
    # (e.g. net revenue is preferred over gross revenue for margin calculations).
    REVENUE_ITEMS = ("net_revenue", "revenue_from_operations", "revenue", "total_revenue", "net_sales", "sales",
                     "gross_revenue")
    COGS_ITEMS = ("cogs", "cost_of_goods_sold", "cost_of_sales", "cost_of_revenue")
    OPEX_ITEMS = ("opex", "operating_expenses", "total_opex", "total_operating_expenses")
    CASH_ITEMS = ("net_cash_flow", "operating_cash_flow", "free_cash_flow")

    CATEGORY_BY_ITEM = {
        **{n: "revenue" for n in REVENUE_ITEMS},
        **{n: "cogs" for n in COGS_ITEMS},
        **{n: "opex" for n in OPEX_ITEMS},
        **{n: "cash" for n in CASH_ITEMS},
    }

    def __init__(self, overrun_threshold_pct: float = 10.0, critical_threshold_pct: float = 20.0):
        if critical_threshold_pct < overrun_threshold_pct:
            raise ValueError("critical_threshold_pct must be >= overrun_threshold_pct.")
        self.overrun_threshold_pct = float(overrun_threshold_pct)
        self.critical_threshold_pct = float(critical_threshold_pct)

    @classmethod
    def categorize(cls, item_name) -> Optional[str]:
        return cls.CATEGORY_BY_ITEM.get(normalize_item(item_name))

    def process_bva(self, payload: dict) -> dict:
        period = payload.get("period", "Current Period")
        currency = payload.get("currency", "EUR")
        report_type = payload.get("report_type", "Monthly Financial Close")
        # "bva" compares against a budget; "yoy" against the prior year (annual reports).
        analysis_mode = payload.get("analysis_mode", "yoy" if payload.get("baseline_label") == "prior year" else "bva")
        baseline = payload.get("baseline_label", "prior year" if analysis_mode == "yoy" else "budget")
        line_items_data = payload.get("line_items", {})

        if not line_items_data or not isinstance(line_items_data, dict):
            raise ValueError("Provided payload contains no valid line items data.")

        df = pd.DataFrame.from_dict(line_items_data, orient="index")
        df.index.name = "line_item"
        df = df.reset_index()

        if "actual" not in df.columns or "budget" not in df.columns:
            raise ValueError("Financial payload must contain 'budget' and 'actual' columns.")

        df["line_item"] = df["line_item"].astype(str)
        df["normalized_item"] = df["line_item"].map(normalize_item)
        df["category"] = pd.Series(
            [self.CATEGORY_BY_ITEM.get(n) for n in df["normalized_item"]], index=df.index, dtype=object
        )

        for col in ("budget", "actual"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        bad = df[df["budget"].isna() | df["actual"].isna()]["line_item"].tolist()
        if bad:
            raise ValueError(f"Non-numeric or missing budget/actual values for: {', '.join(bad)}")

        df["variance_abs"] = df["actual"] - df["budget"]
        # Undefined (None) rather than inf/NaN when there is no baseline to compare against.
        # dtype=object keeps None from being coerced to NaN.
        df["variance_pct"] = pd.Series(
            [(float(v) / abs(float(b)) * 100.0) if b != 0 else None for v, b in zip(df["variance_abs"], df["budget"])],
            index=df.index, dtype=object,
        )

        rev_row = self._primary_row(df, self.REVENUE_ITEMS)
        revenue_growth = rev_row["variance_pct"] if rev_row is not None else None

        flags = []
        is_favorable_list = []
        flag_notes = []

        for _, row in df.iterrows():
            category = row["category"]
            b = float(row["budget"])
            a = float(row["actual"])
            var_pct = row["variance_pct"]
            flag_str = None

            if category in ("cogs", "opex"):
                is_fav = a <= b
                if analysis_mode == "yoy" and revenue_growth is not None and var_pct is not None:
                    # Costs growing with the business are normal; only costs outpacing sales are a concern.
                    is_fav = var_pct <= revenue_growth
                if not is_fav:
                    severity = None
                    if var_pct is None:
                        flag_str = f"Unbudgeted spend of {format_money(a - b, currency)}"
                        severity = "CRITICAL"
                    elif var_pct > self.overrun_threshold_pct:
                        if analysis_mode == "yoy":
                            growth = f", ahead of revenue growth of {revenue_growth:.1f}%" if revenue_growth is not None else ""
                            flag_str = f"Cost growth of {var_pct:.2f}% vs {baseline}{growth}"
                        else:
                            flag_str = f"Budget overrun of {var_pct:.2f}% (> {self.overrun_threshold_pct:g}% threshold)"
                        severity = self._severity(var_pct)
                    if flag_str:
                        flags.append({
                            "line_item": row["line_item"],
                            "type": "EXPENSE_OVERRUN",
                            "severity": severity,
                            "message": flag_str,
                            "excess_spend": round(a - b, 2)
                        })

            elif category == "revenue":
                is_fav = a >= b
                if not is_fav and var_pct is not None and abs(var_pct) > self.overrun_threshold_pct:
                    flag_str = f"Revenue shortfall of {abs(var_pct):.2f}% vs {baseline}"
                    flags.append({
                        "line_item": row["line_item"],
                        "type": "REVENUE_SHORTFALL",
                        "severity": self._severity(abs(var_pct)),
                        "message": flag_str,
                        "shortfall": round(b - a, 2)
                    })

            elif category == "cash":
                is_fav = a >= b
                if a < 0:
                    flag_str = f"Negative {row['line_item']} of {format_money(a, currency)}"
                    flags.append({
                        "line_item": row["line_item"],
                        "type": "NEGATIVE_CASH_FLOW",
                        "severity": "CRITICAL",
                        "message": flag_str,
                        "cash_burn": round(abs(a), 2)
                    })
            else:
                is_fav = a >= b

            is_favorable_list.append(bool(is_fav))
            flag_notes.append(flag_str)

        df["is_favorable"] = is_favorable_list
        df["flag"] = pd.Series(flag_notes, index=df.index, dtype=object)

        cogs_row = self._primary_row(df, self.COGS_ITEMS)
        opex_row = self._primary_row(df, self.OPEX_ITEMS)
        cash_row = self._primary_row(df, self.CASH_ITEMS)

        actual_rev, budget_rev = self._values(rev_row)
        actual_cogs, budget_cogs = self._values(cogs_row)
        actual_opex, budget_opex = self._values(opex_row)
        actual_cash, budget_cash = self._values(cash_row)

        actual_gp = actual_rev - actual_cogs
        budget_gp = budget_rev - budget_cogs

        actual_gm_pct = (actual_gp / actual_rev * 100.0) if actual_rev != 0 else None
        budget_gm_pct = (budget_gp / budget_rev * 100.0) if budget_rev != 0 else None
        gm_variance_bps = (
            (actual_gm_pct - budget_gm_pct) * 100 if actual_gm_pct is not None and budget_gm_pct is not None else None
        )

        actual_ebit = actual_gp - actual_opex
        budget_ebit = budget_gp - budget_opex
        actual_om_pct = (actual_ebit / actual_rev * 100.0) if actual_rev != 0 else None
        budget_om_pct = (budget_ebit / budget_rev * 100.0) if budget_rev != 0 else None

        # Runway only when reserves are supplied and cash is actually burning.
        # period_months converts the period's cash flow into a monthly burn rate.
        cash_reserves = payload.get("cash_reserves")
        period_months = float(payload.get("period_months", 1) or 1)
        monthly_burn = abs(actual_cash) / period_months if actual_cash < 0 else 0.0
        if cash_reserves is not None and monthly_burn > 0:
            runway_months = round(float(cash_reserves) / monthly_burn, 1)
        else:
            runway_months = None

        kpis = {
            "revenue": {
                "budget": round(budget_rev, 2),
                "actual": round(actual_rev, 2),
                "variance_abs": round(actual_rev - budget_rev, 2),
                # 1 decimal so every place that shows it (cards, insights, commentary) agrees.
                "variance_pct": _round_or_none(revenue_growth, 1),
            },
            "gross_margin_pct": {
                "budget": _round_or_none(budget_gm_pct, 2),
                "actual": _round_or_none(actual_gm_pct, 2),
                "variance_bps": _round_or_none(gm_variance_bps, 1),
            },
            "operating_income": {
                "budget": round(budget_ebit, 2),
                "actual": round(actual_ebit, 2),
                "variance_abs": round(actual_ebit - budget_ebit, 2),
                "variance_pct": _round_or_none((actual_ebit - budget_ebit) / abs(budget_ebit) * 100 if budget_ebit else None, 2),
            },
            "operating_margin_pct": {
                "budget": _round_or_none(budget_om_pct, 2),
                "actual": _round_or_none(actual_om_pct, 2),
            },
            "net_cash_flow": {
                "budget": round(budget_cash, 2),
                "actual": round(actual_cash, 2),
                "is_negative": bool(actual_cash < 0),
                "available": cash_row is not None,
            },
            "cash_reserves": _round_or_none(cash_reserves, 2),
            "cash_runway_months": runway_months,
            # Where each 100 of sales went: cost of goods, running costs, and what's left.
            "per_100_of_revenue": {
                "cogs": _round_or_none(actual_cogs / actual_rev * 100 if actual_rev else None, 1),
                "opex": _round_or_none(actual_opex / actual_rev * 100 if actual_rev else None, 1),
                "operating_income": _round_or_none(actual_om_pct, 1),
            } if actual_rev else None,
        }

        commentary = self._generate_cfo_commentary(
            period=period, currency=currency, report_type=report_type, baseline=baseline, rev_row=rev_row,
            opex_row=opex_row, cash_row=cash_row, actual_gm_pct=actual_gm_pct, budget_gm_pct=budget_gm_pct,
            gm_variance_bps=gm_variance_bps, actual_ebit=actual_ebit, budget_ebit=budget_ebit,
            flags=flags, runway_months=runway_months, analysis_mode=analysis_mode
        )
        insights = self._generate_plain_insights(kpis, flags, currency, baseline, rev_row is not None,
                                                 cogs_row is not None, opex_row is not None)

        variance_details = []
        for _, row in df.iterrows():
            variance_details.append({
                "line_item": row["line_item"],
                "category": row["category"],
                "budget": round(float(row["budget"]), 2),
                "actual": round(float(row["actual"]), 2),
                "variance_abs": round(float(row["variance_abs"]), 2),
                "variance_pct": _round_or_none(row["variance_pct"], 2),
                "is_favorable": bool(row["is_favorable"]),
                "flag": row["flag"]
            })

        return {
            "metadata": {
                "period": str(period),
                "currency": str(currency),
                "report_type": str(report_type),
                "analysis_mode": analysis_mode,
                "baseline": str(baseline),
                "overrun_threshold_pct": float(self.overrun_threshold_pct),
                "critical_threshold_pct": float(self.critical_threshold_pct),
                "kpi_sources": {
                    "revenue": _name(rev_row), "cogs": _name(cogs_row),
                    "opex": _name(opex_row), "cash": _name(cash_row),
                },
            },
            "summary_kpis": kpis,
            "line_item_analysis": variance_details,
            "critical_flags": flags,
            "cfo_executive_summary": commentary,
            "plain_english": insights,
        }

    def forecast_scenario(self, base_payload: dict, revenue_growth_pct: float, opex_adjustment_pct: float,
                          cogs_scales_with_revenue: bool = True) -> dict:
        """
        Forward-looking What-If Scenario Modeler.

        - Revenue actuals scale by revenue_growth_pct.
        - COGS scales with revenue (constant unit cost) unless cogs_scales_with_revenue=False,
          in which case it follows opex_adjustment_pct.
        - OpEx scales by opex_adjustment_pct.
        - Cash lines move by the resulting change in operating income (simplification:
          ignores tax and working-capital effects).
        """
        simulated_payload = copy.deepcopy(base_payload)
        simulated_payload["period"] = f"Scenario Forecast (Rev: {revenue_growth_pct:+.1f}%, OpEx: {opex_adjustment_pct:+.1f}%)"
        simulated_payload["report_type"] = "Scenario Forecast"

        rev_factor = 1.0 + revenue_growth_pct / 100.0
        opex_factor = 1.0 + opex_adjustment_pct / 100.0
        cogs_factor = rev_factor if cogs_scales_with_revenue else opex_factor

        line_items = simulated_payload.get("line_items", {})
        names = list(line_items)
        rev_name = next((n for p in self.REVENUE_ITEMS for n in names if normalize_item(n) == p), None)
        cogs_name = next((n for p in self.COGS_ITEMS for n in names if normalize_item(n) == p), None)
        opex_name = next((n for p in self.OPEX_ITEMS for n in names if normalize_item(n) == p), None)

        # Only the KPI rows drive the EBIT delta, so related lines (e.g. gross and net
        # revenue) are scaled together without double-counting the cash impact.
        ebit_delta = 0.0
        for item, values in line_items.items():
            category = self.categorize(item)
            factor = {"revenue": rev_factor, "cogs": cogs_factor, "opex": opex_factor}.get(category)
            if factor is None:
                continue
            old = float(values["actual"])
            values["actual"] = old * factor
            delta = values["actual"] - old
            if item == rev_name:
                ebit_delta += delta
            elif item in (cogs_name, opex_name):
                ebit_delta -= delta

        for item, values in line_items.items():
            if self.categorize(item) == "cash":
                values["actual"] = float(values["actual"]) + ebit_delta

        return self.process_bva(simulated_payload)

    def _severity(self, pct: float) -> str:
        return "CRITICAL" if pct > self.critical_threshold_pct else "WARNING"

    @staticmethod
    def _primary_row(df, priority):
        for name in priority:
            match = df[df["normalized_item"] == name]
            if len(match) > 0:
                return match.iloc[0]
        return None

    @staticmethod
    def _values(row):
        if row is None:
            return 0.0, 0.0
        return float(row["actual"]), float(row["budget"])

    def _generate_cfo_commentary(self, period, currency, report_type, baseline, rev_row, opex_row, cash_row,
                                 actual_gm_pct, budget_gm_pct, gm_variance_bps, actual_ebit, budget_ebit, flags,
                                 runway_months, analysis_mode="bva"):
        is_budget = baseline == "budget"
        vs_baseline = "budgeted" if is_budget else f"in the {baseline}"
        money = lambda v, signed=False: format_money(v, currency, signed)

        if rev_row is not None:
            rev_pct = rev_row["variance_pct"]
            rev_abs = float(rev_row["variance_abs"])
            rev_actual = float(rev_row["actual"])
            if rev_pct is None:
                topline_narrative = f"Revenue closed at {money(rev_actual)} against no {baseline} revenue."
            elif rev_abs > 0:
                topline_narrative = (
                    f"Revenue reached {money(rev_actual)}, outperforming budget by {rev_pct:.1f}% ({money(rev_abs, True)})."
                    if is_budget else
                    f"Revenue reached {money(rev_actual)}, up {rev_pct:.1f}% on the {baseline} ({money(rev_abs, True)})."
                )
            elif rev_abs < 0:
                topline_narrative = (
                    f"Revenue fell short of plan by {abs(rev_pct):.1f}% ({money(rev_abs)}), closing at {money(rev_actual)}."
                    if is_budget else
                    f"Revenue declined {abs(rev_pct):.1f}% on the {baseline} ({money(rev_abs)}), closing at {money(rev_actual)}."
                )
            else:
                topline_narrative = f"Revenue closed in line with {baseline} at {money(rev_actual)}."
        else:
            topline_narrative = "Revenue data not provided."

        if actual_gm_pct is not None and budget_gm_pct is not None:
            cost_narrative = f"Gross Margin was {actual_gm_pct:.1f}% vs. {budget_gm_pct:.1f}% {vs_baseline} ({gm_variance_bps:+.0f} bps). "
        else:
            cost_narrative = "Gross Margin not computable (zero revenue). "

        if opex_row is not None:
            opex_pct = opex_row["variance_pct"]
            opex_abs = float(opex_row["variance_abs"])
            pct_text = f"{abs(opex_pct):.1f}% " if opex_pct is not None else ""
            ebit_text = f"{money(actual_ebit)} vs. {money(budget_ebit)} {vs_baseline}"
            over = "exceeded plan by" if is_budget else f"rose vs. the {baseline} by"
            under = "came in under budget by" if is_budget else f"fell vs. the {baseline} by"
            if opex_abs > 0:
                if actual_ebit < budget_ebit:
                    cost_narrative += f"OpEx {over} {pct_text}({money(opex_abs, True)}), depressing Operating Income to {ebit_text}."
                else:
                    cost_narrative += f"OpEx {over} {pct_text}({money(opex_abs, True)}), though Operating Income still closed at {ebit_text}."
            elif opex_abs < 0:
                cost_narrative += f"OpEx {under} {pct_text}({money(opex_abs)}); Operating Income closed at {ebit_text}."
            else:
                cost_narrative += f"OpEx landed on {baseline}; Operating Income closed at {ebit_text}."

        cash_val = float(cash_row["actual"]) if cash_row is not None else 0.0
        cash_label = str(cash_row["line_item"]) if cash_row is not None else "Net Cash Flow"
        if cash_row is None:
            liquidity_narrative = "Cash flow data not provided."
        elif cash_val < 0:
            liquidity_narrative = f"{cash_label} dropped into negative territory at {money(cash_val)}. "
            liquidity_narrative += (
                f"Current cash runway is estimated at **{runway_months} months** based on reported reserves."
                if runway_months is not None else
                "Cash runway not estimated (no cash reserves provided)."
            )
        else:
            liquidity_narrative = f"{cash_label} closed positive at {money(cash_val)}."

        recommendations = []
        if any(f["type"] == "EXPENSE_OVERRUN" for f in flags):
            recommendations.append(
                f"Implement discretionary spend controls and audit departmental cost overruns >{self.overrun_threshold_pct:g}%."
                if analysis_mode == "bva" else
                "Review cost lines growing faster than revenue and protect operating margin."
            )
        if any(f["type"] == "NEGATIVE_CASH_FLOW" for f in flags): recommendations.append("Accelerate receivables collection (DSO sprint) and delay non-critical vendor disbursements.")
        if any(f["type"] == "REVENUE_SHORTFALL" for f in flags): recommendations.append("Audit pricing realization and review conversion velocity with sales leadership.")
        if not recommendations: recommendations.append("Operating within plan tolerances. Maintain current operational cadence.")

        status = "⚠️ ATTENTION REQUIRED" if flags else "🟢 WITHIN TOLERANCES"
        return {"headline": f"{status} | {report_type} - {period}", "topline_performance": topline_narrative, "cost_and_margins": cost_narrative, "cash_and_liquidity": liquidity_narrative, "recommended_actions": recommendations}

    def _generate_plain_insights(self, kpis, flags, currency, baseline, has_rev, has_cogs, has_opex):
        """Everyday-language verdict, wins, watch-outs and next steps for non-finance readers."""
        compare = "the budget" if baseline == "budget" else f"the {baseline}"
        good, watch = [], []

        rev = kpis["revenue"]
        if has_rev and rev["variance_pct"] is not None:
            pct = rev["variance_pct"]
            change = format_compact(rev["variance_abs"], currency)
            if pct > 0:
                good.append(f"Sales grew {pct:.1f}% compared with {compare} (an extra {change}).")
            elif pct < 0:
                watch.append(f"Sales were {abs(pct):.1f}% lower than {compare} ({change.lstrip('-')} less).")

        gm = kpis["gross_margin_pct"]
        if has_cogs and gm["actual"] is not None:
            keep = gm["actual"]
            text = f"Out of every 100 in sales, about {keep:.0f} is left after paying for the goods sold"
            if gm["variance_bps"] is not None and gm["variance_bps"] > 0:
                good.append(f"{text}, slightly better than {compare}.")
            elif gm["variance_bps"] is not None and gm["variance_bps"] < -50:
                watch.append(f"{text}, down from {gm['budget']:.0f} in {compare}: goods are getting more expensive to make or buy.")

        oi = kpis["operating_income"]
        if has_rev and (has_cogs or has_opex):
            if oi["actual"] < 0:
                watch.append(f"The business made an operating loss of {format_compact(oi['actual'], currency).lstrip('-')}.")
            elif oi["variance_abs"] > 0:
                good.append(f"Operating profit rose to {format_compact(oi['actual'], currency)} ({format_compact(oi['variance_abs'], currency, True)}).")
            elif oi["variance_abs"] < 0:
                watch.append(f"Operating profit fell to {format_compact(oi['actual'], currency)} ({format_compact(oi['variance_abs'], currency)} vs {compare}).")

        for f in flags:
            if f["type"] == "EXPENSE_OVERRUN":
                extra = format_compact(f["excess_spend"], currency)
                name = {"cogs": "Cost of goods", "opex": "Running costs"}.get(self.categorize(f["line_item"]),
                                                                               f["line_item"])
                if baseline == "budget":
                    watch.append(f"{name} went over budget by {extra}.")
                else:
                    watch.append(f"{name} grew faster than sales (up {extra}), squeezing profit.")

        cash = kpis["net_cash_flow"]
        if cash["available"]:
            if cash["is_negative"]:
                runway = kpis["cash_runway_months"]
                line = f"More cash went out than came in ({format_compact(cash['actual'], currency).lstrip('-')} net outflow)."
                if runway is not None:
                    line += f" At this pace the cash in the bank lasts about {runway:.0f} months."
                watch.append(line)
            else:
                good.append(f"The business added {format_compact(cash['actual'], currency)} of cash.")

        critical = any(f["severity"] == "CRITICAL" for f in flags) or (cash["available"] and cash["is_negative"]) \
            or oi["actual"] < 0
        if critical:
            status, verdict = "at_risk", "Needs action: there are serious issues below to deal with now."
        elif watch:
            status, verdict = "watch", "Mostly healthy, with a few things to keep an eye on."
        else:
            status, verdict = "healthy", "Healthy: the business is performing well on every measure we checked."

        next_steps = []
        if any(f["type"] == "EXPENSE_OVERRUN" for f in flags):
            next_steps.append("Review the cost lines marked as alerts and find out what is driving the increase.")
        if any(f["type"] == "REVENUE_SHORTFALL" for f in flags):
            next_steps.append("Talk to the sales team about why sales came in lower and what can be recovered.")
        if cash["available"] and cash["is_negative"]:
            next_steps.append("Collect money owed by customers faster and delay payments that aren't urgent.")
        if not next_steps:
            next_steps.append("Keep doing what's working; no urgent action needed.")

        # 0-100 health score: start at 100 and deduct for each problem found.
        score = 100
        for f in flags:
            score -= 25 if f["severity"] == "CRITICAL" else 12
        if oi["actual"] < 0:
            score -= 25
        if has_rev and rev["variance_pct"] is not None and rev["variance_pct"] < 0:
            score -= 5
        if gm["variance_bps"] is not None and gm["variance_bps"] < -50:
            score -= 5
        score = max(0, min(100, score))

        return {"status": status, "verdict": verdict, "health_score": score,
                "good_news": good, "watch_out": watch, "next_steps": next_steps}


def _round_or_none(value, digits):
    if value is None:
        return None
    value = float(value)
    return None if math.isnan(value) or math.isinf(value) else round(value, digits)


def _name(row):
    return None if row is None else str(row["line_item"])
