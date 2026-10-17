"""Damodaran industry data — Return on Capital by Sector (US).

Supplies the TERMINAL (perpetuity) ROIC ceiling used by the valuation engine.
Damodaran's argument: competition erodes excess returns, so in stable growth a
firm converges toward what its INDUSTRY can sustain — not toward one global
constant. This module replaces the old flat ROIC_TERMINAL = 15% with the real
industry figure from his published dataset.

Which number we use, and why: **"Normalized ROIC (last 10 years)"**.

A perpetuity assumption needs a through-the-cycle number, and "normalized over
10 years" is the definition of one. The single-year column was tried first (as
min(single-year, normalized), to be conservative) and it was wrong twice over:

  * the two series disagree violently — Chemical (Basic) 3.72% vs 23.40%,
    Drugs (Biotechnology) 3.53% vs 21.60%, Precious Metals 25.45% vs 7.24% —
    so a min() picks whichever series is more distorted this year, and a fair
    value could swing ~50% on his next annual update with no company news;
  * a single-year INDUSTRY AGGREGATE is sum(EBIT)/sum(IC) over every filer in
    the bucket. "Drugs (Biotechnology)" holds 496 firms, most pre-revenue and
    burning cash, so 3.53% describes clinical-stage startups — not a profitable
    biotech. Applied to REGN (own ROIC 10.9%) it forced the terminal ROIC down
    to the cost-of-capital floor and moved fair value by 51 percentage points.

Trade-off accepted: the normalized column is not lease/R&D adjusted, so it sits
on a slightly different basis than the engine's own ROIC and reads a little
generous. That is the safe direction for a CEILING — it can only ever restrain
a company, never gift it anything: the engine applies it as
max(WACC, min(own_ROIC, sector_cap, half_fade)), and in practice half_fade or
the firm's own ROIC binds first for all but a handful of names, and a firm earning
below its industry is still held to its own number.

Network is optional. The bundled FALLBACK snapshot (his January 2026 analysis)
is used whenever the fetch or the cache is unavailable, so a refresh never
fabricates and never fails because a university web server is down.
"""
import json
import logging
import os
import re
import time
from html.parser import HTMLParser

log = logging.getLogger(__name__)

ROC_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/roc.html"
CACHE_FILE = "damodaran_roc.json"
# his dataset is rebuilt once a year, so a month-long TTL still refreshes well
# inside one publication cycle while keeping refreshes off his server
TTL_DAYS = 30

ADJ_HEADER = "lease & r&d adjusted after-tax roic"
NORM_HEADER = "normalized roic (last 10 years)"
NAME_HEADER = "industry name"

# --- bundled snapshot: Damodaran "Return on Capital by Sector (US)", analysis
# date January 2026. Format: industry | lease&R&D-adj after-tax ROIC | normalized
# 10y ROIC (both %). Used when the live table cannot be fetched.
FALLBACK_RAW = """
Advertising|27.72|33.31
Aerospace/Defense|16.01|12.70
Air Transport|7.93|11.01
Apparel|15.77|19.75
Auto & Truck|2.25|5.53
Auto Parts|8.98|19.29
Beverage (Alcoholic)|15.74|20.20
Beverage (Soft)|29.03|24.61
Broadcasting|14.57|18.77
Building Materials|20.16|22.64
Business & Consumer Services|28.30|30.96
Cable TV|12.06|16.06
Chemical (Basic)|3.72|23.40
Chemical (Diversified)|4.05|12.41
Chemical (Specialty)|10.95|15.38
Coal & Related Energy|-4.76|4.39
Computer Services|26.35|37.44
Computers/Peripherals|44.76|49.78
Construction Supplies|16.71|16.21
Diversified|14.82|15.53
Drugs (Biotechnology)|3.53|21.60
Drugs (Pharmaceutical)|16.95|23.52
Education|15.94|15.26
Electrical Equipment|15.60|15.70
Electronics (Consumer & Office)|-8.35|-9.25
Electronics (General)|17.91|17.47
Engineering/Construction|25.32|14.26
Entertainment|12.42|14.69
Environmental & Waste Services|31.75|22.94
Farming/Agriculture|7.28|10.14
Food Processing|15.95|21.75
Food Wholesalers|17.84|17.07
Furn/Home Furnishings|10.59|17.82
Green & Renewable Energy|3.64|1.82
Healthcare Products|16.98|16.36
Healthcare Support Services|31.19|41.07
Heathcare Information and Technology|13.72|21.84
Homebuilding|14.91|18.38
Hospitals/Healthcare Facilities|22.16|18.29
Hotel/Gaming|14.58|10.14
Household Products|34.42|36.93
Information Services|22.17|23.93
Insurance (General)|44.51|27.91
Insurance (Life)|10.77|8.35
Insurance (Prop/Cas.)|18.49|10.19
Investments & Asset Management|14.25|5.18
Machinery|24.41|26.59
Metals & Mining|27.04|13.04
Office Equipment & Services|18.26|22.23
Oil/Gas (Integrated)|8.45|8.04
Oil/Gas (Production and Exploration)|13.73|5.21
Oil/Gas Distribution|12.35|7.88
Oilfield Svcs/Equip.|11.94|12.42
Packaging & Container|14.82|18.11
Paper/Forest Products|10.41|14.87
Power|6.92|6.45
Precious Metals|25.45|7.24
Publishing & Newspapers|17.63|15.87
R.E.I.T.|3.20|2.99
Real Estate (Development)|7.04|3.62
Real Estate (General/Diversified)|5.19|3.85
Real Estate (Operations & Services)|5.94|10.53
Recreation|8.02|15.73
Reinsurance|10.14|10.27
Restaurant/Dining|18.36|22.92
Retail (Automotive)|12.19|15.26
Retail (Building Supply)|34.95|39.33
Retail (Distributors)|16.11|16.12
Retail (General)|13.59|15.18
Retail (Grocery and Food)|6.76|12.29
Retail (REITs)|5.16|4.56
Retail (Special Lines)|21.54|24.98
Rubber& Tires|3.17|9.81
Semiconductor|27.23|20.53
Semiconductor Equip|28.40|34.44
Shipbuilding & Marine|11.99|10.90
Shoe|20.93|36.10
Software (Entertainment)|27.02|24.21
Software (Internet)|3.43|4.40
Software (System & Application)|29.32|22.95
Steel|6.72|20.88
Telecom (Wireless)|10.37|8.61
Telecom. Equipment|25.47|33.23
Telecom. Services|12.04|17.38
Tobacco|63.08|77.69
Transportation|13.00|16.94
Transportation (Railroads)|14.24|18.19
Trucking|9.30|15.06
Utility (General)|5.99|6.37
Utility (Water)|7.25|7.04
Total Market (without financials)|15.07|14.92
"""

# Damodaran classifies companies into the industries above; FMP's 11 GICS-style
# sectors are far too coarse (one "Healthcare" bucket holds LLY at 17.0% and
# REGN at 3.5%). Explicit per-ticker mapping wins; the sector map is the fallback
# for anything not listed. Keys must match FALLBACK_RAW names exactly.
INDUSTRY_BY_TICKER = {
    "MSFT": "Software (System & Application)", "ORCL": "Software (System & Application)",
    "GOOGL": "Advertising", "GOOG": "Advertising", "META": "Advertising",
    "NVDA": "Semiconductor", "AVGO": "Semiconductor", "TSM": "Semiconductor",
    "AMD": "Semiconductor", "MU": "Semiconductor", "INTC": "Semiconductor",
    "ASML": "Semiconductor Equip", "AMAT": "Semiconductor Equip", "LRCX": "Semiconductor Equip",
    "LLY": "Drugs (Pharmaceutical)", "PFE": "Drugs (Pharmaceutical)",
    "ABBV": "Drugs (Pharmaceutical)", "NVO": "Drugs (Pharmaceutical)",
    "MRK": "Drugs (Pharmaceutical)", "JNJ": "Drugs (Pharmaceutical)",
    "REGN": "Drugs (Biotechnology)", "VRTX": "Drugs (Biotechnology)",
    "AMGN": "Drugs (Biotechnology)", "MRNA": "Drugs (Biotechnology)",
    "UNH": "Healthcare Support Services", "ELV": "Healthcare Support Services",
    "CI": "Healthcare Support Services", "CVS": "Healthcare Support Services",
    "HIMS": "Heathcare Information and Technology",   # sic — his spelling
    "TMDX": "Healthcare Products", "ISRG": "Healthcare Products",
    "AXON": "Aerospace/Defense", "RKLB": "Aerospace/Defense", "LMT": "Aerospace/Defense",
    "TSLA": "Auto & Truck", "GM": "Auto & Truck", "F": "Auto & Truck",
    "CELH": "Beverage (Soft)", "KO": "Beverage (Soft)", "PEP": "Beverage (Soft)",
    "MELI": "Retail (General)", "AMZN": "Retail (General)",
    "AAPL": "Computers/Peripherals",
}

SECTOR_TO_INDUSTRY = {
    "technology": "Software (System & Application)",
    "healthcare": "Drugs (Pharmaceutical)",
    "communication services": "Advertising",
    "consumer cyclical": "Retail (General)",
    "consumer defensive": "Food Processing",
    "industrials": "Machinery",
    "energy": "Oil/Gas (Production and Exploration)",
    "basic materials": "Chemical (Specialty)",
    "utilities": "Utility (General)",
    "real estate": "R.E.I.T.",
    "financial services": "Investments & Asset Management",
}


def _norm(name):
    """Normalize an industry label: lowercase, collapse whitespace, strip nbsp."""
    return re.sub(r"\s+", " ", (name or "").replace("\xa0", " ")).strip().lower()


class _TableParser(HTMLParser):
    """Minimal <table> row/cell extractor. The page is Excel-exported HTML, so
    cells carry heavy style attributes but the tr/td structure is plain."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self._row, self._cell, self._in_cell = [], None, [], False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._in_cell, self._cell = True, []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._in_cell = False
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._in_cell:
            self._cell.append(data)


def _pct(text):
    """'16.95%' -> 0.1695 ; 'NA' / '' -> None."""
    t = (text or "").replace("%", "").replace(",", "").strip()
    if not t or t.upper() in ("NA", "N/A", "-"):
        return None
    try:
        return float(t) / 100.0
    except ValueError:
        return None


def parse_roc_html(html):
    """Parse Damodaran's roc.html into {normalized industry: terminal ROIC}.

    Terminal ROIC = "Normalized ROIC (last 10 years)" — see the module docstring
    for why the single-year column is not used. Columns are located BY HEADER
    TEXT, not by position, so his adding or moving a column does not silently
    shift us onto the wrong number. Returns {} if the expected headers are absent
    — the caller then keeps the previous snapshot rather than guessing."""
    p = _TableParser()
    p.feed(html)
    i_name = i_norm = None
    out = {}
    for row in p.rows:
        cells = [_norm(c) for c in row]
        if i_norm is None:
            if NAME_HEADER in cells and NORM_HEADER in cells:
                i_name, i_norm = cells.index(NAME_HEADER), cells.index(NORM_HEADER)
            continue
        if len(row) <= max(i_norm, i_name):
            continue
        name, norm = _norm(row[i_name]), _pct(row[i_norm])
        if name and norm is not None:
            out[name] = norm
    return out


def _fallback_table():
    """Snapshot rows are 'industry|single-year adj|normalized 10y'. We use the
    normalized column; the single-year one is kept in the raw text only as a
    reference for anyone re-checking the source page."""
    out = {}
    for line in FALLBACK_RAW.strip().splitlines():
        parts = line.split("|")
        if len(parts) != 3:
            continue
        try:
            out[_norm(parts[0])] = float(parts[2]) / 100.0
        except ValueError:
            continue
    return out


FALLBACK = _fallback_table()


def fetch_roc_table(cache_dir=None, ttl_days=TTL_DAYS, requests_mod=None, timeout=12,
                    user_agent="PortfolioDeepApp"):
    """Return {industry: terminal ROIC}. Disk cache -> network -> bundled snapshot.

    Never raises: a valuation refresh must not fail because a university web
    server is slow. Falls back to the January 2026 snapshot instead."""
    return _fetch_table(ROC_URL, CACHE_FILE, parse_roc_html, FALLBACK, "roc",
                        cache_dir, ttl_days, requests_mod, timeout, user_agent)


def _fetch_table(url, cache_file, parse, fallback, label, cache_dir=None, ttl_days=TTL_DAYS,
                 requests_mod=None, timeout=12, user_agent="PortfolioDeepApp"):
    """{industry: value} from one Damodaran dataset page. Fresh disk cache -> network
    -> stale cache -> bundled snapshot. Never raises."""
    path = os.path.join(cache_dir, cache_file) if cache_dir else None
    if path and os.path.exists(path):
        try:
            if (time.time() - os.path.getmtime(path)) < ttl_days * 86400:
                with open(path, encoding="utf-8") as fh:
                    cached = json.load(fh)
                if cached.get("table"):
                    return {k: float(v) for k, v in cached["table"].items()}
        except Exception as e:
            log.warning("damodaran %s cache read failed: %s", label, e)
    table = {}
    try:
        import requests as _r
        rq = requests_mod or _r
        r = rq.get(url, headers={"User-Agent": user_agent}, timeout=timeout)
        r.raise_for_status()
        table = parse(r.text)
    except Exception as e:
        log.warning("damodaran %s fetch failed (%s) - using bundled snapshot", label, e)
    if not table:
        # stale cache beats a year-old bundled snapshot
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    cached = json.load(fh)
                if cached.get("table"):
                    return {k: float(v) for k, v in cached["table"].items()}
            except Exception:
                pass
        return dict(fallback)
    if path:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"fetched": time.time(), "url": url, "table": table}, fh)
        except Exception as e:
            log.warning("damodaran %s cache write failed: %s", label, e)
    return table


# Market-wide normalized ROIC from the same table — the "converge to the average
# company" number used when we have no real industry view.
MARKET_DEFAULT = 0.1492


def terminal_roic_for(ticker, sector, table=None):
    """Industry terminal-ROIC ceiling for a ticker, or None when unmapped.

    None means 'no industry view' — the engine then keeps its global default
    rather than inventing a number.

    A hit on SECTOR_TO_INDUSTRY is NOT an industry view. FMP's eleven
    GICS-style sectors are far coarser than Damodaran's ninety-odd industries, so
    mapping the whole "healthcare" bucket onto Drugs (Pharmaceutical) at 23.52%
    hands every unmapped healthcare name the ceiling of one of its most profitable
    corners — the exact "constant applied to everyone" shape this codebase keeps
    paying for. A coarse hit is now capped at the market-average normalized ROIC, so
    it can still pull a company DOWN but can never gift it a premium it has not been
    shown to earn. An explicit per-ticker mapping is unaffected."""
    table = table if table is not None else FALLBACK
    key = (ticker or "").upper()
    ind, precise = INDUSTRY_BY_TICKER.get(key), True
    if not ind:
        ind, precise = SECTOR_TO_INDUSTRY.get(_norm(sector)), False
    if not ind:
        return None
    v = table.get(_norm(ind))
    if v is None or v <= 0:
        return None
    if not precise:
        market = table.get(_norm("Total Market (without financials)")) or MARKET_DEFAULT
        return min(v, market)
    return v


# --------------------------------------------------------------------------- #
#  Bottom-up beta (Damodaran "Betas by Sector (US)")                           #
# --------------------------------------------------------------------------- #
# A regression beta on one stock is noisy (NVO 0.35 against a pharma industry 0.98);
# Damodaran's prescription is the industry's UNLEVERED beta, corrected for cash,
# re-levered at the firm's own market debt-to-equity. Same page family, same update
# cycle and the same fetch/cache/fallback path as the ROC table.
BETA_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/Betas.html"
BETA_CACHE_FILE = "damodaran_beta.json"
BETA_HEADER = "unlevered beta corrected for cash"
# The page un-levers at the US marginal rate, so re-levering uses the same rate.
MARGINAL_TAX_US = 0.25

# January 2026 snapshot. Format: industry | beta | D/E | effective tax | unlevered
# beta | cash/firm value | unlevered beta corrected for cash (the column used).
BETA_FALLBACK_RAW = """
Advertising|1.21|40.20|5.02|0.93|7.73|1.01
Aerospace/Defense|0.95|15.56|11.58|0.85|2.61|0.87
Auto & Truck|1.46|19.70|3.74|1.27|2.99|1.31
Beverage (Soft)|0.64|20.59|6.85|0.56|3.44|0.58
Chemical (Specialty)|0.97|29.88|13.68|0.79|3.91|0.82
Computers/Peripherals|1.35|4.62|5.91|1.31|1.47|1.32
Drugs (Biotechnology)|1.14|13.04|1.08|1.03|4.20|1.08
Drugs (Pharmaceutical)|0.98|14.54|2.99|0.89|3.16|0.92
Food Processing|0.61|43.73|10.37|0.46|2.56|0.47
Healthcare Products|0.91|12.79|4.85|0.83|3.25|0.86
Healthcare Support Services|0.87|35.43|9.80|0.69|7.51|0.74
Heathcare Information and Technology|1.11|15.74|6.38|0.99|2.46|1.02
Investments & Asset Management|0.66|32.69|3.53|0.53|9.92|0.59
Machinery|0.96|14.69|13.37|0.87|2.91|0.89
Oil/Gas (Production and Exploration)|0.72|37.59|6.66|0.56|2.73|0.58
R.E.I.T.|0.64|84.46|1.58|0.39|1.91|0.40
Retail (General)|0.81|7.94|19.10|0.76|2.68|0.78
Semiconductor|1.52|2.59|5.11|1.49|1.02|1.50
Semiconductor Equip|1.40|4.86|9.96|1.35|3.13|1.39
Software (System & Application)|1.28|5.58|5.51|1.23|1.83|1.25
Utility (General)|0.24|81.48|12.77|0.15|0.33|0.15
Total Market|0.91|35.17|8.30|0.72|4.50|0.76
Total Market (without financials)|0.99|17.29|7.10|0.88|2.60|0.90
"""


def _beta_fallback():
    out = {}
    for line in BETA_FALLBACK_RAW.strip().splitlines():
        parts = line.split("|")
        if len(parts) == 7:
            try:
                out[_norm(parts[0])] = float(parts[6])
            except ValueError:
                continue
    return out


BETA_FALLBACK = _beta_fallback()


def _num(text):
    t = (text or "").replace(",", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def parse_beta_html(html):
    """Betas.html -> {normalized industry: unlevered beta corrected for cash}.
    Located by header text; {} when the header is absent (the caller keeps the
    previous snapshot rather than guessing a column)."""
    p = _TableParser()
    p.feed(html)
    i_name = i_beta = None
    out = {}
    for row in p.rows:
        cells = [_norm(c) for c in row]
        if i_beta is None:
            if NAME_HEADER in cells and BETA_HEADER in cells:
                i_name, i_beta = cells.index(NAME_HEADER), cells.index(BETA_HEADER)
            continue
        if len(row) <= max(i_beta, i_name):
            continue
        name, b = _norm(row[i_name]), _num(row[i_beta])
        if name and b is not None and 0 < b < 5:
            out[name] = b
    return out


_BETA_MEMO = {}


def fetch_beta_table(cache_dir=None, ttl_days=TTL_DAYS, requests_mod=None, timeout=12,
                     user_agent="PortfolioDeepApp"):
    """{industry: unlevered beta corrected for cash}. Memoized for an hour in-process
    so a batch refresh reads the page (or its disk cache) once, not once per ticker."""
    hit = _BETA_MEMO.get(cache_dir)
    if hit and time.time() - hit[0] < 3600 and requests_mod is None:
        return hit[1]
    table = _fetch_table(BETA_URL, BETA_CACHE_FILE, parse_beta_html, BETA_FALLBACK, "beta",
                         cache_dir, ttl_days, requests_mod, timeout, user_agent)
    _BETA_MEMO[cache_dir] = (time.time(), table)
    return table


def industry_beta_for(ticker, sector, table=None):
    """(unlevered beta, industry name) for a ticker, or (None, None) when unmapped.
    Per-ticker industry first; a coarse sector hit is used as well (unlike the ROC
    ceiling, a beta is a risk estimate, not a premium), and is labelled as such."""
    table = table if table is not None else BETA_FALLBACK
    key = (ticker or "").upper()
    ind, precise = INDUSTRY_BY_TICKER.get(key), True
    if not ind:
        ind, precise = SECTOR_TO_INDUSTRY.get(_norm(sector)), False
    if not ind:
        return None, None
    b = table.get(_norm(ind))
    if b is None or b <= 0:
        return None, None
    return b, ind if precise else f"{ind} (sector-level)"
