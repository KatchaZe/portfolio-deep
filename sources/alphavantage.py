"""
Alpha Vantage source adapter — FREE tier (25 req/day). OPTIONAL 4th EPS-surprise
source: its EARNINGS function returns *historical* quarterly reported-vs-estimated
EPS, so unlike our build-forward revenue history it gives an immediate surprise
track record. Off unless ALPHAVANTAGE_API_KEY is set (the daily cap is tight, so
treat it as a cross-check, not a per-refresh call for many tickers).

Fetch (network) separated from parse (pure) for offline unit tests.
Endpoint:  /query?function=EARNINGS&symbol=X&apikey=KEY
"""
from sources.jsonapi import get_json
from sources.surprise import grade

BASE = "https://www.alphavantage.co/query"


def _f(x):
    try:
        if x in (None, "", "None"):
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_earnings(raw):
    """Alpha Vantage EARNINGS -> [{quarter, eps_actual, eps_estimate, surprise_pct,
    grade}], oldest->newest, last 4. Mirrors the other adapters. Empty/throttled
    responses ('Note'/'Information' keys) yield []."""
    q = (raw or {}).get("quarterlyEarnings") if isinstance(raw, dict) else None
    if not isinstance(q, list):
        return []
    out = []
    for e in q[:8]:
        if not isinstance(e, dict):
            continue
        act = _f(e.get("reportedEPS"))
        est = _f(e.get("estimatedEPS"))
        if act is None:
            continue
        sp = _f(e.get("surprisePercentage"))
        if sp is None and est not in (None, 0):
            sp = (act - est) / abs(est) * 100
        out.append({"quarter": e.get("fiscalDateEnding"), "eps_actual": act,
                    "eps_estimate": est,
                    "surprise_pct": round(sp, 1) if sp is not None else None,
                    "grade": grade(sp)})
    out.sort(key=lambda x: x["quarter"] or "")
    return out[-4:]


def fetch_earnings(symbol, key, requests_mod=None, timeout=20):
    """Historical EPS surprise (FREE, 25/day). Returns a raw dict or {} on any error
    / no key."""
    if not key:
        return {}
    import requests as _r
    j = get_json(BASE, requests_mod or _r,
                 params={"function": "EARNINGS", "symbol": symbol, "apikey": key},
                 timeout=timeout)
    if refusal(j):
        return j                 # handed back so `cached` can name the refusal
    return j if isinstance(j, dict) and isinstance(j.get("quarterlyEarnings"), list) else {}


# --------------------------------------------------------------------------- #
#  EARNINGS_ESTIMATES: the third consensus source (after FMP and Yahoo)        #
# --------------------------------------------------------------------------- #
# Free tier (checked with the user's key on ORCL, 2026-10-21). Rows come as
# {"date", "horizon": "fiscal year" | "fiscal quarter", "revenue_estimate_average",
#  "revenue_estimate_analyst_count", "eps_estimate_average", ...}, strings throughout.
# Only "fiscal year" rows that end after the last reported fiscal year are kept.
ESTIMATES_TTL_DAYS = 7


def _year_rows(raw, latest_fy=None):
    """Unreported fiscal-year rows, oldest first: [(date, revenue, n_rev, eps, n_eps)]."""
    rows = (raw or {}).get("estimates") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return []
    out = []
    for e in rows:
        if not isinstance(e, dict) or e.get("horizon") != "fiscal year" or not e.get("date"):
            continue
        if latest_fy and str(e["date"])[:10] <= str(latest_fy)[:10]:
            continue
        out.append((str(e["date"])[:10], _f(e.get("revenue_estimate_average")),
                    int(_f(e.get("revenue_estimate_analyst_count")) or 0),
                    _f(e.get("eps_estimate_average")),
                    int(_f(e.get("eps_estimate_analyst_count")) or 0)))
    out.sort()
    return out


def parse_estimate_path(raw, latest_fy=None, latest_revenue=None):
    """[[growth, analysts], ...] from the last ACTUAL fiscal year's revenue, the same
    shape FMP and Yahoo hand the engine (which applies the analyst and growth rules).
    [] without an actual-year anchor or an unreported year."""
    if not latest_revenue or latest_revenue <= 0:
        return []
    seq, path = latest_revenue, []
    for _, rev, n, _, _ in _year_rows(raw, latest_fy):
        if not rev or rev <= 0:
            break
        path.append([round(rev / seq - 1, 4), n])
        seq = rev
    return path


def parse_ntm_eps(raw, latest_fy=None, today=None):
    """Next-twelve-months EPS from the unreported fiscal-year rows (the clock every
    other forward-EPS candidate is put on), or None."""
    from pipeline.consensus import ntm_eps
    pts = [(d, eps) for d, _, _, eps, _ in _year_rows(raw, latest_fy) if eps]
    return ntm_eps(pts, today=today) if pts else None


def fetch_estimates(symbol, key, requests_mod=None, timeout=20):
    """EARNINGS_ESTIMATES raw dict, {} on any error, a throttle note or no key."""
    if not key:
        return {}
    import requests as _r
    j = get_json(BASE, requests_mod or _r,
                 params={"function": "EARNINGS_ESTIMATES", "symbol": symbol, "apikey": key},
                 timeout=timeout)
    if refusal(j):
        return j
    if not isinstance(j, dict) or not isinstance(j.get("estimates"), list):
        return {}
    return j


# Every Alpha Vantage call in a run goes through `cached`, which (live 2026-10-24) has
# to hold three rules the free tier enforces silently:
#   * 1 request per second: the parallel refresh fired 4 at once, and AV answered
#     {"Information": "...1 request per second..."} with HTTP 200;
#   * 25 requests a day, for every function together;
#   * a refusal is not data: 12 tickers had that "Information" reply cached as their
#     EARNINGS history for 7 days, so their surprise track came out empty with no flag.
REFUSAL_KEYS = ("Information", "Note", "Error Message")
MIN_GAP_SECS = 1.2
DAILY_CAP = 24                 # one under the free tier, so a manual check still fits
# The quarterly statements move a fair value (ASML/TSM/NVO, 9 calls a week); EARNINGS
# and EARNINGS_ESTIMATES only feed cross-checks. Those stop at DAILY_CAP - RESERVED so a
# cold cache of 21 earnings histories cannot crowd the statements out of the day.
RESERVED_FOR_PRIORITY = 9
_gate = __import__("threading").Lock()
_last_call = [0.0]


def refusal(data):
    """Why AV refused ('Information'/'Note'/'Error Message'), in our words, else None.

    Never the reply's own text: the daily-limit reply quotes the API key ("We have
    detected your API key as ..."), which then sat on the card and in portfolio.json
    (live 2026-10-24)."""
    if isinstance(data, dict):
        for k in REFUSAL_KEYS:
            if k in data and len(data) <= 2:
                t = str(data[k]).lower()
                if "per day" in t or "daily" in t:
                    return "Alpha Vantage daily limit reached"
                if "per second" in t or "per minute" in t or "sparingly" in t:
                    return "Alpha Vantage rate limit (1 request per second)"
                if "premium" in t:
                    return "Alpha Vantage: premium endpoint"
                if "invalid" in t or k == "Error Message":
                    return "Alpha Vantage rejected the request (symbol or key)"
                return "Alpha Vantage refused the request"
    return None


def _take_daily(cache_dir, now, priority=False):
    """True when one more call fits today's cap (a small counter file, under the gate)."""
    import datetime as _dt
    import json
    import os
    path = os.path.join(cache_dir, "av_usage.json")
    day = _dt.datetime.utcfromtimestamp(now).date().isoformat()
    try:
        with open(path, encoding="utf-8") as fh:
            u = json.load(fh)
    except (OSError, ValueError):
        u = {}
    n = u.get("n", 0) if u.get("date") == day else 0
    if n >= (DAILY_CAP if priority else DAILY_CAP - RESERVED_FOR_PRIORITY):
        return False
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"date": day, "n": n + 1}, fh)
    except OSError:
        pass
    return True


def cached(kind, symbol, key, fetch, cache_dir, ttl_days=ESTIMATES_TTL_DAYS, now=None, sleep=None,
           priority=False):
    """(raw, calls). A real answer is reused for `ttl_days`. A refusal, the daily cap
    or an empty answer comes back as {"_refused": reason} and is never cached, so the
    caller can say why instead of reading it as "no data"."""
    import json
    import os
    import time
    now = time.time() if now is None else now
    path = os.path.join(cache_dir, f"av_{kind}_{symbol}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            c = json.load(fh)
        if c.get("data") and not refusal(c["data"]) and now - c.get("at", 0) < ttl_days * 86400:
            return c["data"], 0
    except (OSError, ValueError):
        pass
    if not key:
        return {"_refused": "ALPHAVANTAGE_API_KEY is not set"}, 0
    with _gate:                                    # one AV request at a time, spaced
        if not _take_daily(cache_dir, now, priority):
            return {"_refused": f"Alpha Vantage daily limit ({DAILY_CAP}) reached"}, 0
        wait = MIN_GAP_SECS - (time.time() - _last_call[0])
        if wait > 0:
            (sleep or time.sleep)(wait)
        try:
            data = fetch(symbol, key)
        finally:
            _last_call[0] = time.time()
    why = refusal(data)
    if why or not data:
        return {"_refused": why or "Alpha Vantage returned nothing"}, 1
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"at": now, "data": data}, fh)
    except OSError:
        pass
    return data, 1


# --------------------------------------------------------------------------- #
#  Quarterly statements (INCOME_STATEMENT / BALANCE_SHEET / CASH_FLOW)         #
# --------------------------------------------------------------------------- #
# For a foreign filer whose SEC base is a year-old 20-F (its quarters arrive as 6-K
# with no XBRL). Live check 2026-10-24: ASML and TSM to Q3 2026, NVO to Q2 2026; the
# FY2025 quarters sum to the filed 20-F figures exactly for ASML and NVO.
STATEMENTS = ("INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW")
STATEMENT_TTL_DAYS = 7


def fetch_statement(function, symbol, key, requests_mod=None, timeout=30):
    """Raw AV statement, or {} when the answer has no quarterly reports (a throttle
    'Note'/'Information' reply must never be cached as data)."""
    import requests as _r
    j = get_json(BASE, requests_mod or _r, timeout=timeout,
                 params={"function": function, "symbol": symbol, "apikey": key}) or {}
    if refusal(j):
        return j
    return j if isinstance(j, dict) and j.get("quarterlyReports") else {}


def parse_quarters(raw):
    """{fiscalDateEnding: {AV field: float or None, 'currency': str or None}}."""
    out = {}
    for q in (raw or {}).get("quarterlyReports") or []:
        end = q.get("fiscalDateEnding")
        if not end:
            continue
        cur = q.get("reportedCurrency")
        row = {k: _f(v) for k, v in q.items() if k not in ("fiscalDateEnding", "reportedCurrency")}
        row["currency"] = cur if cur not in (None, "", "None") else None
        out[end] = row
    return out
