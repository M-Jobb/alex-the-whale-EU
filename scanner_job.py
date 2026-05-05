"""
scanner_job.py
==============
Entrypoint for GitHub Actions cron-jobb.

Kjøres daglig (mandag-fredag) kl 22:05 CET via .github/workflows/daily_scan.yml.

Henter siste prisdata for alle aksjer, kjører signal-scan på alle 3 regioner,
og lagrer resultatet til data/signals_state.json.

Streamlit-appen leser denne fila og viser signalene.
"""

import sys
from pathlib import Path

# Sørg for at lokale imports fungerer
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from core.data import clear_cache as clear_data_cache
from core.benchmarks import clear_benchmark_cache
from scanner_core import scan_all_regions, save_signals_state


def main() -> None:
    print("=== Smart Money EU — daglig scan ===", flush=True)

    # Tøm caches for å garantere ferskt data
    clear_data_cache()
    clear_benchmark_cache()

    # Kjør scan
    signals = scan_all_regions(period="1y", min_final_score=60.0)

    # Logg sammendrag
    total = 0
    for region, sigs in signals.items():
        n = len(sigs)
        total += n
        print(f"  {region}: {n} signaler", flush=True)
        for s in sigs[:5]:  # topp 5 per region
            print(f"    {s.symbol:14s} {s.name:25s} score={s.final_score:.0f}", flush=True)

    print(f"\nTotalt {total} signaler skrevet til data/signals_state.json", flush=True)

    # Lagre state
    save_signals_state(signals)
    print("✓ Ferdig", flush=True)


if __name__ == "__main__":
    main()
