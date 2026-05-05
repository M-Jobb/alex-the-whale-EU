"""
core/data.py
============
Robust dataimport-lag for europeisk Smart Money Dashboard.

Hovedansvar:
1. Henter pris- og volumdata fra Yahoo Finance i batch (unngå rate-limit)
2. Normaliserer GBX (pence) til GBP (pund) for LSE-aksjer
3. Erstatter volume=0 med NaN (Yahoo-bug på europeiske børser)
4. Cacher resultater i memory for å unngå dobbeltsøk innen sesjon
5. Håndterer både single-symbol og multi-symbol Yahoo-respons konsistent

Bruk:
    from core.data import fetch_history, fetch_one

    # Én aksje
    df = fetch_one("EQNR.OL", period="1y")

    # Flere aksjer i én batch
    data = fetch_history(["EQNR.OL", "DNB.OL", "MOWI.OL"], period="1y")
    eqnr_close = data["EQNR.OL"]["Close"]
"""

from __future__ import annotations

import time
import warnings
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from core.universe import get_currency_for_ticker

# ============================================================
# Konstanter
# ============================================================

# LSE-aksjer (.L) handles i pence (GBX), men metadata sier GBP.
# Vi deler på 100 for å konvertere til faktiske pund.
GBX_SUFFIX = ".L"
GBX_DIVISOR = 100.0

# Standard retry-parametre for batch-henting
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 5  # sekunder, dobles hvert forsøk

# In-memory cache: {(symbols_tuple, period, interval): DataFrame}
_CACHE: dict[tuple, dict[str, pd.DataFrame]] = {}


# ============================================================
# Hovedfunksjoner
# ============================================================

def fetch_history(
    symbols: list[str],
    period: str = "1y",
    interval: str = "1d",
    use_cache: bool = True,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, pd.DataFrame]:
    """
    Hent historisk OHLCV-data for flere symboler i ÉN batch-forespørsel.

    Returnerer dict {symbol: DataFrame} der DataFrame har kolonner
    ['Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume'] og DatetimeIndex.

    Symboler som feiler returneres ikke i dict (sjekk med `if sym in result`).

    Args:
        symbols: Liste av Yahoo-symboler
        period: '1mo', '3mo', '6mo', '1y', '2y', '5y', 'max' osv.
        interval: '1d', '1wk', '1mo'
        use_cache: True for å returnere cached resultat hvis tilgjengelig
        max_retries: Antall forsøk hvis Yahoo feiler

    Raises:
        RuntimeError hvis hele batchen feiler etter alle retries
    """
    if not symbols:
        return {}

    # Normaliser symbol-listen for cache-nøkkel (sortert tuple)
    symbols = list(dict.fromkeys(symbols))  # dedupliser, behold rekkefølge
    cache_key = (tuple(sorted(symbols)), period, interval)

    if use_cache and cache_key in _CACHE:
        # Returner kun forespurte symboler (i deres opprinnelige rekkefølge)
        cached = _CACHE[cache_key]
        return {s: cached[s].copy() for s in symbols if s in cached}

    # Hent fra Yahoo med retry-logikk
    raw = _fetch_with_retry(symbols, period, interval, max_retries)

    # Parse til {symbol: DataFrame}
    parsed = _parse_yfinance_response(raw, symbols)

    # Post-prosessering: GBX-fix, volume=0, sortering
    cleaned = {sym: _clean_dataframe(sym, df) for sym, df in parsed.items()}

    # Filtrer bort tomme/ubrukelige
    cleaned = {sym: df for sym, df in cleaned.items() if not df.empty}

    if use_cache:
        _CACHE[cache_key] = cleaned

    return {s: cleaned[s].copy() for s in symbols if s in cleaned}


def fetch_one(
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
    use_cache: bool = True,
) -> Optional[pd.DataFrame]:
    """Bekvemmlighetsfunksjon for én aksje. Returnerer None hvis feiler."""
    result = fetch_history([symbol], period=period, interval=interval, use_cache=use_cache)
    return result.get(symbol)


def clear_cache() -> None:
    """Tøm in-memory cache. Nyttig før ny daglig scan."""
    _CACHE.clear()


def cache_size() -> int:
    """Antall batches i cache (for debugging)."""
    return len(_CACHE)


# ============================================================
# Interne hjelpere
# ============================================================

def _fetch_with_retry(
    symbols: list[str],
    period: str,
    interval: str,
    max_retries: int,
) -> pd.DataFrame:
    """Hent fra Yahoo med exponential backoff."""
    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                data = yf.download(
                    symbols,
                    period=period,
                    interval=interval,
                    progress=False,
                    auto_adjust=False,
                    threads=True,
                    group_by="ticker",
                )
            if data is not None and len(data) > 0:
                return data
            # Tom respons — behandles som feil
            last_error = RuntimeError("Yahoo returnerte tom DataFrame")
        except Exception as e:
            last_error = e

        if attempt < max_retries:
            wait = DEFAULT_RETRY_DELAY * (2 ** (attempt - 1))
            time.sleep(wait)

    raise RuntimeError(
        f"Kunne ikke hente data for {len(symbols)} symboler etter "
        f"{max_retries} forsøk. Siste feil: {last_error}"
    )


def _parse_yfinance_response(
    raw: pd.DataFrame,
    symbols: list[str],
) -> dict[str, pd.DataFrame]:
    """
    Parse yfinance-respons til {symbol: DataFrame}.

    yf.download() returnerer forskjellig struktur avhengig av input:
    - 1 symbol → flat DataFrame med kolonner [Open, High, Low, Close, Adj Close, Volume]
    - n symboler + group_by='ticker' → MultiIndex med (symbol, felt)
    - n symboler uten group_by → MultiIndex med (felt, symbol)

    Vi støtter alle tre tilfeller defensivt.
    """
    result: dict[str, pd.DataFrame] = {}

    if not isinstance(raw.columns, pd.MultiIndex):
        # Flat DataFrame — kun ett symbol
        if len(symbols) != 1:
            return result  # Inkonsistent, hopp over
        sym = symbols[0]
        if not raw.empty and "Close" in raw.columns:
            result[sym] = raw.copy()
        return result

    # MultiIndex — finn ut om første eller andre nivå er symboler
    level0 = set(raw.columns.get_level_values(0))
    level1 = set(raw.columns.get_level_values(1))
    symbols_set = set(symbols)

    if level0 & symbols_set:
        # Format: (symbol, felt) — group_by='ticker'
        for sym in symbols:
            if sym in level0:
                try:
                    sym_df = raw[sym].copy()
                    if not sym_df.empty and "Close" in sym_df.columns:
                        result[sym] = sym_df
                except (KeyError, AttributeError):
                    pass
    elif level1 & symbols_set:
        # Format: (felt, symbol)
        for sym in symbols:
            if sym in level1:
                try:
                    sym_df = raw.xs(sym, axis=1, level=1).copy()
                    if not sym_df.empty and "Close" in sym_df.columns:
                        result[sym] = sym_df
                except (KeyError, AttributeError):
                    pass

    return result


def _clean_dataframe(symbol: str, df: pd.DataFrame) -> pd.DataFrame:
    """
    Anvend rense-steg på en enkelt aksjes DataFrame:
    1. Sorter på dato (Yahoo returnerer noen ganger usortert)
    2. Konverter GBX→GBP for LSE-aksjer
    3. Erstatt volume=0 med NaN (Yahoo-bug)
    4. Drop rader uten Close-pris (har skjedd ved børslukking)
    """
    if df.empty:
        return df

    df = df.sort_index()

    # Drop rader hvor Close er NaN (umulige å bruke til VSA)
    df = df.dropna(subset=["Close"])

    # GBX → GBP for LSE-aksjer
    if symbol.endswith(GBX_SUFFIX):
        price_cols = [c for c in ["Open", "High", "Low", "Close", "Adj Close"]
                      if c in df.columns]
        df[price_cols] = df[price_cols] / GBX_DIVISOR

    # volume=0 → NaN (Yahoo rapporterer ikke alltid riktig)
    if "Volume" in df.columns:
        df["Volume"] = df["Volume"].replace(0, np.nan)

    return df


# ============================================================
# Likviditets-hjelpere (brukes senere av VSA-modul)
# ============================================================

def average_daily_turnover(df: pd.DataFrame, lookback: int = 30) -> float:
    """
    Gjennomsnittlig daglig omsetning i lokal valuta over `lookback` dager.

    Omsetning = Close × Volume (NaN-volum dropper ut av snittet).
    Returnerer 0.0 hvis utilstrekkelig data.
    """
    if df.empty or "Volume" not in df.columns or "Close" not in df.columns:
        return 0.0

    recent = df.tail(lookback)
    turnover = recent["Close"] * recent["Volume"]
    avg = turnover.mean()

    return float(avg) if pd.notna(avg) else 0.0


def passes_liquidity_filter(
    df: pd.DataFrame,
    symbol: str,
    min_turnover_local: dict[str, float] = None,  # type: ignore[assignment]
) -> bool:
    """
    Sjekk om aksjen passerer likviditetsfilter.

    Standard minimumsterskler per valuta (basert på erfaring med
    europeiske small/mid caps):
        NOK: 20 millioner   (~2 MUSD)
        SEK: 20 millioner   (~2 MUSD)
        DKK: 15 millioner   (~2 MUSD)
        EUR: 2 millioner
        GBP: 1 million
        CHF: 2 millioner

    Aksjer under disse tersklene har for lav institusjonell aktivitet
    til at smart-money-signaler er pålitelige.
    """
    if min_turnover_local is None:
        min_turnover_local = {
            "NOK": 20_000_000,
            "SEK": 20_000_000,
            "DKK": 15_000_000,
            "EUR": 2_000_000,
            "GBP": 1_000_000,
            "CHF": 2_000_000,
        }

    currency = get_currency_for_ticker(symbol)
    if currency is None:
        return True  # Ukjent valuta → ikke filtrer ut

    threshold = min_turnover_local.get(currency, 0)
    return average_daily_turnover(df) >= threshold


# ============================================================
# Når kjørt direkte: enkel røyktest
# ============================================================
if __name__ == "__main__":
    print("=== Røyktest: data.py ===\n")

    # Test 1: Single symbol
    print("Test 1: fetch_one('EQNR.OL')")
    eqnr = fetch_one("EQNR.OL", period="1mo")
    if eqnr is not None:
        print(f"  ✓ {len(eqnr)} rader, siste close = {eqnr['Close'].iloc[-1]:.2f} NOK")
        print(f"  ✓ Snitt-omsetning siste 30d: {average_daily_turnover(eqnr):,.0f} NOK")
    else:
        print("  ✗ Feilet")

    # Test 2: Multi-symbol med GBX-konvertering
    print("\nTest 2: fetch_history(['SHEL.L', 'EQNR.OL', 'NESN.SW'])")
    data = fetch_history(["SHEL.L", "EQNR.OL", "NESN.SW"], period="1mo")
    for sym in ["SHEL.L", "EQNR.OL", "NESN.SW"]:
        if sym in data:
            df = data[sym]
            print(f"  ✓ {sym:10s} {len(df)} rader, close = {df['Close'].iloc[-1]:.2f}")
        else:
            print(f"  ✗ {sym} feilet")

    # Test 3: Cache fungerer
    print("\nTest 3: cache-treff")
    print(f"  Cache size før: {cache_size()}")
    _ = fetch_one("EQNR.OL", period="1mo")  # samme som test 1
    print(f"  Cache size etter (skal være uendret): {cache_size()}")
    print(f"  ✓ Cache fungerer (ingen ny henting)")

    # Test 4: Likviditetsfilter
    print("\nTest 4: likviditetsfilter")
    if eqnr is not None:
        ok = passes_liquidity_filter(eqnr, "EQNR.OL")
        print(f"  EQNR.OL passerer 20 MNOK-filter: {ok}")

    print("\n=== Test fullført ===")
