"""Printable HTML board report built from an engine result (open in a browser, then Print → Save as PDF)."""
from datetime import date
from html import escape

from fpa_engine import format_compact, format_money
from ui import BRAND, COLORS, STATUS_LABEL, STATUS_TONE, TONE


def build_board_report(result: dict) -> str:
    meta, k, pe = result["metadata"], result["summary_kpis"], result["plain_english"]
    ex = result["cfo_executive_summary"]
    cur = meta["currency"]
    vs = "vs budget" if meta["baseline"] == "budget" else "vs last year"
    base_col = "Budget" if meta["baseline"] == "budget" else "Last year"
    act_col = "Actual" if meta["baseline"] == "budget" else "This year"
    tone_color, tone_bg = TONE[STATUS_TONE[pe["status"]]]

    def kpi(label, value, delta):
        return (f'<div class="kpi"><div class="l">{escape(label)}</div><div class="v">{escape(value)}</div>'
                f'<div class="d">{escape(delta or "")}</div></div>')

    gm, oi, rev, cash = k["gross_margin_pct"], k["operating_income"], k["revenue"], k["net_cash_flow"]
    kpis = "".join([
        kpi("Sales", format_compact(rev["actual"], cur),
            f"{rev['variance_pct']:+.1f}% {vs}" if rev["variance_pct"] is not None else ""),
        kpi("Gross margin", f"{gm['actual']:.1f}%" if gm["actual"] is not None else "N/A",
            f"{gm['variance_bps'] / 100:+.1f} pts {vs}" if gm["variance_bps"] is not None else ""),
        kpi("Operating profit", format_compact(oi["actual"], cur), f"{format_compact(oi['variance_abs'], cur, True)} {vs}"),
        kpi("Cash flow", format_compact(cash["actual"], cur, True) if cash["available"] else "Not provided", ""),
    ])

    def bullets(items):
        return "".join(f"<li>{escape(i)}</li>" for i in items) or "<li>None</li>"

    rows = "".join(
        f"<tr><td>{escape(r['line_item'])}</td><td>{escape(format_money(r['budget'], cur))}</td>"
        f"<td>{escape(format_money(r['actual'], cur))}</td><td>{escape(format_money(r['variance_abs'], cur, True))}</td>"
        f"<td>{(format(r['variance_pct'], '+.1f') + '%') if r['variance_pct'] is not None else 'n/a'}</td>"
        f"<td>{'Alert' if r['flag'] else ('Better' if r['is_favorable'] else 'Worse')}</td></tr>"
        for r in result["line_item_analysis"]
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(BRAND)} Board Report · {escape(meta['period'])}</title>
<style>
  body {{ font-family: Inter, -apple-system, 'Segoe UI', sans-serif; color:{COLORS['ink']}; margin:0; background:#fff; }}
  .page {{ max-width: 860px; margin: 0 auto; padding: 40px 36px; }}
  .top {{ display:flex; justify-content:space-between; align-items:flex-end; border-bottom:3px solid {COLORS['primary']}; padding-bottom:14px; }}
  .brand {{ font-weight:800; color:{COLORS['primary']}; font-size:14px; letter-spacing:.04em; text-transform:uppercase; }}
  h1 {{ margin:4px 0 0; font-size:26px; }}
  .meta {{ color:{COLORS['muted']}; font-size:13px; text-align:right; }}
  .verdict {{ margin:22px 0; padding:16px 18px; border-radius:12px; background:{tone_bg}; border-left:5px solid {tone_color}; }}
  .verdict b {{ color:{tone_color}; }}
  .kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }}
  .kpi {{ border:1px solid {COLORS['line']}; border-radius:12px; padding:12px 14px; }}
  .kpi .l {{ font-size:12px; color:{COLORS['muted']}; font-weight:600; }}
  .kpi .v {{ font-size:20px; font-weight:800; margin:4px 0; }}
  .kpi .d {{ font-size:12px; color:{COLORS['muted']}; }}
  h2 {{ font-size:16px; margin:28px 0 8px; }}
  .cols {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; }}
  ul {{ margin:0; padding-left:18px; line-height:1.6; font-size:14px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th, td {{ text-align:left; padding:8px 6px; border-bottom:1px solid {COLORS['line']}; }}
  th {{ color:{COLORS['muted']}; font-weight:600; }}
  p {{ font-size:14px; line-height:1.6; }}
  .foot {{ margin-top:30px; color:{COLORS['muted']}; font-size:11px; border-top:1px solid {COLORS['line']}; padding-top:10px; }}
  @media print {{ .page {{ padding: 0; }} }}
  @media (max-width: 640px) {{ .kpis, .cols {{ grid-template-columns:1fr 1fr; }} }}
</style></head>
<body><div class="page">
  <div class="top">
    <div><div class="brand">{escape(BRAND)}</div><h1>Financial Performance Report</h1></div>
    <div class="meta">{escape(meta['report_type'])}<br>{escape(meta['period'])}<br>Amounts in {escape(cur)}</div>
  </div>
  <div class="verdict"><b>{escape(STATUS_LABEL[pe['status']])} · Health score {pe['health_score']}/100.</b> {escape(pe['verdict'])}</div>
  <div class="kpis">{kpis}</div>
  <div class="cols">
    <div><h2>What went well</h2><ul>{bullets(pe['good_news'])}</ul></div>
    <div><h2>What to watch</h2><ul>{bullets(pe['watch_out'])}</ul></div>
  </div>
  <h2>Recommended next steps</h2><ul>{bullets(pe['next_steps'])}</ul>
  <h2>CFO commentary</h2>
  <p>{escape(ex['topline_performance'])} {escape(ex['cost_and_margins'])} {escape(ex['cash_and_liquidity'])}</p>
  <h2>Line items</h2>
  <table><tr><th>Item</th><th>{base_col}</th><th>{act_col}</th><th>Change</th><th>Change %</th><th>Status</th></tr>{rows}</table>
  <div class="foot">Generated by {escape(BRAND)} on {date.today():%d %b %Y}. Figures as supplied; analysis is automated and should be reviewed before external use.</div>
</div></body></html>"""
