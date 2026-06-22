"""
Finnhub source adapter — FREE tier (60 req/min, no card). Used to add a THIRD
EPS-surprise source (cross-check vs Yahoo/FMP) and a best-effort forward-EPS
estimate for the consensus blend. Key from FINNHUB_API_KEY (optional — without it
this source is simply skipped).

Fetch (network) is separated from parse (pure) so the parsers are unit-testable
offline. Endpoints:
  /stock/earnings?symbol=X      -> EPS surprise history  (FREE)
  /stock/eps-estimate?symbol=X  -> forward EPS estimate  (often PREMIUM -> {} / skip)
"""
BASE = "https://finnhub.io/api/v1"


def _grade(surprise_pct):
    """BEAT/MEET/MISS at ±2% — same rule as the Yahoo/FMP adapters so all sources
    are directly comparable."""
    if surprise_pct is None:
        return None
    if surprise_pct > 2:
        return "beat"
    if surprise_pct < -2:
        return "miss"
    return "meet"


def parse_earnings(raw):
    """Finnhub /stock/earnings -> [{quarter, eps_actual, eps_estimate, surprise_pct,
    grade}], oldest->newest, last 4. Mirrors yahoo/fmp parse_earnings exactly so the
    display + reconcile code is source-agnostic. Skips not-yet-reported rows."""
    rows = raw if isinstance(raw, list) else []
    out = []
    for e in rows:
        if not isinstance(e, dict):
            continue
        act = e.get("actual")
        est = e.get("estimate")
        if act is None:                       # future / unreported
            continue
        sp = e.get("surprisePercent")
        if sp is None and est not in (None, 0):
            sp = (act - est) / abs(est) * 100
        out.append({"quarter": e.get("period"), "eps_actual": act, "eps_estimate": est,
                    "surprise_pct": round(sp, 1) if sp is not None else None,
                    "grade": _grade(sp)})
    out.sort(key=lambda x: x["quarter"] or "")
    return out[-4:]


def parse_eps_estimate(raw):
    """Finnhub /stock/eps-estimate -> nearest future-period epsAvg (forward EPS), or
    None. Premium on many plans -> raw is {} / has no 'data' -> returns None safely."""
    data = (raw or {}).get("data") if isinstance(raw, dict) else None
    if not isinstance(data, list) or not data:
        return None
    rows = [d for d in data if isinstance(d, dict) and d.get("period") and d.get("epsAvg")]
    if not rows:
        return None
    rows.sort(key=lambda d: d["period"])
    try:
        return float(rows[0]["epsAvg"])
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
#  FETCH (network — needs FINNHUB_API_KEY)                                      #
# --------------------------------------------------------------------------- #
def fetch_earnings(symbol, key, requests_mod=None, timeout=15, limit=8):
    """EPS surprise history (FREE). Returns a raw list or [] on any error / no key."""
    if not key:
        return []
    import requests as _r
    requests_mod = requests_mod or _r
    try:
        r = requests_mod.get(f"{BASE}/stock/earnings",
                             params={"symbol": symbol, "limit": limit, "token": key},
                             timeout=timeout)
        if r.status_code != 200:
            return []
        j = r.json()
        return j if isinstance(j, list) else []
    except Exception:
        return []


def fetch_eps_estimate(symbol, key, requests_mod=None, timeout=15, freq="annual"):
    """Forward EPS estimate (often PREMIUM). Returns a raw dict or {} — caller treats
    a missing value as 'this source unavailable' (the blend still works on the rest)."""
    if not key:
        return {}
    import requests as _r
    requests_mod = requests_mod or _r
    try:
        r = requests_mod.get(f"{BASE}/stock/eps-estimate",
                             params={"symbol": symbol, "freq": freq, "token": key},
                             timeout=timeout)
        if r.status_code != 200:
            return {}
        j = r.json()
        return j if isinstance(j, dict) else {}
    except Exception:
        return {}
