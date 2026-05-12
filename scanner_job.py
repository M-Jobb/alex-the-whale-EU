"""
scanner_job.py
==============
Entrypoint for GitHub Actions cron-jobb.

Workflow:
1. Tøm caches (ferskt Yahoo-data)
2. Kjør signal-scan på alle 3 regioner
3. Lagre signaler til data/signals_state.json
4. Kjør paper trading daglig tick
5. Lagre portefølje til data/portfolio.json
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from core.data import clear_cache as clear_data_cache, fetch_history
from core.benchmarks import clear_benchmark_cache
from scanner_core import scan_all_regions, save_signals_state
from paper_trading import (
    load_portfolio,
    save_portfolio,
    daglig_tick,
    beregn_statistikk,
)


def main() -> None:
    print("=== Smart Money EU — daglig scan + paper trading ===", flush=True)
    i_dag_iso = datetime.now(timezone.utc).isoformat()

    # 1. Tøm caches
    clear_data_cache()
    clear_benchmark_cache()

    # 2. Signal-scan
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
    save_signals_state(signals)
    print("✓ Skrevet til data/signals_state.json", flush=True)

    # 3. Bygg kursdata for paper trading
    print("\n--- Paper trading ---", flush=True)
    portfolio = load_portfolio()

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
    else:
        kursdata = {}

    # 4. Daglig tick
    log = daglig_tick(
        portfolio=portfolio,
        signaler=alle_signaler,
        kursdata=kursdata,
        i_dag_iso=i_dag_iso,
        min_signal_score=60.0,
    )
    for line in log:
        print(f"  {line}", flush=True)

    # 5. Lagre
    save_portfolio(portfolio)

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
