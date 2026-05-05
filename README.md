# Smart Money EU Dashboard

Wyckoff/VSA/RS-analyse for europeiske aksjer (Oslo Børs, Norden, STOXX 600).

## Funksjoner

- **3 region-faner**: Oslo, Norden, Europa
- **Selvkonstruerte sektor-benchmarks** (likt-vektet, rebasert til 100)
- **Trippel-RS** for OSE-aksjer (lokal sektor + OSEBX + STOXX 600)
- **VSA-deteksjon**: absorpsjon, no-supply, shakeout, climactic buy
- **Wyckoff-fase-klassifisering**: akkumulering / mark-up / distribusjon / mark-down
- **Spring/Markup-deteksjon** for handlebare breakouts
- **Daglig cron-scan** via GitHub Actions
- **Likviditetsfilter** kalibrert for europeiske volum

## Filstruktur

```
smart-money-eu/
├── app.py                      # Streamlit-hovedapp
├── scanner_core.py             # Signal-motor (ren logikk)
├── scanner_job.py              # GitHub Actions entrypoint
├── core/
│   ├── universe.py             # Ticker-univers (170 aksjer)
│   ├── data.py                 # yfinance-wrapper
│   ├── benchmarks.py           # Sektor-kurver
│   ├── relative_strength.py    # RS-analyse
│   ├── vsa.py                  # VSA-signaler
│   └── wyckoff.py              # Fase-deteksjon
├── tabs/
│   └── tab_region.py           # Generisk region-fane
├── data/
│   └── signals_state.json      # Auto-oppdatert av cron
├── requirements.txt
├── .python-version             # 3.11
└── .github/workflows/
    └── daily_scan.yml          # Cron: 21:05 UTC / 22:05 CET
```

## Lokal kjøring

```cmd
pip install -r requirements.txt

# Kjør én scan manuelt
python scanner_job.py

# Start Streamlit-appen
streamlit run app.py
```

## Deployment til Streamlit Cloud

1. Push til GitHub (eget repo)
2. Gå til https://share.streamlit.io
3. "New app" → velg repo → main branch → app.py
4. Ingen secrets nødvendig (alt bruker yfinance gratis)

## GitHub Actions cron

Workflow kjører automatisk mandag-fredag 21:05 UTC (22:05 norsk vintertid /
23:05 sommertid). For manuell trigger: gå til Actions-fanen → "Daily smart
money scan (EU)" → "Run workflow".

**Viktig**: Repository må gi Actions skrive-tilgang. Sjekk:
Settings → Actions → General → Workflow permissions → "Read and write".

## Signal-kriterier

En aksje får signal-flagg når:
- Likviditetsfilter passert (≥20 MNOK / ≥2 MEUR / ≥1 MGBP daglig snitt)
- Wyckoff-fase = `accumulation` eller `markup`, eller spring/markup detected
- RS-aggregert score > 60
- VSA bullish-score > 60
- Final score = 0.4 × Wyckoff + 0.3 × RS + 0.3 × VSA, terskel 60

## Kalibrering for EU vs US

| Parameter | US (S&P) | EU (denne) | Begrunnelse |
|-----------|----------|------------|-------------|
| Høyt volum | 2.0× snitt | 1.3× snitt | EU-børser viser mindre av reelt volum |
| Ekstrem volum | 3.0× | 1.8× | Samme |
| Liquidity-floor | $1M | varierer per valuta | Mindre likvide markeder |
| Sektor-benchmark | XLE/XLF | egen-konstruert | STOXX-indekser fjernet fra Yahoo |

## Disclaimer

Dette er et **forsknings-/opplæringsverktøy**, ikke et investeringsråd.
Yahoo Finance-data er gratis men ikke garantert nøyaktig. Bruk på egen risiko.
