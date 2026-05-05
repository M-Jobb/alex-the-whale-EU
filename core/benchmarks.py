"""
core/benchmarks.py
==================
Selvkonstruerte sektor-benchmarks for europeisk Smart Money Dashboard.

Yahoo Finance fjernet STOXX-sektorindeksene i 2024-2025. Vi bygger
derfor våre egne, og det er faktisk finansielt overlegent fordi:

1. LIKT-VEKTET (ikke free-float). En sektor-rotasjon FRA megacap TIL
   midcap er smart money-signal vi vil fange. Free-float-indekser
   skjuler dette fordi Shell/Novartis/Total dominerer kurven.

2. REBASERT TIL 100. Vi normaliserer hver aksje til 100 ved start
   av lookback-perioden, så snitt-vekter likt. Aksjer med ulike
   nominalkurser (NOK 280, GBP 27, CHF 250) får da lik innflytelse.

3. OMSETNING I STEDET FOR VOLUM. Volum (antall aksjer) er ikke
   sammenlignbart mellom selskaper. Vi konverterer til Close×Volume
   i lokal valuta og rebaser også det til 100.

4. FALLBACK TIL EUROPE. Hvis en sektor i en region har <3 aksjer,
   blir kurven praktisk talt aksjen selv. Vi henter da automatisk
   EUROPE-sektoren som proxy.

5. HÅNDTERER FX IKKE. Aksjer i ulike valutaer rebaseres til 100
   uavhengig — vi sammenligner prosentbevegelser, ikke nominalverdier.
   Dette er korrekt fordi Wyckoff/VSA-analyse jobber med relativ
   bevegelse, ikke valutapurchasing power.

Bruk:
    from core.benchmarks import compute_sector_benchmark, get_region_index_data

    # Sektor-kurv for Energy i OSLO-regionen
    benchmark = compute_sector_benchmark("OSLO", "Energy", period="6mo")
    # Returnerer DataFrame med kolonner ['Close', 'Volume']
    # der Close er rebasert til 100 ved start

    # Hovedindeks-data (OSEBX, ^OMX, ^STOXX) — bare en wrapper
    osebx = get_region_index_data("OSLO", period="6mo")
"""

from __future__ import annotations

from typing import Optional
import functools

import numpy as np
import pandas as pd

from core.universe import (
    Region,
    get_tickers_by_sector,
    get_region_benchmark,
)
from core.data import fetch_history, fetch_one


# ============================================================
# Konstanter
# ============================================================

# Hvis en sektor i en region har færre enn dette antallet
# aksjer, faller vi tilbake til EUROPE-sektoren som proxy.
MIN_SECTOR_COMPONENTS = 3

# Rebaserings-startverdi (typisk 100 for indekser)
REBASE_VALUE = 100.0


# ============================================================
# Hovedfunksjoner
# ============================================================

@functools.lru_cache(maxsize=128)
def compute_sector_benchmark(
    region: Region,
    sector: str,
    period: str = "1y",
) -> Optional[pd.DataFrame]:
    """
    Konstruer selvbygd sektor-benchmark for en gitt region+sektor.

    Returnerer DataFrame med kolonner:
        - Close:  Likt-vektet, rebasert prisindeks (start = 100)
        - Volume: Likt-vektet, rebasert omsetnings-indeks (start = 100)

    Algoritme:
        1. Hent alle komponenter i sektoren (innen regionen)
        2. Hvis <3 komponenter: fallback til EUROPE-sektoren
        3. Hent OHLCV-data for alle komponenter i én batch
        4. For hver komponent: rebaser Close til 100 ved start
        5. For hver komponent: beregn omsetning (Close×Volume), rebaser til 100
        6. Snitt likt-vektet over alle komponenter (skip NaN)
        7. Returner felles DataFrame

    Args:
        region:  "OSLO", "NORDIC", eller "EUROPE"
        sector:  Sektor-navn (må matche VALID_SECTORS i universe.py)
        period:  Lookback-periode for yfinance ('6mo', '1y', '2y'...)

    Returns:
        DataFrame med Close og Volume rebasert, eller None hvis ingen
        komponenter har data (ekstremt sjelden hvis universet er rent).

    NB: Resultatet er cached via lru_cache. Bruk clear_benchmark_cache()
        ved start av ny daglig scan for å sikre fersk data.
    """
    # Steg 1-2: Finn komponenter, evt. fallback til EUROPE
    components, used_region = _resolve_components(region, sector)

    if not components:
        return None

    symbols = [t.symbol for t in components]

    # Steg 3: Batch-henting (cached i data.py)
    raw = fetch_history(symbols, period=period, use_cache=True)

    if not raw:
        return None

    # Steg 4-5: Rebaser hver komponent
    rebased_prices: list[pd.Series] = []
    rebased_turnovers: list[pd.Series] = []

    for sym, df in raw.items():
        if df.empty or "Close" not in df.columns:
            continue

        close = df["Close"].dropna()
        if len(close) < 2:
            continue

        # Rebaser pris til 100 ved første tilgjengelige dag
        first_close = close.iloc[0]
        if first_close <= 0:
            continue
        rebased_close = (close / first_close) * REBASE_VALUE
        rebased_prices.append(rebased_close.rename(sym))

        # Beregn omsetning og rebaser
        if "Volume" in df.columns:
            turnover = (df["Close"] * df["Volume"]).dropna()
            if len(turnover) >= 2 and turnover.iloc[0] > 0:
                rebased_turnover = (turnover / turnover.iloc[0]) * REBASE_VALUE
                rebased_turnovers.append(rebased_turnover.rename(sym))

    if not rebased_prices:
        return None

    # Steg 6: Likt-vektet snitt (NaN ignoreres automatisk i .mean(axis=1))
    price_matrix = pd.concat(rebased_prices, axis=1)
    benchmark_close = price_matrix.mean(axis=1, skipna=True)

    # Volum/omsetning kan ha færre komponenter (noen har volume=NaN-dager)
    if rebased_turnovers:
        turnover_matrix = pd.concat(rebased_turnovers, axis=1)
        benchmark_volume = turnover_matrix.mean(axis=1, skipna=True)
    else:
        benchmark_volume = pd.Series(np.nan, index=benchmark_close.index)

    result = pd.DataFrame({
        "Close": benchmark_close,
        "Volume": benchmark_volume,
    })

    # Drop første rad hvis den er NaN (kan skje hvis komponenter har
    # ulik første-tilgjengelig-dato)
    result = result.dropna(subset=["Close"])

    # Metadata for diagnostikk
    result.attrs["region"] = region
    result.attrs["sector"] = sector
    result.attrs["used_region"] = used_region  # kan avvike pga. fallback
    result.attrs["n_components"] = len(rebased_prices)
    result.attrs["component_symbols"] = list(price_matrix.columns)

    return result


def get_region_index_data(
    region: Region,
    period: str = "1y",
) -> Optional[pd.DataFrame]:
    """
    Hent OHLCV-data for regionens hovedindeks.

    OSLO   → OSEBX.OL
    NORDIC → ^OMX
    EUROPE → ^STOXX

    Returnerer DataFrame som fra data.fetch_one(), eller None.
    """
    symbol = get_region_benchmark(region)
    return fetch_one(symbol, period=period, use_cache=True)


def clear_benchmark_cache() -> None:
    """Tøm sektor-benchmark cache. Brukes av daglig scan."""
    compute_sector_benchmark.cache_clear()


def benchmark_cache_info() -> str:
    """Diagnostikk-streng for cache."""
    info = compute_sector_benchmark.cache_info()
    return (
        f"Sektor-benchmark cache: hits={info.hits}, misses={info.misses}, "
        f"size={info.currsize}/{info.maxsize}"
    )


# ============================================================
# Interne hjelpere
# ============================================================

def _resolve_components(
    region: Region,
    sector: str,
) -> tuple[list, Region]:
    """
    Returner liste av Ticker-objekter for sektor i region.

    Hvis regionen har færre enn MIN_SECTOR_COMPONENTS aksjer i
    sektoren, fallback til EUROPE-sektoren.

    Returns:
        (components, used_region) der used_region kan avvike fra
        input-region hvis fallback ble brukt.
    """
    components = get_tickers_by_sector(region, sector)

    if len(components) >= MIN_SECTOR_COMPONENTS:
        return components, region

    # Fallback: bruk EUROPE-sektoren
    if region != "EUROPE":
        europe_components = get_tickers_by_sector("EUROPE", sector)
        if len(europe_components) >= MIN_SECTOR_COMPONENTS:
            return europe_components, "EUROPE"

    # Ingen tilstrekkelig dekning verken regionalt eller i EUROPE
    return components, region  # returner uansett (kan være tom liste)


# ============================================================
# Hjelpefunksjon for diagnostikk / dashboard
# ============================================================

def describe_sector_coverage(region: Region) -> pd.DataFrame:
    """
    Returner en oversikt over alle sektorer i regionen og om de har
    nok komponenter for direkte benchmark, eller faller tilbake til EUROPE.

    Brukes til diagnostikk i Streamlit-app for å vise hvilke sektorer
    er "ekte regionale" og hvilke som er proxy.
    """
    from core.universe import VALID_SECTORS, get_sectors_in_region

    rows = []
    region_sectors = set(get_sectors_in_region(region))

    for sector in sorted(VALID_SECTORS):
        components = get_tickers_by_sector(region, sector)
        n_local = len(components)

        if n_local >= MIN_SECTOR_COMPONENTS:
            status = "regional"
            used = region
        elif sector in region_sectors:
            # Regionen har noen aksjer, men for få for egen kurv
            europe_n = len(get_tickers_by_sector("EUROPE", sector))
            if europe_n >= MIN_SECTOR_COMPONENTS:
                status = "EUROPE-proxy (for få lokalt)"
                used = "EUROPE"
            else:
                status = "manglende"
                used = None
        else:
            europe_n = len(get_tickers_by_sector("EUROPE", sector))
            if europe_n >= MIN_SECTOR_COMPONENTS:
                status = "EUROPE-proxy (ingen lokalt)"
                used = "EUROPE"
            else:
                status = "manglende"
                used = None

        rows.append({
            "Sektor": sector,
            "Lokale komponenter": n_local,
            "Status": status,
            "Brukt region": used,
        })

    return pd.DataFrame(rows)


# ============================================================
# Røyktest når kjørt direkte
# ============================================================
if __name__ == "__main__":
    print("=== Røyktest: benchmarks.py ===\n")

    # Test 1: Sektor-dekning per region
    for region in ("OSLO", "NORDIC", "EUROPE"):
        print(f"\n--- Sektor-dekning for {region} ---")
        coverage = describe_sector_coverage(region)  # type: ignore[arg-type]
        # Vis kun sektorer som er regionale eller EUROPE-proxy
        relevant = coverage[coverage["Status"] != "manglende"]
        print(relevant.to_string(index=False))

    # Test 2: Bygg én faktisk sektor-kurv (krever Yahoo)
    print("\n\n--- Test: bygg OSE Energy-kurv ---")
    try:
        bench = compute_sector_benchmark("OSLO", "Energy", period="3mo")
        if bench is not None and not bench.empty:
            n = bench.attrs.get("n_components", 0)
            used = bench.attrs.get("used_region", "?")
            syms = bench.attrs.get("component_symbols", [])
            print(f"  ✓ Bygde kurv med {n} komponenter (region: {used})")
            print(f"  ✓ Komponenter: {', '.join(syms)}")
            print(f"  ✓ {len(bench)} dager, start={bench['Close'].iloc[0]:.2f}, "
                  f"slutt={bench['Close'].iloc[-1]:.2f}")
            ret_pct = (bench["Close"].iloc[-1] / bench["Close"].iloc[0] - 1) * 100
            print(f"  ✓ Periode-avkastning (rebasert): {ret_pct:+.1f}%")
        else:
            print("  ✗ Kurv ble tom eller None")
    except Exception as e:
        print(f"  ✗ Feilet: {type(e).__name__}: {e}")
        print("    (Sannsynligvis Yahoo rate-limit. Prøv igjen senere.)")

    # Test 3: Cache-funksjon
    print("\n--- Cache-info ---")
    print(f"  {benchmark_cache_info()}")
    print("\n=== Test fullført ===")
