"""Visual polish only: fonts, spacing, cards, badges. No behavior lives here."""

from __future__ import annotations

import streamlit as st

CSS = """
<style>
:root {
    --brand: #4F46E5;
    --brand-dark: #3730A3;
    --ink: #0F172A;
    --muted: #64748B;
    --line: #E2E8F0;
    --surface: #F8FAFC;
}

html, body, [class*="css"] {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, Helvetica, Arial, sans-serif !important;
}

#MainMenu, footer, header[data-testid="stHeader"] { visibility: hidden; height: 0; }
div[data-testid="stToolbar"] { display: none; }
div[data-testid="stDecoration"] { display: none; }
.block-container { padding-top: 2rem; padding-bottom: 3rem; max-width: 1200px; }

/* ---- headings ---- */
h1, h2, h3 { color: var(--ink); letter-spacing: -0.02em; font-weight: 700; }
h2 { font-size: 1.65rem !important; margin-bottom: 0.15rem !important; }
[data-testid="stCaptionContainer"] { color: var(--muted); font-size: 0.95rem; }

/* ---- sidebar ---- */
section[data-testid="stSidebar"] {
    background: var(--surface);
    border-right: 1px solid var(--line);
}
section[data-testid="stSidebar"] .block-container { padding-top: 1.75rem; }
.brand-card {
    display: flex; align-items: center; gap: 0.65rem;
    padding: 0 0 1rem 0; margin-bottom: 1rem;
    border-bottom: 1px solid var(--line);
}
.brand-mark {
    width: 38px; height: 38px; border-radius: 10px;
    background: linear-gradient(135deg, var(--brand), var(--brand-dark));
    display: flex; align-items: center; justify-content: center;
    font-size: 1.15rem; flex-shrink: 0;
    box-shadow: 0 2px 6px rgba(79, 70, 229, 0.35);
}
.brand-title { font-weight: 700; font-size: 1.02rem; color: var(--ink); line-height: 1.15; }
.brand-subtitle { font-size: 0.78rem; color: var(--muted); margin-top: 1px; }

.status-chip {
    display: inline-flex; align-items: center; gap: 0.4rem;
    font-size: 0.82rem; font-weight: 600; padding: 0.3rem 0.6rem;
    border-radius: 999px; margin-bottom: 0.9rem;
}
.status-chip.ok { background: #DCFCE7; color: #15803D; }
.status-chip.bad { background: #FEE2E2; color: #B91C1C; }
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }

section[data-testid="stSidebar"] label[data-testid="stWidgetLabel"] p {
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--muted); font-weight: 700;
}

/* ---- buttons ---- */
.stButton > button, .stFormSubmitButton > button, .stDownloadButton > button {
    border-radius: 8px; font-weight: 600; transition: all 0.15s ease;
    border: 1px solid var(--line);
}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
    background: var(--brand); border-color: var(--brand);
    box-shadow: 0 1px 2px rgba(79, 70, 229, 0.25);
}
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover {
    background: var(--brand-dark); border-color: var(--brand-dark);
}

/* ---- cards: forms, bordered containers, expanders ---- */
div[data-testid="stForm"], div[data-testid="stExpander"] {
    border: 1px solid var(--line) !important; border-radius: 12px !important;
    background: #FFFFFF; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(> div > div[data-testid="stVerticalBlock"]) {
    border-radius: 12px;
}

/* ---- metrics ---- */
div[data-testid="stMetric"] {
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 10px; padding: 0.85rem 1rem 0.7rem 1rem;
}
[data-testid="stMetricLabel"] p {
    font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.03em;
    color: var(--muted); font-weight: 700;
    white-space: normal !important; overflow: visible !important;
    text-overflow: clip !important; line-height: 1.25;
}
div[data-testid="stMetricValue"] { font-weight: 700; color: var(--ink); }

/* ---- tabs ---- */
.stTabs [data-baseweb="tab-list"] { gap: 1.75rem; border-bottom: 1px solid var(--line); }
.stTabs [data-baseweb="tab"] {
    font-weight: 600; color: var(--muted); padding-bottom: 0.6rem;
}
.stTabs [aria-selected="true"] { color: var(--brand) !important; }
.stTabs [data-baseweb="tab-highlight"] { background-color: var(--brand) !important; height: 2.5px; }

/* ---- dataframes / tables ---- */
div[data-testid="stDataFrame"], div[data-testid="stTable"] {
    border: 1px solid var(--line); border-radius: 10px; overflow: hidden;
}

/* ---- alerts ---- */
div[data-testid="stAlertContentSuccess"], div[data-testid="stAlertContentWarning"],
div[data-testid="stAlertContentError"], div[data-testid="stAlertContentInfo"] {
    border-radius: 10px;
}
div[data-testid="stAlert"] { border-radius: 10px; }

/* ---- code ---- */
code, pre, div[data-testid="stCodeBlock"] { border-radius: 8px !important; }

hr { border-color: var(--line); }
</style>
"""


def apply_theme() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def sidebar_brand(title: str, subtitle: str, icon: str = "\U0001f9ed") -> None:
    st.markdown(
        f"""<div class="brand-card">
            <div class="brand-mark">{icon}</div>
            <div>
                <div class="brand-title">{title}</div>
                <div class="brand-subtitle">{subtitle}</div>
            </div>
        </div>""",
        unsafe_allow_html=True,
    )


def status_chip(ok: bool, label: str) -> None:
    cls = "ok" if ok else "bad"
    st.markdown(f'<div class="status-chip {cls}"><span class="status-dot"></span>{label}</div>', unsafe_allow_html=True)
