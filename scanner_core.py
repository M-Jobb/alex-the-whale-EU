"""
scanner_core.py
===============
Hovedmotor for daglig signalskan.

Setter sammen:
- Trippel-RS (relative_strength.py)
- VSA-rapport (vsa.py)
- Wyckoff-analyse (wyckoff.py)
- Likviditetsfilter (data.py)

Returnerer ranked liste av smart-money-kandidater per region.

KRITERIER FOR ET SIGNAL:
1. Aksjen passerer likviditetsfilter
2. Wyckoff-fase er 'accumulation' eller 'markup' (eller spring/markup detected)
3. RS-score > 60 (utkonkurrerer minst én benchmark)
4. VSA bullish_score > 60

Score-aggregering:
    final_score = 0.4 × wyckoff_score + 0.3 × rs_score + 0.3 × vsa_score
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
from typing import Optional

import pandas as pd

from core.universe import Region, Ticker, get_universe
from core.data import fetch_history, fetch_one, passes_liquidity_filter
from core.relative_strength import compute_triple_rs, TripleRS
from core.vsa import vsa_report
from core.wyckoff import analyze_wyckoff, WyckoffAnalysis


# ============================================================
# Datatyper
# ============================================================

@dataclass
class Signal:
    """Et komplett smart money-signal for én aksje."""
    symbol: str
    name: str
    sector: str
    region: str
    currency: str

    last_close: float
    avg_turnover: float

    wyckoff_phase: str
    support: float
    resistance: float
    spring: bool
    markup: bool
    days_in_range: int

    rs_sector: Optional[float]       # 20d % endring
    rs_region: Optional[float]
    rs_secondary: Optional[float]
    rs_aggregate_score: float
    triple_rs_strong: bool

    vsa_bullish: float
    vsa_obv_rising: bool
    vsa_recent_count: int

    final_score: float
    signal_date: str  # ISO

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# Hjelpere
# ============================================================

def _wyckoff_score(w: WyckoffAnalysis) -> float:
    """Konverter Wyckoff-analyse til 0-100 score."""
    s = 50.0
    if w.phase == "accumulation":
        s += 20
    elif w.phase == "markup":
        s += 15
    elif w.phase == "distribution":
        s -= 15
    elif w.phase == "markdown":
        s -= 25
    if w.spring_detected:
        s += 25  # Spring er sterkt signal
    if w.markup_detected:
        s += 20
    return float(max(0, min(100, s)))


# ============================================================
# Hovedfunksjon
# ============================================================

def scan_region(
    region: Region,
    period: str = "1y",
    min_final_score: float = 60.0,
    apply_liquidity_filter: bool = True,
) -> list[Signal]:
    """
    Skann alle aksjer i regionen og returner rangerte signaler.

    Returnerer kun aksjer med final_score >= min_final_score.
    Sortert synkende på final_score.
    """
    universe = get_universe(region)
    symbols = [t.symbol for t in universe]

    # Forhandshent alle aksjer i én batch (cached)
    print(f"[{region}] Henter data for {len(symbols)} aksjer...", flush=True)
    fetch_history(symbols, period=period, use_cache=True)

    signals: list[Signal] = []

    for ticker in universe:
        try:
            df = fetch_one(ticker.symbol, period=period, use_cache=True)
            if df is None or df.empty or len(df) < 60:
                continue

            # Likviditet
            if apply_liquidity_filter and not passes_liquidity_filter(df, ticker.symbol):
                continue

            # Wyckoff
            w = analyze_wyckoff(df)
            wyckoff_sc = _wyckoff_score(w)

            # VSA
            v = vsa_report(df)
            vsa_sc = v["bullish_score"]

            # Trippel-RS
            triple = compute_triple_rs(ticker, period=period)
            if triple is None:
                rs_sc = 50.0
                rs_sector_chg = rs_region_chg = rs_sec_chg = None
                triple_strong = False
            else:
                rs_sc = triple.aggregate_score()
                rs_sector_chg = triple.vs_sector.change_20d if triple.vs_sector else None
                rs_region_chg = triple.vs_region_index.change_20d if triple.vs_region_index else None
                rs_sec_chg = triple.vs_secondary.change_20d if triple.vs_secondary else None
                triple_strong = triple.is_triple_strong()

            # Aggregert score
            final_sc = 0.4 * wyckoff_sc + 0.3 * rs_sc + 0.3 * vsa_sc

            if final_sc < min_final_score:
                continue

            from core.data import average_daily_turnover
            sig = Signal(
                symbol=ticker.symbol,
                name=ticker.name,
                sector=ticker.sector,
                region=ticker.region,
                currency=ticker.currency,
                last_close=float(df["Close"].iloc[-1]),
                avg_turnover=average_daily_turnover(df),
                wyckoff_phase=w.phase,
                support=w.support,
                resistance=w.resistance,
                spring=w.spring_detected,
                markup=w.markup_detected,
                days_in_range=w.days_in_range,
                rs_sector=rs_sector_chg,
                rs_region=rs_region_chg,
                rs_secondary=rs_sec_chg,
                rs_aggregate_score=rs_sc,
                triple_rs_strong=triple_strong,
                vsa_bullish=vsa_sc,
                vsa_obv_rising=v["obv_rising"],
                vsa_recent_count=v["recent_signal_count"],
                final_score=final_sc,
                signal_date=df.index[-1].strftime("%Y-%m-%d"),
            )
            signals.append(sig)

        except Exception as e:
            print(f"  Feil ved {ticker.symbol}: {type(e).__name__}: {e}", flush=True)
            continue

    signals.sort(key=lambda s: s.final_score, reverse=True)
    return signals


def scan_all_regions(
    period: str = "1y",
    min_final_score: float = 60.0,
) -> dict[str, list[Signal]]:
    """Kjør scan på alle 3 regioner."""
    return {
        region: scan_region(region, period=period, min_final_score=min_final_score)
        for region in ("OSLO", "NORDIC", "EUROPE")
    }


# ============================================================
# State-fil-håndtering (for daglig cron-jobb)
# ============================================================

def save_signals_state(
    signals_per_region: dict[str, list[Signal]],
    path: str = "data/signals_state.json",
) -> None:
    """Lagre alle signaler til JSON for Streamlit-app å lese."""
    import os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    state = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regions": {
            region: [s.to_dict() for s in sigs]
            for region, sigs in signals_per_region.items()
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)


def load_signals_state(path: str = "data/signals_state.json") -> Optional[dict]:
    """Les inn lagret state. Returnerer None hvis ikke eksisterer."""
    import os
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
