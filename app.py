"""
app.py
======
Streamlit-hovedapp for Smart Money EU Dashboard.

Faner:
- 🇳🇴 Oslo Børs
- 🇸🇪🇩🇰🇫🇮 Norden
- 🇪🇺 Europa (STOXX 600)
- 💰 Paper trading
"""

import streamlit as st
from datetime import datetime

from scanner_core import load_signals_state
from tabs.tab_region import render_region_tab
from tabs.tab_paper_trading import render_paper_trading_tab


st.set_page_config(
    page_title="Smart Money EU",
    page_icon="🐋",
    layout="wide",
)

st.title("🐋 Smart Money EU Dashboard")
st.caption(
    "Wyckoff/VSA/RS-analyse for europeiske aksjer + paper trading. "
    "Forsknings-/opplæringsverktøy — ikke investeringsråd."
)

state = load_signals_state()
if state is None:
    st.warning(
        "Ingen daglig scan funnet ennå. "
        "Kjør `python scanner_job.py` lokalt eller vent på første cron-run."
    )
    st.stop()

generated_at = state.get("generated_at", "ukjent")
try:
    dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    age_str = dt.strftime("%Y-%m-%d %H:%M UTC")
except Exception:
    age_str = generated_at

st.caption(f"Siste scan: **{age_str}**")

tab_oslo, tab_nordic, tab_europe, tab_paper = st.tabs([
    "🇳🇴 Oslo Børs",
    "🇸🇪🇩🇰🇫🇮 Norden",
    "🇪🇺 Europa",
    "💰 Paper trading",
])

with tab_oslo:
    render_region_tab("OSLO", state)

with tab_nordic:
    render_region_tab("NORDIC", state)

with tab_europe:
    render_region_tab("EUROPE", state)

with tab_paper:
    render_paper_trading_tab()

st.markdown("---")
st.caption("Bygd med Streamlit + yfinance. Kode: GitHub.")
