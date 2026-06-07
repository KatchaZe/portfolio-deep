"""
Yahoo source adapter — forward EPS (adjusted consensus), beta, price, shares,
growth, FX rates, and (later) daily momentum. Parse is separated from fetch so
it is unit-testable against saved quoteSummary fixtures.
"""


def _raw(node):
    if isinstance(node, dict):
        return node.get("raw")
    return node


def parse_consensus(qs_json):
    """From a Yahoo quoteSummary response -> dict of consensus/market fields."""
    out = {"forward_eps": None, "beta": None, "price": None, "shares": None,
           "growth_lt": None, "revenue_growth": None}
    try:
        res = qs_json["quoteSummary"]["result"][0]
    except Exception:
        return out
    dks = res.get("defaultKeyStatistics", {}) or {}
    fin = res.get("financialData", {}) or {}
    price = res.get("price", {}) or {}
    out["forward_eps"] = _raw(dks.get("forwardEps"))
    out["beta"] = _raw(dks.get("beta"))
    out["shares"] = _raw(dks.get("sharesOutstanding")) or _raw(price.get("sharesOutstanding"))
    out["price"] = _raw(price.get("regularMarketPrice")) or _raw(fin.get("currentPrice"))
    out["revenue_growth"] = _raw(fin.get("revenueGrowth"))
    for tr in res.get("earningsTrend", {}).get("trend", []):
        if tr.get("period") == "+5y":
            out["growth_lt"] = _raw(tr.get("growth"))
            break
    return out


def _grade(surprise_pct):
    """BEAT/MEET/MISS from an EPS surprise percent (threshold ±2%)."""
    if surprise_pct is None:
        return None
    if surprise_pct > 2:
        return "beat"
    if surprise_pct < -2:
        return "miss"
    return "meet"


def parse_earnings_history(qs_json):
    """From a Yahoo quoteSummary response -> list of recent EPS-surprise quarters,
    oldest first. Each: {quarter, eps_actual, eps_estimate, surprise_pct, grade}.
    Yahoo's earningsHistory returns ~4 quarters; EPS only (the street/adjusted
    consensus basis). Returns [] when the module is absent (e.g. old fixtures)."""
    try:
        res = qs_json["quoteSummary"]["result"][0]
    except Exception:
        return []
    hist = (res.get("earningsHistory") or {}).get("history") or []
    out = []
    for h in hist:
        act = _raw(h.get("epsActual"))
        est = _raw(h.get("epsEstimate"))
        sp = _raw(h.get("surprisePercent"))
        if sp is None and act is not None and est not in (None, 0):
            sp = (act - est) / abs(est) * 100
        if act is None and est is None:
            continue
        out.append({"quarter": h.get("quarter", {}).get("fmt") if isinstance(h.get("quarter"), dict)
                    else _raw(h.get("quarter")),
                    "eps_actual": act, "eps_estimate": est,
                    "surprise_pct": round(sp, 1) if sp is not None else None,
                    "grade": _grade(sp)})
    # earningsHistory is newest-first; present oldest -> newest for the circle row
    return list(reversed(out))[-4:]


# --------------------------------------------------------------------------- #
#  FETCH (network)                                                             #
# --------------------------------------------------------------------------- #
_UA = {"User-Agent": "Mozilla/5.0"}
_fx_cache = {}


def _session(requests_mod):
    s = requests_mod.Session()
    s.headers.update(_UA)
    for u in ("https://fc.yahoo.com", "https://finance.yahoo.com"):
        try:
            s.get(u, timeout=8)
        except Exception:
            pass
    crumb = ""
    try:
        crumb = s.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=8).text.strip()
    except Exception:
        pass
    return s, crumb


def fetch_consensus(ticker, requests_mod=None, timeout=15):
    import requests as _r
    requests_mod = requests_mod or _r
    s, crumb = _session(requests_mod)
    try:
        r = s.get(f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}",
                  params={"modules": "defaultKeyStatistics,financialData,earningsTrend,earningsHistory,price", "crumb": crumb},
                  timeout=timeout)
        return r.json()
    except Exception as e:
        return {"_error": str(e)[:120]}


def fetch_chart(ticker, requests_mod=None, rng="3mo", interval="1d", timeout=20):
    """Daily closes + volumes + dates for momentum."""
    import requests as _r
    requests_mod = requests_mod or _r
    import datetime as dt
    r = requests_mod.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                         params={"range": rng, "interval": interval}, headers=_UA, timeout=timeout)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    ts = res["timestamp"]
    closes, vols, dates = [], [], []
    for i in range(len(ts)):
        c, v = q["close"][i], q["volume"][i]
        if c is not None and v is not None:
            closes.append(float(c)); vols.append(float(v))
            dates.append(dt.datetime.utcfromtimestamp(ts[i]).strftime("%Y-%m-%d"))
    return {"closes": closes, "volumes": vols, "dates": dates}


def fetch_treasury_10y(requests_mod=None, timeout=12):
    """10-year US treasury yield as a decimal (e.g. 0.043). Uses ^TNX."""
    try:
        d = fetch_chart("%5ETNX", requests_mod=requests_mod, rng="5d", timeout=timeout)
        if d["closes"]:
            return d["closes"][-1] / 100.0
    except Exception:
        pass
    return 0.043


def fetch_fx_to_usd(currency, requests_mod=None, timeout=12):
    """How many USD one unit of `currency` is worth (e.g. DKK ~0.145)."""
    import requests as _r
    requests_mod = requests_mod or _r
    ccy = (currency or "USD").upper()
    if ccy == "USD":
        return 1.0
    if ccy in _fx_cache:
        return _fx_cache[ccy]
    for sym, inv in ((f"{ccy}USD=X", False), (f"USD{ccy}=X", True)):
        try:
            r = requests_mod.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                                 params={"range": "5d", "interval": "1d"}, headers=_UA, timeout=timeout)
            c = r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            v = [x for x in c if x][-1]
            rate = (1.0 / v) if inv else v
            _fx_cache[ccy] = rate
            return rate
        except Exception:
            continue
    return None
