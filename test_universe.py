"""
test_universe.py
================
Sjekker at alle tickere returnerer data fra Yahoo Finance.

Bruker BATCH-download (én forespørsel for alle symboler) for å unngå
Yahoo sitt rate-limit som slår inn ved mange enkeltforespørsler.

Yahoo's "possibly delisted; no price data found"-feil er villedende
— den utløses ofte av rate-limiting, ikke faktisk delisting.

Kjør fra prosjektrot:
    python test_universe.py

Lagre output til fil:
    python test_universe.py > test_resultat.txt
"""

import sys
import time
import yfinance as yf
import pandas as pd
from core.universe import (
    OSLO_TICKERS,
    NORDIC_TICKERS,
    EUROPE_TICKERS,
    REGION_BENCHMARKS,
)


def batch_test(symbols: list[str], label: str) -> dict[str, bool]:
    """
    Test alle symboler i én batch-forespørsel.
    Returnerer dict {symbol: ok}.

    yf.download med liste av symboler returnerer en MultiIndex-DataFrame
    der kolonner er (felt, symbol). Vi sjekker hvilke symboler faktisk
    har data ved å se på Close-kolonnen per symbol.
    """
    print(f"\nHenter batch ({len(symbols)} symboler) for {label}...", flush=True)

    # Forsøk opptil 3 ganger med pause hvis rate-limit slår til
    for attempt in range(1, 4):
        try:
            data = yf.download(
                symbols,
                period="10d",         # 10 dager gir buffer hvis enkeltdager mangler
                progress=False,
                auto_adjust=False,
                threads=True,         # parallelle forespørsler innen batchen
                group_by="ticker",    # gjør parsing enklere
            )
            break
        except Exception as e:
            print(f"  Forsøk {attempt} feilet: {e}", flush=True)
            if attempt < 3:
                time.sleep(5 * attempt)
            else:
                # Returner alle som feilet
                return {s: False for s in symbols}

    # Parse resultatet
    results: dict[str, bool] = {}

    if data is None or len(data) == 0:
        print(f"  ✗ Hele batchen returnerte tom data — Yahoo blokkerer", flush=True)
        return {s: False for s in symbols}

    if len(symbols) == 1:
        # Single-symbol returnerer flat DataFrame, ikke MultiIndex
        sym = symbols[0]
        ok = "Close" in data.columns and data["Close"].notna().any()
        results[sym] = bool(ok)
    else:
        # Multi-symbol returnerer MultiIndex med (symbol, felt)
        for sym in symbols:
            try:
                if sym in data.columns.get_level_values(0):
                    sym_data = data[sym]
                    close_col = sym_data["Close"] if "Close" in sym_data.columns else None
                    ok = close_col is not None and close_col.notna().any()
                    results[sym] = bool(ok)
                else:
                    results[sym] = False
            except (KeyError, AttributeError):
                results[sym] = False

    return results


def main() -> None:
    # ============================================================
    # 1. Region-benchmarks (test individuelt — kun 3 stk)
    # ============================================================
    print("=" * 60)
    print("REGION-BENCHMARKS")
    print("=" * 60)

    bench_symbols = list(REGION_BENCHMARKS.values())
    bench_results = batch_test(bench_symbols, "benchmarks")

    bench_failed: list[str] = []
    for region, idx in REGION_BENCHMARKS.items():
        ok = bench_results.get(idx, False)
        status = "✓" if ok else "✗"
        print(f"  {status} {idx:10s} {region}")
        if not ok:
            bench_failed.append(f"{idx} ({region})")

    # Pause mellom batches for å være snill mot Yahoo
    time.sleep(3)

    # ============================================================
    # 2. Aksjer per region (én batch per region)
    # ============================================================
    aksje_failed: dict[str, list[str]] = {"OSLO": [], "NORDIC": [], "EUROPE": []}

    for region_name, tickers in [
        ("OSLO", OSLO_TICKERS),
        ("NORDIC", NORDIC_TICKERS),
        ("EUROPE", EUROPE_TICKERS),
    ]:
        symbols = [t.symbol for t in tickers]
        results = batch_test(symbols, region_name)

        print()
        print("=" * 60)
        print(f"{region_name} — {len(tickers)} tickere")
        print("=" * 60)

        for t in tickers:
            ok = results.get(t.symbol, False)
            if ok:
                print(f"  ✓ {t.symbol:12s} {t.name}")
            else:
                print(f"  ✗ {t.symbol:12s} {t.name}")
                aksje_failed[region_name].append(f"{t.symbol} ({t.name})")

        # Pause mellom regioner
        time.sleep(3)

    # ============================================================
    # Oppsummering
    # ============================================================
    print()
    print("=" * 60)
    print("OPPSUMMERING")
    print("=" * 60)

    if bench_failed:
        print(f"\n✗ Region-benchmarks feilet ({len(bench_failed)}):")
        for b in bench_failed:
            print(f"    {b}")
    else:
        print("✓ Alle region-benchmarks OK")

    for region, failed in aksje_failed.items():
        if failed:
            print(f"\n✗ {region}: {len(failed)} aksjer feilet:")
            for a in failed:
                print(f"    {a}")
        else:
            print(f"✓ {region}: alle aksjer OK")

    total_failed = len(bench_failed) + sum(len(v) for v in aksje_failed.values())
    print()
    if total_failed == 0:
        print("🎉 Alle tickere fungerer! Klar for neste modul.")
    else:
        print(f"⚠  Totalt {total_failed} tickere feilet.")
        print()
        print("MERK: Hvis mange tickere feiler, er det nesten alltid")
        print("Yahoo Finance som rate-limiter. Vent 10-15 minutter og")
        print("prøv igjen. Ekte delistinger viser seg ved at SAMME")
        print("ticker feiler på flere uavhengige forsøk.")
        sys.exit(1)


if __name__ == "__main__":
    main()
