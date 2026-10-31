"""
Nexus FP&A: automated financial reporting, variance analysis and what-if scenarios,
explained so anyone can read it.

Run with:  streamlit run app.py
"""
import io
import json
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

import ui
from fpa_engine import CURRENCY_SYMBOLS, FPAAnalysisEngine, format_compact, format_money
from ind_as_extractor import ExtractionError, extract_payload_from_pdf
from report import build_board_report

SAMPLE_PAYLOAD = {
    "period": "October 2026",
    "currency": "EUR",
    "report_type": "Monthly Financial Close",
    "analysis_mode": "bva",
    "cash_reserves": 1_200_000.0,
    "line_items": {
        "Revenue": {"budget": 1_500_000.0, "actual": 1_380_000.0},
        "COGS": {"budget": 450_000.0, "actual": 510_000.0},
        "OpEx": {"budget": 700_000.0, "actual": 790_000.0},
        "Net Cash Flow": {"budget": 200_000.0, "actual": -45_000.0},
    },
}

TEMPLATE_CSV = "Line Item,Budget,Actual\nRevenue,1500000,1380000\nCOGS,450000,510000\nOpEx,700000,790000\nNet Cash Flow,200000,-45000\n"

CURRENCIES = ["INR Cr", "INR", "EUR", "USD", "GBP"]

GLOSSARY = {
    "Sales (Revenue)": "All the money earned from selling products and services.",
    "Cost of goods (COGS)": "What it cost to make or buy the things that were sold: materials, stock, production.",
    "Running costs (OpEx)": "Everyday costs of running the business: salaries, rent, marketing, utilities.",
    "Gross margin": "Out of every 100 in sales, how much is left after paying for the goods sold. Higher is better.",
    "Operating profit": "What's left after paying for goods and running costs, before interest, depreciation and tax.",
    "Net cash flow": "Cash that came in minus cash that went out during the period.",
    "Cash runway": "If cash keeps going out at this pace, how many months the cash in the bank would last.",
    "Health score": "A 0–100 summary: starts at 100 and drops for each warning, serious alert, loss or decline.",
    "Budget vs actual": "Comparing what the business planned to earn and spend with what really happened.",
    "Year over year": "Comparing this year with last year.",
}


# ----------------------------------------------------------------------------
# Loading data
# ----------------------------------------------------------------------------
def _to_number(value) -> float:
    s = str(value).strip().replace(",", "")
    for sym in ("€", "$", "₹", "£"):
        s = s.replace(sym, "")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    return float(s)


def table_to_payload(df: pd.DataFrame, mode: str, currency: str, period: str) -> dict:
    """Turn a CSV/Excel table (line item, baseline, actual) into an engine payload."""
    cols = [str(c).strip() for c in df.columns]
    lower = [c.lower() for c in cols]

    def pick(keywords, fallback_idx):
        for c, l in zip(cols, lower):
            if any(k in l for k in keywords):
                return c
        return cols[fallback_idx] if len(cols) > fallback_idx else None

    df.columns = cols
    item_col = pick(["item", "line", "account", "category", "description", "name", "particular"], 0)
    base_col = pick(["budget", "plan", "target", "forecast", "prior", "previous", "last"], 1)
    actual_col = pick(["actual", "current", "this", "result", "amount"], 2)
    if base_col is None or actual_col is None or base_col == actual_col:
        raise ValueError(f"Couldn't find separate budget and actual columns in: {cols}. "
                         "Use the template: Line Item, Budget, Actual.")

    items, skipped = {}, []
    for _, row in df.iterrows():
        name = str(row[item_col]).strip()
        if not name or name.lower() in ("nan", "none"):
            continue
        try:
            items[name] = {"budget": _to_number(row[base_col]), "actual": _to_number(row[actual_col])}
        except (ValueError, TypeError):
            skipped.append(name)
    if not items:
        raise ValueError("No rows with numbers were found. Use the template: Line Item, Budget, Actual.")

    return {
        "period": period or "Uploaded data",
        "currency": currency,
        "report_type": "Budget vs Actual" if mode == "bva" else "Year over Year",
        "analysis_mode": mode,
        "line_items": items,
        "source": {"file_columns": {"item": item_col, "baseline": base_col, "actual": actual_col},
                   "skipped_rows": skipped},
    }


@st.cache_data(show_spinner=False)
def load_pdf(file_bytes: bytes) -> dict:
    return extract_payload_from_pdf(io.BytesIO(file_bytes))


def landing_page():
    ui.hero("Welcome to Nexus FP&A", "Upload your financials and get a clear, plain-English health check in seconds.",
            ["Annual report PDFs", "Budget vs actual", "What-if scenarios", "Board-ready report"])
    c1, c2, c3 = st.columns(3)
    with c1:
        ui.feature("📤", "1. Upload", "Drop a company annual report (PDF) or your own budget-vs-actual table "
                                     "(CSV or Excel) in the sidebar.")
    with c2:
        ui.feature("🔎", "2. Understand", "See a health score, the key numbers, and what went well or badly — "
                                         "explained without jargon.")
    with c3:
        ui.feature("✅", "3. Act", "Get recommended next steps, test what-if scenarios, and download a "
                                  "board-ready report.")
    st.write("")
    st.info("👈 Upload a file in the sidebar to begin, or switch to **Sample data** to look around.")
    ui.footer(f"{ui.BRAND} · {ui.TAGLINE}")
    st.stop()


def load_payload():
    """Sidebar data-source controls. Returns a payload, or stops the page with a friendly message."""
    ui.sidebar_step("1 · Data source")
    source = st.sidebar.radio("Data source", ["Sample data", "Upload my file"], label_visibility="collapsed")
    if source == "Sample data":
        st.sidebar.caption("A demo company's October results, compared with its budget.")
        return SAMPLE_PAYLOAD

    uploaded = st.sidebar.file_uploader(
        "Upload a file", type=["pdf", "csv", "xlsx", "xls", "json"], label_visibility="collapsed",
        help="**PDF**: a company's annual report (we read the official financial statements).\n\n"
             "**CSV / Excel**: a table with Line Item, Budget (or Last Year) and Actual columns.",
    )
    st.sidebar.download_button("Download CSV template", TEMPLATE_CSV, "nexus_fpa_template.csv", "text/csv")

    if uploaded is None:
        landing_page()

    ext = uploaded.name.rsplit(".", 1)[-1].lower()
    try:
        if ext == "pdf":
            with st.spinner("Reading the financial statements in your PDF… large reports take ~20 seconds."):
                payload = load_pdf(uploaded.getvalue())
            src = payload["source"]
            st.sidebar.success(f"Read the {src['scope'].lower()} statements "
                               f"(pages {src['profit_and_loss_page']} & {src['cash_flow_page']}).")
            return payload
        if ext == "json":
            return json.load(uploaded)

        df = pd.read_csv(uploaded) if ext == "csv" else pd.read_excel(uploaded)
        mode_label = st.sidebar.radio("What are you comparing?", ["Budget vs actual", "This year vs last year"])
        currency = st.sidebar.selectbox("Currency", CURRENCIES, index=2)
        period = st.sidebar.text_input("Period name", "My period")
        payload = table_to_payload(df, "bva" if mode_label == "Budget vs actual" else "yoy", currency, period)
        if payload["source"]["skipped_rows"]:
            st.sidebar.warning(f"Skipped rows without numbers: {', '.join(payload['source']['skipped_rows'])}")
        return payload
    except ExtractionError as e:
        st.error(f"We couldn't find the financial statements in this PDF.\n\n{e}\n\n"
                 "Make sure it's a company's annual report that includes a Statement of Profit and Loss.")
        st.stop()
    except Exception as e:  # noqa: BLE001 - show any parsing problem to the user instead of crashing
        st.error(f"We couldn't read this file: {e}")
        st.stop()


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def names(result):
    budget = result["metadata"]["baseline"] == "budget"
    return {
        "vs": "vs budget" if budget else "vs last year",
        "base": "Budget" if budget else "Last year",
        "act": "Actual" if budget else "This year",
        "compare": "its budget" if budget else "the previous year",
    }


def styled(chart):
    return (chart.configure(font="Inter")
            .configure_view(strokeWidth=0)
            .configure_axis(labelColor=ui.COLORS["muted"], titleColor=ui.COLORS["muted"], gridColor="#EEF0F5",
                            domainColor=ui.COLORS["line"], tickColor=ui.COLORS["line"], labelFontSize=12)
            .configure_legend(labelColor=ui.COLORS["ink"], labelFontSize=12, symbolType="circle"))


def snapshot_sentence(result) -> str:
    k, cur, n = result["summary_kpis"], result["metadata"]["currency"], names(result)
    parts = []
    if k["revenue"]["variance_pct"] is not None:
        parts.append(f"Sales {k['revenue']['variance_pct']:+.1f}% {n['vs']}")
    oi = k["operating_income"]
    parts.append(f"operating profit {format_compact(oi['variance_abs'], cur, True)}")
    if k["net_cash_flow"]["available"]:
        parts.append("cash going out" if k["net_cash_flow"]["is_negative"] else "cash building up")
    sentence = ", ".join(parts)
    return sentence[:1].upper() + sentence[1:] + "."


# ----------------------------------------------------------------------------
# Dashboard sections
# ----------------------------------------------------------------------------
def kpi_row(result):
    k, cur, n = result["summary_kpis"], result["metadata"]["currency"], names(result)
    annual = result["metadata"]["analysis_mode"] == "yoy"
    symbol = CURRENCY_SYMBOLS.get(cur.split()[0] if cur else "", "")
    cards = []

    rev = k["revenue"]
    pct = rev["variance_pct"]
    cards.append(ui.kpi_html("💰", "Sales", format_compact(rev["actual"], cur),
                             f"{pct:+.1f}% {n['vs']}" if pct is not None else None,
                             "good" if (pct or 0) >= 0 else "bad",
                             f"{n['base']}: {format_compact(rev['budget'], cur)}", ui_help("Sales (Revenue)")))

    gm = k["gross_margin_pct"]
    bps = gm["variance_bps"]
    cards.append(ui.kpi_html("🧾", "Gross margin", f"{gm['actual']:.1f}%" if gm["actual"] is not None else "N/A",
                             f"{bps / 100:+.1f} pts {n['vs']}" if bps is not None else None,
                             "good" if (bps or 0) >= 0 else "bad",
                             f"{symbol}{gm['actual']:.0f} kept from every {symbol}100 of sales"
                             if gm["actual"] is not None else None, ui_help("Gross margin")))

    oi = k["operating_income"]
    om = k["operating_margin_pct"]["actual"]
    cards.append(ui.kpi_html("🏭", "Operating profit", format_compact(oi["actual"], cur),
                             f"{format_compact(oi['variance_abs'], cur, True)} {n['vs']}",
                             "good" if oi["variance_abs"] >= 0 else "bad",
                             (f"{om:.1f}% of sales" if om is not None else "") + (" · before depreciation" if annual else ""),
                             ui_help("Operating profit")))

    cash = k["net_cash_flow"]
    if not cash["available"]:
        cards.append(ui.kpi_html("🏦", "Cash flow", "Not provided", None, "neutral",
                                 "No cash flow line in this data", ui_help("Net cash flow")))
    elif cash["is_negative"]:
        runway = k["cash_runway_months"]
        cards.append(ui.kpi_html("🏦", "Cash flow", format_compact(cash["actual"], cur), "Cash going out",
                                 "bad" if runway is None or runway < 12 else "warn",
                                 f"Cash in the bank lasts ~{runway:.0f} months at this pace" if runway is not None else
                                 "Add cash reserves to estimate how long cash will last", ui_help("Cash runway")))
    else:
        reserves = k["cash_reserves"]
        cards.append(ui.kpi_html("🏦", "Cash flow", format_compact(cash["actual"], cur, True), "Cash building up",
                                 "good", f"Cash in the bank: {format_compact(reserves, cur)}" if reserves is not None
                                 else None, ui_help("Net cash flow")))
    ui.kpi_grid(cards)


def ui_help(term):
    return GLOSSARY[term]


def overview_tab(result):
    pe = result["plain_english"]
    left, right = st.columns(2)
    with left:
        ui.insight_card("What went well", "✅", pe["good_news"], "good", "Nothing stood out as better than expected.")
    with right:
        ui.insight_card("What to watch", "⚠️", pe["watch_out"], "warn", "Nothing worrying. 🎉")
    st.write("")
    ui.insight_card("Recommended next steps", "👉", pe["next_steps"], "neutral", "", numbered=True)
    st.write("")
    with st.expander("📝 CFO commentary — finance wording for board packs"):
        ex = result["cfo_executive_summary"]
        st.markdown(f"**Topline.** {ex['topline_performance']}")
        st.markdown(f"**Costs & margins.** {ex['cost_and_margins']}")
        st.markdown(f"**Cash.** {ex['cash_and_liquidity']}")
        st.markdown("**Recommended actions.**")
        for a in ex["recommended_actions"]:
            st.markdown(f"- {a}")


def waterfall_chart(result):
    k, cur = result["summary_kpis"], result["metadata"]["currency"]
    sources = result["metadata"]["kpi_sources"]
    rows = {r["line_item"]: r for r in result["line_item_analysis"]}
    rev = k["revenue"]["actual"]
    cogs = rows[sources["cogs"]]["actual"] if sources["cogs"] else 0.0
    opex = rows[sources["opex"]]["actual"] if sources["opex"] else 0.0
    profit = k["operating_income"]["actual"]

    steps, running = [], 0.0
    for label, amount, kind in (("Sales", rev, "total"), ("Cost of goods", -cogs, "cost"), ("Running costs", -opex, "cost")):
        start, end = (0.0, amount) if kind == "total" else (running, running + amount)
        steps.append({"Step": label, "start": start, "end": end, "kind": label, "amount": amount})
        running = end
    steps.append({"Step": "Operating profit", "start": 0.0, "end": profit,
                  "kind": "Operating profit" if profit >= 0 else "Operating loss", "amount": profit})
    df = pd.DataFrame(steps)
    df["label"] = df["amount"].map(lambda v: format_compact(v, cur))
    # Short labels on the bars so neighbours don't overlap; the tooltip keeps the full wording.
    df["short"] = df["label"].str.replace(" lakh crore", "L Cr").str.replace(" crore", " Cr").str.replace(" lakh", "L")
    order = list(df["Step"])

    color = alt.Color("kind:N", legend=None, scale=alt.Scale(
        domain=["Sales", "Cost of goods", "Running costs", "Operating profit", "Operating loss"],
        range=[ui.COLORS["actual"], ui.COLORS["cogs"], ui.COLORS["opex"], ui.COLORS["profit"], ui.COLORS["loss"]]))
    bars = alt.Chart(df).mark_bar(cornerRadius=6).encode(
        x=alt.X("Step:N", sort=order, title=None, scale=alt.Scale(paddingInner=0.25),
                axis=alt.Axis(labelAngle=0, labelFontWeight=600, labelOverlap=False, labelLimit=120,
                              labelExpr="split(datum.label, ' ')")),
        y=alt.Y("start:Q", title=None, axis=alt.Axis(labels=False, ticks=False, domain=False)),
        y2="end:Q", color=color,
        tooltip=["Step", alt.Tooltip("label:N", title="Amount")],
    )
    text = alt.Chart(df).mark_text(dy=-10, fontWeight="bold", fontSize=12.5, color=ui.COLORS["ink"]).encode(
        x=alt.X("Step:N", sort=order), y=alt.Y("max_y:Q"), text="short:N",
    ).transform_calculate(max_y="max(datum.start, datum.end)")
    return styled((bars + text).properties(height=330))


def per_100_chart(result):
    per100 = result["summary_kpis"].get("per_100_of_revenue")
    if not per100 or per100["cogs"] is None:
        return None
    profit_name = "Operating profit" if per100["operating_income"] >= 0 else "Operating loss"
    parts = pd.DataFrame([
        {"Part": "Cost of goods", "Amount": per100["cogs"], "order": 1},
        {"Part": "Running costs", "Amount": per100["opex"], "order": 2},
        {"Part": profit_name, "Amount": abs(per100["operating_income"]), "order": 3},
    ])
    parts["Legend"] = parts.apply(lambda r: f"{r['Part']}  {r['Amount']:.0f}", axis=1)
    domain = list(parts["Legend"])
    colors = [ui.COLORS["cogs"], ui.COLORS["opex"],
              ui.COLORS["profit"] if per100["operating_income"] >= 0 else ui.COLORS["loss"]]
    donut = alt.Chart(parts).mark_arc(innerRadius=56, outerRadius=92, cornerRadius=4, padAngle=0.015).encode(
        theta=alt.Theta("Amount:Q", stack=True),
        color=alt.Color("Legend:N", sort=domain, scale=alt.Scale(domain=domain, range=colors),
                        legend=alt.Legend(orient="bottom", title=None, direction="vertical", labelFontSize=13)),
        order="order:Q",
        tooltip=["Part", alt.Tooltip("Amount:Q", format=".1f", title="Per 100 of sales")],
    )
    labels = alt.Chart(pd.DataFrame([{"t": f"{parts.iloc[2]['Amount']:.0f}", "s": "left as profit"
                                      if per100["operating_income"] >= 0 else "lost"}])).mark_text(
        fontSize=26, fontWeight="bold", color=ui.COLORS["ink"], dy=-6).encode(text="t:N")
    sub = alt.Chart(pd.DataFrame([{"s": labels.data.iloc[0]["s"]}])).mark_text(
        fontSize=12, color=ui.COLORS["muted"], dy=16).encode(text="s:N")
    labels = labels + sub
    return styled((donut + labels).properties(height=330))


def compare_chart(result):
    k, cur, n = result["summary_kpis"], result["metadata"]["currency"], names(result)
    rows = []
    for label, key in (("Sales", "revenue"), ("Operating profit", "operating_income")):
        rows += [{"Measure": label, "Period": n["base"], "Value": k[key]["budget"]},
                 {"Measure": label, "Period": n["act"], "Value": k[key]["actual"]}]
    for item in result["line_item_analysis"]:
        if item["category"] in ("cogs", "opex") and item["line_item"] in result["metadata"]["kpi_sources"].values():
            name = "Cost of goods" if item["category"] == "cogs" else "Running costs"
            rows += [{"Measure": name, "Period": n["base"], "Value": item["budget"]},
                     {"Measure": name, "Period": n["act"], "Value": item["actual"]}]
    df = pd.DataFrame(rows)
    df["Label"] = df["Value"].map(lambda v: format_compact(v, cur))
    order = ["Sales", "Cost of goods", "Running costs", "Operating profit"]
    chart = alt.Chart(df).mark_bar(cornerRadiusTopLeft=5, cornerRadiusTopRight=5).encode(
        x=alt.X("Measure:N", sort=order, title=None, axis=alt.Axis(labelAngle=0, labelFontWeight=600)),
        xOffset=alt.XOffset("Period:N", sort=[n["base"], n["act"]]),
        y=alt.Y("Value:Q", title=None, axis=alt.Axis(format="~s")),
        color=alt.Color("Period:N", sort=[n["base"], n["act"]],
                        scale=alt.Scale(range=[ui.COLORS["baseline"], ui.COLORS["actual"]]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=["Measure", "Period", alt.Tooltip("Label:N", title="Amount")],
    ).properties(height=330)
    return styled(chart)


def charts_tab(result):
    symbol = CURRENCY_SYMBOLS.get(result["metadata"]["currency"].split()[0], "")
    n = names(result)
    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            ui.section_title("From sales to profit", "How sales turn into operating profit after costs.")
            st.altair_chart(waterfall_chart(result), use_container_width=True)
    with right:
        with st.container(border=True):
            ui.section_title(f"Where every {symbol}100 of sales went", "Share of sales spent on each cost, and what's left.")
            donut = per_100_chart(result)
            if donut is not None:
                st.altair_chart(donut, use_container_width=True)
            else:
                st.caption("Needs both sales and cost of goods to draw.")
    with st.container(border=True):
        ui.section_title(f"{n['act']} compared with {n['base'].lower()}")
        st.altair_chart(compare_chart(result), use_container_width=True)


def line_items_tab(result):
    cur, n = result["metadata"]["currency"], names(result)
    friendly = {"revenue": "Sales", "cogs": "Cost of goods", "opex": "Running costs", "cash": "Cash"}
    rows = []
    for r in result["line_item_analysis"]:
        rows.append({
            "Item": r["line_item"],
            "Type": friendly.get(r["category"], "Other"),
            n["base"]: format_money(r["budget"], cur),
            n["act"]: format_money(r["actual"], cur),
            "Change": format_money(r["variance_abs"], cur, signed=True),
            "Change %": f"{r['variance_pct']:+.1f}%" if r["variance_pct"] is not None else "n/a",
            "Status": "🔴 Alert" if r["flag"] else ("🟢 Better" if r["is_favorable"] else "🟠 Worse"),
            "Why": r["flag"] or "",
        })
    with st.container(border=True):
        ui.section_title("Every line, side by side",
                         "🟢 Better = good for the business · 🟠 Worse = moved the wrong way · "
                         "🔴 Alert = moved the wrong way by more than your alert setting")
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.download_button("Download table (CSV)", pd.DataFrame(result["line_item_analysis"]).to_csv(index=False),
                           "line_items.csv", "text/csv")


def what_if_tab(engine, payload, base):
    cur = base["metadata"]["currency"]
    controls, results = st.columns([2, 3])
    with controls:
        with st.container(border=True):
            ui.section_title("Try a scenario", "Move the sliders to see the effect on profit and cash.")
            rev = st.slider("Sales change (%)", -50, 50, 0, 1)
            opex = st.slider("Running costs change (%)", -50, 50, 0, 1)
            cogs_follows = st.toggle("Cost of goods moves with sales", value=True,
                                     help="Usually true: if you sell more, you buy or make more.")
    sim = engine.forecast_scenario(payload, revenue_growth_pct=rev, opex_adjustment_pct=opex,
                                   cogs_scales_with_revenue=cogs_follows)
    b, s = base["summary_kpis"], sim["summary_kpis"]

    def card(col, icon, label, before, after):
        diff = after - before
        with col:
            ui.kpi_card(icon, label, format_compact(after, cur),
                        format_compact(diff, cur, True) if round(diff) else "No change",
                        "good" if diff > 0 else "bad" if diff < 0 else "neutral",
                        f"Today: {format_compact(before, cur)}")

    with results:
        c1, c2, c3 = st.columns(3)
        card(c1, "💰", "Sales", b["revenue"]["actual"], s["revenue"]["actual"])
        card(c2, "🏭", "Operating profit", b["operating_income"]["actual"], s["operating_income"]["actual"])
        if s["net_cash_flow"]["available"]:
            card(c3, "🏦", "Cash flow", b["net_cash_flow"]["actual"], s["net_cash_flow"]["actual"])
        st.write("")
        if rev or opex:
            before, after = b["operating_income"]["actual"], s["operating_income"]["actual"]
            direction = "rise" if after > before else "fall"
            st.info(f"With sales {rev:+d}% and running costs {opex:+d}%, operating profit would {direction} from "
                    f"**{format_compact(before, cur)}** to **{format_compact(after, cur)}**.")
            if s["cash_runway_months"] is not None:
                st.warning(f"Cash would run out in about **{s['cash_runway_months']:.0f} months** at that pace.")
        else:
            st.caption("Move a slider to see a scenario.")
        st.caption("Simple model: cash moves by the same amount as operating profit; taxes and timing are ignored.")


def data_tab(result, payload):
    src = payload.get("source", {})
    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            ui.section_title("Where the numbers came from")
            if "profit_and_loss_page" in src:
                st.markdown(f"- **{src['scope']}** financial statements")
                st.markdown(f"- Profit & loss statement: page **{src['profit_and_loss_page']}**")
                if src.get("cash_flow_page"):
                    st.markdown(f"- Cash flow statement: page **{src['cash_flow_page']}**")
                checks = src.get("checks", {})
                if checks and all(checks.values()):
                    st.success("The extracted figures add up: totals and cash balances reconcile.")
                elif checks:
                    st.warning(f"Some totals don't add up: {checks}. Double-check against the PDF.")
                cur = result["metadata"]["currency"]
                ebitda = result["summary_kpis"]["operating_income"]["actual"] + src["other_income"]["actual"]
                st.markdown(f"- EBITDA incl. other income: **{format_money(ebitda, cur)}**")
                st.markdown(f"- EBIT (after depreciation): **{format_money(ebitda - src['depreciation']['actual'], cur)}**")
            elif "file_columns" in src:
                cols = src["file_columns"]
                st.markdown(f"Read **{cols['item']}** (item), **{cols['baseline']}** (comparison) and "
                            f"**{cols['actual']}** (actual) from your file.")
            else:
                st.markdown("Demo data for a fictional company.")
    with right:
        with st.container(border=True):
            ui.section_title("What the words mean")
            for term, meaning in GLOSSARY.items():
                st.markdown(f"**{term}** — {meaning}")


def export_sidebar(result):
    ui.sidebar_step("3 · Export")
    period = result["metadata"]["period"].replace(" ", "_")
    st.sidebar.download_button("📄 Board report (HTML → PDF)", build_board_report(result),
                               f"Nexus_FPA_Report_{period}.html", "text/html",
                               help="Open the file in your browser and use Print → Save as PDF.")
    st.sidebar.download_button("Full data (JSON)", json.dumps(result, indent=2, ensure_ascii=False),
                               f"Nexus_FPA_{period}.json", "application/json")


# ----------------------------------------------------------------------------
# Page
# ----------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Nexus FP&A", page_icon="📊", layout="wide")
    ui.inject_css()
    ui.sidebar_brand()

    payload = load_payload()

    ui.sidebar_step("2 · Alert settings")
    with st.sidebar.expander("Sensitivity"):
        threshold = st.slider("Warn when a number moves the wrong way by more than (%)", 1.0, 25.0, 10.0, 0.5)
        critical = st.slider("Mark as serious above (%)", 10.0, 50.0, 20.0, 1.0)
    if critical < threshold:
        st.sidebar.error("The 'serious' level must be higher than the warning level.")
        st.stop()

    engine = FPAAnalysisEngine(overrun_threshold_pct=threshold, critical_threshold_pct=critical)
    try:
        result = engine.process_bva(payload)
    except ValueError as e:
        st.error(f"The data couldn't be analysed: {e}")
        st.stop()

    export_sidebar(result)

    meta, pe, n = result["metadata"], result["plain_english"], names(result)
    src = payload.get("source", {})
    ui.hero("How is the business doing?",
            f"A plain-English health check of {meta['period']}, compared with {n['compare']}.",
            [meta["report_type"], meta["period"], f"Amounts in {meta['currency']}",
             f"Source: PDF p.{src['profit_and_loss_page']}" if "profit_and_loss_page" in src else None])

    ui.verdict_card(pe["status"], pe["verdict"], snapshot_sentence(result), pe["health_score"])
    st.write("")
    kpi_row(result)
    st.write("")

    tabs = st.tabs(["Overview", "Charts", "Line items", "What if?", "Data & glossary"])
    with tabs[0]:
        overview_tab(result)
    with tabs[1]:
        charts_tab(result)
    with tabs[2]:
        line_items_tab(result)
    with tabs[3]:
        what_if_tab(engine, payload, result)
    with tabs[4]:
        data_tab(result, payload)

    ui.footer(f"{ui.BRAND} · Generated {date.today():%d %b %Y} · Automated analysis — review before external use")


if __name__ == "__main__":
    main()
