"""
app.py
======
Streamlit-hovedapp for Smart Money EU Dashboard.

Tre region-faner:
- Oslo Børs
- Norden (Sverige, Danmark, Finland)
- Europa (STOXX 600)

Hver fane viser:
1. Sektor-varmekart (RS vs region-indeks)
2. Signal-liste fra siste daglige scan
3. Drill-down: klikk på aksje for VSA + Wyckoff-detaljer
"""

import streamlit as st
from datetime import datetime

from scanner_core import load_signals_state
from tabs.tab_region import render_region_tab


st.set_page_config(
    page_title="Smart Money EU",
    page_icon="🇪🇺",
    layout="wide",
)

st.title("🇪🇺 Smart Money EU Dashboard")
st.caption(
    "Wyckoff-, VSA- og RS-analyse for europeiske aksjer. "
    "Forsknings-/opplæringsverktøy — ikke investeringsråd."
)

# Last inn siste scan
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

# Region-faner
tab_oslo, tab_nordic, tab_europe = st.tabs([
    "🇳🇴 Oslo Børs",
    "🇸🇪🇩🇰🇫🇮 Norden",
    "🇪🇺 Europa (STOXX 600)",
])

with tab_oslo:
    render_region_tab("OSLO", state)

with tab_nordic:
    render_region_tab("NORDIC", state)

with tab_europe:
    render_region_tab("EUROPE", state)

st.markdown("---")
st.caption("Bygd med Streamlit + yfinance. Kode: GitHub.")
