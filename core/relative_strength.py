"""
core/relative_strength.py
=========================
Relativ styrke-analyse for europeiske aksjer.

KJERNE-IDÉEN: En aksje som utkonkurrerer sin sektor og hovedindeksen
samtidig viser tegn på institusjonell akkumulering. Smart money flytter
penger INN i utvalgte aksjer mens de roterer UT av andre.

Tre nivåer av RS:
1. Mot egen sektor-kurv (regionalt)
2. Mot region-hovedindeks (OSEBX/^OMX/^STOXX)
3. Mot STOXX 600 (kun for OSLO — gir europeisk kontekst)

Når alle tre stiger samtidig over flere uker = TRIPPEL-RS-SIGNAL.
Det er det sterkeste pre-akkumulerings-signalet i denne appen.

RS-MOMENTUM: Hastigheten i RS-endring. Akselererende RS er sterkere
enn flat-stigende. Vi bruker 5d og 20d glidende endring.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from core.universe import Region, Ticker, OSLO_SECONDARY_BENCHMARK
from core.data import fetch_one
from core.benchmarks import compute_sector_benchmark, get_region_index_data


# ============================================================
# Datatyper
# ============================================================

@dataclass
class RSReading:
    """En RS-måling for en aksje mot en benchmark."""
    ratio: float          # Aksje/benchmark, rebasert til 100
    change_5d: float      # % endring i ratio siste 5d
    change_20d: float     # % endring i ratio siste 20d
    is_rising: bool       # Siste 20d trend opp?
    is_accelerating: bool # 5d-momentum > 20d-momentum?

    def score(self) -> float:
        """0-100 score for hvor sterk RS er. Brukes til ranking."""
        s = 50.0  # nøytral
        if self.is_rising:
            s += 20
        if self.is_accelerating:
            s += 15
        s += np.clip(self.change_20d, -15, 15)  # normaliser til ±15
        return float(np.clip(s, 0, 100))


@dataclass
class TripleRS:
    """Trippel-RS for en aksje. Hvis vs_secondary er None, gjelder dual-RS."""
    symbol: str
    vs_sector: Optional[RSReading]
    vs_region_index: Optional[RSReading]
    vs_secondary: Optional[RSReading]  # Kun for OSLO: ^STOXX

    def is_triple_strong(self) -> bool:
        """Alle tre RS-målinger viser positiv momentum."""
        readings = [self.vs_sector, self.vs_region_index, self.vs_secondary]
        actual = [r for r in readings if r is not None]
        if len(actual) < 2:
            return False
        return all(r.is_rising for r in actual)

    def aggregate_score(self) -> float:
        """Vektet snitt-score over alle tilgjengelige RS-målinger."""
        scores = []
        weights = []
        if self.vs_sector is not None:
            scores.append(self.vs_sector.score())
            weights.append(0.5)  # sektor er viktigst
        if self.vs_region_index is not None:
            scores.append(self.vs_region_index.score())
            weights.append(0.3)
        if self.vs_secondary is not None:
            scores.append(self.vs_secondary.score())
            weights.append(0.2)
        if not scores:
            return 50.0
        return float(np.average(scores, weights=weights))


# ============================================================
# Hovedfunksjoner
# ============================================================

def compute_rs_ratio(stock: pd.Series, benchmark: pd.Series) -> pd.Series:
    """
    Beregn rebasert RS-ratio.

    Stock og benchmark er Close-serier (kan ha ulik lengde).
    Vi joiner på dato, rebaser begge til 100, deler stock/benchmark,
    og rebaser resultatet til 100.

    Stigende ratio = aksjen utkonkurrerer benchmarken.
    """
    # Felles dato-index
    common = pd.concat([stock, benchmark], axis=1, join="inner").dropna()
    if len(common) < 2:
        return pd.Series(dtype=float)

    s = common.iloc[:, 0]
    b = common.iloc[:, 1]

    # Rebase begge til 100 ved start
    s_reb = (s / s.iloc[0]) * 100
    b_reb = (b / b.iloc[0]) * 100

    # Ratio (begge starter i 100, så ratio starter i 1.0)
    ratio = s_reb / b_reb

    # Rebase ratio til 100 for lesbarhet
    return ratio * 100


def compute_rs_reading(stock: pd.Series, benchmark: pd.Series) -> Optional[RSReading]:
    """Komputer RS-ratio og avled trend/momentum."""
    ratio = compute_rs_ratio(stock, benchmark)
    if len(ratio) < 21:
        return None

    last = float(ratio.iloc[-1])
    val_5d_ago = float(ratio.iloc[-6])
    val_20d_ago = float(ratio.iloc[-21])

    change_5d = (last / val_5d_ago - 1) * 100 if val_5d_ago > 0 else 0.0
    change_20d = (last / val_20d_ago - 1) * 100 if val_20d_ago > 0 else 0.0

    # Lineær regresjon på siste 20d for å vurdere trend
    recent = ratio.tail(20)
    x = np.arange(len(recent))
    slope = np.polyfit(x, recent.values, 1)[0]
    is_rising = slope > 0

    # Akselererende: 5d% > 20d%/4 (skalert)
    is_accelerating = change_5d > change_20d / 4

    return RSReading(
        ratio=last,
        change_5d=change_5d,
        change_20d=change_20d,
        is_rising=is_rising,
        is_accelerating=is_accelerating,
    )


def compute_triple_rs(
    ticker: Ticker,
    period: str = "6mo",
) -> Optional[TripleRS]:
    """
    Komputer trippel-RS for en aksje.

    Returnerer TripleRS-objekt med opp til 3 RS-målinger:
    - vs sektor-kurv (regionalt eller EUROPE-fallback)
    - vs region-hovedindeks
    - vs ^STOXX (kun for OSLO-aksjer)
    """
    # Hent aksjedata
    stock_df = fetch_one(ticker.symbol, period=period)
    if stock_df is None or stock_df.empty:
        return None

    stock_close = stock_df["Close"]

    # 1. Sektor-RS
    sector_bench = compute_sector_benchmark(ticker.region, ticker.sector, period=period)
    vs_sector = None
    if sector_bench is not None and not sector_bench.empty:
        vs_sector = compute_rs_reading(stock_close, sector_bench["Close"])

    # 2. Region-indeks RS
    region_idx_df = get_region_index_data(ticker.region, period=period)
    vs_region = None
    if region_idx_df is not None and not region_idx_df.empty:
        vs_region = compute_rs_reading(stock_close, region_idx_df["Close"])

    # 3. STOXX 600 (kun for OSLO som sekundær benchmark)
    vs_secondary = None
    if ticker.region == "OSLO":
        stoxx_df = fetch_one(OSLO_SECONDARY_BENCHMARK, period=period)
        if stoxx_df is not None and not stoxx_df.empty:
            vs_secondary = compute_rs_reading(stock_close, stoxx_df["Close"])

    return TripleRS(
        symbol=ticker.symbol,
        vs_sector=vs_sector,
        vs_region_index=vs_region,
        vs_secondary=vs_secondary,
    )


# ============================================================
# Heatmap-data: alle sektorer i en region
# ============================================================

def sector_relative_strength_matrix(
    region: Region,
    period: str = "3mo",
) -> pd.DataFrame:
    """
    Bygg matrise med sektor-RS for varmekart.

    Returnerer DataFrame med kolonner:
        sector, change_20d, change_5d, score, used_region
    """
    from core.universe import VALID_SECTORS

    region_idx = get_region_index_data(region, period=period)
    if region_idx is None or region_idx.empty:
        return pd.DataFrame()

    rows = []
    for sector in sorted(VALID_SECTORS):
        bench = compute_sector_benchmark(region, sector, period=period)
        if bench is None or bench.empty:
            continue

        reading = compute_rs_reading(bench["Close"], region_idx["Close"])
        if reading is None:
            continue

        used = bench.attrs.get("used_region", region)
        rows.append({
            "sector": sector,
            "change_20d": reading.change_20d,
            "change_5d": reading.change_5d,
            "score": reading.score(),
            "is_rising": reading.is_rising,
            "is_accelerating": reading.is_accelerating,
            "used_region": used,
            "n_components": bench.attrs.get("n_components", 0),
        })

    return pd.DataFrame(rows).sort_values("score", ascending=False)
