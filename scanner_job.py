"""
scanner_job.py
==============
Entrypoint for GitHub Actions cron-jobb.

Bruker ABSOLUTTE stier for å unngå working-directory-problemer.
Logger eksplisitt hver fil-operasjon for å forenkle debugging.
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Bruk absolutt sti basert på hvor scanner_job.py ligger
ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT))

from core.data import clear_cache as clear_data_cache, fetch_history
from core.benchmarks import clear_benchmark_cache
from scanner_core import scan_all_regions, save_signals_state
import paper_trading
from paper_trading import (
    load_portfolio,
    save_portfolio,
    daglig_tick,
    beregn_statistikk,
)


# Tving absolutte stier — fungerer uansett hvor scriptet kjøres fra
SIGNALS_PATH = str(ROOT / "data" / "signals_state.json")
PORTFOLIO_PATH = str(ROOT / "data" / "portfolio.json")


def main() -> None:
    print("=== Smart Money EU — daglig scan + paper trading ===", flush=True)
    print(f"  Working dir: {os.getcwd()}", flush=True)
    print(f"  Script root: {ROOT}", flush=True)
    print(f"  Signals path: {SIGNALS_PATH}", flush=True)
    print(f"  Portfolio path: {PORTFOLIO_PATH}", flush=True)

    i_dag_iso = datetime.now(timezone.utc).isoformat()

    clear_data_cache()
    clear_benchmark_cache()

    # ============================================================
    # 1. Signal-scan
    # ============================================================
    print("\n--- Signal-scan ---", flush=True)
    signals = scan_all_regions(period="1y", min_final_score=60.0)
    total = 0
    for region, sigs in signals.items():
        n = len(sigs)
        total += n
        print(f"  {region}: {n} signaler", flush=True)
        for s in sigs[:5]:
            print(f"    {s.symbol:14s} {s.name:25s} score={s.final_score:.0f}", flush=True)
    print(f"\nTotalt {total} signaler funnet", flush=True)

    save_signals_state(signals, path=SIGNALS_PATH)
    print(f"✓ Skrevet til {SIGNALS_PATH}", flush=True)
    print(f"  Filstørrelse: {os.path.getsize(SIGNALS_PATH)} bytes", flush=True)

    # ============================================================
    # 2. Paper trading
    # ============================================================
    print("\n--- Paper trading ---", flush=True)
    print(f"  Leser portfolio fra: {PORTFOLIO_PATH}", flush=True)
    print(f"  Eksisterer fra før?: {os.path.exists(PORTFOLIO_PATH)}", flush=True)

    portfolio = load_portfolio(path=PORTFOLIO_PATH)
    print(f"  Kontanter ved start:    {portfolio['kontanter_nok']:,.0f} NOK".replace(",", " "), flush=True)
    print(f"  Pending ordre ved start: {len(portfolio.get('pending_orders', []))}", flush=True)
    print(f"  Åpne posisjoner:         {len(portfolio.get('open_positions', []))}", flush=True)

    relevante_symboler = set()
    for pos in portfolio.get("open_positions", []):
        relevante_symboler.add(pos["symbol"])
    for ordre in portfolio.get("pending_orders", []):
        relevante_symboler.add(ordre["symbol"])

    alle_signaler = []
    for region_sigs in signals.values():
        for s in region_sigs:
            relevante_symboler.add(s.symbol)
            alle_signaler.append(s.to_dict())

    if relevante_symboler:
        print(f"  Henter siste-dags OHLC for {len(relevante_symboler)} aksjer...", flush=True)
        kursdata_raw = fetch_history(list(relevante_symboler), period="5d", use_cache=True)
        kursdata = {sym: df.tail(1) for sym, df in kursdata_raw.items() if not df.empty}
        print(f"  Fikk kursdata for {len(kursdata)} aksjer", flush=True)
    else:
        kursdata = {}

    log = daglig_tick(
        portfolio=portfolio,
        signaler=alle_signaler,
        kursdata=kursdata,
        i_dag_iso=i_dag_iso,
        min_signal_score=60.0,
    )
    for line in log:
        print(f"  {line}", flush=True)

    # Eksplisitt lagring til absolutt sti
    save_portfolio(portfolio, path=PORTFOLIO_PATH)
    print(f"\n✓ Lagret portefølje til {PORTFOLIO_PATH}", flush=True)
    print(f"  Filstørrelse: {os.path.getsize(PORTFOLIO_PATH)} bytes", flush=True)

    # Verifiser at filen ble skrevet riktig
    import json
    with open(PORTFOLIO_PATH, "r", encoding="utf-8") as f:
        saved = json.load(f)
    print(f"  Verifisert lest tilbake: {len(saved.get('pending_orders', []))} pending ordre", flush=True)

    # Sammendrag
    stats = beregn_statistikk(portfolio)
    print(f"\n--- Portefølje-status ---", flush=True)
    print(f"  Kontanter:        {portfolio['kontanter_nok']:,.0f} NOK".replace(",", " "), flush=True)
    print(f"  Åpne posisjoner:  {len(portfolio['open_positions'])}", flush=True)
    print(f"  Pending ordre:    {len(portfolio['pending_orders'])}", flush=True)
    print(f"  Lukkede trades:   {stats['n_trades']}", flush=True)
    if stats["n_trades"] > 0:
        print(f"  Win rate:         {stats['win_rate']:.1f}%", flush=True)
        print(f"  Profit factor:    {stats['profit_factor']:.2f}", flush=True)
        print(f"  Total P&L:        {stats['total_pnl_nok']:+,.0f} NOK".replace(",", " "), flush=True)

    print("\n✓ Ferdig", flush=True)


if __name__ == "__main__":
    main()
