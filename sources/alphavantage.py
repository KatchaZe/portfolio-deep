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
    return j if isinstance(j, dict) else {}


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
    if not isinstance(j, dict) or not isinstance(j.get("estimates"), list):
        return {}               # 'Note' / 'Information' = throttled or premium
    return j


def cached(kind, symbol, key, fetch, cache_dir, ttl_days=ESTIMATES_TTL_DAYS, now=None):
    """(raw, calls). A non-empty answer is reused for `ttl_days`: the free tier is 25
    calls a day, shared by every function, so nothing here is fetched per refresh."""
    import json
    import os
    import time
    now = time.time() if now is None else now
    path = os.path.join(cache_dir, f"av_{kind}_{symbol}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            c = json.load(fh)
        if c.get("data") and now - c.get("at", 0) < ttl_days * 86400:
            return c["data"], 0
    except (OSError, ValueError):
        pass
    if not key:
        return {}, 0
    data = fetch(symbol, key)
    if data:
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
