"""
Damodaran's company-by-industry list (indname.xls, ~48,000 firms, January vintage) and
SEC frames (one call = one concept for every filer), the two inputs that pick a firm's
relative-valuation peers on Damodaran's own industry definitions.

Why his list and not a vendor's peer API: his industry averages (psdata) are computed
over exactly these members, so the peer set and the industry multiple describe the
same group. FMP's lists put HIMS beside cosmetics makers and GOOGL beside Korean
portals (live, 2026-10-23).

Only US-listed, US-domiciled rows are kept (the peers must file with the SEC to be
valued on one clock). The 22 MB workbook is parsed once and cached as a small JSON
table; the raw file is not kept.
"""
import datetime as dt
import json
import logging
import os
import time

log = logging.getLogger("portfolio.damodaran_companies")

INDNAME_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/indname.xls"
INDNAME_CACHE = "damodaran_indname.json"
INDNAME_TTL_DAYS = 120
US_EXCHANGES = {"NYSE", "NasdaqGS", "NasdaqGM", "NasdaqCM", "NYSEAM"}
SHEET = "By industry"
MIN_ROWS = 1000                  # a parse with fewer US rows is a format change, not data

FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/USD/CY{year}.json"
# Every us-gaap revenue concept a filer may tag its top line with: on the first two
# alone MSFT's industry had 167 of 194 filers and pharma fell under 70% coverage
# (live 2026-11-17). The largest per filer wins, as before. One call per concept a year.
FRAMES_CONCEPTS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                   "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax")
FRAMES_TTL_DAYS = 30


def parse_indname(xls_bytes):
    """{ticker: [industry, sic, country]} for the US-listed rows of the workbook.
    Raises on a layout it does not recognise (the caller keeps its cache)."""
    import xlrd
    book = xlrd.open_workbook(file_contents=xls_bytes, on_demand=True)
    sh = book.sheet_by_name(SHEET)
    out = rows_to_table(sh.row_values(r) for r in range(sh.nrows))
    book.release_resources()
    return out


def rows_to_table(rows):
    """The sheet's rows (header first) -> {ticker: [industry, sic, country]}."""
    rows = iter(rows)
    head = [str(c).strip() for c in next(rows)]
    col = {name: head.index(name) for name in ("Exchange:Ticker", "Industry Group", "SIC Code", "Country")}
    out = {}
    for row in rows:
        ex, _, tk = str(row[col["Exchange:Ticker"]]).partition(":")
        if ex not in US_EXCHANGES or not tk:
            continue
        sic = row[col["SIC Code"]]
        sic = str(int(sic)) if isinstance(sic, float) else str(sic).strip()
        out.setdefault(tk.strip().upper(), [str(row[col["Industry Group"]]).strip(), sic,
                                            str(row[col["Country"]]).strip()])
    if len(out) < MIN_ROWS:
        raise ValueError(f"only {len(out)} US rows: the workbook layout changed")
    return out


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write(path, obj):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        os.replace(tmp, path)
    except OSError as e:
        log.warning("cache write failed (%s): %s", path, e)


def load_indname(cache_dir, user_agent="PortfolioDeepApp", requests_mod=None, timeout=90, now=None):
    """(table, note). Fresh cache -> download -> stale cache (noted) -> (None, reason).
    Never raises."""
    now = now or time.time()
    path = os.path.join(cache_dir, INDNAME_CACHE)
    cached = _read(path)
    if cached and now - cached.get("at", 0) < INDNAME_TTL_DAYS * 86400:
        return cached["rows"], None
    try:
        import requests as _r
        r = (requests_mod or _r).get(INDNAME_URL, headers={"User-Agent": user_agent}, timeout=timeout)
        r.raise_for_status()
        rows = parse_indname(r.content)
        _write(path, {"at": now, "fetched": dt.date.today().isoformat(),
                      "last_modified": r.headers.get("Last-Modified"), "rows": rows})
        return rows, None
    except Exception as e:
        log.warning("indname fetch failed: %s", e)
        if cached:
            return cached["rows"], f"Damodaran company list from {cached.get('fetched')} (refresh failed)"
        return None, f"Damodaran company list unavailable ({type(e).__name__})"


def members(rows, industry, exclude=()):
    """US-domiciled tickers in one Damodaran industry."""
    ex = {t.upper() for t in exclude}
    return [t for t, (ind, _sic, ctry) in (rows or {}).items()
            if ind == industry and ctry == "United States" and t not in ex]


def foreign_members(rows, industry):
    """US-listed members domiciled elsewhere (MELI: Uruguay). Some file 10-Ks in USD and
    sit in SEC's frames; the industry share counts those (peer_panel.industry_share)."""
    return [t for t, (ind, _sic, ctry) in (rows or {}).items()
            if ind == industry and ctry != "United States"]


def frames_year(today):
    """The last calendar year SEC frames have complete (10-Ks land by ~March)."""
    return today.year - 1 if today.month >= 4 else today.year - 2


def _frames(cache_dir, year, user_agent, requests_mod=None, timeout=60, now=None):
    """({cik: revenue}, {cik: period end}, note) for calendar year `year`: the largest of
    the FRAMES_CONCEPTS per filer, and the end date of the period SEC's frame put
    in that year (a June filer's FY lands in the CY frames chose, not one we assume).
    A cache written before the end dates were kept returns an empty end map."""
    now = now or time.time()
    path = os.path.join(cache_dir, f"sec_frames_revenue_CY{year}.json")
    cached = _read(path)

    def _from(c):
        return ({int(k): v for k, v in c["rev"].items()},
                {int(k): v for k, v in (c.get("end") or {}).items()})

    # a cache read on a shorter concept list undercounts the industry: refresh it
    same_ladder = bool(cached) and cached.get("concepts") == list(FRAMES_CONCEPTS)
    if same_ladder and now - cached.get("at", 0) < FRAMES_TTL_DAYS * 86400:
        return (*_from(cached), None)
    out, end = {}, {}
    try:
        import requests as _r
        rq = requests_mod or _r
        for concept in FRAMES_CONCEPTS:
            r = rq.get(FRAMES_URL.format(concept=concept, year=year),
                       headers={"User-Agent": user_agent}, timeout=timeout)
            if getattr(r, "status_code", 200) == 404:
                continue                        # nobody tagged this concept that year
            r.raise_for_status()
            for d in r.json().get("data") or []:
                if d.get("cik") and isinstance(d.get("val"), (int, float)):
                    cik = int(d["cik"])
                    if d["val"] > out.get(cik, 0):
                        out[cik], end[cik] = d["val"], d.get("end")
                    else:
                        out.setdefault(cik, 0)
        if out:
            _write(path, {"at": now, "concepts": list(FRAMES_CONCEPTS), "rev": out, "end": end})
            return out, end, None
        raise ValueError("empty frames")
    except Exception as e:
        log.warning("SEC frames CY%s failed: %s", year, e)
        if cached:
            return (*_from(cached), "SEC revenue frames from cache (refresh failed)")
        return {}, {}, f"SEC revenue frames unavailable ({type(e).__name__})"


ASSETS_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/Assets/USD/CY{year}Q4I.json"


def _assets(cache_dir, year, user_agent, requests_mod=None, timeout=60, now=None):
    """({cik}, note): the filers with a year-end balance sheet (us-gaap Assets, the
    CY{year}Q4I instant frame). A member here with no revenue frame filed for the year
    and booked no revenue (a pre-revenue biotech): covered, at revenue 0 (SPEC R20)."""
    now = now or time.time()
    path = os.path.join(cache_dir, f"sec_frames_assets_CY{year}.json")
    cached = _read(path)
    if cached and now - cached.get("at", 0) < FRAMES_TTL_DAYS * 86400:
        return set(cached["ciks"]), None
    try:
        import requests as _r
        r = (requests_mod or _r).get(ASSETS_URL.format(year=year), headers={"User-Agent": user_agent},
                                     timeout=timeout)
        r.raise_for_status()
        ciks = sorted({int(d["cik"]) for d in r.json().get("data") or [] if d.get("cik")})
        if not ciks:
            raise ValueError("empty frames")
        _write(path, {"at": now, "ciks": ciks})
        return set(ciks), None
    except Exception as e:
        log.warning("SEC Assets frames CY%s failed: %s", year, e)
        if cached:
            return set(cached["ciks"]), "SEC Assets frames from cache (refresh failed)"
        return set(), f"SEC Assets frames unavailable ({type(e).__name__})"


def load_assets_frames_years(cache_dir, today, user_agent, requests_mod=None, timeout=60, now=None):
    """({CY: {cik}}, notes) for panel_years(today). A failed year is absent (its
    coverage then counts revenue filers only) and named in the notes. Never raises."""
    out, notes = {}, []
    for year in panel_years(today):
        ciks, note = _assets(cache_dir, year, user_agent, requests_mod, timeout, now)
        if ciks:
            out[year] = ciks
        if note:
            notes.append(f"CY{year}: {note}")
    return out, notes


def load_revenue_frames(cache_dir, year, user_agent, requests_mod=None, timeout=60, now=None):
    """({cik: revenue}, note) for calendar year `year`, the largest revenue concept per
    filer. Never raises."""
    rev, _end, note = _frames(cache_dir, year, user_agent, requests_mod, timeout, now)
    return rev, note


PANEL_YEARS = 5


def panel_years(today):
    """The calendar years the industry-share panel covers, oldest first."""
    last = frames_year(today)
    return list(range(last - PANEL_YEARS + 1, last + 1))


def load_revenue_frames_years(cache_dir, today, user_agent, requests_mod=None, timeout=60, now=None):
    """({CY: {cik: [revenue, period end]}}, notes) for panel_years(today), one cache file
    per year (the same files load_revenue_frames reads). A year that fails is absent
    and named in the notes. Never raises."""
    out, notes = {}, []
    for year in panel_years(today):
        rev, end, note = _frames(cache_dir, year, user_agent, requests_mod, timeout, now)
        if rev:
            out[year] = {cik: [v, end.get(cik)] for cik, v in rev.items()}
        if note:
            notes.append(f"CY{year}: {note}")
    return out, notes
