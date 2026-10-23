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
FRAMES_CONCEPTS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax")
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


def frames_year(today):
    """The last calendar year SEC frames have complete (10-Ks land by ~March)."""
    return today.year - 1 if today.month >= 4 else today.year - 2


def load_revenue_frames(cache_dir, year, user_agent, requests_mod=None, timeout=60, now=None):
    """({cik: revenue}, note) for calendar year `year`, the larger of the two revenue
    concepts per filer. Never raises."""
    now = now or time.time()
    path = os.path.join(cache_dir, f"sec_frames_revenue_CY{year}.json")
    cached = _read(path)
    if cached and now - cached.get("at", 0) < FRAMES_TTL_DAYS * 86400:
        return {int(k): v for k, v in cached["rev"].items()}, None
    out = {}
    try:
        import requests as _r
        rq = requests_mod or _r
        for concept in FRAMES_CONCEPTS:
            r = rq.get(FRAMES_URL.format(concept=concept, year=year),
                       headers={"User-Agent": user_agent}, timeout=timeout)
            r.raise_for_status()
            for d in r.json().get("data") or []:
                if d.get("cik") and isinstance(d.get("val"), (int, float)):
                    out[int(d["cik"])] = max(out.get(int(d["cik"]), 0), d["val"])
        if out:
            _write(path, {"at": now, "rev": out})
            return out, None
        raise ValueError("empty frames")
    except Exception as e:
        log.warning("SEC frames CY%s failed: %s", year, e)
        if cached:
            return {int(k): v for k, v in cached["rev"].items()}, "SEC revenue frames from cache (refresh failed)"
        return {}, f"SEC revenue frames unavailable ({type(e).__name__})"
