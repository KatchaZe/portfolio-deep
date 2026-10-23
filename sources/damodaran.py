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

# Which industry a ticker belongs to is decided in sources/industries.py (per-ticker
# override, then the FMP industry, then keywords, then the sector) for every caller.
from sources.industries import FINANCIAL_INDUSTRIES, industry_for, industry_mix   # noqa: E402
from sources import damodaran_snapshot as _snap  # noqa: E402


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


def terminal_roic_for(ticker, sector, table=None, fmp_industry=None):
    """Industry terminal-ROIC ceiling for a ticker, or None when unmapped.

    None means 'no industry view' — the engine then keeps its global default
    rather than inventing a number.

    Only a company-level mapping (per-ticker override or the FMP industry) is an
    industry view. A keyword guess or the eleven-bucket sector is coarse: mapping the
    whole "healthcare" bucket onto Drugs (Pharmaceutical) at 23.52% would hand every
    unmapped healthcare name the ceiling of one of its most profitable corners. A
    coarse hit is capped at the market-average normalized ROIC, so it can pull a
    company DOWN but never gift it a premium it has not been shown to earn."""
    table = table if table is not None else FALLBACK
    mix = industry_mix(ticker)
    if mix:
        return mixed_value(table, mix)
    ind, basis = industry_for(ticker, fmp_industry, sector)
    if not ind:
        return None
    v = table.get(_norm(ind))
    if v is None or v <= 0:
        return None
    if basis in ("sector", "keyword"):
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

def _num(text):
    t = (text or "").replace(",", "").replace("$", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def _snapshot_column(raw, header, match, conv):
    """{normalized industry: value} from one column of a bundled snapshot
    (sources/damodaran_snapshot.py), the column found by `match(header cell)`."""
    i = next(k for k, h in enumerate(header) if match(_norm(h)))
    out = {}
    for line in raw.strip().splitlines():
        cells = line.split("|")
        if len(cells) == len(header):
            v = conv(cells[i])
            if v is not None:
                out[_norm(cells[0])] = v
    return out


def _parse_by_header(html, match, conv, valid=lambda v: True):
    """{normalized industry: value} from a dataset page, the value column located by
    `match(normalized header cell)`. {} when the header is absent, so the caller keeps
    the previous snapshot rather than guessing a column."""
    p = _TableParser()
    p.feed(html)
    i_name = i_val = None
    out = {}
    for row in p.rows:
        cells = [_norm(c) for c in row]
        if i_val is None:
            hit = [k for k, c in enumerate(cells) if match(c)]
            if NAME_HEADER in cells and hit:
                i_name, i_val = cells.index(NAME_HEADER), hit[0]
            continue
        if len(row) <= max(i_val, i_name):
            continue
        name, v = _norm(row[i_name]), conv(row[i_val])
        if name and v is not None and valid(v):
            out[name] = v
    return out


def _beta_fallback():
    return _snapshot_column(_snap.BETAS, _snap.BETAS_HEADER, lambda h: h == BETA_HEADER,
                            lambda t: _num(t))


BETA_FALLBACK = _beta_fallback()


def parse_beta_html(html):
    """Betas.html -> {normalized industry: unlevered beta corrected for cash}."""
    return _parse_by_header(html, lambda h: h == BETA_HEADER, _num, lambda b: 0 < b < 5)


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


def industry_beta_for(ticker, sector, table=None, fmp_industry=None):
    """(unlevered beta, industry label) for a ticker, or (None, None) when unmapped.
    A coarse (sector/keyword) hit is used as well, since a beta is a risk estimate,
    not a premium, and is labelled as such."""
    table = table if table is not None else BETA_FALLBACK
    mix = industry_mix(ticker)
    if mix:
        # a lender's unlevered beta is un-levered over its funding debt (0.33 for
        # Financial Svcs): averaged in, it would understate an operating firm's risk
        op = {k: w for k, w in mix.items() if k not in FINANCIAL_INDUSTRIES} or mix
        b = mixed_value(table, op)
        lbl = mix_label(op) + (" (financial business excluded: its beta is a lender's)"
                               if len(op) < len(mix) else "")
        return (b, lbl) if b else (None, None)
    ind, basis = industry_for(ticker, fmp_industry, sector)
    if not ind:
        return None, None
    b = table.get(_norm(ind))
    if b is None or b <= 0:
        return None, None
    return b, ind if basis in ("override", "industry") else f"{ind} ({basis}-level)"


# --------------------------------------------------------------------------- #
#  Industry operating margin and sales-to-capital (margin.html, capex.html)     #
# --------------------------------------------------------------------------- #
# The FCFF DCF needs two industry anchors: the operating margin a pre-profit firm
# converges on, and the sales-to-capital ratio that turns revenue growth into
# reinvestment. The margin is the PRE-TAX UNADJUSTED operating margin, the same
# EBIT / revenue the engine measures for the firm itself. margin.html scrambles some
# column labels in its source, so the column is found by keywords, not exact text.
MARGIN_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/margin.html"
MARGIN_CACHE_FILE = "damodaran_margin.json"
S2C_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/capex.html"
S2C_CACHE_FILE = "damodaran_s2c.json"


def _is_margin_col(h):
    return "pre-tax" in h and "unadjusted" in h and "margin" in h


def _is_s2c_col(h):
    return "sales/" in h.replace(" ", "") and "invested capital" in h


def _pct_signed(text):
    v = _pct(text)
    return v if v is not None and -1.0 < v < 1.0 else None


MARGIN_FALLBACK = _snapshot_column(_snap.MARGINS, _snap.MARGINS_HEADER, _is_margin_col, _pct_signed)
S2C_FALLBACK = _snapshot_column(_snap.CAPEX, _snap.CAPEX_HEADER, _is_s2c_col, _num)


def parse_margin_html(html):
    return _parse_by_header(html, _is_margin_col, _pct_signed)


def parse_s2c_html(html):
    return _parse_by_header(html, _is_s2c_col, _num, lambda v: 0 < v < 50)


_TABLE_MEMO = {}


def _memo_fetch(key, cache_dir, fetch):
    hit = _TABLE_MEMO.get((key, cache_dir))
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    table = fetch()
    _TABLE_MEMO[(key, cache_dir)] = (time.time(), table)
    return table


def fetch_margin_table(cache_dir=None, ttl_days=TTL_DAYS, requests_mod=None, timeout=12,
                       user_agent="PortfolioDeepApp"):
    """{industry: pre-tax unadjusted operating margin}. Never raises."""
    go = lambda: _fetch_table(MARGIN_URL, MARGIN_CACHE_FILE, parse_margin_html,  # noqa: E731
                              MARGIN_FALLBACK, "margin", cache_dir, ttl_days, requests_mod,
                              timeout, user_agent)
    return go() if requests_mod is not None else _memo_fetch("margin", cache_dir, go)


def fetch_s2c_table(cache_dir=None, ttl_days=TTL_DAYS, requests_mod=None, timeout=12,
                    user_agent="PortfolioDeepApp"):
    """{industry: sales / invested capital (LTM)}. Never raises."""
    go = lambda: _fetch_table(S2C_URL, S2C_CACHE_FILE, parse_s2c_html,  # noqa: E731
                              S2C_FALLBACK, "s2c", cache_dir, ttl_days, requests_mod,
                              timeout, user_agent)
    return go() if requests_mod is not None else _memo_fetch("s2c", cache_dir, go)


def industry_value(table, industry, mix=None, skip_financial=False):
    """The table's value for a Damodaran industry name, or the business-mix weighted
    value when the company runs several (config.INDUSTRY_MIX). None when unknown.
    `skip_financial` leaves lending businesses out of a balance-sheet ratio (sales to
    capital 0.07 is a loan book, not the capital an operating business needs)."""
    if mix:
        if skip_financial:
            mix = {k: w for k, w in mix.items() if k not in FINANCIAL_INDUSTRIES} or mix
        return mixed_value(table, mix)
    if not table or not industry:
        return None
    return table.get(_norm(industry))


def mixed_value(table, mix):
    """Weighted average over a {industry: weight} business mix (Damodaran's bottom-up
    beta for a multi-business firm), re-weighted over the industries the table has."""
    if not table:
        return None
    vals = [(w, table.get(_norm(ind))) for ind, w in mix.items()]
    vals = [(w, v) for w, v in vals if v is not None and w > 0]
    wsum = sum(w for w, _ in vals)
    return sum(w * v for w, v in vals) / wsum if wsum else None


def mix_label(mix):
    tot = sum(mix.values()) or 1.0
    return " + ".join(f"{ind} {w / tot:.0%}" for ind, w in sorted(mix.items(), key=lambda kv: -kv[1]))


# --------------------------------------------------------------------------- #
#  Implied equity risk premium (home page, refreshed monthly)                  #
# --------------------------------------------------------------------------- #
# The ERP was a hand-typed constant: 4.45% set in July while Damodaran's 1 October
# figure was 4.20%, and the staleness flag only fires after three months. His home
# page states the current month's implied ERP in one sentence; it is read here, cached,
# and overlaid by store._apply_assumptions unless a NEWER manual value was saved.
ERP_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/home.htm"
ERP_CACHE_FILE = "damodaran_erp.json"
ERP_TTL_DAYS = 7
ERP_SANE = (0.02, 0.09)
_MONTHS = {m: i for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"), 1)}
_ERP_RE = re.compile(r"implied\s+erp\s+on\s+([a-z]+)\s+(\d{1,2}),?\s+(\d{4})\W{0,40}?(\d{1,2}\.\d{1,2})\s*%",
                     re.I)
_RF_RE = re.compile(r"treasury\s+rate\s+of\s+(\d{1,2}\.\d{1,2})\s*%", re.I)


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def parse_implied_erp(html):
    """'Implied ERP on October 1, 2026 = 4.20% (Trailing 12 month, with adjusted
    payout)' -> {"erp": 0.042, "as_of": "2026-10", "date": "2026-10-01", "rf": 0.0529}.
    The first figure is his headline (trailing 12m, adjusted payout). None when the
    sentence is missing or the number is outside a sane band: never guess."""
    p = _TextParser()
    p.feed(html or "")
    text = re.sub(r"\s+", " ", " ".join(p.parts).replace("\xa0", " "))
    m = _ERP_RE.search(text)
    if not m or m.group(1).lower() not in _MONTHS:
        return None
    erp = float(m.group(4)) / 100.0
    if not ERP_SANE[0] <= erp <= ERP_SANE[1]:
        return None
    month, day, year = _MONTHS[m.group(1).lower()], int(m.group(2)), int(m.group(3))
    rf = _RF_RE.search(text[m.end():m.end() + 600])
    return {"erp": round(erp, 5), "as_of": f"{year:04d}-{month:02d}",
            "date": f"{year:04d}-{month:02d}-{day:02d}",
            "rf": round(float(rf.group(1)) / 100.0, 5) if rf else None}


def cached_implied_erp(cache_dir):
    """The last implied ERP read from his page, from disk only (no network)."""
    path = os.path.join(cache_dir, ERP_CACHE_FILE) if cache_dir else None
    try:
        with open(path, encoding="utf-8") as fh:
            v = json.load(fh)
        if ERP_SANE[0] <= float(v["erp"]) <= ERP_SANE[1] and re.match(r"\d{4}-\d{2}$", v["as_of"]):
            return v
    except Exception:
        pass
    return None


def fetch_implied_erp(cache_dir=None, ttl_days=ERP_TTL_DAYS, requests_mod=None, timeout=12,
                      user_agent="PortfolioDeepApp"):
    """Current implied ERP from the home page, cached ttl_days. Returns the cached or
    fresh dict, or None. Never raises."""
    path = os.path.join(cache_dir, ERP_CACHE_FILE) if cache_dir else None
    cached = cached_implied_erp(cache_dir) if cache_dir else None
    if cached and path and (time.time() - os.path.getmtime(path)) < ttl_days * 86400:
        return cached
    try:
        import requests as _r
        rq = requests_mod or _r
        r = rq.get(ERP_URL, headers={"User-Agent": user_agent}, timeout=timeout)
        r.raise_for_status()
        live = parse_implied_erp(r.text)
    except Exception as e:
        log.warning("damodaran implied ERP fetch failed (%s)", e)
        return cached
    if not live:
        log.warning("damodaran implied ERP sentence not found on the home page")
        return cached
    if path:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({**live, "fetched": time.time(), "url": ERP_URL}, fh)
        except Exception as e:
            log.warning("damodaran implied ERP cache write failed: %s", e)
    return live


# --------------------------------------------------------------------------- #
#  EV/Sales by industry (psdata) — the relative-valuation industry multiple     #
# --------------------------------------------------------------------------- #
PSDATA_URL = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/psdata.html"
PSDATA_TTL_DAYS = 120          # an annual dataset; re-read a few times a year
PSDATA_COLUMNS = {             # field -> (header test, converter, plausible)
    "ev_sales": (lambda h: h == "ev/sales", _num, lambda v: 0 < v < 100),
    "margin": (lambda h: h == "pre-tax operating margin", _pct_signed, lambda v: True),
    "n": (lambda h: h == "number of firms", _num, lambda v: v >= 1),
}


def _psdata_fallback(field):
    test, conv, _ = PSDATA_COLUMNS[field]
    return _snapshot_column(_snap.PSDATA, _snap.PSDATA_HEADER, test, conv)


def fetch_psdata(cache_dir=None, ttl_days=PSDATA_TTL_DAYS, requests_mod=None, timeout=12,
                 user_agent="PortfolioDeepApp"):
    """{field: {industry: value}} for ev_sales, margin and n. Never raises: live page,
    then cache, then the bundled January 2026 snapshot."""
    out = {}
    for field, (test, conv, ok) in PSDATA_COLUMNS.items():
        parse = lambda html, test=test, conv=conv, ok=ok: _parse_by_header(html, test, conv, ok)  # noqa: E731
        go = lambda field=field, parse=parse: _fetch_table(  # noqa: E731
            PSDATA_URL, f"damodaran_psdata_{field}.json", parse, _psdata_fallback(field),
            f"psdata {field}", cache_dir, ttl_days, requests_mod, timeout, user_agent)
        out[field] = go() if requests_mod is not None else _memo_fetch(f"psdata_{field}", cache_dir, go)
    # the bundled table is a fixed vintage: say so, so a later year never labels it as new
    out["snapshot"] = out["ev_sales"] == _psdata_fallback("ev_sales")
    return out


def industry_multiple(psdata, industry):
    """{'ev_sales', 'margin', 'n'} for one Damodaran industry, or None."""
    k = _norm(industry)
    if not psdata or k not in (psdata.get("ev_sales") or {}):
        return None
    return {f: (psdata.get(f) or {}).get(k) for f in ("ev_sales", "margin", "n")}
