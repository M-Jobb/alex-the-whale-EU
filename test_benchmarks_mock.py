"""
test_benchmarks_mock.py
=======================
Verifiserer benchmarks.py uten å trenge Yahoo Finance.

Tester:
- Likt-vekting av komponenter
- Rebasering til 100
- Omsetnings-beregning
- Fallback til EUROPE for tynne sektorer
- Cache-funksjonalitet

Kjør fra prosjektrot:
    python test_benchmarks_mock.py
"""

import sys
import pandas as pd
import numpy as np
from unittest.mock import patch
from core import benchmarks


def make_fake_data(symbols: list[str], n_days: int = 60):
    """
    Lag fake fetch_history-respons.

    Hver aksje får ulik startpris og ulik trend, slik at vi kan
    teste om rebaseringen fjerner pris-nivå-effekten.
    """
    dates = pd.date_range("2026-02-01", periods=n_days, freq="B")
    result = {}
    np.random.seed(42)

    for i, sym in enumerate(symbols):
        # Vidt forskjellige startpriser for å teste rebasering
        start_price = 50 + i * 100
        # Ulike trends per aksje (-5% til +15% over perioden)
        trend = -0.05 + i * 0.05
        prices = start_price * (1 + trend * np.linspace(0, 1, n_days))
        # Legg til litt støy
        prices = prices * (1 + np.random.normal(0, 0.005, n_days))

        result[sym] = pd.DataFrame({
            "Open": prices * 0.99,
            "High": prices * 1.01,
            "Low": prices * 0.98,
            "Close": prices,
            "Adj Close": prices,
            "Volume": [1_000_000 * (i + 1)] * n_days,  # ulike volum
        }, index=dates)
    return result


def test_1_basic_construction():
    """Sektor-kurv bygges korrekt med flere komponenter."""
    print("Test 1: Grunnleggende sektor-konstruksjon")
    fake = make_fake_data(["EQNR.OL", "AKRBP.OL", "VAR.OL", "DNO.OL", "BWE.OL"])

    benchmarks.clear_benchmark_cache()
    with patch("core.benchmarks.fetch_history", return_value=fake):
        bench = benchmarks.compute_sector_benchmark("OSLO", "Energy", period="3mo")

    assert bench is not None, "Kurv skulle ikke være None"
    assert not bench.empty, "Kurv skulle ikke være tom"
    assert "Close" in bench.columns and "Volume" in bench.columns

    # Første dag skal være ~100 (rebasert)
    first_close = bench["Close"].iloc[0]
    assert abs(first_close - 100.0) < 0.5, (
        f"Første rebaserte close skal være ~100, var {first_close:.2f}"
    )
    print(f"  ✓ Kurv bygd, første rebaserte close = {first_close:.2f}")
    print(f"  ✓ Antall komponenter: {bench.attrs.get('n_components')}")


def test_2_rebasing_eliminates_price_level():
    """
    Rebasering må fjerne effekten av nominalpris.
    Aksje A fra 50→55 (10%) og B fra 500→550 (10%) skal bidra likt.
    """
    print("\nTest 2: Rebasering fjerner nominalpris-effekt")

    # To aksjer med vidt forskjellige nominalpriser men IDENTISK avkastning
    dates = pd.date_range("2026-02-01", periods=30, freq="B")

    # A går fra 50 til 55 (10% opp)
    a_prices = np.linspace(50, 55, 30)
    # B går fra 500 til 550 (10% opp — identisk avkastning)
    b_prices = np.linspace(500, 550, 30)
    # C går fra 100 til 90 (10% ned)
    c_prices = np.linspace(100, 90, 30)

    fake = {
        "A.OL": pd.DataFrame({"Open": a_prices, "High": a_prices, "Low": a_prices,
                              "Close": a_prices, "Adj Close": a_prices,
                              "Volume": [1_000_000] * 30}, index=dates),
        "B.OL": pd.DataFrame({"Open": b_prices, "High": b_prices, "Low": b_prices,
                              "Close": b_prices, "Adj Close": b_prices,
                              "Volume": [1_000_000] * 30}, index=dates),
        "C.OL": pd.DataFrame({"Open": c_prices, "High": c_prices, "Low": c_prices,
                              "Close": c_prices, "Adj Close": c_prices,
                              "Volume": [1_000_000] * 30}, index=dates),
    }

    benchmarks.clear_benchmark_cache()
    with patch("core.benchmarks.fetch_history", return_value=fake):
        # Vi mock-er også universet til å returnere disse symbolene
        from core.universe import Ticker
        fake_tickers = [
            Ticker("A.OL", "Aksje A", "Energy", "NOK", "OSLO"),
            Ticker("B.OL", "Aksje B", "Energy", "NOK", "OSLO"),
            Ticker("C.OL", "Aksje C", "Energy", "NOK", "OSLO"),
        ]
        with patch("core.benchmarks.get_tickers_by_sector", return_value=fake_tickers):
            bench = benchmarks.compute_sector_benchmark("OSLO", "Energy", period="3mo")

    # A og B (begge +10%) og C (-10%) → snitt skal være ~+3.33%
    # ((110 + 110 + 90) / 3) = 103.33
    last_close = bench["Close"].iloc[-1]
    expected = (110 + 110 + 90) / 3
    assert abs(last_close - expected) < 1.0, (
        f"Forventet ~{expected:.2f}, fikk {last_close:.2f}"
    )
    print(f"  ✓ A(+10%), B(+10%), C(-10%) → kurv-snitt = {last_close:.2f} (forventet {expected:.2f})")
    print(f"  ✓ Bevis: nominalpris (50 vs 500) påvirket ikke vekting")


def test_3_fallback_to_europe():
    """Sektor med <3 lokale komponenter skal falle tilbake til EUROPE."""
    print("\nTest 3: Fallback til EUROPE for tynne sektorer")

    from core.universe import Ticker

    # Lokal region har 1 aksje, EUROPE har 5
    local_tickers = [
        Ticker("ENTRA.OL", "Entra", "Real Estate", "NOK", "OSLO"),
    ]
    europe_tickers = [
        Ticker("VNA.DE", "Vonovia", "Real Estate", "EUR", "EUROPE"),
        Ticker("URW.AS", "Unibail-Rodamco-Westfield", "Real Estate", "EUR", "EUROPE"),
        Ticker("CSGN.SW", "Credit Suisse RE", "Real Estate", "CHF", "EUROPE"),
        Ticker("LAND.L", "Land Securities", "Real Estate", "GBP", "EUROPE"),
        Ticker("BLND.L", "British Land", "Real Estate", "GBP", "EUROPE"),
    ]

    def fake_get_tickers(region, sector):
        if region == "OSLO":
            return local_tickers
        if region == "EUROPE":
            return europe_tickers
        return []

    europe_data = make_fake_data([t.symbol for t in europe_tickers])

    benchmarks.clear_benchmark_cache()
    with patch("core.benchmarks.get_tickers_by_sector", side_effect=fake_get_tickers):
        with patch("core.benchmarks.fetch_history", return_value=europe_data):
            bench = benchmarks.compute_sector_benchmark("OSLO", "Real Estate", period="3mo")

    assert bench is not None
    assert bench.attrs["used_region"] == "EUROPE", (
        f"Skulle bruke EUROPE-fallback, brukte {bench.attrs.get('used_region')}"
    )
    assert bench.attrs["n_components"] == 5
    print(f"  ✓ OSLO Real Estate (1 aksje) → fallback til EUROPE (5 aksjer)")
    print(f"  ✓ Brukt region: {bench.attrs['used_region']}")


def test_4_cache_works():
    """LRU-cache skal returnere uten ny henting."""
    print("\nTest 4: LRU-cache fungerer")

    fake = make_fake_data(["EQNR.OL", "AKRBP.OL", "VAR.OL"])
    call_count = {"n": 0}

    def counting_fetch(*args, **kwargs):
        call_count["n"] += 1
        return fake

    benchmarks.clear_benchmark_cache()
    with patch("core.benchmarks.fetch_history", side_effect=counting_fetch):
        # Første kall — henter
        b1 = benchmarks.compute_sector_benchmark("OSLO", "Energy", period="3mo")
        # Andre kall, samme args — cache-treff, ingen henting
        b2 = benchmarks.compute_sector_benchmark("OSLO", "Energy", period="3mo")
        # Tredje kall, annen periode — ny henting
        b3 = benchmarks.compute_sector_benchmark("OSLO", "Energy", period="6mo")

    assert call_count["n"] == 2, (
        f"Forventet 2 fetch-kall (1 cache-treff), fikk {call_count['n']}"
    )
    info = benchmarks.compute_sector_benchmark.cache_info()
    assert info.hits >= 1
    print(f"  ✓ 3 sektor-kall → kun {call_count['n']} fetch_history-kall")
    print(f"  ✓ Cache hits: {info.hits}, misses: {info.misses}")


def test_5_volume_rebased():
    """Volum/omsetning skal også rebaseres til 100."""
    print("\nTest 5: Omsetning rebaseres korrekt")

    fake = make_fake_data(["EQNR.OL", "AKRBP.OL", "VAR.OL"])

    benchmarks.clear_benchmark_cache()
    with patch("core.benchmarks.fetch_history", return_value=fake):
        bench = benchmarks.compute_sector_benchmark("OSLO", "Energy", period="3mo")

    first_volume = bench["Volume"].iloc[0]
    assert abs(first_volume - 100.0) < 5.0, (
        f"Første rebaserte volum skal være ~100, var {first_volume:.2f}"
    )
    print(f"  ✓ Første rebaserte omsetning: {first_volume:.2f} (≈ 100)")
    print(f"  ✓ Volum-serie har {bench['Volume'].notna().sum()} gyldige verdier")


def test_6_describe_coverage():
    """describe_sector_coverage gir riktig oversikt."""
    print("\nTest 6: Sektor-dekning oversikt")

    cov = benchmarks.describe_sector_coverage("OSLO")
    assert "Sektor" in cov.columns
    assert "Status" in cov.columns
    assert len(cov) > 0

    # Energy bør være "regional" for OSLO (har 8 aksjer)
    energy_row = cov[cov["Sektor"] == "Energy"].iloc[0]
    assert energy_row["Status"] == "regional", (
        f"OSLO Energy skal være regional, var: {energy_row['Status']}"
    )
    print(f"  ✓ OSLO Energy klassifisert som: {energy_row['Status']}")

    # Real Estate bør være "EUROPE-proxy" (har bare 1 aksje lokalt)
    re_row = cov[cov["Sektor"] == "Real Estate"].iloc[0]
    assert "EUROPE" in re_row["Status"]
    print(f"  ✓ OSLO Real Estate klassifisert som: {re_row['Status']}")


# ============================================================
# Kjør alle tester
# ============================================================
if __name__ == "__main__":
    tests = [
        test_1_basic_construction,
        test_2_rebasing_eliminates_price_level,
        test_3_fallback_to_europe,
        test_4_cache_works,
        test_5_volume_rebased,
        test_6_describe_coverage,
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
        print(f"⚠  {failures}/{len(tests)} tester feilet")
        sys.exit(1)
