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
