"""
core/wyckoff.py
===============
Wyckoff-fase-deteksjon med pivot-basert støtte/motstand.

KJERNE-IDÉEN: I stedet for å lete etter "trading range" som et lav-
volatilitet-vindu, finner vi de FAKTISKE pivot-toppene (lokale High-
maks) og pivot-bunnene (lokale Low-min) — slik en menneskelig trader
ville tegne det.

Algoritme:
1. Pivot-high: en dag er pivot-high hvis dens High er høyere enn alle
   `pivot_width` dager til venstre OG `pivot_width` dager til høyre.
2. Pivot-low: tilsvarende for lokal bunn.
3. Vi tar de N nyeste pivot-highs som motstandskandidater og N nyeste
   pivot-lows som støttekandidater
4. Hvis aksjen er over en gruppe pivot-highs (breakout-scenario), bruker
   vi de GAMLE pivot-highs som motstand — de står stille!
5. Tilsvarende for støtte.

Dette gir oss det Felix Prehn ville tegnet: en motstandslinje på den
nylige toppen som AKSJEN BRØT UT FRA, og en støttelinje på siste
solide bunn.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


WyckoffPhase = Literal["accumulation", "markup", "distribution", "markdown", "unclear"]


@dataclass
class WyckoffAnalysis:
    phase: WyckoffPhase
    support: float
    resistance: float
    last_close: float
    in_range: bool
    spring_detected: bool
    markup_detected: bool
    range_pct: float
    days_in_range: int
    tr_start_idx: int
    tr_end_idx: int


# ============================================================
# Pivot-deteksjon
# ============================================================

def find_pivots(
    df: pd.DataFrame,
    pivot_width: int = 5,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """
    Finn pivot-highs og pivot-lows i hele historikken.

    En pivot-high på indeks i er: H[i] >= H[i-w...i-1] OG H[i] >= H[i+1...i+w]
    En pivot-low: L[i] <= L[i-w...i-1] OG L[i] <= L[i+1...i+w]

    Args:
        df: OHLC DataFrame
        pivot_width: Antall dager på hver side som må være lavere/høyere

    Returns:
        (pivot_highs, pivot_lows) hver liste = [(idx, pris), ...]
    """
    n = len(df)
    if n < 2 * pivot_width + 1:
        return [], []

    highs = df["High"].values
    lows = df["Low"].values

    pivot_highs = []
    pivot_lows = []

    for i in range(pivot_width, n - pivot_width):
        left = highs[i - pivot_width:i]
        right = highs[i + 1:i + pivot_width + 1]
        if highs[i] >= left.max() and highs[i] >= right.max():
            pivot_highs.append((i, highs[i]))

        left_l = lows[i - pivot_width:i]
        right_l = lows[i + 1:i + pivot_width + 1]
        if lows[i] <= left_l.min() and lows[i] <= right_l.min():
            pivot_lows.append((i, lows[i]))

    return pivot_highs, pivot_lows


def find_support_resistance_from_pivots(
    df: pd.DataFrame,
    pivot_width: int = 5,
    cluster_pct: float = 3.0,
) -> tuple[float, float, int, int]:
    """
    Beregn støtte og motstand fra pivot-clusters.

    Algoritme:
    1. Finn alle pivot-highs og pivot-lows
    2. Cluster pivot-highs som ligger innenfor `cluster_pct` av hverandre
    3. Resistance = nyeste cluster av pivot-highs SOM ER OVER siste close
       (hvis ingen over, bruk siste cluster under siste close — typisk
       breakout-scenario, vi viser old resistance)
    4. Tilsvarende for support

    Returns:
        (support, resistance, tr_start_idx, tr_end_idx)
        tr_*_idx peker på pivot-indeksene som ble brukt
    """
    n = len(df)
    if n < 30:
        return _percentile_fallback(df)

    pivot_highs, pivot_lows = find_pivots(df, pivot_width)
    if not pivot_highs or not pivot_lows:
        return _percentile_fallback(df)

    last_close = float(df["Close"].iloc[-1])

    # --- Resistance ---
    # Strategi:
    # - Hvis pris er UNDER en cluster pivot-highs: bruk nyeste cluster over pris
    # - Hvis pris er OVER alle pivot-highs (markup): bruk siste signifikante
    #   cluster av pivot-highs FØR markup begynte. Dvs. den cluster der
    #   pivots ligger nær hverandre i tid OG pris (klassisk TR-topp).
    resistance_idx = None
    resistance = None

    above_close = [(i, p) for i, p in pivot_highs if p > last_close]
    if above_close:
        # Pris under en eller flere pivot-highs — bruk nærmeste over
        resistance_idx, resistance = above_close[-1]
    else:
        # Pris er over ALLE pivot-highs (markup-scenario)
        # Finn den eldste cluster som har minst 2 pivots OG hvor pivotene
        # er nær hverandre i pris (innenfor cluster_pct) — det er TR-topp
        clusters = _cluster_pivots(pivot_highs, cluster_pct)
        # Filtrér til clusters med minst 2 pivots
        substantial = [c for c in clusters if len(c) >= 2]
        if substantial:
            # Velg den NYESTE av disse substantielle clustere som er UNDER siste close
            # (= TR-topp før markup)
            candidates = []
            for cluster in substantial:
                cluster_max = max(p for _, p in cluster)
                cluster_max_idx = max(i for i, p in cluster)
                if cluster_max < last_close:
                    candidates.append((cluster_max_idx, cluster_max, cluster))
            if candidates:
                # Velg nyeste under last_close
                candidates.sort(key=lambda x: x[0])
                resistance_idx, resistance, _ = candidates[-1]
        if resistance is None and clusters:
            # Fallback: nyeste cluster (alt-i-alt)
            last_cluster = clusters[-1]
            resistance_idx = max(i for i, _ in last_cluster)
            resistance = float(np.mean([p for _, p in last_cluster]))

    # --- Support ---
    support_idx = None
    support = None
    below_close = [(i, p) for i, p in pivot_lows if p < last_close]
    if below_close:
        support_idx, support = below_close[-1]
    else:
        clusters = _cluster_pivots(pivot_lows, cluster_pct)
        if clusters:
            last_cluster = clusters[-1]
            support_idx = max(i for i, _ in last_cluster)
            support = float(np.mean([p for _, p in last_cluster]))

    if support is None or resistance is None:
        return _percentile_fallback(df)

    # TR-grensene fra de respective pivot-indeksene
    tr_start = min(support_idx, resistance_idx)
    tr_end = max(support_idx, resistance_idx)

    return support, resistance, tr_start, tr_end


def _cluster_pivots(
    pivots: list[tuple[int, float]],
    cluster_pct: float,
) -> list[list[tuple[int, float]]]:
    """
    Gruppér pivots som ligger innenfor `cluster_pct` av hverandre.

    Returnerer liste av lister, der hver indre liste er ett cluster.
    Clusters er sortert etter posisjon (nyeste sist).
    """
    if not pivots:
        return []
    # Sorter etter pris
    sorted_pivots = sorted(pivots, key=lambda x: x[1])
    clusters = [[sorted_pivots[0]]]
    for i, p in sorted_pivots[1:]:
        last_cluster = clusters[-1]
        avg = np.mean([pp for _, pp in last_cluster])
        if abs(p - avg) / avg * 100 <= cluster_pct:
            last_cluster.append((i, p))
        else:
            clusters.append([(i, p)])
    # Sorter clusters etter nyeste indeks
    return sorted(clusters, key=lambda c: max(i for i, _ in c))


def _percentile_fallback(df: pd.DataFrame) -> tuple[float, float, int, int]:
    """Fallback hvis pivot-deteksjon feiler."""
    n = len(df)
    if n <= 5:
        last = float(df["Close"].iloc[-1])
        return last * 0.95, last * 1.05, 0, n - 1
    lookback = min(60, max(20, n - 5))
    baseline = df.iloc[-lookback - 5:-5]
    if baseline.empty:
        last = float(df["Close"].iloc[-1])
        return last * 0.95, last * 1.05, 0, n - 1
    support = float(np.percentile(baseline["Low"], 10))
    resistance = float(np.percentile(baseline["High"], 90))
    return support, resistance, max(0, n - lookback - 5), n - 6


# ============================================================
# Fase-deteksjon
# ============================================================

def detect_phase(
    df: pd.DataFrame,
    support: float,
    resistance: float,
    tr_start_idx: int,
    tr_end_idx: int,
) -> WyckoffPhase:
    if len(df) < 30:
        return "unclear"

    last = float(df["Close"].iloc[-1])

    if last > resistance * 1.02:
        return "markup"
    if last < support * 0.98:
        return "markdown"

    if support <= last <= resistance:
        if tr_start_idx > 10:
            before = df["Close"].iloc[:tr_start_idx].tail(60).values
            if len(before) >= 5:
                slope = _slope_pct(before)
                if slope < -0.1:
                    return "accumulation"
                if slope > 0.1:
                    return "distribution"
        return "accumulation"

    return "unclear"


def _slope_pct(prices) -> float:
    if len(prices) < 3:
        return 0.0
    x = np.arange(len(prices))
    slope = np.polyfit(x, prices, 1)[0]
    mean = np.mean(prices)
    return float(slope / mean * 100) if mean > 0 else 0.0


# ============================================================
# Spring og Markup-deteksjon
# ============================================================

def detect_spring(df: pd.DataFrame, support: float, lookback: int = 5) -> bool:
    if len(df) < 25:
        return False
    recent = df.tail(lookback)
    avg_vol = df["Volume"].rolling(20).mean()
    for date in recent.index:
        low = df.loc[date, "Low"]
        close = df.loc[date, "Close"]
        vol = df.loc[date, "Volume"]
        avg = avg_vol.loc[date] if date in avg_vol.index else None
        if avg is None or pd.isna(avg) or pd.isna(vol):
            continue
        if low < support * 0.99 and close > support and vol >= avg * 1.5:
            return True
    return False


def detect_markup_breakout(df: pd.DataFrame, resistance: float, lookback: int = 5) -> bool:
    if len(df) < 25:
        return False
    recent = df.tail(lookback)
    avg_vol = df["Volume"].rolling(20).mean()
    for date in recent.index:
        close = df.loc[date, "Close"]
        vol = df.loc[date, "Volume"]
        avg = avg_vol.loc[date] if date in avg_vol.index else None
        if avg is None or pd.isna(avg) or pd.isna(vol):
            continue
        if (close > resistance * 1.01 and close < resistance * 1.07
                and vol >= avg * 1.5):
            return True
    return False


def days_since_tr_end(df: pd.DataFrame, tr_end_idx: int) -> int:
    return max(0, len(df) - 1 - tr_end_idx)


# ============================================================
# Hovedanalyse
# ============================================================

def analyze_wyckoff(df: pd.DataFrame) -> WyckoffAnalysis:
    if df.empty:
        return WyckoffAnalysis(
            phase="unclear", support=0.0, resistance=0.0, last_close=0.0,
            in_range=False, spring_detected=False, markup_detected=False,
            range_pct=0.0, days_in_range=0, tr_start_idx=0, tr_end_idx=0,
        )

    support, resistance, tr_start, tr_end = find_support_resistance_from_pivots(df)
    last = float(df["Close"].iloc[-1])
    phase = detect_phase(df, support, resistance, tr_start, tr_end)
    in_range = support <= last <= resistance
    spring = detect_spring(df, support)
    markup = detect_markup_breakout(df, resistance)
    range_pct = (resistance - support) / support * 100 if support > 0 else 0
    days = days_since_tr_end(df, tr_end)

    return WyckoffAnalysis(
        phase=phase, support=support, resistance=resistance, last_close=last,
        in_range=in_range, spring_detected=spring, markup_detected=markup,
        range_pct=range_pct, days_in_range=days,
        tr_start_idx=tr_start, tr_end_idx=tr_end,
    )


# Bakoverkompatibilitet
def find_support_resistance(df: pd.DataFrame, lookback: int = 60,
                            exclude_recent: int = 5) -> tuple[float, float]:
    support, resistance, _, _ = find_support_resistance_from_pivots(df)
    return support, resistance


def find_trading_range(df: pd.DataFrame, **kwargs) -> tuple[float, float, int, int]:
    """Alias for find_support_resistance_from_pivots."""
    return find_support_resistance_from_pivots(df)
