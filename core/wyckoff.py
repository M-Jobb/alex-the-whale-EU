"""
core/wyckoff.py
===============
Wyckoff-fase-deteksjon og breakout-signaler.

KJERNE-IDÉEN: Markedet beveger seg gjennom 4 faser:

1. AKKUMULERING — Smart money bygger posisjoner. Pris går sidelengs i
   et trading range (TR). Volum er typisk fallende mot slutten av fasen.

2. MARK-UP — Public oppdager. Pris bryter ut over TR med høyt volum.

3. DISTRIBUSJON — Smart money selger til public. Pris går sidelengs
   nær toppen, ofte med spredt volum.

4. MARK-DOWN — Pris bryter ned. Smart money er ute, public er fanget.

KRITISKE EVENTS:
- SC (Selling Climax): Panikk-salg som markerer slutten på mark-down
- AR (Automatic Rally): Reaksjon opp etter SC
- ST (Secondary Test): Re-test av SC-low på lavere volum
- SPRING: Falsk breakdown fra TR — stop-loss-jakt før markup
- LPS (Last Point of Support): Siste lave i TR før markup

Vi fokuserer på SPRING og MARKUP-deteksjon fordi de er handlebare.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Literal

import numpy as np
import pandas as pd


WyckoffPhase = Literal["accumulation", "markup", "distribution", "markdown", "unclear"]


@dataclass
class WyckoffAnalysis:
    """Aggregert Wyckoff-analyse for en aksje."""
    phase: WyckoffPhase
    support: float            # Nedre grense for nylig TR
    resistance: float         # Øvre grense for nylig TR
    last_close: float
    in_range: bool            # Pris innenfor TR
    spring_detected: bool     # Spring-kandidat siste 5 dager
    markup_detected: bool     # Markup-breakout siste 5 dager
    range_pct: float          # (resist-support)/support, % bredde av TR
    days_in_range: int        # Hvor mange dager pris har vært i TR


def find_support_resistance(
    df: pd.DataFrame,
    lookback: int = 60,
    exclude_recent: int = 5,
) -> tuple[float, float]:
    """
    Identifiser TR-grenser fra historisk data.

    Vi ekskluderer de siste N dagene for å unngå at en pågående breakout
    forurenser baseline. Vi tar persentil i stedet for absolutt min/max
    for å filtrere bort enkeltdag-spikes.
    """
    if len(df) < lookback + exclude_recent:
        # Fallback: bruk hele datasettet
        baseline = df.iloc[:-exclude_recent] if len(df) > exclude_recent else df
    else:
        baseline = df.iloc[-(lookback + exclude_recent):-exclude_recent]

    if baseline.empty:
        last = float(df["Close"].iloc[-1])
        return last * 0.95, last * 1.05

    # 10. og 90. persentil
    support = float(np.percentile(baseline["Low"], 10))
    resistance = float(np.percentile(baseline["High"], 90))
    return support, resistance


def detect_phase(
    df: pd.DataFrame,
    support: float,
    resistance: float,
) -> WyckoffPhase:
    """
    Klassifiser nåværende fase fra prismønster.

    Forenklet algoritme:
    - Markup: pris >resist ×1.02 og siste 20d-trend opp
    - Markdown: pris <support ×0.98 og siste 20d-trend ned
    - Akkumulering: pris i TR og siste 60d-trend flat/svakt opp etter nedgang
    - Distribusjon: pris i TR og siste 60d-trend flat/svakt ned etter oppgang
    """
    if len(df) < 60:
        return "unclear"

    last = float(df["Close"].iloc[-1])

    # Klare breakouts
    if last > resistance * 1.02:
        if _trend_slope(df["Close"].tail(20)) > 0:
            return "markup"
    if last < support * 0.98:
        if _trend_slope(df["Close"].tail(20)) < 0:
            return "markdown"

    # I trading range
    if support <= last <= resistance:
        # Hva skjedde før TR?
        before_tr = df["Close"].iloc[-90:-30] if len(df) >= 90 else df["Close"].iloc[:-30]
        if len(before_tr) > 0:
            before_slope = _trend_slope(before_tr)
            if before_slope < -0.1:
                return "accumulation"  # nedgang før TR
            if before_slope > 0.1:
                return "distribution"  # oppgang før TR

    return "unclear"


def _trend_slope(prices: pd.Series) -> float:
    """Lineær regresjons-helning normalisert til %/dag."""
    if len(prices) < 5:
        return 0.0
    x = np.arange(len(prices))
    slope = np.polyfit(x, prices.values, 1)[0]
    return float(slope / prices.mean() * 100) if prices.mean() > 0 else 0.0


def detect_spring(
    df: pd.DataFrame,
    support: float,
    lookback: int = 5,
) -> bool:
    """
    SPRING: Pris dipper kort under support, men close er tilbake over.
    Volum er gjerne høyt (panic-selling absorbert).

    Krav siste `lookback` dager:
    - En dag har Low < support × 0.99
    - Samme dag har Close > support
    - Volum den dagen >= 1.5× 20d-snitt
    """
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


def detect_markup_breakout(
    df: pd.DataFrame,
    resistance: float,
    lookback: int = 5,
) -> bool:
    """
    MARKUP: Pris bryter over resistance med volum.

    Krav siste `lookback` dager:
    - Close > resistance × 1.01
    - Close ikke mer enn 7% over resistance (fersk breakout)
    - Volum den dagen >= 1.5× 20d-snitt
    """
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
        if (
            close > resistance * 1.01
            and close < resistance * 1.07
            and vol >= avg * 1.5
        ):
            return True
    return False


def days_in_trading_range(
    df: pd.DataFrame,
    support: float,
    resistance: float,
) -> int:
    """Tell antall sammenhengende dager prisen har vært innenfor TR."""
    count = 0
    for close in reversed(df["Close"].values):
        if support <= close <= resistance:
            count += 1
        else:
            break
    return count


def analyze_wyckoff(df: pd.DataFrame) -> WyckoffAnalysis:
    """Hovedanalyse — kjør alle steg."""
    if df.empty:
        return WyckoffAnalysis(
            phase="unclear", support=0.0, resistance=0.0, last_close=0.0,
            in_range=False, spring_detected=False, markup_detected=False,
            range_pct=0.0, days_in_range=0,
        )

    support, resistance = find_support_resistance(df)
    last = float(df["Close"].iloc[-1])
    phase = detect_phase(df, support, resistance)
    in_range = support <= last <= resistance
    spring = detect_spring(df, support)
    markup = detect_markup_breakout(df, resistance)
    range_pct = (resistance - support) / support * 100 if support > 0 else 0
    days = days_in_trading_range(df, support, resistance)

    return WyckoffAnalysis(
        phase=phase,
        support=support,
        resistance=resistance,
        last_close=last,
        in_range=in_range,
        spring_detected=spring,
        markup_detected=markup,
        range_pct=range_pct,
        days_in_range=days,
    )
