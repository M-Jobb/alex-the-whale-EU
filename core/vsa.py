"""
core/vsa.py
===========
Volume Spread Analysis (VSA) for europeiske aksjer.

KJERNE-IDÉEN: Smart money etterlater spor i volum-pris-forholdet.
Når institusjoner akkumulerer, ser vi:

- ABSORPSJON: Høyt volum + liten prisnedgang = noen kjøper alt som selges
- NO-SUPPLY: Lavt volum på pull-back = ingen vil selge mer
- SHAKEOUT: Falsk breakdown med høyt volum, så raskt opp = stop-loss-svetting
- CLIMACTIC ACTION: Ekstremvolum ved bunn = panikk-overgivelse, smart money kjøper

KALIBRERING FOR EUROPEISK VOLUM:
S&P 500-aksjer har 60-70% av reelt volum synlig på primærbørs.
Europeiske aksjer (særlig OSE) har bare ~50% pga MTF-er som Cboe Europe.
Vi bruker derfor LAVERE volum-multipler enn standard VSA-litteratur:
- "Høyt volum" = 1.3× snitt (ikke 2× som på US)
- "Ekstremvolum" = 1.8× snitt (ikke 3×)

OBV (On-Balance Volume) bruker vi som confirmation: hvis OBV stiger
mens prisen er flat, er det subtil akkumulering.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


# ============================================================
# Konstanter (kalibrerte for EU)
# ============================================================

VOLUME_LOOKBACK = 20         # Rullerende vindu for volum-snitt
HIGH_VOLUME_MULT = 1.3       # 1.3× snitt = "høyt volum"
EXTREME_VOLUME_MULT = 1.8    # 1.8× snitt = "ekstremvolum"
NARROW_RANGE_PCT = 0.5       # Daglig range <50% av nylig snitt = smal


# ============================================================
# Datatyper
# ============================================================

@dataclass
class VSASignal:
    """En VSA-deteksjon på en gitt dag."""
    date: pd.Timestamp
    signal_type: str  # 'absorption', 'no_supply', 'shakeout', 'climactic_buy', 'no_demand'
    strength: float   # 0-1, hvor sterk indikasjonen er
    note: str


# ============================================================
# Hjelpefunksjoner
# ============================================================

def compute_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """
    On-Balance Volume — kumulert volum etter prisretning.

    Pris opp → legg til volum. Pris ned → trekk fra volum.
    OBV-trend før prisen rører seg er klassisk smart-money-tegn.
    """
    direction = np.sign(close.diff()).fillna(0)
    return (volume * direction).fillna(0).cumsum()


def is_obv_rising(obv: pd.Series, lookback: int = 20) -> bool:
    """OBV stiger over `lookback` dager (lineær regresjons-helning > 0)."""
    if len(obv) < lookback:
        return False
    recent = obv.tail(lookback)
    x = np.arange(len(recent))
    slope = np.polyfit(x, recent.values, 1)[0]
    return slope > 0


def daily_range_pct(df: pd.DataFrame) -> pd.Series:
    """Dagens range (high-low) som % av close."""
    return ((df["High"] - df["Low"]) / df["Close"]) * 100


def is_narrow_range(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
    """Boolean serie: dagens range < 50% av snittet over lookback."""
    rng = daily_range_pct(df)
    avg_rng = rng.rolling(lookback).mean()
    return rng < (avg_rng * NARROW_RANGE_PCT)


def relative_volume(df: pd.DataFrame, lookback: int = VOLUME_LOOKBACK) -> pd.Series:
    """Volum / snittvolum siste 20d. Høy = mange ganger normalt."""
    avg_vol = df["Volume"].rolling(lookback).mean()
    return df["Volume"] / avg_vol


# ============================================================
# Signal-deteksjon
# ============================================================

def detect_absorption(df: pd.DataFrame, lookback: int = 60) -> list[VSASignal]:
    """
    ABSORPSJON: Høyt volum, prisen burde falle, men gjør det knapt.
    Tolkning: Noen kjøper alt som selges. Klassisk Wyckoff-tegn.

    Krav:
    - Volum >= HIGH_VOLUME_MULT × snitt
    - Close ned <2% fra forrige (eller opp)
    - Range bred (ekte battle-stang, ikke bare doji)
    """
    if len(df) < VOLUME_LOOKBACK + 5:
        return []

    rel_vol = relative_volume(df)
    rng_pct = daily_range_pct(df)
    avg_rng = rng_pct.rolling(VOLUME_LOOKBACK).mean()
    pct_change = df["Close"].pct_change() * 100

    signals = []
    recent = df.tail(lookback)
    for date in recent.index:
        if pd.isna(rel_vol.loc[date]) or pd.isna(avg_rng.loc[date]):
            continue
        rv = rel_vol.loc[date]
        change = pct_change.loc[date]
        rng = rng_pct.loc[date]
        avg = avg_rng.loc[date]

        # Høyvolum + bred range + ikke ned mer enn 2%
        if rv >= HIGH_VOLUME_MULT and rng > avg and change > -2.0:
            strength = min(1.0, (rv - 1.0) / 1.5)
            signals.append(VSASignal(
                date=date,
                signal_type="absorption",
                strength=strength,
                note=f"vol={rv:.1f}× snitt, range={rng:.1f}%, close {change:+.1f}%",
            ))
    return signals


def detect_no_supply(df: pd.DataFrame, lookback: int = 60) -> list[VSASignal]:
    """
    NO-SUPPLY: Pull-back på lavt volum + smal range.
    Tolkning: Selgerne er tomme. Neste oppgang møter ikke motstand.

    Krav:
    - Close lavere enn forrige
    - Volum < 0.7 × snitt
    - Smal range
    """
    if len(df) < VOLUME_LOOKBACK + 5:
        return []

    rel_vol = relative_volume(df)
    narrow = is_narrow_range(df)
    pct_change = df["Close"].pct_change() * 100

    signals = []
    recent = df.tail(lookback)
    for date in recent.index:
        if pd.isna(rel_vol.loc[date]):
            continue
        if (
            pct_change.loc[date] < 0
            and rel_vol.loc[date] < 0.7
            and narrow.loc[date]
        ):
            strength = float(np.clip((0.7 - rel_vol.loc[date]) / 0.5, 0, 1))
            signals.append(VSASignal(
                date=date,
                signal_type="no_supply",
                strength=strength,
                note=f"vol={rel_vol.loc[date]:.1f}× snitt, smal range, ned {pct_change.loc[date]:.1f}%",
            ))
    return signals


def detect_shakeout(df: pd.DataFrame, lookback: int = 60) -> list[VSASignal]:
    """
    SHAKEOUT: Falsk breakdown — pris bryter under nylig støtte intradag,
    men close tilbake innenfor + høyt volum. Stop-loss-jakt fra smart money.

    Krav:
    - Low under forrige 20d-min
    - Close >= forrige 20d-min × 0.99
    - Volum >= HIGH_VOLUME_MULT × snitt
    """
    if len(df) < 30:
        return []

    rolling_min = df["Low"].rolling(20).min().shift(1)
    rel_vol = relative_volume(df)

    signals = []
    recent = df.tail(lookback)
    for date in recent.index:
        if pd.isna(rolling_min.loc[date]) or pd.isna(rel_vol.loc[date]):
            continue
        prev_min = rolling_min.loc[date]
        low = df.loc[date, "Low"]
        close = df.loc[date, "Close"]
        rv = rel_vol.loc[date]

        if low < prev_min and close >= prev_min * 0.99 and rv >= HIGH_VOLUME_MULT:
            strength = min(1.0, (rv - 1.0) / 1.5)
            signals.append(VSASignal(
                date=date,
                signal_type="shakeout",
                strength=strength,
                note=f"low brøt 20d-min, close tilbake, vol={rv:.1f}×",
            ))
    return signals


def detect_climactic_buy(df: pd.DataFrame, lookback: int = 60) -> list[VSASignal]:
    """
    CLIMACTIC BUY: Ekstremvolum + bred range nedoverdag, men close i øvre halvdel.
    Tolkning: Panikk-salg absorberes av smart money. Bunn ofte nær.

    Krav:
    - Volum >= EXTREME_VOLUME_MULT × snitt
    - Range > 1.5× snitt-range
    - Close i øvre halvdel av dagens range
    """
    if len(df) < VOLUME_LOOKBACK + 5:
        return []

    rel_vol = relative_volume(df)
    rng_pct = daily_range_pct(df)
    avg_rng = rng_pct.rolling(VOLUME_LOOKBACK).mean()

    signals = []
    recent = df.tail(lookback)
    for date in recent.index:
        if pd.isna(rel_vol.loc[date]) or pd.isna(avg_rng.loc[date]):
            continue
        rv = rel_vol.loc[date]
        high = df.loc[date, "High"]
        low = df.loc[date, "Low"]
        close = df.loc[date, "Close"]
        if high == low:
            continue
        close_pos = (close - low) / (high - low)  # 0=bottom, 1=top
        rng = rng_pct.loc[date]
        avg = avg_rng.loc[date]

        if rv >= EXTREME_VOLUME_MULT and rng > avg * 1.5 and close_pos > 0.5:
            strength = min(1.0, (rv - 1.0) / 2.0)
            signals.append(VSASignal(
                date=date,
                signal_type="climactic_buy",
                strength=strength,
                note=f"vol={rv:.1f}× (ekstrem), close i topp {close_pos:.0%} av range",
            ))
    return signals


# ============================================================
# Aggregert VSA-rapport
# ============================================================

def vsa_report(df: pd.DataFrame, lookback: int = 60) -> dict:
    """
    Kjør alle VSA-detektorer og oppsummer.

    Returnerer dict med:
        signals: liste av alle VSASignal-objekter
        obv_rising: bool
        recent_signal_count: antall signaler siste 10 dager
        bullish_score: 0-100 aggregert score
    """
    if len(df) < VOLUME_LOOKBACK + 10:
        return {
            "signals": [],
            "obv_rising": False,
            "recent_signal_count": 0,
            "bullish_score": 50.0,
        }

    all_signals = (
        detect_absorption(df, lookback)
        + detect_no_supply(df, lookback)
        + detect_shakeout(df, lookback)
        + detect_climactic_buy(df, lookback)
    )

    # OBV-trend
    obv = compute_obv(df["Close"], df["Volume"])
    obv_up = is_obv_rising(obv)

    # Tell signaler siste 10 dager
    cutoff = df.index[-1] - pd.Timedelta(days=14)
    recent = [s for s in all_signals if s.date >= cutoff]

    # Aggregert bullish score
    score = 50.0
    if obv_up:
        score += 15
    score += min(20, len(recent) * 5)  # opp til +20 fra signaler
    score += sum(s.strength * 5 for s in recent[:3])  # topp 3 styrker

    return {
        "signals": sorted(all_signals, key=lambda s: s.date, reverse=True),
        "obv_rising": obv_up,
        "recent_signal_count": len(recent),
        "bullish_score": float(np.clip(score, 0, 100)),
    }
