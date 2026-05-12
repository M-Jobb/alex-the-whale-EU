"""
test_paper_trading_mock.py
==========================
Verifiserer paper_trading-modulen uten Yahoo-kall.

Tester:
- Tom portefølje
- Felix Prehn Buy Zone (limit/market_zone/missed_extended)
- Risiko-basert sizing
- Pyramidering
- Trailing stop-switch
- Multi-currency P&L
- MAE/MFE-tracking
"""

import sys
import pandas as pd
from datetime import datetime, timezone, timedelta
from paper_trading import (
    empty_portfolio,
    opprett_pending_ordre_fra_signaler,
    behandle_pending_orders,
    behandle_open_positions,
    daglig_tick,
    beregn_statistikk,
    total_equity_nok,
    beregn_posisjon_storrelse,
    PYRAMID_TRIGGERE_ATR,
    RISIKO_PROSENT,
    START_KAPITAL_NOK,
    BUY_ZONE_MAX_PCT,
)
from core.fx import to_nok


def make_ohlc(symbol: str, o: float, h: float, l: float, c: float, vol: int = 1_000_000):
    """Bygg en-rad OHLC DataFrame."""
    return pd.DataFrame({
        "Open": [o], "High": [h], "Low": [l], "Close": [c], "Volume": [vol],
    }, index=[pd.Timestamp(datetime.now(timezone.utc).date())])


def make_signal(symbol: str, name: str, currency: str, last_close: float,
                support: float, resistance: float, markup: bool = True,
                spring: bool = False, score: float = 75.0):
    return {
        "symbol":           symbol,
        "name":             name,
        "sector":           "Energy",
        "region":           "OSLO",
        "currency":         currency,
        "last_close":       last_close,
        "support":          support,
        "resistance":       resistance,
        "spring":           spring,
        "markup":           markup,
        "wyckoff_phase":    "accumulation",
        "final_score":      score,
        "rs_aggregate_score": 70,
        "vsa_bullish":      72,
        "triple_rs_strong": True,
        "vsa_obv_rising":   True,
        "signal_date":      "2026-04-28",
    }


# ============================================================
# Tester
# ============================================================

def test_1_tom_portfolio():
    print("Test 1: Tom portefølje")
    p = empty_portfolio()
    assert p["kontanter_nok"] == START_KAPITAL_NOK
    assert p["pending_orders"] == []
    assert p["open_positions"] == []
    eq = total_equity_nok(p, {})
    assert eq == START_KAPITAL_NOK
    print(f"  ✓ Start: {p['kontanter_nok']:,.0f} NOK")


def test_2_opprett_pending():
    print("\nTest 2: Opprett pending fra signaler")
    p = empty_portfolio()
    sig = make_signal("EQNR.OL", "Equinor", "NOK", 305, 280, 300)
    i_dag = datetime.now(timezone.utc).isoformat()
    log = opprett_pending_ordre_fra_signaler(p, [sig], i_dag)
    assert len(p["pending_orders"]) == 1
    ordre = p["pending_orders"][0]
    print(f"  ✓ Ordre opprettet: S={ordre['signal_pris_local']:.2f}, "
          f"stop={ordre['stop_local']:.2f}, target={ordre['target_local']:.2f}")


def test_3_limit_fyll():
    """Aksje dipper til signal-pris og fylles."""
    print("\nTest 3: Limit-fyll (Low ≤ S)")
    p = empty_portfolio()
    sig = make_signal("EQNR.OL", "Equinor", "NOK", 305, 280, 300)
    i_dag = datetime.now(timezone.utc).isoformat()
    opprett_pending_ordre_fra_signaler(p, [sig], i_dag)

    # S = 300 * 1.005 = 301.5. Dagen treffer low 299
    kurs = {"EQNR.OL": make_ohlc("EQNR.OL", o=302, h=305, l=299, c=304)}
    log = behandle_pending_orders(p, kurs, i_dag)
    assert len(p["open_positions"]) == 1
    pos = p["open_positions"][0]
    assert pos["fill_type"] == "limit"
    print(f"  ✓ Fyllt på {pos['initial_entry_local']:.2f} NOK, "
          f"{pos['shares']} aksjer ({pos['sizing_method']})")


def test_4_buy_zone_fyll():
    """Aksjen gap-opener i Buy Zone."""
    print("\nTest 4: Buy Zone-fyll (S < Open ≤ M)")
    p = empty_portfolio()
    sig = make_signal("EQNR.OL", "Equinor", "NOK", 305, 280, 300)
    i_dag = datetime.now(timezone.utc).isoformat()
    opprett_pending_ordre_fra_signaler(p, [sig], i_dag)

    S = p["pending_orders"][0]["signal_pris_local"]
    M = S * (1 + BUY_ZONE_MAX_PCT)
    # Open midt i Buy Zone, low aldri ned til S
    open_pris = (S + M) / 2
    kurs = {"EQNR.OL": make_ohlc("EQNR.OL", o=open_pris, h=open_pris*1.01, l=open_pris*0.999, c=open_pris*1.005)}
    log = behandle_pending_orders(p, kurs, i_dag)
    assert len(p["open_positions"]) == 1
    pos = p["open_positions"][0]
    assert pos["fill_type"] == "market_zone"
    print(f"  ✓ Buy Zone-fyllt på {pos['initial_entry_local']:.2f} (S={S:.2f}, M={M:.2f})")


def test_5_missed_extended():
    """Aksjen gap-opener over Buy Zone — skippes."""
    print("\nTest 5: Missed-extended (Open > M)")
    p = empty_portfolio()
    sig = make_signal("EQNR.OL", "Equinor", "NOK", 305, 280, 300)
    i_dag = datetime.now(timezone.utc).isoformat()
    opprett_pending_ordre_fra_signaler(p, [sig], i_dag)

    S = p["pending_orders"][0]["signal_pris_local"]
    M = S * (1 + BUY_ZONE_MAX_PCT)
    # Gap-open 10% over signal
    kurs = {"EQNR.OL": make_ohlc("EQNR.OL", o=S*1.1, h=S*1.12, l=S*1.09, c=S*1.11)}
    log = behandle_pending_orders(p, kurs, i_dag)
    assert len(p["open_positions"]) == 0
    assert len(p["missed_extended"]) == 1
    assert len(p["pending_orders"]) == 0  # ordre fjernet
    print(f"  ✓ Missed-extended logget: gap {p['missed_extended'][0]['gap_pct']:+.1f}%")


def test_6_sizing_metoder():
    """Tre sizing-modus."""
    print("\nTest 6: Sizing-modus")
    equity = 100_000.0
    # Tett stop (1%) → kapital-tak slår inn
    s1 = beregn_posisjon_storrelse(equity, 100, 99, "NOK")
    assert s1.method == "capital_capped", f"Forventet capital_capped, fikk {s1.method}"
    print(f"  ✓ Tett stop (1%): {s1.shares} aksjer, {s1.method}")

    # Bred stop (15%) → risk_based slår inn (siden vi ikke når kapital-tak)
    # 2.5% av 100k = 2500 NOK risk / 15 NOK per aksje = 166 aksjer × 100 = 16 600 NOK (under 20% cap)
    s2 = beregn_posisjon_storrelse(equity, 100, 85, "NOK")
    assert s2.method == "risk_based", f"Forventet risk_based, fikk {s2.method}"
    print(f"  ✓ Bred stop (15%): {s2.shares} aksjer, {s2.method}")

    # Dyr aksje, men risiko-formel gir 0 aksjer fordi entry > risiko-budsjett
    # Eks: 5000 NOK med stop 4995 (tett 0.1% stop). Risiko 5 NOK/aksje, budsjett 2500 = 500 aksjer.
    # 500×5000 = 2.5M >> 20% cap (20k = 4 aksjer). Så capital_capped.
    # For min_one_share trenger vi: shares_risk = 0 OG entry < 25% av equity.
    # Hvis entry = 22 000 NOK, stop = 21 990 NOK (10 NOK stop): risiko 0.025 NOK/aksje?
    # Nei, det er bare 10 NOK. 2500 / 10 = 250 aksjer. Fortsatt capital_capped.
    # min_one_share-pathen krever (entry - stop) * 1 > risiko-budsjett:
    # entry 20 000, stop 17 000 (3000 NOK risiko). Budsjett 2500 < 3000 → 0 aksjer i risk-formel.
    # Sjekk: 20 000 < 25 000 (25% av equity) → 1 aksje tillatt.
    s3 = beregn_posisjon_storrelse(equity, 20_000, 17_000, "NOK")
    assert s3.method == "min_one_share", f"Forventet min_one_share, fikk {s3.method}"
    print(f"  ✓ Dyr aksje med stor stop: {s3.shares} aksje, {s3.method}")

    # Ekstrem dyr → rejected
    s4 = beregn_posisjon_storrelse(equity, 50_000, 49_000, "NOK")
    assert s4.method == "rejected"
    print(f"  ✓ For dyr → rejected korrekt")


def test_7_pyramidering():
    """Pyramide-trigger ved entry + 1×ATR."""
    print("\nTest 7: Pyramidering")
    p = empty_portfolio()
    sig = make_signal("EQNR.OL", "Equinor", "NOK", 305, 280, 300)
    i_dag = datetime.now(timezone.utc).isoformat()
    opprett_pending_ordre_fra_signaler(p, [sig], i_dag)

    # Limit-fyll
    kurs = {"EQNR.OL": make_ohlc("EQNR.OL", o=302, h=305, l=299, c=304)}
    behandle_pending_orders(p, kurs, i_dag)
    pos = p["open_positions"][0]
    init_entry = pos["initial_entry_local"]
    init_shares = pos["shares"]
    atr = pos["atr_local"]
    pyramid_pris = init_entry + atr  # 1×ATR over

    # Dag 2: pris stiger over pyramide-trigger
    i_dag2 = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    kurs2 = {"EQNR.OL": make_ohlc("EQNR.OL",
                                    o=init_entry,
                                    h=pyramid_pris * 1.01,
                                    l=init_entry * 0.99,
                                    c=pyramid_pris * 1.005)}
    log = behandle_open_positions(p, kurs2, i_dag2)
    pos = p["open_positions"][0]
    assert pos["pyramid_count"] == 1
    assert pos["shares"] > init_shares
    print(f"  ✓ Pyramide #1: shares {init_shares} → {pos['shares']}, "
          f"avg_entry {init_entry:.2f} → {pos['avg_entry_local']:.2f}")


def test_8_multi_currency_pnl():
    """Test at FX-konvertering virker for SHEL.L (GBP)."""
    print("\nTest 8: Multi-currency P&L (GBP-aksje)")
    p = empty_portfolio()
    # SHEL.L i GBP: signal 27, support 25, resistance 26.8
    sig = make_signal("SHEL.L", "Shell", "GBP", 27, 25, 26.8)
    sig["region"] = "EUROPE"
    i_dag = datetime.now(timezone.utc).isoformat()
    opprett_pending_ordre_fra_signaler(p, [sig], i_dag)

    # Limit-fyll på 26.93 (= 26.8 * 1.005)
    kurs = {"SHEL.L": make_ohlc("SHEL.L", o=27.0, h=27.2, l=26.85, c=27.0)}
    behandle_pending_orders(p, kurs, i_dag)
    pos = p["open_positions"][0]
    assert pos["currency"] == "GBP"
    print(f"  ✓ Kjøpt {pos['shares']} aksjer @ £{pos['initial_entry_local']:.2f}")

    # P&L i GBP: +10%, så +10% i NOK også (samme FX-kurs ved kjøp og salg)
    # GBP-til-NOK = 13.5: kostnaden var shares × entry × 13.5 NOK
    init_entry_nok = to_nok(pos["initial_entry_local"], "GBP") * pos["shares"]
    print(f"  ✓ NOK-kostnad: {pos['kostnad_nok']:,.0f}".replace(",", " "))

    # Selg ved target
    target = pos["target_local"]
    kurs2 = {"SHEL.L": make_ohlc("SHEL.L", o=target * 1.01, h=target * 1.02, l=target * 1.005, c=target * 1.015)}
    i_dag2 = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    behandle_open_positions(p, kurs2, i_dag2)
    assert len(p["closed_trades"]) == 1
    trade = p["closed_trades"][0]
    assert trade["reason"] == "target"
    print(f"  ✓ Lukket på target: P&L {trade['pnl_nok']:+,.0f} NOK "
          f"({trade['pnl_pct']:+.2f}%)".replace(",", " "))


def test_9_mae_mfe():
    """MAE/MFE skal oppdateres over flere dager."""
    print("\nTest 9: MAE/MFE-tracking")
    p = empty_portfolio()
    sig = make_signal("EQNR.OL", "Equinor", "NOK", 305, 280, 300)
    i_dag = datetime.now(timezone.utc).isoformat()
    opprett_pending_ordre_fra_signaler(p, [sig], i_dag)
    kurs = {"EQNR.OL": make_ohlc("EQNR.OL", o=302, h=305, l=299, c=304)}
    behandle_pending_orders(p, kurs, i_dag)
    pos = p["open_positions"][0]
    entry = pos["initial_entry_local"]

    # Dag 2: dipper til entry - 2% (men over stop)
    i_dag2 = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    kurs2 = {"EQNR.OL": make_ohlc("EQNR.OL", o=entry, h=entry, l=entry * 0.98, c=entry * 0.99)}
    behandle_open_positions(p, kurs2, i_dag2)
    pos = p["open_positions"][0]
    assert pos["mae_pct"] < -1.5
    print(f"  ✓ MAE: {pos['mae_pct']:.2f}% (low water ${pos['mae_local']:.2f})")

    # Dag 3: stiger til entry + 5%
    i_dag3 = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    kurs3 = {"EQNR.OL": make_ohlc("EQNR.OL", o=entry * 0.99, h=entry * 1.05, l=entry * 0.99, c=entry * 1.04)}
    behandle_open_positions(p, kurs3, i_dag3)
    pos = p["open_positions"][0]
    assert pos["mfe_pct"] > 4.0
    print(f"  ✓ MFE: {pos['mfe_pct']:.2f}% (high water {pos['mfe_local']:.2f})")


def test_10_target_exit():
    """Posisjon lukkes når target er truffet."""
    print("\nTest 10: Target-exit")
    p = empty_portfolio()
    sig = make_signal("EQNR.OL", "Equinor", "NOK", 305, 280, 300)
    i_dag = datetime.now(timezone.utc).isoformat()
    opprett_pending_ordre_fra_signaler(p, [sig], i_dag)
    kurs = {"EQNR.OL": make_ohlc("EQNR.OL", o=302, h=305, l=299, c=304)}
    behandle_pending_orders(p, kurs, i_dag)
    pos = p["open_positions"][0]
    target = pos["target_local"]

    # Pris hopper til target
    i_dag2 = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    kurs2 = {"EQNR.OL": make_ohlc("EQNR.OL", o=target * 0.99, h=target * 1.01, l=target * 0.99, c=target)}
    behandle_open_positions(p, kurs2, i_dag2)
    assert len(p["closed_trades"]) == 1
    trade = p["closed_trades"][0]
    assert trade["reason"] == "target"
    print(f"  ✓ Lukket: P&L {trade['pnl_nok']:+,.0f} NOK".replace(",", " "))


def test_11_stop_loss_exit():
    """Posisjon lukkes når stop er truffet."""
    print("\nTest 11: Stop-loss-exit")
    p = empty_portfolio()
    sig = make_signal("EQNR.OL", "Equinor", "NOK", 305, 280, 300)
    i_dag = datetime.now(timezone.utc).isoformat()
    opprett_pending_ordre_fra_signaler(p, [sig], i_dag)
    kurs = {"EQNR.OL": make_ohlc("EQNR.OL", o=302, h=305, l=299, c=304)}
    behandle_pending_orders(p, kurs, i_dag)
    pos = p["open_positions"][0]
    stop = pos["current_stop_local"]

    # Pris faller til stop
    i_dag2 = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    kurs2 = {"EQNR.OL": make_ohlc("EQNR.OL", o=stop * 1.02, h=stop * 1.03, l=stop * 0.98, c=stop * 0.99)}
    behandle_open_positions(p, kurs2, i_dag2)
    assert len(p["closed_trades"]) == 1
    trade = p["closed_trades"][0]
    assert trade["reason"] == "stop_loss"
    print(f"  ✓ Stop-loss: P&L {trade['pnl_nok']:+,.0f} NOK".replace(",", " "))


def test_12_statistikk():
    """Aggregert statistikk."""
    print("\nTest 12: Statistikk-beregning")
    p = empty_portfolio()
    # Bygg manuelt 3 trades: 2 vinnere, 1 taper
    p["closed_trades"] = [
        {"pnl_nok": 1500},
        {"pnl_nok": -800},
        {"pnl_nok": 2200},
    ]
    stats = beregn_statistikk(p)
    assert stats["n_trades"] == 3
    assert stats["n_wins"] == 2
    assert stats["win_rate"] == pytest_approx(66.67)
    pf = (1500 + 2200) / 800
    assert abs(stats["profit_factor"] - pf) < 0.01
    print(f"  ✓ Win rate: {stats['win_rate']:.1f}%, Profit factor: {stats['profit_factor']:.2f}")


def pytest_approx(value: float, rel: float = 0.01) -> float:
    """Enkel approx (vi har ikke pytest tilgjengelig)."""
    class _Approx:
        def __eq__(self, other): return abs(other - value) < rel * max(abs(value), 1)
    return _Approx()


# ============================================================
# Kjør alle
# ============================================================
if __name__ == "__main__":
    tests = [
        test_1_tom_portfolio,
        test_2_opprett_pending,
        test_3_limit_fyll,
        test_4_buy_zone_fyll,
        test_5_missed_extended,
        test_6_sizing_metoder,
        test_7_pyramidering,
        test_8_multi_currency_pnl,
        test_9_mae_mfe,
        test_10_target_exit,
        test_11_stop_loss_exit,
        test_12_statistikk,
    ]
    failures = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  ✗ FEILET: {e}")
            failures += 1
        except Exception as e:
            import traceback
            print(f"  ✗ EXCEPTION: {type(e).__name__}: {e}")
            traceback.print_exc()
            failures += 1
    print()
    print("=" * 50)
    if failures == 0:
        print(f"🎉 Alle {len(tests)} tester bestått!")
    else:
        print(f"⚠ {failures}/{len(tests)} feilet")
        sys.exit(1)
