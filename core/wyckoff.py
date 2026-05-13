"""
core/wyckoff.py
===============
Wyckoff-fase-deteksjon med to-nivå støtte/motstand:

1. KORTSIKTIG (siste konsolidering) — primær, brukes for fase-deteksjon
   og paper trading-signaler. Vi tar den nyligste handlebare TR-en.

2. LANGSIKTIG (base) — sekundær, valgfri visning. Den største/lengste
   konsolideringen som hovedbreakouten kom fra. Brukes til kontekst.

For Nokia-eksempelet:
- Kortsiktig: Motstand 11.91, Støtte 8.37 (siste konsolidering)
- Langsiktig: Motstand ~6.90, Støtte ~5.80 (basen i 2025)
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd


WyckoffPhase = Literal["accumulation", "markup", "distribution", "markdown", "unclear"]


@dataclass
class WyckoffAnalysis:
    # Hovedanalyse (basert på kortsiktig TR)
    phase: WyckoffPhase
    support: float                # Kortsiktig (siste konsolidering)
    resistance: float
    last_close: float
    in_range: bool
    spring_detected: bool
    markup_detected: bool
    range_pct: float
    days_in_range: int
    tr_start_idx: int
    tr_end_idx: int
    # Langsiktig base (valgfri visning)
    base_support: Optional[float] = None
    base_resistance: Optional[float] = None
    base_start_idx: Optional[int] = None
    base_end_idx: Optional[int] = None


# ============================================================
# Pivot-deteksjon
# ============================================================

def find_pivots(
    df: pd.DataFrame,
    pivot_width: int = 5,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """
    Finn pivot-highs og pivot-lows.

    En pivot-high på indeks i: H[i] >= H[i-w...i-1] OG H[i] >= H[i+1...i+w]
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


def _cluster_pivots(
    pivots: list[tuple[int, float]],
    cluster_pct: float,
) -> list[list[tuple[int, float]]]:
    """Gruppér pivots som ligger innenfor cluster_pct av hverandre i pris."""
    if not pivots:
        return []
    sorted_pivots = sorted(pivots, key=lambda x: x[1])
    clusters = [[sorted_pivots[0]]]
    for i, p in sorted_pivots[1:]:
        last_cluster = clusters[-1]
        avg = np.mean([pp for _, pp in last_cluster])
        if abs(p - avg) / avg * 100 <= cluster_pct:
            last_cluster.append((i, p))
        else:
            clusters.append([(i, p)])
    return sorted(clusters, key=lambda c: max(i for i, _ in c))


# ============================================================
# Kortsiktig TR (= siste konsolidering)
# ============================================================

def find_support_resistance_from_pivots(
    df: pd.DataFrame,
    pivot_width: int = 5,
    cluster_pct: float = 3.0,
) -> tuple[float, float, int, int]:
    """
    Finn kortsiktig støtte/motstand basert på de nyeste pivot-clustere.
    """
    n = len(df)
    if n < 30:
        return _percentile_fallback(df)

    pivot_highs, pivot_lows = find_pivots(df, pivot_width)
    if not pivot_highs or not pivot_lows:
        return _percentile_fallback(df)

    last_close = float(df["Close"].iloc[-1])

    # === Resistance ===
    resistance_idx = None
    resistance = None
    above_close = [(i, p) for i, p in pivot_highs if p > last_close]
    if above_close:
        resistance_idx, resistance = above_close[-1]
    else:
        # Markup-scenario: bruk nyeste cluster av pivot-highs
        clusters = _cluster_pivots(pivot_highs, cluster_pct)
        substantial = [c for c in clusters if len(c) >= 2]
        if substantial:
            candidates = []
            for cluster in substantial:
                cluster_max = max(p for _, p in cluster)
                cluster_max_idx = max(i for i, p in cluster)
                if cluster_max < last_close:
                    candidates.append((cluster_max_idx, cluster_max, cluster))
            if candidates:
                candidates.sort(key=lambda x: x[0])
                resistance_idx, resistance, _ = candidates[-1]
        if resistance is None and clusters:
            last_cluster = clusters[-1]
            resistance_idx = max(i for i, _ in last_cluster)
            resistance = float(np.mean([p for _, p in last_cluster]))

    # === Support ===
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

    tr_start = min(support_idx, resistance_idx)
    tr_end = max(support_idx, resistance_idx)
    return support, resistance, tr_start, tr_end


# ============================================================
# LANGSIKTIG BASE (= største/lengste konsolidering)
# ============================================================

def find_base_consolidation(
    df: pd.DataFrame,
    pivot_width: int = 5,
    cluster_pct: float = 5.0,
    min_cluster_size: int = 3,
) -> Optional[tuple[float, float, int, int]]:
    """
    Finn den langsiktige basen — den største/eldste meningsfulle
    konsolideringen i historikken.

    Strategi:
    - Cluster alle pivot-highs og pivot-lows i tette grupper
    - Den BESTE clusteret har: flest pivots, størst tidsutstrekning
    - Vi finner det clusteret som representerer "den lange basen"

    Returns:
        (base_support, base_resistance, start_idx, end_idx) eller None
        hvis ingen klar base finnes.
    """
    n = len(df)
    if n < 60:
        return None

    pivot_highs, pivot_lows = find_pivots(df, pivot_width)
    if len(pivot_highs) < min_cluster_size or len(pivot_lows) < min_cluster_size:
        return None

    # Cluster med bredere terskel for å fange større konsolideringer
    high_clusters = _cluster_pivots(pivot_highs, cluster_pct)
    low_clusters = _cluster_pivots(pivot_lows, cluster_pct)

    # Score hvert cluster: kombinasjon av antall pivots og tidsutstrekning
    def score_cluster(cluster):
        if len(cluster) < min_cluster_size:
            return 0
        indices = [i for i, _ in cluster]
        time_span = max(indices) - min(indices)
        return len(cluster) * time_span  # størst antall × lengst tid

    # Finn beste high-cluster og low-cluster
    high_clusters_sorted = sorted(high_clusters, key=score_cluster, reverse=True)
    low_clusters_sorted = sorted(low_clusters, key=score_cluster, reverse=True)

    best_high = high_clusters_sorted[0] if high_clusters_sorted else None
    best_low = low_clusters_sorted[0] if low_clusters_sorted else None

    if not best_high or not best_low:
        return None
    if len(best_high) < min_cluster_size or len(best_low) < min_cluster_size:
        return None

    # Verifiser at clustere overlapper i tid (= samme TR)
    high_indices = [i for i, _ in best_high]
    low_indices = [i for i, _ in best_low]
    h_start, h_end = min(high_indices), max(high_indices)
    l_start, l_end = min(low_indices), max(low_indices)

    # Overlapp må eksistere
    overlap_start = max(h_start, l_start)
    overlap_end = min(h_end, l_end)
    if overlap_end - overlap_start < 20:  # mindre enn 20d overlapp = ikke samme TR
        return None

    # Hvis denne basen er SAMME som kortsiktig TR, returner None
    # (vi vil ikke duplisere)
    short_s, short_r, short_start, short_end = find_support_resistance_from_pivots(df)
    base_resistance = float(np.mean([p for _, p in best_high]))
    base_support = float(np.mean([p for _, p in best_low]))

    # Hvis bredde og periode er ~lik kortsiktig, det er samme TR
    if (
        abs(base_resistance - short_r) / short_r < 0.05
        and abs(base_support - short_s) / short_s < 0.05
    ):
        return None

    base_start = min(h_start, l_start)
    base_end = max(h_end, l_end)
    return base_support, base_resistance, base_start, base_end


# ============================================================
# Fallback
# ============================================================

def _percentile_fallback(df: pd.DataFrame) -> tuple[float, float, int, int]:
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
# Spring / Markup-deteksjon
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

    # Kortsiktig (siste konsolidering) — primær
    support, resistance, tr_start, tr_end = find_support_resistance_from_pivots(df)
    last = float(df["Close"].iloc[-1])
    phase = detect_phase(df, support, resistance, tr_start, tr_end)
    in_range = support <= last <= resistance
    spring = detect_spring(df, support)
    markup = detect_markup_breakout(df, resistance)
    range_pct = (resistance - support) / support * 100 if support > 0 else 0
    days = days_since_tr_end(df, tr_end)

    # Langsiktig base (valgfri)
    base = find_base_consolidation(df)
    base_support = base_resistance = base_start = base_end = None
    if base is not None:
        base_support, base_resistance, base_start, base_end = base

    return WyckoffAnalysis(
        phase=phase, support=support, resistance=resistance, last_close=last,
        in_range=in_range, spring_detected=spring, markup_detected=markup,
        range_pct=range_pct, days_in_range=days,
        tr_start_idx=tr_start, tr_end_idx=tr_end,
        base_support=base_support, base_resistance=base_resistance,
        base_start_idx=base_start, base_end_idx=base_end,
    )


# Bakoverkompatibilitet
def find_support_resistance(df: pd.DataFrame, lookback: int = 60,
                            exclude_recent: int = 5) -> tuple[float, float]:
    support, resistance, _, _ = find_support_resistance_from_pivots(df)
    return support, resistance


def find_trading_range(df: pd.DataFrame, **kwargs) -> tuple[float, float, int, int]:
    return find_support_resistance_from_pivots(df)
