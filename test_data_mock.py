"""
test_data_mock.py
=================
Verifiserer data.py uten å trenge Yahoo Finance.

Bruker monkey-patching for å erstatte yf.download med syntetiske
responser. Dette tester all parsing-, cleaning- og cache-logikk
uten å belaste Yahoo (og uten å bli rate-limited).

Kjør fra prosjektrot:
    python test_data_mock.py
"""

import sys
import pandas as pd
import numpy as np
from unittest.mock import patch
from core import data as data_module


# ============================================================
# Hjelpere for å bygge fake yfinance-respons
# ============================================================

def make_single_response(symbol: str, n_days: int = 10, base_price: float = 100.0):
    """Lager flat DataFrame som yfinance returnerer for én aksje."""
    dates = pd.date_range("2026-04-01", periods=n_days, freq="B")
    prices = base_price + np.linspace(0, 5, n_days)
    return pd.DataFrame({
        "Open": prices - 0.5,
        "High": prices + 1.0,
        "Low": prices - 1.0,
        "Close": prices,
        "Adj Close": prices,
        "Volume": [1_000_000] * n_days,
    }, index=dates)


def make_multi_response(symbols: list[str], n_days: int = 10):
    """Lager MultiIndex-DataFrame som yfinance returnerer med group_by='ticker'."""
    dates = pd.date_range("2026-04-01", periods=n_days, freq="B")
    frames = {}
    for i, sym in enumerate(symbols):
        base = 100.0 + i * 50
        prices = base + np.linspace(0, 5, n_days)
        volume = [1_000_000] * n_days
        if sym == "BUG.OL":
            volume[3] = 0  # Test: volume=0 skal bli NaN
        if sym == "SHEL.L":
            prices = prices * 100  # GBX (pence) — skal divideres med 100
        frames[sym] = pd.DataFrame({
            "Open": prices - 0.5,
            "High": prices + 1.0,
            "Low": prices - 1.0,
            "Close": prices,
            "Adj Close": prices,
            "Volume": volume,
        }, index=dates)
    return pd.concat(frames, axis=1)


# ============================================================
# Tester
# ============================================================

def test_1_single_symbol():
    print("Test 1: Single symbol parsing")
    fake = make_single_response("EQNR.OL", n_days=20, base_price=300.0)
    with patch("core.data.yf.download", return_value=fake):
        data_module.clear_cache()
        df = data_module.fetch_one("EQNR.OL", period="1mo", use_cache=False)
    assert df is not None
    assert len(df) == 20
    assert df["Close"].iloc[-1] == 305.0
    print(f"  ✓ {len(df)} rader, siste close = {df['Close'].iloc[-1]}")


def test_2_multi_symbol():
    print("\nTest 2: Multi-symbol batch parsing")
    syms = ["EQNR.OL", "DNB.OL", "MOWI.OL"]
    fake = make_multi_response(syms, n_days=15)
    with patch("core.data.yf.download", return_value=fake):
        data_module.clear_cache()
        result = data_module.fetch_history(syms, period="1mo", use_cache=False)
    assert len(result) == 3
    for s in syms:
        assert s in result and len(result[s]) == 15
    print(f"  ✓ Alle 3 symboler parsed korrekt")


def test_3_gbx_conversion():
    print("\nTest 3: GBX→GBP konvertering for LSE")
    syms = ["SHEL.L", "EQNR.OL"]
    fake = make_multi_response(syms, n_days=10)
    raw_shel = fake[("SHEL.L", "Close")].iloc[-1]
    raw_eqnr = fake[("EQNR.OL", "Close")].iloc[-1]
    with patch("core.data.yf.download", return_value=fake):
        data_module.clear_cache()
        result = data_module.fetch_history(syms, period="1mo", use_cache=False)
    shel_close = result["SHEL.L"]["Close"].iloc[-1]
    eqnr_close = result["EQNR.OL"]["Close"].iloc[-1]
    assert abs(shel_close - raw_shel / 100) < 0.01
    assert abs(eqnr_close - raw_eqnr) < 0.01
    print(f"  ✓ SHEL.L: {raw_shel} GBX → {shel_close} GBP")
    print(f"  ✓ EQNR.OL: {raw_eqnr} → {eqnr_close} (uendret)")


def test_4_volume_zero_to_nan():
    print("\nTest 4: volume=0 → NaN")
    syms = ["BUG.OL", "MOWI.OL"]
    fake = make_multi_response(syms, n_days=10)
    with patch("core.data.yf.download", return_value=fake):
        data_module.clear_cache()
        result = data_module.fetch_history(syms, period="1mo", use_cache=False)
    nan_count = result["BUG.OL"]["Volume"].isna().sum()
    assert nan_count == 1
    assert result["MOWI.OL"]["Volume"].isna().sum() == 0
    print(f"  ✓ BUG.OL har {nan_count} NaN-volum")


def test_5_cache():
    print("\nTest 5: In-memory cache")
    fake = make_single_response("EQNR.OL", n_days=10)
    data_module.clear_cache()
    call_count = {"n": 0}

    def counting_download(*args, **kwargs):
        call_count["n"] += 1
        return fake

    with patch("core.data.yf.download", side_effect=counting_download):
        df1 = data_module.fetch_one("EQNR.OL", period="1mo", use_cache=True)
        df2 = data_module.fetch_one("EQNR.OL", period="1mo", use_cache=True)
        df3 = data_module.fetch_one("EQNR.OL", period="1mo", use_cache=False)

    assert call_count["n"] == 2
    print(f"  ✓ 3 fetch_one-kall → kun {call_count['n']} Yahoo-kall (1 cache-treff)")


def test_6_liquidity_filter():
    print("\nTest 6: Likviditetsfilter")
    dates = pd.date_range("2026-04-01", periods=30, freq="B")
    df = pd.DataFrame({
        "Open": [100.0] * 30,
        "High": [101.0] * 30,
        "Low": [99.0] * 30,
        "Close": [100.0] * 30,
        "Volume": [300_000] * 30,
    }, index=dates)

    avg = data_module.average_daily_turnover(df)
    assert abs(avg - 30_000_000) < 1
    assert data_module.passes_liquidity_filter(df, "EQNR.OL")
    print(f"  ✓ 30 MNOK passerer 20 MNOK-terskel")

    df_low = df.copy()
    df_low["Volume"] = 100_000
    assert not data_module.passes_liquidity_filter(df_low, "EQNR.OL")
    print(f"  ✓ 10 MNOK faller ut korrekt")


def test_7_failed_symbol_excluded():
    print("\nTest 7: Mislykkede symboler ekskluderes")
    fake = make_multi_response(["EQNR.OL", "MOWI.OL"], n_days=10)
    with patch("core.data.yf.download", return_value=fake):
        data_module.clear_cache()
        result = data_module.fetch_history(
            ["EQNR.OL", "MOWI.OL", "DELISTED.OL"],
            period="1mo",
            use_cache=False,
        )
    assert "EQNR.OL" in result
    assert "MOWI.OL" in result
    assert "DELISTED.OL" not in result
    print(f"  ✓ DELISTED.OL ekskludert korrekt")


# ============================================================
# Kjør alle tester
# ============================================================
if __name__ == "__main__":
    tests = [
        test_1_single_symbol,
        test_2_multi_symbol,
        test_3_gbx_conversion,
        test_4_volume_zero_to_nan,
        test_5_cache,
        test_6_liquidity_filter,
        test_7_failed_symbol_excluded,
    ]
    failures = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  ✗ FEILET: {e}")
            failures += 1
        except Exception as e:
            print(f"  ✗ EXCEPTION: {type(e).__name__}: {e}")
            failures += 1

    print()
    print("=" * 50)
    if failures == 0:
        print(f"🎉 Alle {len(tests)} tester bestått! data.py-logikken er verifisert.")
        print("(Live Yahoo-test kan ventes med til rate-limit slipper.)")
    else:
        print(f"⚠  {failures}/{len(tests)} tester feilet")
        sys.exit(1)
