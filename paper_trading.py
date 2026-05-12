"""
paper_trading.py
================
Paper trading-motor for Smart Money EU.

Følger samme regler som S&P-versjonen, men tilpasset multi-currency:
- Felix Prehn Buy Zone-strategi for inngang
- Risiko-basert sizing (2.5% per initial, pyramidering 1.25% + 0.625%)
- Wyckoff initial stop, ATR trailing
- Multi-currency P&L i NOK (FX håndtert via core/fx.py)

PORTEFØLJE-STRUKTUR (lagret i data/portfolio.json):
{
    "created_at":        ISO-dato,
    "last_updated":      ISO-dato,
    "start_kapital_nok": 100000.0,
    "kontanter_nok":     <flytende>,
    "pending_orders":    [...],   # limit-ordre som venter på fill
    "open_positions":    [...],   # aktive posisjoner
    "closed_trades":     [...],   # historikk
    "missed_extended":   [...],   # signaler som gikk over Buy Zone
    "equity_curve":      [...]    # daglig snapshot for grafen
}

Verdier i lokal valuta lagres som `_local`-felt, alt i NOK som `_nok`-felt.
"""

from __future__ import annotations
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd

from core.fx import to_nok, get_rate
from core.universe import get_currency_for_ticker


# ============================================================
# Konstanter (Felix Prehn-strategi, samme som US-versjon)
# ============================================================

START_KAPITAL_NOK       = 100_000.0
RISIKO_PROSENT          = 0.025          # 2.5% av equity som risiko per initial trade
PYRAMID_RISIKO          = [0.0125, 0.00625]  # 1.25% og 0.625% for add-on 1 og 2
MAX_KAPITAL_PROSENT     = 0.20           # Hard kapital-tak per posisjon: 20%
MIN_SHARE_KAPITAL_PCT   = 0.25           # Tillat 1 aksje hvis innenfor 25% av equity (for dyre)

PYRAMID_TRIGGERE_ATR    = [1.0, 2.0]     # add-on triggere
BUY_ZONE_MAX_PCT        = 0.05           # M = S × 1.05
ORDRE_GYLDIGHET_DAGER   = 5              # limit-ordre utløper etter 5 dager
TRAILING_ATR_AKTIVERING = 1.0            # switch til trailing når pris > avg_entry + 1×ATR
TRAILING_ATR_AVSTAND    = 2.0            # trailing stop = high_water − 2×ATR

# Kostnader
SLIPPAGE_PCT            = 0.001          # 0.1% slippage på alle fills
KURTASJE_NOK            = 99.0           # per trade (kjøp eller salg, hver pyramide)

# Filer
PORTFOLIO_FILE          = "data/portfolio.json"


# ============================================================
# Hjelpere
# ============================================================

def empty_portfolio() -> dict:
    """Opprett tom portefølje."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "created_at":        now,
        "last_updated":      now,
        "start_kapital_nok": START_KAPITAL_NOK,
        "kontanter_nok":     START_KAPITAL_NOK,
        "pending_orders":    [],
        "open_positions":    [],
        "closed_trades":     [],
        "missed_extended":   [],
        "equity_curve":      [],
    }


def load_portfolio(path: str = PORTFOLIO_FILE) -> dict:
    """Last portefølje fra fil; opprett tom hvis den ikke finnes."""
    if not os.path.exists(path):
        return empty_portfolio()
    with open(path, "r", encoding="utf-8") as f:
        p = json.load(f)
    # Bakoverkompatibilitet: garanter alle nøkler
    for key in ("pending_orders", "open_positions", "closed_trades",
                "missed_extended", "equity_curve"):
        p.setdefault(key, [])
    return p


def save_portfolio(p: dict, path: str = PORTFOLIO_FILE) -> None:
    """Skriv portefølje til fil."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    p["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(p, f, indent=2, ensure_ascii=False, default=str)


def total_equity_nok(portfolio: dict, live_kurser_local: dict[str, float]) -> float:
    """
    Beregn samlet equity i NOK = kontanter + alle åpne posisjoner verdsatt
    til siste kurs konvertert via FX.

    live_kurser_local: dict {symbol: pris i LOKAL valuta}
    """
    equity = portfolio["kontanter_nok"]
    for pos in portfolio["open_positions"]:
        sym = pos["symbol"]
        ccy = pos["currency"]
        price_local = live_kurser_local.get(sym, pos["avg_entry_local"])
        verdi_local = price_local * pos["shares"]
        equity += to_nok(verdi_local, ccy)
    return float(equity)


# ============================================================
# Sizing
# ============================================================

@dataclass
class SizingResult:
    shares: int
    cost_nok: float
    method: str  # 'risk_based', 'capital_capped', 'min_one_share', 'rejected'


def beregn_posisjon_storrelse(
    equity_nok: float,
    entry_local: float,
    stop_local: float,
    currency: str,
    risiko_prosent: float = RISIKO_PROSENT,
) -> SizingResult:
    """
    Risiko-basert sizing med kapital-tak og min-1-aksje-safety.

    1. Risiko-basert: antall aksjer slik at (entry - stop) × shares = X% av equity
    2. Kapital-tak: maks 20% av equity per posisjon
    3. Min 1 aksje: tillat én aksje hvis innenfor 25% av equity (for dyre aksjer)
    """
    if entry_local <= 0 or stop_local >= entry_local:
        return SizingResult(0, 0.0, "rejected")

    # Konverter alt til NOK for beregning
    entry_nok = to_nok(entry_local, currency)
    stop_nok = to_nok(stop_local, currency)
    risiko_per_aksje_nok = entry_nok - stop_nok

    # Risiko-basert kapital
    risiko_kapital_nok = equity_nok * risiko_prosent
    shares_risk = int(risiko_kapital_nok / risiko_per_aksje_nok)

    # Kapital-tak
    max_kapital_nok = equity_nok * MAX_KAPITAL_PROSENT
    shares_cap = int(max_kapital_nok / entry_nok)

    if shares_risk == 0:
        # Sjekk om vi kan tillate min 1 aksje
        cost_one_share = entry_nok
        if cost_one_share <= equity_nok * MIN_SHARE_KAPITAL_PCT:
            return SizingResult(1, cost_one_share, "min_one_share")
        return SizingResult(0, 0.0, "rejected")

    if shares_risk > shares_cap:
        if shares_cap == 0:
            # Kapital-tak gir 0 aksjer — sjekk min-1-share-pathen
            cost_one_share = entry_nok
            if cost_one_share <= equity_nok * MIN_SHARE_KAPITAL_PCT:
                return SizingResult(1, cost_one_share, "min_one_share")
            return SizingResult(0, 0.0, "rejected")
        return SizingResult(shares_cap, shares_cap * entry_nok, "capital_capped")

    return SizingResult(shares_risk, shares_risk * entry_nok, "risk_based")


def beregn_atr_estimate(entry: float, stop: float) -> float:
    """
    Approksimer ATR fra Wyckoff stop-distansen.

    Stop-distansen i Wyckoff er ofte 2-3× ATR. Vi antar 2× som default,
    så ATR ≈ (entry - stop) / 2. Brukes til pyramide-triggere og trailing.
    """
    if entry <= 0 or stop >= entry:
        return entry * 0.02  # fallback: 2% ATR-estimat
    return max((entry - stop) / 2, entry * 0.01)


# ============================================================
# Steg 1: Opprett pending ordre fra signaler
# ============================================================

def opprett_pending_ordre_fra_signaler(
    portfolio: dict,
    signals: list[dict],
    i_dag_iso: str,
    min_score: float = 60.0,
) -> list[str]:
    """
    Filter signaler og opprett limit-ordre.

    Signal-format (fra scanner_core.Signal.to_dict()):
        {
            "symbol", "name", "sector", "region", "currency",
            "last_close", "support", "resistance", "final_score",
            "spring", "markup", "wyckoff_phase",
            ...
        }

    Vi oppretter en limit-ordre per signal som:
    - Har final_score >= min_score
    - Har spring eller markup detected (handlebart event)
    - Ikke allerede har en åpen posisjon eller pending ordre
    """
    log = []
    eksisterende_symboler = {p["symbol"] for p in portfolio["open_positions"]}
    eksisterende_symboler |= {o["symbol"] for o in portfolio["pending_orders"]}

    for sig in signals:
        symbol = sig.get("symbol")
        if not symbol:
            continue
        if symbol in eksisterende_symboler:
            continue
        if sig.get("final_score", 0) < min_score:
            continue
        if not (sig.get("spring") or sig.get("markup")):
            continue

        # Bestem entry og stop fra signalet
        # Markup: entry = resistance × 1.005 (nettopp brutt), stop = support × 0.98
        # Spring: entry = nåværende close (over støtte), stop = support × 0.96
        support = sig.get("support", 0)
        resistance = sig.get("resistance", 0)
        last_close = sig.get("last_close", 0)
        currency = sig.get("currency", "NOK")

        if sig.get("markup"):
            entry = resistance * 1.005
            stop = support * 0.98
            signal_type = "Markup"
        elif sig.get("spring"):
            entry = last_close
            stop = support * 0.96
            signal_type = "Spring"
        else:
            continue

        # Target: 2:1 R:R som default
        risk = entry - stop
        if risk <= 0:
            continue
        target = entry + risk * 2.0
        atr_est = beregn_atr_estimate(entry, stop)

        ordre = {
            "symbol":            symbol,
            "name":              sig.get("name", symbol),
            "sector":            sig.get("sector", ""),
            "region":            sig.get("region", ""),
            "currency":          currency,
            "signal_type":       signal_type,
            "signal_pris_local": float(entry),
            "stop_local":        float(stop),
            "target_local":      float(target),
            "atr_local":         float(atr_est),
            "opprettet_dato":    i_dag_iso,
            "utloper_dato":      (
                datetime.fromisoformat(i_dag_iso.replace("Z", "+00:00"))
                + timedelta(days=ORDRE_GYLDIGHET_DAGER)
            ).isoformat(),
            "signal_snapshot": {
                "score":         sig.get("final_score", 0),
                "rs_score":      sig.get("rs_aggregate_score", 0),
                "vsa_score":     sig.get("vsa_bullish", 0),
                "wyckoff_phase": sig.get("wyckoff_phase", ""),
                "triple_rs":     sig.get("triple_rs_strong", False),
                "obv_rising":    sig.get("vsa_obv_rising", False),
                "dato":          sig.get("signal_date", ""),
            },
        }
        portfolio["pending_orders"].append(ordre)
        log.append(
            f"📋 Pending ordre opprettet: {symbol} ({signal_type}) "
            f"S={entry:.2f} stop={stop:.2f} target={target:.2f} {currency}"
        )
        eksisterende_symboler.add(symbol)

    return log


# ============================================================
# Steg 2: Behandle pending ordre (daglig fill-sjekk + Buy Zone)
# ============================================================

def behandle_pending_orders(
    portfolio: dict,
    kursdata: dict[str, pd.DataFrame],   # {symbol: dagens OHLC i lokal valuta}
    i_dag_iso: str,
) -> list[str]:
    """
    Sjekk hver pending ordre mot dagens OHLC. Tre tilstander:

    🎯 LIMIT-FYLL: Low ≤ S → kjøp på S (med slippage)
    ⚡ BUY ZONE: S < Open ≤ M (M = S × 1.05) → kjøp på Open
    🚫 MISSED-EXTENDED: Open > M → skip og flytt til missed_extended

    Hvis ingen av disse: la ordren stå til utløp.
    """
    log = []
    nye_pending = []

    for ordre in portfolio["pending_orders"]:
        symbol = ordre["symbol"]
        S = ordre["signal_pris_local"]
        M = S * (1 + BUY_ZONE_MAX_PCT)

        # Sjekk utløp
        utloper = datetime.fromisoformat(ordre["utloper_dato"].replace("Z", "+00:00"))
        nu = datetime.fromisoformat(i_dag_iso.replace("Z", "+00:00"))
        if nu > utloper:
            log.append(f"⏰ {symbol} ordre utløpt (ingen fill innen {ORDRE_GYLDIGHET_DAGER} dager)")
            continue

        # Hent dagens OHLC
        df = kursdata.get(symbol)
        if df is None or df.empty:
            nye_pending.append(ordre)
            continue

        # Bruk siste rad som "i dag"
        last = df.iloc[-1]
        try:
            day_open = float(last["Open"])
            day_high = float(last["High"])
            day_low = float(last["Low"])
            day_close = float(last["Close"])
        except (KeyError, ValueError, TypeError):
            nye_pending.append(ordre)
            continue

        # Tre fyll-tilstander i prioritert rekkefølge:
        if day_low <= S:
            # 🎯 Limit-fyll: pris dippet ned til (eller under) S i løpet av dagen
            fill_pris = S
            fill_type = "limit"
        elif S < day_open <= M:
            # ⚡ Buy Zone-fyll: dagen åpnet over S men innenfor Buy Zone
            fill_pris = day_open
            fill_type = "market_zone"
        elif day_open > M:
            # 🚫 Missed-extended: åpner over Buy Zone-grense
            gap_pct = (day_open / S - 1) * 100
            log.append(
                f"🚫 {symbol} missed-extended: open={day_open:.2f} > M={M:.2f} (+{gap_pct:.1f}%)"
            )
            portfolio["missed_extended"].append({
                "symbol":     symbol,
                "signal_pris": S,
                "open_pris":  day_open,
                "gap_pct":    gap_pct,
                "dato":       i_dag_iso,
                "signal_snapshot": ordre.get("signal_snapshot", {}),
            })
            continue
        else:
            # Ingen fyll i dag (low aldri under S, open ikke i Buy Zone), la ordren stå
            nye_pending.append(ordre)
            continue

        # Anvend slippage
        fill_pris *= (1 + SLIPPAGE_PCT)

        # Sizing
        currency = ordre["currency"]
        equity = total_equity_nok(portfolio, {})  # uten åpne — vi har ikke fylt ennå
        sizing = beregn_posisjon_storrelse(
            equity, fill_pris, ordre["stop_local"], currency
        )

        if sizing.shares == 0:
            log.append(f"❌ {symbol} sizing rejected: for liten equity / dårlig R")
            continue

        # Konverter kostnad til NOK
        kostnad_nok = sizing.cost_nok + KURTASJE_NOK

        if kostnad_nok > portfolio["kontanter_nok"]:
            log.append(f"❌ {symbol} for lite kontanter: {kostnad_nok:.0f} > {portfolio['kontanter_nok']:.0f} NOK")
            continue

        portfolio["kontanter_nok"] -= kostnad_nok

        # Opprett ny posisjon
        new_pos = {
            "symbol":            symbol,
            "name":              ordre.get("name", symbol),
            "sector":            ordre.get("sector", ""),
            "region":            ordre.get("region", ""),
            "currency":          currency,
            "signal_type":       ordre["signal_type"],
            "fill_type":         fill_type,
            "signal_pris_local": S,
            "initial_entry_local": fill_pris,
            "avg_entry_local":   fill_pris,
            "shares":            sizing.shares,
            "kostnad_nok":       kostnad_nok,
            "sizing_method":     sizing.method,
            "fills": [{
                "date":       i_dag_iso,
                "price_local": fill_pris,
                "shares":     sizing.shares,
                "cost_nok":   kostnad_nok,
                "type":       "initial",
                "fill_type":  fill_type,
            }],
            "opened_at":         i_dag_iso,
            "initial_stop_local": ordre["stop_local"],
            "current_stop_local": ordre["stop_local"],
            "target_local":      ordre["target_local"],
            "atr_local":         ordre["atr_local"],
            "stop_type":         "wyckoff",
            "high_water_local":  fill_pris,
            "low_water_local":   fill_pris,
            "pyramid_count":     0,
            "skipped_pyramids":  [],
            "signal_snapshot":   ordre.get("signal_snapshot", {}),
            # MAE/MFE i lokal valuta
            "mae_local":         fill_pris,
            "mfe_local":         fill_pris,
            "mae_pct":           0.0,
            "mfe_pct":           0.0,
            "mae_dato":          i_dag_iso,
            "mfe_dato":          i_dag_iso,
            # What-if original-stop
            "orig_stop_truffet": False,
            "orig_stop_dato":    None,
            "orig_stop_pnl_pct": None,
            "orig_target_truffet": False,
            "orig_target_dato":    None,
            "orig_target_pnl_pct": None,
            "stop_historie":     [{
                "date":      i_dag_iso,
                "stop_local": ordre["stop_local"],
                "type":      "initial_wyckoff",
            }],
        }
        portfolio["open_positions"].append(new_pos)

        emoji = "🎯" if fill_type == "limit" else "⚡"
        log.append(
            f"{emoji} {symbol} fyllt: {sizing.shares} aksjer @ {fill_pris:.2f} {currency} "
            f"({sizing.method}, kostnad {kostnad_nok:.0f} NOK)"
        )

    portfolio["pending_orders"] = nye_pending
    return log


# ============================================================
# Steg 3: Behandle åpne posisjoner (daglig oppdatering)
# ============================================================

def behandle_open_positions(
    portfolio: dict,
    kursdata: dict[str, pd.DataFrame],
    i_dag_iso: str,
) -> list[str]:
    """
    Daglig oppdatering for hver åpen posisjon:

    1. Oppdater MAE/MFE
    2. What-if mot original-stop/target
    3. Pyramide-trigger?
    4. Trailing stop-switch?
    5. Stop-loss eller target truffet?
    """
    log = []
    fortsatt_apne = []

    for pos in portfolio["open_positions"]:
        symbol = pos["symbol"]
        df = kursdata.get(symbol)
        if df is None or df.empty:
            fortsatt_apne.append(pos)
            continue

        try:
            last = df.iloc[-1]
            day_high = float(last["High"])
            day_low = float(last["Low"])
            day_close = float(last["Close"])
        except (KeyError, ValueError, TypeError):
            fortsatt_apne.append(pos)
            continue

        currency = pos["currency"]
        initial_entry = pos["initial_entry_local"]

        # === MAE/MFE-oppdatering ===
        if day_low < pos["mae_local"]:
            pos["mae_local"] = day_low
            pos["mae_pct"] = (day_low / initial_entry - 1) * 100
            pos["mae_dato"] = i_dag_iso
        if day_high > pos["mfe_local"]:
            pos["mfe_local"] = day_high
            pos["mfe_pct"] = (day_high / initial_entry - 1) * 100
            pos["mfe_dato"] = i_dag_iso

        # === High water (for trailing) ===
        if day_high > pos["high_water_local"]:
            pos["high_water_local"] = day_high

        # === What-if original stop ===
        if not pos.get("orig_stop_truffet"):
            if day_low <= pos["initial_stop_local"]:
                pos["orig_stop_truffet"] = True
                pos["orig_stop_dato"] = i_dag_iso
                pos["orig_stop_pnl_pct"] = (pos["initial_stop_local"] / initial_entry - 1) * 100
        if not pos.get("orig_target_truffet"):
            if day_high >= pos["target_local"]:
                pos["orig_target_truffet"] = True
                pos["orig_target_dato"] = i_dag_iso
                pos["orig_target_pnl_pct"] = (pos["target_local"] / initial_entry - 1) * 100

        # === Pyramide-trigger ===
        for idx, mult in enumerate(PYRAMID_TRIGGERE_ATR):
            if pos["pyramid_count"] > idx:
                continue  # allerede pyramidert
            trigger_pris = initial_entry + mult * pos["atr_local"]
            if day_high >= trigger_pris:
                # Forsøk pyramide
                pyr_risiko = PYRAMID_RISIKO[idx]
                equity_now = total_equity_nok(portfolio, {symbol: day_close})
                sizing = beregn_posisjon_storrelse(
                    equity_now, trigger_pris, pos["current_stop_local"], currency,
                    risiko_prosent=pyr_risiko,
                )
                if sizing.shares == 0:
                    pos["skipped_pyramids"].append({
                        "trigger_dato":  i_dag_iso,
                        "trigger_pris":  trigger_pris,
                        "grunn":         f"sizing rejected ({sizing.method})",
                    })
                    continue
                pyramide_pris = trigger_pris * (1 + SLIPPAGE_PCT)
                kostnad_nok = sizing.cost_nok + KURTASJE_NOK
                if kostnad_nok > portfolio["kontanter_nok"]:
                    pos["skipped_pyramids"].append({
                        "trigger_dato":  i_dag_iso,
                        "trigger_pris":  trigger_pris,
                        "grunn":         "for lite kontanter",
                    })
                    continue
                portfolio["kontanter_nok"] -= kostnad_nok

                # Oppdater vektet snitt-entry
                tot_shares = pos["shares"] + sizing.shares
                pos["avg_entry_local"] = (
                    (pos["avg_entry_local"] * pos["shares"]
                     + pyramide_pris * sizing.shares) / tot_shares
                )
                pos["shares"] = tot_shares
                pos["kostnad_nok"] += kostnad_nok
                pos["pyramid_count"] += 1
                pos["fills"].append({
                    "date":       i_dag_iso,
                    "price_local": pyramide_pris,
                    "shares":     sizing.shares,
                    "cost_nok":   kostnad_nok,
                    "type":       f"pyramid_{idx+1}",
                })
                log.append(
                    f"➕ {symbol} pyramide #{idx+1}: +{sizing.shares} aksjer @ "
                    f"{pyramide_pris:.2f} {currency} (snitt nå {pos['avg_entry_local']:.2f})"
                )

        # === Trailing stop-switch ===
        if pos["stop_type"] == "wyckoff":
            switch_pris = pos["avg_entry_local"] + TRAILING_ATR_AKTIVERING * pos["atr_local"]
            if day_close > switch_pris:
                pos["stop_type"] = "trailing_atr"
                new_stop = pos["high_water_local"] - TRAILING_ATR_AVSTAND * pos["atr_local"]
                # Trailing stop kan kun gå opp, ikke ned
                if new_stop > pos["current_stop_local"]:
                    pos["current_stop_local"] = new_stop
                    pos["stop_historie"].append({
                        "date":      i_dag_iso,
                        "stop_local": new_stop,
                        "type":      "trailing_atr_initial",
                    })
                    log.append(
                        f"🔄 {symbol} switch til trailing ATR. Ny stop: {new_stop:.2f} {currency}"
                    )

        # === Trailing stop-oppdatering (kun oppover) ===
        if pos["stop_type"] == "trailing_atr":
            new_stop = pos["high_water_local"] - TRAILING_ATR_AVSTAND * pos["atr_local"]
            if new_stop > pos["current_stop_local"]:
                pos["current_stop_local"] = new_stop
                pos["stop_historie"].append({
                    "date":      i_dag_iso,
                    "stop_local": new_stop,
                    "type":      "trailing_atr_update",
                })

        # === Exit-sjekk ===
        # Target truffet?
        if day_high >= pos["target_local"]:
            _luk_posisjon(portfolio, pos, pos["target_local"], "target", i_dag_iso, log)
            continue
        # Stop truffet?
        if day_low <= pos["current_stop_local"]:
            _luk_posisjon(portfolio, pos, pos["current_stop_local"], "stop_loss", i_dag_iso, log)
            continue

        fortsatt_apne.append(pos)

    portfolio["open_positions"] = fortsatt_apne
    return log


def _luk_posisjon(
    portfolio: dict,
    pos: dict,
    exit_pris_local: float,
    reason: str,
    i_dag_iso: str,
    log: list[str],
) -> None:
    """Selg posisjon og flytt til closed_trades."""
    currency = pos["currency"]
    shares = pos["shares"]

    # Anvend slippage
    if reason == "stop_loss":
        exit_pris_local *= (1 - SLIPPAGE_PCT)  # selger litt under stop
    else:
        exit_pris_local *= (1 - SLIPPAGE_PCT)  # litt under target også

    proceeds_local = exit_pris_local * shares
    proceeds_nok = to_nok(proceeds_local, currency) - KURTASJE_NOK
    portfolio["kontanter_nok"] += proceeds_nok

    # P&L
    pnl_nok = proceeds_nok - pos["kostnad_nok"]
    pnl_pct = pnl_nok / pos["kostnad_nok"] * 100 if pos["kostnad_nok"] else 0
    pnl_pct_local = (exit_pris_local / pos["avg_entry_local"] - 1) * 100

    closed = dict(pos)
    closed["exit_pris_local"] = exit_pris_local
    closed["exit_proceeds_nok"] = proceeds_nok
    closed["pnl_nok"] = pnl_nok
    closed["pnl_pct"] = pnl_pct
    closed["pnl_pct_local"] = pnl_pct_local
    closed["reason"] = reason
    closed["closed_at"] = i_dag_iso

    portfolio["closed_trades"].append(closed)

    emoji = "🎯" if reason == "target" else "⛔"
    log.append(
        f"{emoji} {pos['symbol']} LUKKET ({reason}) @ {exit_pris_local:.2f} {currency} → "
        f"P&L {pnl_nok:+,.0f} NOK ({pnl_pct:+.2f}%)".replace(",", " ")
    )


# ============================================================
# Steg 4: Daglig portefølje-tick + statistikk
# ============================================================

def daglig_tick(
    portfolio: dict,
    signaler: list[dict],
    kursdata: dict[str, pd.DataFrame],
    i_dag_iso: str,
    min_signal_score: float = 60.0,
) -> list[str]:
    """
    Hoved-entry for daglig kjøring:
    1. Behandle pending ordre (fill/utløp/missed)
    2. Behandle åpne posisjoner (pyramide/trailing/exit)
    3. Opprett nye pending fra dagens signaler
    4. Skriv til equity_curve
    """
    log = []
    log += behandle_pending_orders(portfolio, kursdata, i_dag_iso)
    log += behandle_open_positions(portfolio, kursdata, i_dag_iso)
    log += opprett_pending_ordre_fra_signaler(portfolio, signaler, i_dag_iso, min_signal_score)

    # Snapshot for equity-kurven
    live_kurser = {}
    for pos in portfolio["open_positions"]:
        df = kursdata.get(pos["symbol"])
        if df is not None and not df.empty:
            live_kurser[pos["symbol"]] = float(df["Close"].iloc[-1])
    equity = total_equity_nok(portfolio, live_kurser)
    portfolio["equity_curve"].append({
        "date":   i_dag_iso[:10],
        "equity_nok": equity,
        "n_open": len(portfolio["open_positions"]),
        "n_pending": len(portfolio["pending_orders"]),
    })
    return log


def beregn_statistikk(portfolio: dict) -> dict:
    """Sammendrag av historisk performance."""
    closed = portfolio["closed_trades"]
    if not closed:
        return {
            "n_trades":      0,
            "n_wins":        0,
            "n_losses":      0,
            "win_rate":      0.0,
            "avg_win_nok":   0.0,
            "avg_loss_nok":  0.0,
            "profit_factor": 0.0,
            "total_pnl_nok": 0.0,
            "n_missed":      len(portfolio.get("missed_extended", [])),
        }

    wins = [t for t in closed if t["pnl_nok"] > 0]
    losses = [t for t in closed if t["pnl_nok"] <= 0]
    sum_win = sum(t["pnl_nok"] for t in wins)
    sum_loss = abs(sum(t["pnl_nok"] for t in losses))

    return {
        "n_trades":      len(closed),
        "n_wins":        len(wins),
        "n_losses":      len(losses),
        "win_rate":      len(wins) / len(closed) * 100 if closed else 0,
        "avg_win_nok":   sum_win / len(wins) if wins else 0,
        "avg_loss_nok":  sum_loss / len(losses) if losses else 0,
        "profit_factor": sum_win / sum_loss if sum_loss > 0 else 0,
        "total_pnl_nok": sum(t["pnl_nok"] for t in closed),
        "n_missed":      len(portfolio.get("missed_extended", [])),
    }
