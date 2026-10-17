"""Country risk premium (Damodaran, ctryprem.html) for any listed company.

Damodaran prices country risk by where a company EARNS its money, not where it is
incorporated: the firm's equity risk premium is the revenue-weighted average of its
markets' ERPs. The engine's ERP (config.ERP) is already the US implied premium, and
the US row carries a 0.23% country premium of its own, so what the engine adds is the
premium OVER the US:

    CRP_firm = sum(w_i x CRP_i) - CRP_US          (floored at 0)
    Ke       = Rf + beta x (ERP + CRP_firm)

Resolution, per ticker:
    1. config.REVENUE_MIX[ticker]  - an editable {market: weight} table from the
       company's own segment disclosure (markets are countries or the regions below)
    2. the FMP profile's country of domicile (ISO-2), for any company not in the table
    3. nothing: CRP stays unset and the engine adds no premium (flagged upstream)

A region (e.g. "Western Europe") is the simple average of its member countries' CRPs:
ctryprem.html has no regional rows, and a plain average is the honest approximation of
Damodaran's GDP-weighted regional figures. Pure apart from the cached page fetch.
"""
import re

import config
from sources import damodaran as _d
from sources import damodaran_snapshot as _snap

CRP_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html"
CRP_CACHE_FILE = "damodaran_ctryprem.json"
COUNTRY_HEADER = "country"
CRP_HEADER = "country risk premium"
US = "united states"


def _country_key(name):
    """'Turkey (updated February 2026)' -> 'turkey'."""
    return _d._norm(re.sub(r"\(updated[^)]*\)", "", name or ""))


def _crp_pct(text):
    v = _d._pct(text)
    return v if v is not None and 0 <= v < 0.5 else None


def _snapshot():
    i = _snap.CTRYPREM_HEADER.index("Country Risk Premium")
    out = {}
    for line in _snap.CTRYPREM.strip().splitlines():
        cells = line.split("|")
        v = _crp_pct(cells[i]) if len(cells) > i else None
        if v is not None:
            out[_country_key(cells[0])] = v
    return out


CRP_FALLBACK = _snapshot()


def parse_ctryprem_html(html):
    """{country: CRP} located by header text; {} when the headers are absent."""
    p = _d._TableParser()
    p.feed(html)
    i_name = i_crp = None
    out = {}
    for row in p.rows:
        cells = [_d._norm(c) for c in row]
        if i_crp is None:
            if COUNTRY_HEADER in cells and CRP_HEADER in cells:
                i_name, i_crp = cells.index(COUNTRY_HEADER), cells.index(CRP_HEADER)
            continue
        if len(row) <= max(i_name, i_crp):
            continue
        name, v = _country_key(row[i_name]), _crp_pct(row[i_crp])
        if name and v is not None:
            out[name] = v
    # a page without the US row cannot be measured against the engine's ERP
    return out if US in out else {}


_MEMO = {}


def fetch_crp_table(cache_dir=None, ttl_days=_d.TTL_DAYS, requests_mod=None, timeout=12,
                    user_agent="PortfolioDeepApp"):
    """{country: CRP}. Fresh cache -> page -> stale cache -> January 2026 snapshot."""
    go = lambda: _d._fetch_table(CRP_URL, CRP_CACHE_FILE, parse_ctryprem_html,  # noqa: E731
                                 CRP_FALLBACK, "ctryprem", cache_dir, ttl_days,
                                 requests_mod, timeout, user_agent)
    return go() if requests_mod is not None else _d._memo_fetch("ctryprem", cache_dir, go)


# Regions used by segment disclosures, as the simple average of member countries.
REGIONS = {
    "western europe": ["Germany", "France", "United Kingdom", "Italy", "Spain", "Netherlands",
                       "Switzerland", "Belgium", "Sweden", "Austria", "Denmark", "Norway",
                       "Ireland", "Finland", "Portugal"],
    "latin america": ["Brazil", "Mexico", "Argentina", "Colombia", "Chile", "Peru"],
    "emerging markets": ["Brazil", "China", "India", "Mexico", "Indonesia", "Turkey",
                         "Saudi Arabia", "South Africa", "Poland", "Thailand"],
    "asia pacific": ["Japan", "Australia", "Korea", "India", "Singapore", "Hong Kong",
                     "Indonesia", "Thailand", "Malaysia", "Philippines", "Vietnam",
                     "New Zealand"],
    "middle east": ["Saudi Arabia", "United Arab Emirates", "Israel", "Qatar", "Kuwait"],
    "africa": ["South Africa", "Nigeria", "Egypt", "Kenya", "Morocco"],
}

# FMP profiles carry ISO-2 country codes; Damodaran's table uses names.
ISO2 = {
    "AE": "United Arab Emirates", "AL": "Albania", "AO": "Angola", "AR": "Argentina",
    "AM": "Armenia", "AU": "Australia", "AT": "Austria", "AZ": "Azerbaijan", "BS": "Bahamas",
    "BH": "Bahrain", "BD": "Bangladesh", "BB": "Barbados", "BY": "Belarus", "BE": "Belgium",
    "BZ": "Belize", "BM": "Bermuda", "BO": "Bolivia", "BA": "Bosnia and Herzegovina",
    "BW": "Botswana", "BR": "Brazil", "BN": "Brunei", "BG": "Bulgaria", "KH": "Cambodia",
    "CM": "Cameroon", "CA": "Canada", "KY": "Cayman Islands", "CL": "Chile", "CN": "China",
    "CO": "Colombia", "CR": "Costa Rica", "CI": "Côte d'Ivoire", "HR": "Croatia",
    "CW": "Curacao", "CY": "Cyprus", "CZ": "Czech Republic", "DK": "Denmark",
    "DO": "Dominican Republic", "EC": "Ecuador", "EG": "Egypt", "SV": "El Salvador",
    "EE": "Estonia", "ET": "Ethiopia", "FJ": "Fiji", "FI": "Finland", "FR": "France",
    "GA": "Gabon", "GE": "Georgia", "DE": "Germany", "GH": "Ghana", "GR": "Greece",
    "GT": "Guatemala", "GG": "Guernsey (States of)", "GY": "Guyana", "HN": "Honduras",
    "HK": "Hong Kong", "HU": "Hungary", "IS": "Iceland", "IN": "India", "ID": "Indonesia",
    "IQ": "Iraq", "IE": "Ireland", "IM": "Isle of Man", "IL": "Israel", "IT": "Italy",
    "JM": "Jamaica", "JP": "Japan", "JE": "Jersey (States of)", "JO": "Jordan",
    "KZ": "Kazakhstan", "KE": "Kenya", "KR": "Korea", "KW": "Kuwait", "KG": "Kyrgyzstan",
    "LA": "Laos", "LV": "Latvia", "LB": "Lebanon", "LI": "Liechtenstein", "LT": "Lithuania",
    "LU": "Luxembourg", "MO": "Macao", "MK": "Macedonia", "MY": "Malaysia", "MV": "Maldives",
    "MT": "Malta", "MU": "Mauritius", "MX": "Mexico", "MD": "Moldova", "MN": "Mongolia",
    "ME": "Montenegro", "MA": "Morocco", "MZ": "Mozambique", "NA": "Namibia", "NP": "Nepal",
    "NL": "Netherlands", "NZ": "New Zealand", "NI": "Nicaragua", "NG": "Nigeria",
    "NO": "Norway", "OM": "Oman", "PK": "Pakistan", "PA": "Panama", "PG": "Papua New Guinea",
    "PY": "Paraguay", "PE": "Peru", "PH": "Philippines", "PL": "Poland", "PT": "Portugal",
    "QA": "Qatar", "RO": "Romania", "RU": "Russia", "RW": "Rwanda", "SA": "Saudi Arabia",
    "SN": "Senegal", "RS": "Serbia", "SG": "Singapore", "SK": "Slovakia", "SI": "Slovenia",
    "ZA": "South Africa", "ES": "Spain", "LK": "Sri Lanka", "SE": "Sweden",
    "CH": "Switzerland", "TW": "Taiwan", "TZ": "Tanzania", "TH": "Thailand",
    "TT": "Trinidad and Tobago", "TN": "Tunisia", "TR": "Turkey", "TC": "Turks and Caicos Islands",
    "UG": "Uganda", "UA": "Ukraine", "GB": "United Kingdom", "UK": "United Kingdom",
    "US": "United States", "UY": "Uruguay", "UZ": "Uzbekistan", "VE": "Venezuela",
    "VN": "Vietnam", "ZM": "Zambia", "ZW": "Zimbabwe",
}


# Where a holding company is incorporated says nothing about where it earns (a
# Brazilian bank incorporated in the Cayman Islands). Priced on domicile, these would
# carry almost no country risk, so the basis says to add a REVENUE_MIX line.
HOLDING_DOMICILES = frozenset({"KY", "BM", "VG", "JE", "GG", "IM", "CW", "BS", "TC", "LU", "PA", "IE"})


def market_crp(market, table):
    """CRP for a country name, ISO-2 code or region, or None when unknown."""
    key = _country_key(ISO2.get((market or "").upper(), market))
    if key in table:
        return table[key]
    members = [table.get(_country_key(c)) for c in REGIONS.get(key, [])]
    members = [m for m in members if m is not None]
    return sum(members) / len(members) if members else None


def crp_for(ticker, country, table=None, mix=None):
    """(CRP over the US, basis) for a ticker, or (None, why) when it cannot be told.
    `mix` defaults to config.REVENUE_MIX[ticker]; `country` is the FMP ISO-2 domicile."""
    table = table if table is not None else CRP_FALLBACK
    us = table.get(US)
    if us is None:
        return None, "country table has no United States row"
    mix = mix if mix is not None else getattr(config, "REVENUE_MIX", {}).get(ticker)
    if mix:
        parts, wsum, unknown = [], 0.0, []
        for market, w in mix.items():
            c = market_crp(market, table)
            if c is None:
                unknown.append(market)
                continue
            parts.append((market, w, c))
            wsum += w
        if parts and wsum > 0:
            crp = sum(w * c for _, w, c in parts) / wsum - us
            mix_txt = ", ".join(f"{m} {w / wsum:.0%}" for m, w, _ in
                                sorted(parts, key=lambda p: -p[1]))
            basis = f"revenue mix: {mix_txt}"
            if unknown:
                basis += f" (no CRP for {', '.join(unknown)}: re-weighted)"
            return max(0.0, crp), basis
    if country:
        c = market_crp(country, table)
        if c is None:
            return None, f"no Damodaran CRP for country '{country}'"
        name = ISO2.get(country.upper(), country)
        if _country_key(name) == US:
            return 0.0, "United States"
        if country.upper() in HOLDING_DOMICILES:
            return max(0.0, c - us), (f"domicile {name} is a holding jurisdiction - revenue is "
                                      f"likely elsewhere; add config.REVENUE_MIX['{ticker}']")
        return max(0.0, c - us), f"domicile {name} (no revenue mix on file)"
    return None, "no country on file"
