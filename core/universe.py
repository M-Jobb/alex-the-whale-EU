"""
core/universe.py
================
Kuratert ticker-univers for europeisk Smart Money Dashboard.

Tre regioner:
- OSLO:   Oslo Børs (mest likvide aksjer)
- NORDIC: Stockholm + København + Helsinki (large/mid cap)
- EUROPE: STOXX 600-komponenter (utvalg per supersektor)

VIKTIG OM SEKTOR-BENCHMARKING
==============================
STOXX-sektorindeksene (^SXEP, ^SX7P osv.) ble fjernet fra Yahoo Finance
i 2024-2025 pga. lisensendringer. Vi konstruerer derfor sektor-benchmarks
selv ved å vekt-snitte komponenter av samme sektor innen hver region.

Dette er faktisk en fordel:
- Full kontroll over benchmark-sammensetning
- Fungerer også for OSE (der ingen offisiell sektor-ETF finnes)
- Konsistent metodologi på tvers av alle tre regioner
- Mindre avhengighet av Yahoo's delisting-luner

Funksjonen `compute_sector_benchmark()` i benchmarks.py bygger disse
kurvene runtime ved å hente data for alle komponenter i en sektor.

NB: Ticker-listene må vedlikeholdes manuelt. Yahoo-symboler endres
sporadisk. Last-verified-dato står øverst i hver liste.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

Region = Literal["OSLO", "NORDIC", "EUROPE"]

# Sektor-taksonomi (forenklet STOXX-supersektor-struktur)
VALID_SECTORS: set[str] = {
    "Energy",
    "Basic Resources",
    "Chemicals",
    "Construction",
    "Industrial Goods",
    "Automobiles",
    "Food Beverage",
    "Personal Household",
    "Health Care",
    "Retail",
    "Media",
    "Travel Leisure",
    "Telecom",
    "Utilities",
    "Banks",
    "Insurance",
    "Financial Services",
    "Technology",
    "Real Estate",
}

# Hovedindekser per region (RS-benchmark) — disse fungerer på Yahoo
REGION_BENCHMARKS: dict[Region, str] = {
    "OSLO":   "OSEBX.OL",      # Oslo Børs Benchmark Index
    "NORDIC": "^OMX",          # OMX Stockholm 30
    "EUROPE": "^STOXX",        # STOXX Europe 600
}

# Sekundærbenchmark for OSE: STOXX 600 (gir europeisk kontekst)
OSLO_SECONDARY_BENCHMARK = "^STOXX"


@dataclass(frozen=True)
class Ticker:
    """En enkelt aksje med metadata."""
    symbol: str           # Yahoo-symbol, f.eks. "EQNR.OL"
    name: str             # Lesbart navn
    sector: str           # Må være i VALID_SECTORS
    currency: str         # NOK, SEK, DKK, EUR, GBP, CHF
    region: Region

    def __post_init__(self):
        if self.sector not in VALID_SECTORS:
            raise ValueError(
                f"Sektor '{self.sector}' for {self.symbol} er ikke gyldig. "
                f"Gyldige: {sorted(VALID_SECTORS)}"
            )


# ============================================================
# OSLO BØRS — kuratert utvalg, kun verifiserte tickere
# Last verified: 2026-04
# Endringslogg fra forrige versjon:
#   - SRBNK.OL -> SB1NO.OL (SpareBank 1 SR-Bank fusjonerte til
#     SpareBank 1 Sør-Norge høst 2024)
#   - CRAYN.OL fjernet (kjøpt opp av Software One 2024)
#   - KAHOT.OL fjernet (privatisert, delistet 2023)
#   - BELCO.OL, GOGL.OL, OLT.OL, XXL.OL fjernet
#     (verifisering feilet, sannsynlig delistet/endret)
# ============================================================
OSLO_TICKERS: list[Ticker] = [
    # Energy
    Ticker("EQNR.OL",  "Equinor",            "Energy", "NOK", "OSLO"),
    Ticker("AKRBP.OL", "Aker BP",            "Energy", "NOK", "OSLO"),
    Ticker("VAR.OL",   "Vår Energi",         "Energy", "NOK", "OSLO"),
    Ticker("DNO.OL",   "DNO",                "Energy", "NOK", "OSLO"),
    Ticker("BWE.OL",   "BW Energy",          "Energy", "NOK", "OSLO"),
    Ticker("TGS.OL",   "TGS",                "Energy", "NOK", "OSLO"),
    Ticker("SUBC.OL",  "Subsea 7",           "Energy", "NOK", "OSLO"),
    Ticker("AKSO.OL",  "Aker Solutions",     "Energy", "NOK", "OSLO"),

    # Food Beverage (sjømat hører hit i STOXX-klassifisering)
    Ticker("MOWI.OL",  "Mowi",               "Food Beverage", "NOK", "OSLO"),
    Ticker("SALM.OL",  "SalMar",             "Food Beverage", "NOK", "OSLO"),
    Ticker("LSG.OL",   "Lerøy Seafood",      "Food Beverage", "NOK", "OSLO"),
    Ticker("BAKKA.OL", "P/F Bakkafrost",     "Food Beverage", "NOK", "OSLO"),
    Ticker("AUSS.OL",  "Austevoll Seafood",  "Food Beverage", "NOK", "OSLO"),
    Ticker("ORK.OL",   "Orkla",              "Food Beverage", "NOK", "OSLO"),

    # Chemicals
    Ticker("YAR.OL",   "Yara International", "Chemicals", "NOK", "OSLO"),
    Ticker("BRG.OL",   "Borregaard",         "Chemicals", "NOK", "OSLO"),
    Ticker("ELK.OL",   "Elkem",              "Chemicals", "NOK", "OSLO"),

    # Basic Resources
    Ticker("NHY.OL",   "Norsk Hydro",        "Basic Resources", "NOK", "OSLO"),

    # Industrial Goods (inkluderer shipping)
    Ticker("KOG.OL",   "Kongsberg Gruppen",  "Industrial Goods", "NOK", "OSLO"),
    Ticker("WAWI.OL",  "Wallenius Wilhelmsen","Industrial Goods", "NOK", "OSLO"),
    Ticker("FRO.OL",   "Frontline",          "Industrial Goods", "NOK", "OSLO"),
    Ticker("HAFNI.OL", "Hafnia",             "Industrial Goods", "NOK", "OSLO"),
    Ticker("ODF.OL",   "Odfjell",            "Industrial Goods", "NOK", "OSLO"),
    Ticker("BWLPG.OL", "BW LPG",             "Industrial Goods", "NOK", "OSLO"),
    Ticker("MPCC.OL",  "MPC Container Ships","Industrial Goods", "NOK", "OSLO"),

    # Banks
    Ticker("DNB.OL",   "DNB Bank",           "Banks", "NOK", "OSLO"),
    Ticker("SB1NO.OL", "SpareBank 1 Sør-Norge","Banks", "NOK", "OSLO"),  # ny etter fusjon
    Ticker("MING.OL",  "SpareBank 1 SMN",    "Banks", "NOK", "OSLO"),
    Ticker("NONG.OL",  "SpareBank 1 Nord-Norge", "Banks", "NOK", "OSLO"),

    # Insurance
    Ticker("GJF.OL",   "Gjensidige Forsikring", "Insurance", "NOK", "OSLO"),
    Ticker("STB.OL",   "Storebrand",         "Insurance", "NOK", "OSLO"),

    # Telecom
    Ticker("TEL.OL",   "Telenor",            "Telecom", "NOK", "OSLO"),

    # Technology
    Ticker("ATEA.OL",  "Atea",               "Technology", "NOK", "OSLO"),
    Ticker("NOD.OL",   "Nordic Semiconductor","Technology", "NOK", "OSLO"),

    # Real Estate
    Ticker("ENTRA.OL", "Entra",              "Real Estate", "NOK", "OSLO"),

    # Retail
    Ticker("EPR.OL",   "Europris",           "Retail", "NOK", "OSLO"),

    # Health Care
    Ticker("NYKD.OL",  "Nykode Therapeutics","Health Care", "NOK", "OSLO"),

    # Utilities
    Ticker("SCATC.OL", "Scatec",             "Utilities", "NOK", "OSLO"),
]


# ============================================================
# NORDIC — Stockholm, København, Helsinki
# Last verified: 2026-04
# Endringslogg:
#   - CGCBV.HE fjernet (Cargotec ble splittet 2024)
# ============================================================
NORDIC_TICKERS: list[Ticker] = [
    # === SVERIGE (.ST) ===
    Ticker("VOLV-B.ST", "Volvo B",           "Industrial Goods", "SEK", "NORDIC"),
    Ticker("ATCO-A.ST", "Atlas Copco A",     "Industrial Goods", "SEK", "NORDIC"),
    Ticker("SAND.ST",   "Sandvik",           "Industrial Goods", "SEK", "NORDIC"),
    Ticker("SKF-B.ST",  "SKF B",             "Industrial Goods", "SEK", "NORDIC"),
    Ticker("ALFA.ST",   "Alfa Laval",        "Industrial Goods", "SEK", "NORDIC"),
    Ticker("SECU-B.ST", "Securitas B",       "Industrial Goods", "SEK", "NORDIC"),
    Ticker("ASSA-B.ST", "ASSA ABLOY B",      "Industrial Goods", "SEK", "NORDIC"),

    Ticker("SEB-A.ST",  "SEB A",             "Banks", "SEK", "NORDIC"),
    Ticker("SHB-A.ST",  "Handelsbanken A",   "Banks", "SEK", "NORDIC"),
    Ticker("SWED-A.ST", "Swedbank A",        "Banks", "SEK", "NORDIC"),
    Ticker("NDA-SE.ST", "Nordea Bank",       "Banks", "SEK", "NORDIC"),

    Ticker("TELIA.ST",  "Telia Company",     "Telecom", "SEK", "NORDIC"),
    Ticker("ERIC-B.ST", "Ericsson B",        "Technology", "SEK", "NORDIC"),

    Ticker("AZN.ST",    "AstraZeneca",       "Health Care", "SEK", "NORDIC"),
    Ticker("GETI-B.ST", "Getinge B",         "Health Care", "SEK", "NORDIC"),

    Ticker("HM-B.ST",   "H&M B",             "Retail", "SEK", "NORDIC"),
    Ticker("EVO.ST",    "Evolution",         "Travel Leisure", "SEK", "NORDIC"),

    Ticker("BALD-B.ST", "Balder B",          "Real Estate", "SEK", "NORDIC"),
    Ticker("CAST.ST",   "Castellum",         "Real Estate", "SEK", "NORDIC"),

    Ticker("BOL.ST",    "Boliden",           "Basic Resources", "SEK", "NORDIC"),
    Ticker("SSAB-A.ST", "SSAB A",            "Basic Resources", "SEK", "NORDIC"),

    # === DANMARK (.CO) ===
    Ticker("NOVO-B.CO", "Novo Nordisk B",    "Health Care", "DKK", "NORDIC"),
    Ticker("GMAB.CO",   "Genmab",            "Health Care", "DKK", "NORDIC"),
    Ticker("DEMANT.CO", "Demant",            "Health Care", "DKK", "NORDIC"),
    Ticker("COLO-B.CO", "Coloplast B",       "Health Care", "DKK", "NORDIC"),

    Ticker("MAERSK-B.CO","A.P. Møller-Mærsk B","Industrial Goods", "DKK", "NORDIC"),
    Ticker("DSV.CO",    "DSV",               "Industrial Goods", "DKK", "NORDIC"),
    Ticker("VWS.CO",    "Vestas Wind Systems","Industrial Goods", "DKK", "NORDIC"),

    Ticker("DANSKE.CO", "Danske Bank",       "Banks", "DKK", "NORDIC"),
    Ticker("CARL-B.CO", "Carlsberg B",       "Food Beverage", "DKK", "NORDIC"),

    # === FINLAND (.HE) ===
    Ticker("NOKIA.HE",  "Nokia",             "Technology", "EUR", "NORDIC"),
    Ticker("KNEBV.HE",  "KONE",              "Industrial Goods", "EUR", "NORDIC"),
    Ticker("WRT1V.HE",  "Wärtsilä",          "Industrial Goods", "EUR", "NORDIC"),
    Ticker("UPM.HE",    "UPM-Kymmene",       "Basic Resources", "EUR", "NORDIC"),
    Ticker("STERV.HE",  "Stora Enso R",      "Basic Resources", "EUR", "NORDIC"),
    Ticker("NDA-FI.HE", "Nordea Bank (FI)",  "Banks", "EUR", "NORDIC"),
    Ticker("NESTE.HE",  "Neste",             "Energy", "EUR", "NORDIC"),
]


# ============================================================
# EUROPE — STOXX 600-komponenter
# Last verified: 2026-04
# Endringslogg:
#   - ING.AS -> INGA.AS (Yahoo bruker INGA-symbolet)
#   - STM.PA -> STMPA.PA (Yahoo-symbol oppdatert)
# ============================================================
EUROPE_TICKERS: list[Ticker] = [
    # Energy
    Ticker("SHEL.L",    "Shell",             "Energy", "GBP", "EUROPE"),
    Ticker("BP.L",      "BP",                "Energy", "GBP", "EUROPE"),
    Ticker("TTE.PA",    "TotalEnergies",     "Energy", "EUR", "EUROPE"),
    Ticker("ENI.MI",    "Eni",               "Energy", "EUR", "EUROPE"),
    Ticker("REP.MC",    "Repsol",            "Energy", "EUR", "EUROPE"),
    Ticker("EQNR.OL",   "Equinor",           "Energy", "NOK", "EUROPE"),

    # Banks
    Ticker("HSBA.L",    "HSBC",              "Banks", "GBP", "EUROPE"),
    Ticker("BNP.PA",    "BNP Paribas",       "Banks", "EUR", "EUROPE"),
    Ticker("SAN.MC",    "Banco Santander",   "Banks", "EUR", "EUROPE"),
    Ticker("ISP.MI",    "Intesa Sanpaolo",   "Banks", "EUR", "EUROPE"),
    Ticker("UCG.MI",    "UniCredit",         "Banks", "EUR", "EUROPE"),
    Ticker("DBK.DE",    "Deutsche Bank",     "Banks", "EUR", "EUROPE"),
    Ticker("BARC.L",    "Barclays",          "Banks", "GBP", "EUROPE"),
    Ticker("LLOY.L",    "Lloyds Banking",    "Banks", "GBP", "EUROPE"),
    Ticker("INGA.AS",   "ING Groep",         "Banks", "EUR", "EUROPE"),  # var ING.AS
    Ticker("BBVA.MC",   "BBVA",              "Banks", "EUR", "EUROPE"),

    # Insurance
    Ticker("ALV.DE",    "Allianz",           "Insurance", "EUR", "EUROPE"),
    Ticker("CS.PA",     "AXA",               "Insurance", "EUR", "EUROPE"),
    Ticker("ZURN.SW",   "Zurich Insurance",  "Insurance", "CHF", "EUROPE"),
    Ticker("G.MI",      "Generali",          "Insurance", "EUR", "EUROPE"),
    Ticker("PRU.L",     "Prudential",        "Insurance", "GBP", "EUROPE"),

    # Health Care
    Ticker("NOVO-B.CO", "Novo Nordisk",      "Health Care", "DKK", "EUROPE"),
    Ticker("ROG.SW",    "Roche",             "Health Care", "CHF", "EUROPE"),
    Ticker("NOVN.SW",   "Novartis",          "Health Care", "CHF", "EUROPE"),
    Ticker("AZN.L",     "AstraZeneca",       "Health Care", "GBP", "EUROPE"),
    Ticker("GSK.L",     "GSK",               "Health Care", "GBP", "EUROPE"),
    Ticker("SAN.PA",    "Sanofi",            "Health Care", "EUR", "EUROPE"),
    Ticker("BAYN.DE",   "Bayer",             "Health Care", "EUR", "EUROPE"),

    # Technology
    Ticker("ASML.AS",   "ASML",              "Technology", "EUR", "EUROPE"),
    Ticker("SAP.DE",    "SAP",               "Technology", "EUR", "EUROPE"),
    Ticker("STMPA.PA",  "STMicroelectronics","Technology", "EUR", "EUROPE"),  # var STM.PA
    Ticker("IFX.DE",    "Infineon",          "Technology", "EUR", "EUROPE"),
    Ticker("CAP.PA",    "Capgemini",         "Technology", "EUR", "EUROPE"),
    Ticker("DSY.PA",    "Dassault Systèmes", "Technology", "EUR", "EUROPE"),

    # Industrial Goods
    Ticker("SIE.DE",    "Siemens",           "Industrial Goods", "EUR", "EUROPE"),
    Ticker("AIR.PA",    "Airbus",            "Industrial Goods", "EUR", "EUROPE"),
    Ticker("SU.PA",     "Schneider Electric","Industrial Goods", "EUR", "EUROPE"),
    Ticker("PHIA.AS",   "Philips",           "Industrial Goods", "EUR", "EUROPE"),
    Ticker("MT.AS",     "ArcelorMittal",     "Basic Resources", "EUR", "EUROPE"),

    # Automobiles
    Ticker("MBG.DE",    "Mercedes-Benz",     "Automobiles", "EUR", "EUROPE"),
    Ticker("BMW.DE",    "BMW",               "Automobiles", "EUR", "EUROPE"),
    Ticker("VOW3.DE",   "Volkswagen",        "Automobiles", "EUR", "EUROPE"),
    Ticker("STLAM.MI",  "Stellantis",        "Automobiles", "EUR", "EUROPE"),
    Ticker("RACE.MI",   "Ferrari",           "Automobiles", "EUR", "EUROPE"),

    # Food Beverage
    Ticker("NESN.SW",   "Nestlé",            "Food Beverage", "CHF", "EUROPE"),
    Ticker("ULVR.L",    "Unilever",          "Food Beverage", "GBP", "EUROPE"),
    Ticker("ABI.BR",    "AB InBev",          "Food Beverage", "EUR", "EUROPE"),
    Ticker("DGE.L",     "Diageo",            "Food Beverage", "GBP", "EUROPE"),

    # Personal Household (luxury)
    Ticker("MC.PA",     "LVMH",              "Personal Household", "EUR", "EUROPE"),
    Ticker("OR.PA",     "L'Oréal",           "Personal Household", "EUR", "EUROPE"),
    Ticker("RMS.PA",    "Hermès",            "Personal Household", "EUR", "EUROPE"),
    Ticker("CFR.SW",    "Richemont",         "Personal Household", "CHF", "EUROPE"),
    Ticker("KER.PA",    "Kering",            "Personal Household", "EUR", "EUROPE"),

    # Telecom
    Ticker("DTE.DE",    "Deutsche Telekom",  "Telecom", "EUR", "EUROPE"),
    Ticker("VOD.L",     "Vodafone",          "Telecom", "GBP", "EUROPE"),
    Ticker("ORA.PA",    "Orange",            "Telecom", "EUR", "EUROPE"),
    Ticker("TEF.MC",    "Telefónica",        "Telecom", "EUR", "EUROPE"),

    # Utilities
    Ticker("IBE.MC",    "Iberdrola",         "Utilities", "EUR", "EUROPE"),
    Ticker("ENEL.MI",   "Enel",              "Utilities", "EUR", "EUROPE"),
    Ticker("EOAN.DE",   "E.ON",              "Utilities", "EUR", "EUROPE"),
    Ticker("ENGI.PA",   "Engie",             "Utilities", "EUR", "EUROPE"),

    # Basic Resources
    Ticker("RIO.L",     "Rio Tinto",         "Basic Resources", "GBP", "EUROPE"),
    Ticker("GLEN.L",    "Glencore",          "Basic Resources", "GBP", "EUROPE"),
    Ticker("AAL.L",     "Anglo American",    "Basic Resources", "GBP", "EUROPE"),

    # Real Estate
    Ticker("VNA.DE",    "Vonovia",           "Real Estate", "EUR", "EUROPE"),
    Ticker("LAND.L",    "Land Securities",   "Real Estate", "GBP", "EUROPE"),
    Ticker("BLND.L",    "British Land",      "Real Estate", "GBP", "EUROPE"),
    Ticker("URW.PA",    "Unibail-Rodamco",   "Real Estate", "EUR", "EUROPE"),

    # Retail
    Ticker("ITX.MC",    "Inditex",           "Retail", "EUR", "EUROPE"),
    Ticker("AD.AS",     "Ahold Delhaize",    "Retail", "EUR", "EUROPE"),
    Ticker("KGF.L",     "Kingfisher",        "Retail", "GBP", "EUROPE"),
    Ticker("NXT.L",     "Next",              "Retail", "GBP", "EUROPE"),

    # Chemicals
    Ticker("LIN.DE",    "Linde",             "Chemicals", "EUR", "EUROPE"),
    Ticker("BAS.DE",    "BASF",              "Chemicals", "EUR", "EUROPE"),
    Ticker("AI.PA",     "Air Liquide",       "Chemicals", "EUR", "EUROPE"),
    Ticker("AKZA.AS",   "Akzo Nobel",        "Chemicals", "EUR", "EUROPE"),

    # Construction
    Ticker("DG.PA",     "Vinci",             "Construction", "EUR", "EUROPE"),
    Ticker("EN.PA",     "Bouygues",          "Construction", "EUR", "EUROPE"),
    Ticker("ACS.MC",    "ACS Construcciones","Construction", "EUR", "EUROPE"),
    Ticker("HOLN.SW",   "Holcim",            "Construction", "CHF", "EUROPE"),
    Ticker("FER.MC",    "Ferrovial",         "Construction", "EUR", "EUROPE"),

    # Financial Services
    Ticker("DB1.DE",    "Deutsche Börse",    "Financial Services", "EUR", "EUROPE"),
    Ticker("AMUN.PA",   "Amundi",            "Financial Services", "EUR", "EUROPE"),
    Ticker("EDEN.PA",   "Edenred",           "Financial Services", "EUR", "EUROPE"),
    Ticker("III.L",     "3i Group",          "Financial Services", "GBP", "EUROPE"),

    # Media
    Ticker("REL.L",     "RELX",              "Media", "GBP", "EUROPE"),
    Ticker("WKL.AS",    "Wolters Kluwer",    "Media", "EUR", "EUROPE"),
    Ticker("VIV.PA",    "Vivendi",           "Media", "EUR", "EUROPE"),
    Ticker("PSON.L",    "Pearson",           "Media", "GBP", "EUROPE"),
    Ticker("PUB.PA",    "Publicis",          "Media", "EUR", "EUROPE"),

    # Travel Leisure
    Ticker("IAG.L",     "International Airlines Group", "Travel Leisure", "GBP", "EUROPE"),
    Ticker("CPG.L",     "Compass Group",     "Travel Leisure", "GBP", "EUROPE"),
    Ticker("WTB.L",     "Whitbread",         "Travel Leisure", "GBP", "EUROPE"),
    Ticker("LHA.DE",    "Lufthansa",         "Travel Leisure", "EUR", "EUROPE"),
    Ticker("FLTR.L",    "Flutter Entertainment","Travel Leisure", "GBP", "EUROPE"),
]


# ============================================================
# Tilgangsfunksjoner
# ============================================================

def get_universe(region: Region) -> list[Ticker]:
    """Returner alle tickere for en region."""
    if region == "OSLO":
        return OSLO_TICKERS
    if region == "NORDIC":
        return NORDIC_TICKERS
    if region == "EUROPE":
        return EUROPE_TICKERS
    raise ValueError(f"Ukjent region: {region}")


def get_tickers_by_sector(region: Region, sector: str) -> list[Ticker]:
    """Returner alle tickere i en gitt region+sektor."""
    return [t for t in get_universe(region) if t.sector == sector]


def get_sectors_in_region(region: Region) -> list[str]:
    """Returner unike sektorer som faktisk har aksjer i regionen."""
    return sorted({t.sector for t in get_universe(region)})


def get_region_benchmark(region: Region) -> str:
    """Returner Yahoo-ticker for hovedindeks i regionen."""
    return REGION_BENCHMARKS[region]


def get_currency_for_ticker(symbol: str) -> str | None:
    """Slå opp valuta for et Yahoo-symbol. Returnerer None hvis ukjent."""
    for region in ("OSLO", "NORDIC", "EUROPE"):
        for t in get_universe(region):  # type: ignore[arg-type]
            if t.symbol == symbol:
                return t.currency
    return None


# ============================================================
# Sanity-check ved import
# ============================================================
def _sanity_check() -> None:
    """Validerer at alle tickere har gyldig sektor-mapping."""
    for region in ("OSLO", "NORDIC", "EUROPE"):
        for t in get_universe(region):  # type: ignore[arg-type]
            assert t.sector in VALID_SECTORS, (
                f"{t.symbol}: ugyldig sektor '{t.sector}'"
            )
    assert set(REGION_BENCHMARKS) == {"OSLO", "NORDIC", "EUROPE"}


_sanity_check()


if __name__ == "__main__":
    from collections import Counter
    for region in ("OSLO", "NORDIC", "EUROPE"):
        tickers = get_universe(region)  # type: ignore[arg-type]
        sectors = get_sectors_in_region(region)  # type: ignore[arg-type]
        print(f"\n=== {region} ===")
        print(f"Antall tickere: {len(tickers)}")
        print(f"Antall sektorer dekket: {len(sectors)}")
        print(f"Benchmark: {get_region_benchmark(region)}")  # type: ignore[arg-type]
        counts = Counter(t.sector for t in tickers)
        for sector, n in sorted(counts.items(), key=lambda x: -x[1]):
            print(f"  {sector:25s} {n:3d}")
