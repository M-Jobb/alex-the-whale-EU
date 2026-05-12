"""
tabs/tab_paper_trading.py
=========================
Paper trading-fane for Streamlit-appen.

Viser:
- Topp-stats (equity, win rate, profit factor)
- Equity-kurve
- Åpne posisjoner med MAE/MFE
- Pending ordre
- Lukkede trades-historikk
- Missed-extended-logg
- Detalj-utforsker per posisjon
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime

from paper_trading import (
    load_portfolio,
    beregn_statistikk,
    total_equity_nok,
    START_KAPITAL_NOK,
)
from core.data import fetch_one
from core.fx import to_nok


def render_paper_trading_tab() -> None:
    """Render hele paper trading-fanen."""
    portfolio = load_portfolio()
    stats = beregn_statistikk(portfolio)

    # Hent live-kurser for åpne posisjoner
    live_kurser = {}
    for pos in portfolio["open_positions"]:
        try:
            df = fetch_one(pos["symbol"], period="5d")
            if df is not None and not df.empty:
                live_kurser[pos["symbol"]] = float(df["Close"].iloc[-1])
        except Exception:
            live_kurser[pos["symbol"]] = pos["avg_entry_local"]

    equity = total_equity_nok(portfolio, live_kurser)
    pnl_total = equity - START_KAPITAL_NOK
    pnl_pct = pnl_total / START_KAPITAL_NOK * 100

    # === Toppstats ===
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric(
            "Total equity",
            f"{equity:,.0f} NOK".replace(",", " "),
            f"{pnl_pct:+.2f}% siden start",
        )
    with c2:
        if stats["n_trades"] > 0:
            st.metric("Win rate", f"{stats['win_rate']:.1f}%",
                      f"{stats['n_wins']}V / {stats['n_losses']}T")
        else:
            st.metric("Win rate", "—", "Ingen lukkede ennå")
    with c3:
        pf = stats["profit_factor"]
        if pf > 0:
            st.metric("Profit factor", f"{pf:.2f}",
                      "Bra" if pf > 1.5 else "OK" if pf > 1.0 else "Tapende")
        else:
            st.metric("Profit factor", "—")
    with c4:
        st.metric("Åpne posisjoner", len(portfolio["open_positions"]),
                  f"{len(portfolio['pending_orders'])} pending")

    st.markdown("---")

    # === Equity-kurve ===
    if portfolio["equity_curve"]:
        st.subheader("📈 Equity-kurve")
        ec_df = pd.DataFrame(portfolio["equity_curve"])
        ec_df["date"] = pd.to_datetime(ec_df["date"])

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=ec_df["date"], y=ec_df["equity_nok"],
            mode="lines+markers", name="Equity (NOK)",
            line=dict(color="#4CAF50", width=2),
        ))
        fig.add_hline(y=START_KAPITAL_NOK, line_dash="dash", line_color="gray",
                      annotation_text=f"Start: {START_KAPITAL_NOK:,.0f}".replace(",", " "))
        fig.update_layout(
            height=350, margin=dict(t=20, l=10, r=10, b=10),
            yaxis_title="NOK", xaxis_title="",
        )
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    # === Åpne posisjoner ===
    st.subheader("📂 Åpne posisjoner")
    if not portfolio["open_positions"]:
        st.info("Ingen åpne posisjoner.")
    else:
        rows = []
        for pos in portfolio["open_positions"]:
            sym = pos["symbol"]
            ccy = pos["currency"]
            cur_kurs = live_kurser.get(sym, pos["avg_entry_local"])
            verdi_local = cur_kurs * pos["shares"]
            verdi_nok = to_nok(verdi_local, ccy)
            pnl_nok = verdi_nok - pos["kostnad_nok"]
            pnl_pct = pnl_nok / pos["kostnad_nok"] * 100 if pos["kostnad_nok"] else 0
            fyll_emoji = "🎯" if pos["fill_type"] == "limit" else "⚡"
            rows.append({
                "Ticker": sym,
                "Navn": pos.get("name", "")[:20],
                "Region": pos["region"],
                "Fyll": fyll_emoji,
                "Type": pos["signal_type"],
                "Aksjer": pos["shares"],
                "Pyramid": pos["pyramid_count"],
                "Snitt entry": f"{pos['avg_entry_local']:.2f} {ccy}",
                "Nå": f"{cur_kurs:.2f} {ccy}",
                "Stop": f"{pos['current_stop_local']:.2f} ({pos['stop_type']})",
                "Target": f"{pos['target_local']:.2f}",
                "MAE": f"{pos.get('mae_pct', 0):+.2f}%",
                "MFE": f"{pos.get('mfe_pct', 0):+.2f}%",
                "P&L NOK": f"{pnl_nok:+,.0f}".replace(",", " "),
                "P&L %": f"{pnl_pct:+.2f}%",
                "Åpnet": pos["opened_at"][:10],
            })
        df_open = pd.DataFrame(rows)
        st.dataframe(df_open, use_container_width=True, hide_index=True)

        # Detalj-utforsker
        st.markdown("##### 🔍 Detaljvisning")
        tickere = [p["symbol"] for p in portfolio["open_positions"]]
        valgt = st.selectbox("Velg posisjon", tickere, key="pt_detail")
        valgt_pos = next((p for p in portfolio["open_positions"] if p["symbol"] == valgt), None)
        if valgt_pos:
            _render_position_detail(valgt_pos, live_kurser.get(valgt, 0))

    st.markdown("---")

    # === Pending ordre ===
    st.subheader("⏳ Pending limit-ordre")
    if not portfolio["pending_orders"]:
        st.info("Ingen pending ordre.")
    else:
        rows = []
        for o in portfolio["pending_orders"]:
            S = o["signal_pris_local"]
            M = S * 1.05
            rows.append({
                "Ticker": o["symbol"],
                "Navn": o.get("name", "")[:20],
                "Region": o.get("region", ""),
                "Type": o["signal_type"],
                "Signal-pris (S)": f"{S:.2f} {o['currency']}",
                "Maks (M=S×1.05)": f"{M:.2f}",
                "Stop": f"{o['stop_local']:.2f}",
                "Target": f"{o['target_local']:.2f}",
                "Opprettet": o["opprettet_dato"][:10],
                "Utløper": o["utloper_dato"][:10],
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.markdown("---")

    # === Lukkede trades ===
    st.subheader("📊 Lukkede trades")
    if not portfolio["closed_trades"]:
        st.info("Ingen lukkede trades ennå.")
    else:
        rows = []
        for t in reversed(portfolio["closed_trades"]):
            grunn = {"stop_loss": "⛔ Stop", "target": "🎯 Target"}.get(t["reason"], t["reason"])
            rows.append({
                "Ticker": t["symbol"],
                "Navn": t.get("name", "")[:20],
                "Type": t["signal_type"],
                "Pyramid": t.get("pyramid_count", 0),
                "Snitt entry": f"{t['avg_entry_local']:.2f} {t['currency']}",
                "Exit": f"{t['exit_pris_local']:.2f}",
                "Grunn": grunn,
                "P&L NOK": f"{t['pnl_nok']:+,.0f}".replace(",", " "),
                "P&L %": f"{t['pnl_pct']:+.2f}%",
                "Åpnet": t["opened_at"][:10],
                "Lukket": t["closed_at"][:10],
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # === Missed-extended ===
    if portfolio.get("missed_extended"):
        with st.expander(f"🚫 Missed-Extended ({len(portfolio['missed_extended'])} signaler)"):
            rows = []
            for m in reversed(portfolio["missed_extended"]):
                rows.append({
                    "Ticker": m["symbol"],
                    "Signal-pris": f"{m['signal_pris']:.2f}",
                    "Åpning": f"{m['open_pris']:.2f}",
                    "Gap": f"{m['gap_pct']:+.2f}%",
                    "Dato": m["dato"][:10],
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(
                "Signaler hvor aksjen åpnet over 5% gap fra signal-pris. "
                "Botten skipper disse fordi R:R blir for skjev."
            )

    st.markdown("---")

    # === Forklaring ===
    with st.expander("📖 Slik fungerer paper trading-roboten"):
        st.markdown("""
**Strategi:** Felix Prehn Buy Zone + Wyckoff/VSA-signaler + ATR trailing.

**Buy Zone-konseptet:**
- **Signal-pris (S)** = Wyckoff-entry over resistance / spring-bunn
- **Maks-pris (M)** = S × 1.05 (5% over signal)
- **Buy Zone** = (S, M]

**Trade-flyt:**
1. **Signal oppdages** av daglig scan → bot oppretter limit-ordre på S
2. **Daglig sjekk mot tre tilstander:**
   - 🎯 **Limit-fyll** (Low ≤ S): kjøp på signal-pris
   - ⚡ **Buy Zone-fyll** (S < Open ≤ M): kjøp på dagens åpning
   - 🚫 **Missed-Extended** (Open > M): skip og logg
3. **Risiko-basert sizing**: 2.5% av equity risiko per initial trade.
   Kapital-tak: maks 20% per posisjon. Min 1 aksje hvis innenfor 25%.
4. **Pyramidering**:
   - Add-on 1 ved entry + 1×ATR (1.25% risiko)
   - Add-on 2 ved entry + 2×ATR (0.625% risiko)
   - Felles vektet snitt-entry og stop for hele posisjonen
5. **Stop-strategi (hybrid)**:
   - Start: Wyckoff-basert (under support × 0.98)
   - Switch til trailing ATR når pris > avg_entry + 1×ATR
   - Trailing: high_water − 2×ATR (kun oppover)
6. **Exit**: stop-loss eller Target 1 (2:1 R:R fra signal)

**Multi-currency:**
Hver posisjon spores i lokal valuta. P&L rapporteres i NOK via
FX-konvertering (default-kurser i `core/fx.py`).

**Kostnader:**
- 0.1% slippage på alle fills
- 99 kr kurtasje per trade (Nordnet-typisk)

**Hva er en god strategi?**
- Win rate 40-55% er normalt for breakout-strategier
- Profit factor > 1.5 = lønnsom, > 2.0 = utmerket
- Drawdowns < 15% = god risikohåndtering

**⚠ Disclaimer**
Dette er en simulering for forskning og opplæring. Resultatene
garanterer ikke fremtidig avkastning. Reell trading med ekte kapital
er mye vanskeligere — start ALLTID med paper trading.
        """)


def _render_position_detail(pos: dict, cur_kurs: float) -> None:
    """Detalj-popup for én åpen posisjon."""
    snap = pos.get("signal_snapshot", {})
    ccy = pos["currency"]
    init_entry = pos["initial_entry_local"]

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**📡 Signal-snapshot**")
        if snap:
            st.markdown(f"""
- Dato: `{snap.get('dato', '—')}`
- Score: **{snap.get('score', 0):.0f}/100**
- RS-score: {snap.get('rs_score', 0):.0f}
- VSA-score: {snap.get('vsa_score', 0):.0f}
- Wyckoff: {snap.get('wyckoff_phase', '—')}
- Trippel-RS sterk: {'✓' if snap.get('triple_rs') else '✗'}
- OBV stigende: {'✓' if snap.get('obv_rising') else '✗'}
""")
        else:
            st.info("Ingen snapshot (gammel posisjon).")

    with col_b:
        st.markdown("**📈 Excursion-tracking**")
        st.markdown(f"""
- MAE: **{pos.get('mae_pct', 0):+.2f}%** ({pos.get('mae_local', 0):.2f} {ccy})
- MFE: **{pos.get('mfe_pct', 0):+.2f}%** ({pos.get('mfe_local', 0):.2f} {ccy})
- High water: {pos.get('high_water_local', 0):.2f} {ccy}
- Stop-type nå: `{pos.get('stop_type', '—')}`
""")

    # What-if mot original stop/target
    if pos.get("orig_stop_truffet") or pos.get("orig_target_truffet"):
        st.markdown("**🔮 What-if mot original-signal**")
        msg = []
        if pos.get("orig_stop_truffet"):
            msg.append(
                f"⛔ Original-stop hadde blitt truffet "
                f"{pos['orig_stop_dato'][:10]} → P&L: {pos['orig_stop_pnl_pct']:+.2f}%"
            )
        if pos.get("orig_target_truffet"):
            msg.append(
                f"🎯 Original-target hadde blitt truffet "
                f"{pos['orig_target_dato'][:10]} → P&L: {pos['orig_target_pnl_pct']:+.2f}%"
            )
        for m in msg:
            st.write(m)

    # Fill-historikk
    if pos.get("fills"):
        st.markdown("**💰 Fill-historikk**")
        fill_rows = []
        for f in pos["fills"]:
            fill_rows.append({
                "Dato": f["date"][:10],
                "Type": f["type"],
                "Pris": f"{f['price_local']:.2f} {ccy}",
                "Aksjer": f["shares"],
                "Kostnad NOK": f"{f['cost_nok']:,.0f}".replace(",", " "),
            })
        st.dataframe(pd.DataFrame(fill_rows), use_container_width=True, hide_index=True)

    # Stop-historikk
    if pos.get("stop_historie") and len(pos["stop_historie"]) > 1:
        with st.expander("📐 Stop-historie"):
            stop_rows = []
            for s in pos["stop_historie"]:
                stop_rows.append({
                    "Dato": s["date"][:10],
                    "Stop": f"{s['stop_local']:.2f}",
                    "Type": s["type"],
                })
            st.dataframe(pd.DataFrame(stop_rows), use_container_width=True, hide_index=True)
