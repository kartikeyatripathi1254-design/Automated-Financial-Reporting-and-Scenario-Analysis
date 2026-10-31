"""
Nexus FP&A design system: styles and HTML components for the Streamlit dashboard.

All user-supplied text (line item names, periods) must go through esc() before
being placed in HTML.
"""
import html

import streamlit as st

BRAND = "Nexus FP&A"
TAGLINE = "Financial performance, explained."

COLORS = {
    "primary": "#4F46E5",
    "primary_dark": "#3730A3",
    "ink": "#0F172A",
    "muted": "#64748B",
    "line": "#E6E8F0",
    "good": "#059669",
    "good_bg": "#ECFDF5",
    "warn": "#B45309",
    "warn_bg": "#FFFBEB",
    "bad": "#DC2626",
    "bad_bg": "#FEF2F2",
    "neutral_bg": "#F1F5F9",
    # Chart palette
    "baseline": "#CBD5E1",
    "actual": "#4F46E5",
    "cogs": "#94A3B8",
    "opex": "#F59E0B",
    "profit": "#10B981",
    "loss": "#EF4444",
}

TONE = {
    "good": (COLORS["good"], COLORS["good_bg"]),
    "warn": (COLORS["warn"], COLORS["warn_bg"]),
    "bad": (COLORS["bad"], COLORS["bad_bg"]),
    "neutral": (COLORS["muted"], COLORS["neutral_bg"]),
}

STATUS_TONE = {"healthy": "good", "watch": "warn", "at_risk": "bad"}
STATUS_LABEL = {"healthy": "Healthy", "watch": "Watch", "at_risk": "At risk"}


def esc(text) -> str:
    return html.escape(str(text))


def _html(markup: str, sidebar: bool = False):
    """Render raw HTML. Blank lines are removed: Markdown would end the HTML block at them."""
    clean = "\n".join(line for line in markup.splitlines() if line.strip())
    (st.sidebar if sidebar else st).markdown(clean, unsafe_allow_html=True)


def inject_css():
    c = COLORS
    _html(f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
html, body, [class*="css"], .stApp, button, input, textarea, select {{
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
}}
.stApp {{ background: #F6F7FB; }}
[data-testid="stHeader"] {{ background: transparent; }}
.stDeployButton, [data-testid="stStatusWidget"] {{ display: none; }}
.block-container {{ padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1280px; }}

/* Sidebar */
[data-testid="stSidebar"] {{ background: #0F172A; }}
[data-testid="stSidebar"] * {{ color: #E2E8F0; }}
[data-testid="stSidebar"] .stButton button, [data-testid="stSidebar"] .stDownloadButton button {{
  background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.14); color: #F8FAFC; width: 100%;
}}
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {{
  background: rgba(255,255,255,.05); border: 1px dashed rgba(255,255,255,.25);
}}
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] button,
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] button * {{ color: #0F172A !important; }}
[data-testid="stSidebar"] [data-testid="stFileUploaderFile"] * {{ color: #0F172A !important; }}
[data-testid="stSidebar"] [data-testid="stExpander"] {{ border-color: rgba(255,255,255,.14); }}
[data-testid="stSidebar"] input, [data-testid="stSidebar"] [data-baseweb="select"] * {{ color: #0F172A !important; }}
.nx-side-brand {{ display:flex; align-items:center; gap:10px; margin: 4px 0 18px; }}
.nx-logo {{ width:36px; height:36px; border-radius:10px; display:grid; place-items:center;
  background: linear-gradient(135deg, {c['primary']}, #06B6D4); color:#fff !important; font-weight:800; font-size:15px; }}
.nx-side-name {{ font-weight:800; font-size:17px; color:#fff !important; line-height:1.1; }}
.nx-side-tag {{ font-size:11.5px; color:#94A3B8 !important; }}
.nx-step {{ font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:#94A3B8 !important;
  font-weight:700; margin: 18px 0 6px; }}

/* Hero header */
.nx-hero {{ background: linear-gradient(120deg, {c['primary_dark']} 0%, {c['primary']} 55%, #0EA5E9 100%);
  border-radius: 18px; padding: 26px 30px; color: #fff; margin-bottom: 18px;
  box-shadow: 0 10px 30px -12px rgba(79,70,229,.45); }}
.nx-hero h1 {{ color:#fff; font-size: 28px; font-weight: 800; margin: 0 0 4px; padding:0; letter-spacing:-.02em; }}
.nx-hero p {{ color: rgba(255,255,255,.85); margin: 0 0 14px; font-size: 14.5px; }}
.nx-chip {{ display:inline-block; background: rgba(255,255,255,.16); border: 1px solid rgba(255,255,255,.22);
  color:#fff; border-radius: 999px; padding: 4px 11px; font-size: 12.5px; font-weight: 500; margin: 0 6px 6px 0; }}

/* Cards */
.nx-card {{ background:#fff; border:1px solid {c['line']}; border-radius:16px; padding:18px 20px;
  box-shadow: 0 1px 2px rgba(15,23,42,.04); height:100%; }}
.nx-kpi-label {{ font-size:12.5px; font-weight:600; color:{c['muted']}; display:flex; align-items:center; gap:6px; }}
.nx-kpi-value {{ font-size:27px; font-weight:800; color:{c['ink']}; letter-spacing:-.02em; margin:6px 0 8px;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.nx-pill {{ display:inline-block; border-radius:999px; padding:3px 10px; font-size:12px; font-weight:600; }}
.nx-kpi-note {{ font-size:12px; color:{c['muted']}; margin-top:8px; line-height:1.4; }}
.nx-info {{ display:inline-grid; place-items:center; width:15px; height:15px; border-radius:50%;
  background:{c['neutral_bg']}; color:{c['muted']}; font-size:10px; font-weight:700; cursor:help; }}

.nx-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(195px, 1fr)); gap:14px; }}
@media (min-width: 1100px) and (max-width: 1500px) {{ .nx-kpi-value {{ font-size:23px; }} }}
/* Verdict */
.nx-verdict {{ display:flex; align-items:center; justify-content:space-between; gap:24px; }}
.nx-verdict-title {{ font-size:20px; font-weight:800; color:{c['ink']}; margin:6px 0 4px; letter-spacing:-.01em; }}
.nx-verdict-sub {{ font-size:14px; color:{c['muted']}; margin:0; line-height:1.5; }}
.nx-score {{ text-align:center; min-width:120px; }}
.nx-score-num {{ font-size:13px; color:{c['muted']}; font-weight:600; margin-top:2px; }}

/* Insight lists */
.nx-section-title {{ font-size:15px; font-weight:700; color:{c['ink']}; margin: 2px 0 12px; display:flex; gap:8px; align-items:center; }}
.nx-item {{ display:flex; gap:10px; align-items:flex-start; padding:10px 0; border-top:1px solid {c['line']};
  font-size:14px; color:#334155; line-height:1.5; }}
.nx-item:first-of-type {{ border-top:none; padding-top:2px; }}
.nx-dot {{ flex:0 0 22px; height:22px; border-radius:7px; display:grid; place-items:center; font-size:12px; font-weight:700; }}
.nx-empty {{ color:{c['muted']}; font-size:14px; }}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {{ gap: 4px; background:#fff; border:1px solid {c['line']}; border-radius:12px; padding:4px; }}
.stTabs [data-baseweb="tab"] {{ height:40px; border-radius:9px; padding: 0 16px; font-weight:600; color:{c['muted']}; }}
.stTabs [aria-selected="true"] {{ background:{c['primary']}10; color:{c['primary']} !important; }}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {{ display:none; }}

/* Landing */
.nx-feature {{ font-size:14px; color:#334155; line-height:1.55; }}
.nx-feature b {{ color:{c['ink']}; display:block; font-size:15px; margin:10px 0 4px; }}
.nx-feature-icon {{ width:40px; height:40px; border-radius:11px; display:grid; place-items:center; font-size:20px;
  background:{c['primary']}12; }}

.nx-footer {{ text-align:center; color:{c['muted']}; font-size:12px; margin-top:36px; }}
[data-testid="stMetric"] {{ background:#fff; border:1px solid {c['line']}; border-radius:14px; padding:14px 16px; }}
</style>
""")


def sidebar_brand():
    _html(f"""
<div class="nx-side-brand">
  <div class="nx-logo">N</div>
  <div><div class="nx-side-name">{esc(BRAND)}</div><div class="nx-side-tag">{esc(TAGLINE)}</div></div>
</div>""", sidebar=True)


def sidebar_step(text: str):
    _html(f'<div class="nx-step">{esc(text)}</div>', sidebar=True)


def hero(title: str, subtitle: str, chips: list):
    chip_html = "".join(f'<span class="nx-chip">{esc(c)}</span>' for c in chips if c)
    _html(f"""
<div class="nx-hero">
  <h1>{esc(title)}</h1>
  <p>{esc(subtitle)}</p>
  <div>{chip_html}</div>
</div>""")


def score_ring(score: int, tone: str, size: int = 104) -> str:
    color, bg = TONE[tone]
    r = 42
    circ = 2 * 3.14159 * r
    filled = circ * max(0, min(100, score)) / 100
    return f"""
<svg width="{size}" height="{size}" viewBox="0 0 100 100" role="img" aria-label="Health score {score} out of 100">
  <circle cx="50" cy="50" r="{r}" fill="none" stroke="{bg}" stroke-width="10"/>
  <circle cx="50" cy="50" r="{r}" fill="none" stroke="{color}" stroke-width="10" stroke-linecap="round"
          stroke-dasharray="{filled:.1f} {circ:.1f}" transform="rotate(-90 50 50)"/>
  <text x="50" y="56" text-anchor="middle" font-size="24" font-weight="800" fill="{COLORS['ink']}"
        font-family="Inter, sans-serif">{score}</text>
</svg>"""


def verdict_card(status: str, verdict: str, detail: str, score: int):
    tone = STATUS_TONE[status]
    color, bg = TONE[tone]
    _html(f"""
<div class="nx-card" style="border-left:5px solid {color};">
  <div class="nx-verdict">
    <div>
      <span class="nx-pill" style="background:{bg};color:{color};">● {esc(STATUS_LABEL[status])}</span>
      <div class="nx-verdict-title">{esc(verdict)}</div>
      <p class="nx-verdict-sub">{esc(detail)}</p>
    </div>
    <div class="nx-score">{score_ring(score, tone)}<div class="nx-score-num">Health score</div></div>
  </div>
</div>""")


def kpi_html(icon: str, label: str, value: str, delta: str = None, tone: str = "neutral",
             note: str = None, help_text: str = None) -> str:
    color, bg = TONE[tone]
    info = f'<span class="nx-info" title="{esc(help_text)}">?</span>' if help_text else ""
    delta_html = f'<span class="nx-pill" style="background:{bg};color:{color};">{esc(delta)}</span>' if delta else ""
    note_html = f'<div class="nx-kpi-note">{esc(note)}</div>' if note else ""
    return (f'<div class="nx-card"><div class="nx-kpi-label">{icon} {esc(label)} {info}</div>'
            f'<div class="nx-kpi-value" title="{esc(value)}">{esc(value)}</div>{delta_html}{note_html}</div>')


def kpi_card(*args, **kwargs):
    _html(kpi_html(*args, **kwargs))


def kpi_grid(cards: list):
    """Cards side by side on wide screens, wrapping to 2 x 2 or a single column when space is tight."""
    _html(f'<div class="nx-grid">{"".join(cards)}</div>')


def insight_card(title: str, icon: str, items: list, tone: str, empty_text: str, numbered: bool = False):
    color, bg = TONE[tone]
    if items:
        rows = "".join(
            f'<div class="nx-item"><span class="nx-dot" style="background:{bg};color:{color};">'
            f'{i if numbered else ("✓" if tone == "good" else "!")}</span><span>{esc(t)}</span></div>'
            for i, t in enumerate(items, 1)
        )
    else:
        rows = f'<div class="nx-empty">{esc(empty_text)}</div>'
    _html(f"""
<div class="nx-card">
  <div class="nx-section-title">{icon} {esc(title)}</div>
  {rows}
</div>""")


def section_title(text: str, caption: str = None):
    cap = f'<div style="color:{COLORS["muted"]};font-size:13px;margin:-6px 0 12px;">{esc(caption)}</div>' if caption else ""
    _html(f'<div class="nx-section-title" style="font-size:17px;margin-top:8px;">{esc(text)}</div>{cap}')


def feature(icon: str, title: str, text: str):
    _html(f"""
<div class="nx-card nx-feature">
  <div class="nx-feature-icon">{icon}</div>
  <b>{esc(title)}</b>{esc(text)}
</div>""")


def footer(text: str):
    _html(f'<div class="nx-footer">{esc(text)}</div>')
