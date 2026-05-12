"""
core/fx.py
==========
Valutakurs-håndtering for paper trading.

Eier konvertering mellom alle valutaer i universet og NOK
(rapporterings-valutaen).

DEFAULT-KURSER: Hardkodet snitt fra 2025. Brukes hvis fx_rates.json ikke
finnes. Når GitHub Actions kjører kan vi senere oppdatere dette via
yfinance (NOKEUR=X, NOKDKK=X osv.).

Bruk:
    from core.fx import to_nok, get_rate

    # Konverter 100 GBP til NOK
    nok = to_nok(100.0, "GBP")

    # Hent kurs for valuta
    rate = get_rate("EUR")  # = 11.5 (1 EUR = 11.5 NOK)
"""

from __future__ import annotations
import json
import os
from typing import Optional

# Default-kurser (oppdaterbare via data/fx_rates.json)
# Verdier = hvor mange NOK 1 enhet av valutaen er verdt
_DEFAULT_RATES_TO_NOK: dict[str, float] = {
    "NOK": 1.0,
    "SEK": 1.0,    # ~1.0 fra 2024-2026
    "DKK": 1.55,   # ~1.55
    "EUR": 11.5,   # ~11.5
    "GBP": 13.5,   # ~13.5
    "CHF": 12.0,   # ~12.0
    "USD": 10.5,   # ~10.5 (samme som S&P-versjonen for konsistens)
}

_FX_FILE = "data/fx_rates.json"


def _load_rates() -> dict[str, float]:
    """Last fx-kurser fra fil, eller fallback til defaults."""
    if os.path.exists(_FX_FILE):
        try:
            with open(_FX_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Merge med defaults for ev. manglende valutaer
                rates = dict(_DEFAULT_RATES_TO_NOK)
                rates.update(data.get("rates", {}))
                return rates
        except Exception:
            pass
    return dict(_DEFAULT_RATES_TO_NOK)


def get_rate(currency: str) -> float:
    """Hent kurs (NOK per 1 enhet valuta)."""
    rates = _load_rates()
    return rates.get(currency.upper(), 1.0)


def to_nok(amount: float, currency: str) -> float:
    """Konverter beløp fra `currency` til NOK."""
    if currency.upper() == "NOK":
        return amount
    return amount * get_rate(currency)


def from_nok(nok_amount: float, currency: str) -> float:
    """Konverter beløp fra NOK til `currency`."""
    if currency.upper() == "NOK":
        return nok_amount
    rate = get_rate(currency)
    if rate <= 0:
        return 0.0
    return nok_amount / rate


def save_rates(rates: dict[str, float]) -> None:
    """Skriv FX-kurser til fil. Brukes av FX-oppdaterings-jobb."""
    os.makedirs(os.path.dirname(_FX_FILE), exist_ok=True)
    with open(_FX_FILE, "w", encoding="utf-8") as f:
        json.dump({"rates": rates}, f, indent=2)


if __name__ == "__main__":
    print("Default FX-kurser (1 enhet → NOK):")
    rates = _load_rates()
    for ccy, rate in sorted(rates.items()):
        print(f"  {ccy}: {rate:.4f}")
    print(f"\nTest: 100 GBP = {to_nok(100, 'GBP'):.2f} NOK")
    print(f"Test: 1000 NOK = {from_nok(1000, 'EUR'):.2f} EUR")
