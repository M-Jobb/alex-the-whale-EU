"""
tabs/tab_region.py
==================
Generisk region-fane brukt for OSLO, NORDIC, EUROPE.

NB: Alle Streamlit-elementer (selectbox, plotly_chart, dataframe) MÅ ha
en unik `key` som inkluderer regionen. Ellers krasjer Streamlit hvis
samme aksje finnes i flere regioner (f.eks. EQNR.OL finnes i både
OSLO og EUROPE-univers).
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd

from core.universe import Region, get_universe, get_tickers_by_sector
from core.data import fetch_one
from core.relative_strength import sector_relative_strength_matrix
from core.vsa import vsa_report, compute_obv
from core.wyckoff import analyze_wyckoff


def render_region_tab(region: Region, state: dict) -> None:
    """Render hele region-fanen. Region brukes som suffix på alle Streamlit keys."""
    region_signals = state.get("regions", {}).get(region, [])

    # === Toppstats ===
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Aksjer i univers", len(get_universe(region)))
    with col2:
        st.metric("Signaler i siste scan", len(region_signals))
    with col3:
        triple_strong = sum(1 for s in region_signals if s.get("triple_rs_strong"))
        st.metric("Trippel-RS-sterke", triple_strong)

    st.markdown("---")

    # === 1. Sektor-varmekart ===
    st.subheader("🗺 Sektor-varmekart (relativ styrke)")
    st.caption(
        "Sektorer fargekodet etter 20d % endring i RS-ratio mot region-indeks."
    )

    with st.spinner("Beregner sektor-RS..."):
        try:
            matrix = sector_relative_strength_matrix(region, period="3mo")
        except Exception as e:
            st.error(f"Kunne ikke beregne sektor-matrise: {e}")
            matrix = pd.DataFrame()

    if matrix.empty:
        st.warning("Ingen sektor-data tilgjengelig.")
    else:
        fig = px.treemap(
            matrix,
            path=["sector"],
            values="n_components",
            color="change_20d",
            color_continuous_scale="RdYlGn",
            color_continuous_midpoint=0,
            hover_data={"change_20d": ":.2f", "score": ":.0f", "used_region": True},
        )
        fig.update_layout(height=400, margin=dict(t=10, l=10, r=10, b=10))
        st.plotly_chart(fig, use_container_width=True, key=f"treemap_{region}")

        with st.expander("Rådata"):
            st.dataframe(
                matrix.style.format({
                    "change_20d": "{:+.2f}%",
                    "change_5d": "{:+.2f}%",
                    "score": "{:.0f}",
                }),
                use_container_width=True,
                key=f"sector_df_{region}",
            )

    st.markdown("---")

    # === 2. Topp-signaler ===
    st.subheader("🎯 Topp signaler (fra siste daglige scan)")
    if not region_signals:
        st.info("Ingen signaler i siste scan.")
    else:
        df = pd.DataFrame(region_signals)
        cols = [
            "symbol", "name", "sector", "wyckoff_phase",
            "spring", "markup", "triple_rs_strong",
            "vsa_bullish", "rs_aggregate_score", "final_score",
        ]
        cols = [c for c in cols if c in df.columns]
        st.dataframe(
            df[cols].style.format({
                "vsa_bullish": "{:.0f}",
                "rs_aggregate_score": "{:.0f}",
                "final_score": "{:.0f}",
            }).background_gradient(subset=["final_score"], cmap="Greens"),
            use_container_width=True,
            hide_index=True,
            key=f"signals_df_{region}",
        )

    st.markdown("---")

    # === 3. Drill-down ===
    st.subheader("🔬 Drill-down")
    universe = get_universe(region)
    options = [f"{t.symbol} — {t.name}" for t in universe]
    selected = st.selectbox(
        "Velg aksje for VSA + Wyckoff-analyse:",
        options,
        key=f"drilldown_select_{region}",
    )
    if selected:
        symbol = selected.split(" — ")[0]
        ticker = next(t for t in universe if t.symbol == symbol)
        _render_drilldown(ticker, region)


def _render_drilldown(ticker, region: Region) -> None:
    """Detaljert analyse for én aksje. region brukes for unike keys."""
    with st.spinner(f"Henter data for {ticker.symbol}..."):
        df = fetch_one(ticker.symbol, period="1y")

    if df is None or df.empty:
        st.error(f"Ingen data for {ticker.symbol}")
        return

    w = analyze_wyckoff(df)
    v = vsa_report(df)
    obv = compute_obv(df["Close"], df["Volume"])

    # Toppstats
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Wyckoff-fase", w.phase.upper())
    with c2:
        st.metric("Siste close", f"{w.last_close:.2f} {ticker.currency}")
    with c3:
        st.metric("VSA bullish-score", f"{v['bullish_score']:.0f}/100")
    with c4:
        st.metric("Spring/Markup", f"{'🌱' if w.spring_detected else ''}{'🚀' if w.markup_detected else ''}" or "—")

    # Pris + støtte/motstand
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
        name="Pris",
    ))
    fig.add_hline(y=w.support, line_dash="dash", line_color="green",
                  annotation_text=f"Støtte {w.support:.2f}")
    fig.add_hline(y=w.resistance, line_dash="dash", line_color="red",
                  annotation_text=f"Motstand {w.resistance:.2f}")

    for sig in v["signals"][:10]:
        color = {"absorption": "blue", "shakeout": "orange",
                 "climactic_buy": "purple", "no_supply": "green"}.get(sig.signal_type, "gray")
        fig.add_annotation(
            x=sig.date, y=df.loc[sig.date, "High"] if sig.date in df.index else None,
            text=sig.signal_type[:3].upper(),
            showarrow=True, arrowhead=2, bgcolor=color, font=dict(color="white", size=9),
        )

    fig.update_layout(
        title=f"{ticker.symbol} — {ticker.name}",
        xaxis_rangeslider_visible=False,
        height=500,
    )
    st.plotly_chart(fig, use_container_width=True, key=f"price_chart_{region}_{ticker.symbol}")

    # Volum + OBV
    col_v, col_o = st.columns(2)
    with col_v:
        vol_fig = go.Figure(go.Bar(x=df.index, y=df["Volume"], name="Volum"))
        vol_fig.update_layout(title="Volum", height=250, margin=dict(t=30))
        st.plotly_chart(vol_fig, use_container_width=True, key=f"vol_chart_{region}_{ticker.symbol}")
    with col_o:
        obv_fig = go.Figure(go.Scatter(x=obv.index, y=obv.values, name="OBV"))
        obv_fig.update_layout(
            title=f"OBV ({'stigende ✓' if v['obv_rising'] else 'fallende ✗'})",
            height=250, margin=dict(t=30),
        )
        st.plotly_chart(obv_fig, use_container_width=True, key=f"obv_chart_{region}_{ticker.symbol}")

    # VSA-signaler
    with st.expander("VSA-signaler (siste 60d)"):
        if v["signals"]:
            sig_df = pd.DataFrame([
                {
                    "Dato": s.date.strftime("%Y-%m-%d"),
                    "Type": s.signal_type,
                    "Styrke": f"{s.strength:.0%}",
                    "Note": s.note,
                }
                for s in v["signals"]
            ])
            st.dataframe(sig_df, hide_index=True, use_container_width=True,
                         key=f"vsa_sig_df_{region}_{ticker.symbol}")
        else:
            st.info("Ingen VSA-signaler funnet siste 60 dager.")

    # Wyckoff-detaljer
    with st.expander("Wyckoff-analyse-detaljer"):
        st.json({
            "phase": w.phase,
            "support": round(w.support, 2),
            "resistance": round(w.resistance, 2),
            "range_width_pct": round(w.range_pct, 2),
            "days_in_range": w.days_in_range,
            "in_range": w.in_range,
            "spring_detected": w.spring_detected,
            "markup_detected": w.markup_detected,
        })
