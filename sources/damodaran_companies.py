"""
Damodaran's company-by-industry list (indname.xls, ~48,000 firms, January vintage) and
SEC frames (one call = one concept for every filer), the two inputs that pick a firm's
relative-valuation peers on Damodaran's own industry definitions.

Why his list and not a vendor's peer API: his industry averages (psdata) are computed
over exactly these members, so the peer set and the industry multiple describe the
same group. FMP's lists put HIMS beside cosmetics makers and GOOGL beside Korean
portals (live, 2026-10-23).

Only US-listed rows feed membership (the peers must file with the SEC to be valued on
one clock). The non-US rows go to a separate cache (damodaran_global_sic.json) that only
resolves a home-listed target's own SIC (SPEC R29). The 22 MB workbook is parsed once
and cached as small JSON tables; the raw file is not kept.
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
# The non-US rows (home-exchange listings, "ENXTAM:ASML"), kept apart so US membership and
# industry_share never see them; only a target's own SIC is read from here (SPEC R29)
GLOBAL_CACHE = "damodaran_global_sic.json"
GLOBAL_COLS = ("Company Name", "Exchange:Ticker", "Country", "SIC Code", "Industry Group")
MIN_GLOBAL_ROWS = 1000

FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/USD/CY{year}.json"
# Every us-gaap revenue concept a filer may tag its top line with: on the first two
# alone MSFT's industry had 167 of 194 filers and pharma fell under 70% coverage
# (live 2026-11-17). One call per concept a year. Two readings of the same calls:
#   rev / end  the largest concept per filer, as before: the relative model sizes its
#              Damodaran peers on it (Universe.rev), so it must not move (PP1);
#   panel      the industry share's revenue, frames_revenue.choose per filer (R35), with
#              ifrs-full Revenue in USD for a filer with no us-gaap value (R34).
FRAMES_CONCEPTS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                   "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax")
FRAMES_TTL_DAYS = 30
IFRS_URL = "https://data.sec.gov/api/xbrl/frames/ifrs-full/Revenue/{unit}/CY{year}.json"
# R41: excise is read for the panel's net-revenue rule only (frames_revenue.choose), never sized on
EXCISE_CONCEPT = "ExciseAndSalesTaxes"
# 20-F filers' reporting currencies, read only to NAME a member that reports outside USD
# (R34: never converted, never counted). One call each per year, 404 when nobody filed.
IFRS_OTHER_CURRENCIES = ("EUR", "GBP", "CAD", "JPY", "CHF", "AUD", "CNY", "HKD", "BRL", "INR", "ILS",
                         "SEK", "DKK", "NOK", "MXN", "KRW", "TWD", "ZAR")
PANEL_SCHEMA = 1                 # a cache without the panel reading is refreshed
SEC_MIN_INTERVAL = 0.15          # sec_edgar's fair-access spacing (< 10 requests a second)
# Damodaran's company name per US ticker, for the name fallback of R33
NAMES_CACHE = "damodaran_us_names.json"


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


def rows_to_names(rows):
    """The sheet's rows (header first) -> {ticker: company name} for the US-exchange rows
    (the same rows and first-wins rule as rows_to_table)."""
    rows = iter(rows)
    head = [str(c).strip() for c in next(rows)]
    col = {name: head.index(name) for name in ("Exchange:Ticker", "Company Name")}
    out = {}
    for row in rows:
        ex, _, tk = str(row[col["Exchange:Ticker"]]).partition(":")
        if ex in US_EXCHANGES and tk:
            out.setdefault(tk.strip().upper(), str(row[col["Company Name"]]).strip())
    if len(out) < MIN_ROWS:
        raise ValueError(f"only {len(out)} US names: the workbook layout changed")
    return out


def parse_indname_names(xls_bytes):
    import xlrd
    book = xlrd.open_workbook(file_contents=xls_bytes, on_demand=True)
    sh = book.sheet_by_name(SHEET)
    out = rows_to_names(sh.row_values(r) for r in range(sh.nrows))
    book.release_resources()
    return out


def write_names(cache_dir, names, now=None, last_modified=None):
    _write(os.path.join(cache_dir, NAMES_CACHE),
           {"at": now or time.time(), "fetched": dt.date.today().isoformat(),
            "last_modified": last_modified, "names": names})


def load_us_names(cache_dir, user_agent="PortfolioDeepApp", requests_mod=None, timeout=90, now=None):
    """(names, note) like load_global_sic, for damodaran_us_names.json. A download writes
    this cache only. Never raises."""
    now = now or time.time()
    path = os.path.join(cache_dir, NAMES_CACHE)
    cached = _read(path)
    if cached and now - cached.get("at", 0) < INDNAME_TTL_DAYS * 86400:
        return cached["names"], None
    try:
        import requests as _r
        r = (requests_mod or _r).get(INDNAME_URL, headers={"User-Agent": user_agent}, timeout=timeout)
        r.raise_for_status()
        names = parse_indname_names(r.content)
        write_names(cache_dir, names, now, r.headers.get("Last-Modified"))
        return names, None
    except Exception as e:
        log.warning("indname US names failed: %s", e)
        if cached:
            return cached["names"], f"ชื่อบริษัทของ Damodaran จาก {cached.get('fetched')} (ดาวน์โหลดใหม่ไม่สำเร็จ)"
        return {}, f"ไม่มีชื่อบริษัทของ Damodaran ({type(e).__name__}) จึงจับคู่ชื่อหา CIK ไม่ได้"


def parse_indname_global(xls_bytes):
    """[[name, exchange:ticker, country, sic, industry]] for the rows not on a US exchange.
    Raises on a layout it does not recognise, and when those rows carry no SIC code."""
    import xlrd
    book = xlrd.open_workbook(file_contents=xls_bytes, on_demand=True)
    sh = book.sheet_by_name(SHEET)
    out = rows_to_global(sh.row_values(r) for r in range(sh.nrows))
    book.release_resources()
    return out


def _sic_text(v):
    if isinstance(v, float):
        return str(int(v)) if v == v and v > 0 else ""
    return str(v or "").strip()


def rows_to_global(rows):
    """The sheet's rows (header first) -> the non-US-exchange rows, one list each."""
    rows = iter(rows)
    head = [str(c).strip() for c in next(rows)]
    col = {name: head.index(name) for name in GLOBAL_COLS}
    out = []
    for row in rows:
        ext = str(row[col["Exchange:Ticker"]]).strip()
        ex, _, tk = ext.partition(":")
        if not tk or ex in US_EXCHANGES:
            continue
        out.append([str(row[col["Company Name"]]).strip(), ext, str(row[col["Country"]]).strip(),
                    _sic_text(row[col["SIC Code"]]), str(row[col["Industry Group"]]).strip()])
    if len(out) < MIN_GLOBAL_ROWS:
        raise ValueError(f"only {len(out)} non-US rows: the workbook layout changed")
    if not any(r[3] for r in out):
        raise ValueError("the non-US rows carry no SIC code")
    return out


def write_global(cache_dir, rows, now=None, last_modified=None):
    _write(os.path.join(cache_dir, GLOBAL_CACHE),
           {"at": now or time.time(), "fetched": dt.date.today().isoformat(),
            "last_modified": last_modified, "columns": ["name", "exchange_ticker", "country", "sic", "industry"],
            "rows": rows})


def load_global_sic(cache_dir, user_agent="PortfolioDeepApp", requests_mod=None, timeout=90, now=None):
    """(rows, note) like load_indname, for damodaran_global_sic.json. A download writes
    this cache only: the US table keeps its own vintage. Never raises."""
    now = now or time.time()
    path = os.path.join(cache_dir, GLOBAL_CACHE)
    cached = _read(path)
    if cached and now - cached.get("at", 0) < INDNAME_TTL_DAYS * 86400:
        return cached["rows"], None
    try:
        import requests as _r
        r = (requests_mod or _r).get(INDNAME_URL, headers={"User-Agent": user_agent}, timeout=timeout)
        r.raise_for_status()
        rows = parse_indname_global(r.content)
        write_global(cache_dir, rows, now, r.headers.get("Last-Modified"))
        return rows, None
    except Exception as e:
        log.warning("indname global rows failed: %s", e)
        if cached:
            return cached["rows"], f"Damodaran global list from {cached.get('fetched')} (refresh failed)"
        return None, f"Damodaran global list unavailable ({type(e).__name__})"


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
        try:                     # the same download carries the non-US rows (R29)
            write_global(cache_dir, parse_indname_global(r.content), now, r.headers.get("Last-Modified"))
        except Exception as e:
            log.warning("indname global rows not cached: %s", e)
        try:                     # and the US company names (R33)
            write_names(cache_dir, parse_indname_names(r.content), now, r.headers.get("Last-Modified"))
        except Exception as e:
            log.warning("indname US names not cached: %s", e)
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


def _frames(cache_dir, year, user_agent, requests_mod=None, timeout=60, now=None, throttle=None):
    """({cik: revenue}, {cik: period end}, note) for calendar year `year`: the largest of
    the FRAMES_CONCEPTS per filer, and the end date of the period SEC's frame put
    in that year (a June filer's FY lands in the CY frames chose, not one we assume).
    A cache written before the end dates were kept returns an empty end map.
    The same fetch writes the panel reading (panel_frames_of): per filer the R35 choice
    over the us-gaap concepts, else ifrs-full Revenue in USD (R34), the entity names
    (R33) and the members filing IFRS in another currency. A failed IFRS call costs the
    IFRS part only (noted in the cache), never the us-gaap reading."""
    from domain import frames_revenue
    now = now or time.time()
    path = os.path.join(cache_dir, f"sec_frames_revenue_CY{year}.json")
    cached = _read(path)
    if throttle is None and requests_mod is None:     # an injected requests_mod paces itself
        from sources import sec_edgar
        throttle = lambda: sec_edgar._throttle(SEC_MIN_INTERVAL)      # noqa: E731
    throttle = throttle or (lambda: None)

    def _from(c):
        return ({int(k): v for k, v in c["rev"].items()},
                {int(k): v for k, v in (c.get("end") or {}).items()})

    # a cache read on a shorter concept list, or before the panel reading, is refreshed
    same_ladder = (bool(cached) and cached.get("concepts") == list(FRAMES_CONCEPTS)
                   and cached.get("panel_schema") == PANEL_SCHEMA and "excise_read" in cached)
    if same_ladder and now - cached.get("at", 0) < FRAMES_TTL_DAYS * 86400:
        return (*_from(cached), None)
    out, end, raw, names, other = {}, {}, {}, {}, {}
    try:
        import requests as _r
        rq = requests_mod or _r

        def get(url):
            throttle()
            r = rq.get(url, headers={"User-Agent": user_agent}, timeout=timeout)
            if getattr(r, "status_code", 200) == 404:
                return []                       # nobody tagged this concept that year
            r.raise_for_status()
            return r.json().get("data") or []

        for concept in FRAMES_CONCEPTS:
            for d in get(FRAMES_URL.format(concept=concept, year=year)):
                if d.get("cik") and isinstance(d.get("val"), (int, float)):
                    cik = int(d["cik"])
                    if d["val"] > out.get(cik, 0):
                        out[cik], end[cik] = d["val"], d.get("end")
                    else:
                        out.setdefault(cik, 0)
                    raw.setdefault(cik, {})["us-gaap:" + concept] = d
                    if d.get("entityName"):
                        names.setdefault(cik, d["entityName"])
        if not out:
            raise ValueError("empty frames")
    except Exception as e:
        log.warning("SEC frames CY%s failed: %s", year, e)
        if cached:
            return (*_from(cached), "SEC revenue frames from cache (refresh failed)")
        return {}, {}, f"SEC revenue frames unavailable ({type(e).__name__})"
    excise_read = True
    try:
        for d in get(FRAMES_URL.format(concept=EXCISE_CONCEPT, year=year)):
            if d.get("cik") and isinstance(d.get("val"), (int, float)) and int(d["cik"]) in raw:
                raw[int(d["cik"])]["us-gaap:" + EXCISE_CONCEPT] = d
    except Exception as e:
        log.warning("SEC excise frames CY%s failed: %s", year, e)
        excise_read = False
    ifrs_note = None
    try:
        for d in get(IFRS_URL.format(unit="USD", year=year)):
            if d.get("cik") and isinstance(d.get("val"), (int, float)):
                cik = int(d["cik"])
                raw.setdefault(cik, {})["ifrs-full:Revenue"] = d
                names.setdefault(cik, d.get("entityName"))
        for cur in IFRS_OTHER_CURRENCIES:
            for d in get(IFRS_URL.format(unit=cur, year=year)):
                if d.get("cik") and int(d["cik"]) not in raw:
                    other.setdefault(int(d["cik"]), cur)
                    names.setdefault(int(d["cik"]), d.get("entityName"))
    except Exception as e:
        log.warning("SEC IFRS frames CY%s failed: %s", year, e)
        ifrs_note = f"CY{year}: อ่านรายได้ IFRS จาก SEC ไม่ได้ ({type(e).__name__}) จึงไม่นับบริษัทที่รายงาน IFRS"
    panel = {}
    for cik, by in raw.items():
        c = frames_revenue.choose({k: {"val": d.get("val"), "end": d.get("end"), "accn": d.get("accn")}
                                   for k, d in by.items()})
        if c:
            panel[cik] = [c["val"], c["end"], c["concept"], c["note"]]
    _write(path, {"at": now, "concepts": list(FRAMES_CONCEPTS), "rev": out, "end": end,
                  "panel_schema": PANEL_SCHEMA, "panel": panel, "name": {k: v for k, v in names.items() if v},
                  "ifrs_other": other, "ifrs_note": ifrs_note, "excise_read": excise_read})
    return out, end, None


def panel_frames_of(cache_dir, year):
    """({cik: [revenue, end, concept, note]}, {cik: entity name}, {cik: currency}) from the
    year's cache file as written by _frames, no fetch. A cache from before the panel
    reading gives its largest-concept values with concept None (load_revenue_frames_years
    notes it)."""
    c = _read(os.path.join(cache_dir, f"sec_frames_revenue_CY{year}.json")) or {}
    if c.get("panel_schema") == PANEL_SCHEMA:
        panel = {int(k): list(v) for k, v in (c.get("panel") or {}).items()}
    else:
        e = c.get("end") or {}
        panel = {int(k): [v, e.get(k), None, None] for k, v in (c.get("rev") or {}).items()}
    return (panel, {int(k): v for k, v in (c.get("name") or {}).items()},
            {int(k): v for k, v in (c.get("ifrs_other") or {}).items()})


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
    """({CY: {cik: [revenue, period end, concept, note]}}, notes) for panel_years(today):
    the panel reading of each year's cache (the same files load_revenue_frames reads),
    rebucketed by frames_revenue.rebucket (R36), so the year before the panel is read too
    (a June filer SEC put there can belong to the first panel year). A year that fails is
    absent and named in the notes. Never raises."""
    from domain import frames_revenue
    years = panel_years(today)
    by_sec, notes = {}, []
    for year in [years[0] - 1] + years:
        rev, _end, note = _frames(cache_dir, year, user_agent, requests_mod, timeout, now)
        if rev:
            by_sec[year] = panel_frames_of(cache_dir, year)[0] or {c: [v, _end.get(c), None, None]
                                                                   for c, v in rev.items()}
            c = _read(os.path.join(cache_dir, f"sec_frames_revenue_CY{year}.json")) or {}
            if c.get("panel_schema") != PANEL_SCHEMA and year in years:
                notes.append(f"CY{year}: รายได้ส่วนแบ่งอุตสาหกรรมมาจากแคชรุ่นเก่า (ค่าที่มากที่สุดในหลาย concept)")
            elif c.get("ifrs_note") and year in years:
                notes.append(c["ifrs_note"])
            if year in years and c.get("excise_read") is not True:
                notes.append(f"CY{year}: ไม่มีข้อมูลภาษีสรรพสามิตของ SEC ในแคชรายได้ จึงยังไม่ปรับรายได้ของผู้ที่แท็กทั้งยอดก่อนและหลังภาษีเป็นรายได้สุทธิ")
        if note and year in years:
            notes.append(f"CY{year}: {note}")
    cy = frames_revenue.rebucket(by_sec)
    return {y: cy[y] for y in years if cy.get(y)}, notes


def load_frame_extras(cache_dir, today):
    """({cik: [SEC entity names, oldest year first]}, {cik: IFRS currency}, [CY whose IFRS
    frames were not read]) over the panel years' caches (plus the year before), read after
    load_revenue_frames_years. No fetch. Never raises."""
    names, other, unread = {}, {}, []
    years = panel_years(today)
    for year in [years[0] - 1] + years:
        try:
            _p, n, o = panel_frames_of(cache_dir, year)
        except Exception as e:                   # a damaged cache costs only the extras
            log.warning("frames extras CY%s: %s", year, e)
            continue
        for k, v in n.items():
            if v not in names.setdefault(k, []):
                names[k].append(v)               # a renamed filer keeps every name it used
        if year in years:
            other.update(o)
            c = _read(os.path.join(cache_dir, f"sec_frames_revenue_CY{year}.json")) or {}
            if c.get("ifrs_note"):
                unread.append(year)
    return names, other, unread
