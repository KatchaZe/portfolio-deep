"""
Yahoo source adapter — forward EPS (adjusted consensus), beta, price, shares,
growth, FX rates, and (later) daily momentum. Parse is separated from fetch so
it is unit-testable against saved quoteSummary fixtures.
"""
import time


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


def parse_revenue_estimate(qs_json):
    """Current-quarter (not-yet-reported) revenue consensus from earningsTrend.
    Returns {quarter_end, estimate} or None. We snapshot this each refresh and
    later compare to the SEC actual for that quarter (build-our-own surprise,
    since Yahoo doesn't expose *historical* revenue estimates for free)."""
    try:
        res = qs_json["quoteSummary"]["result"][0]
    except Exception:
        return None
    for tr in res.get("earningsTrend", {}).get("trend", []):
        if tr.get("period") == "0q":                     # the quarter about to report
            est = _raw((tr.get("revenueEstimate") or {}).get("avg"))
            end = tr.get("endDate")                       # 'YYYY-MM-DD'
            if est and end:
                return {"quarter_end": end, "estimate": float(est)}
            return None
    return None


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


_QS_MODULES = "defaultKeyStatistics,financialData,earningsTrend,earningsHistory,price"


def _qs_ok(j):
    """True when a quoteSummary response actually carries a result block."""
    try:
        return bool(j["quoteSummary"]["result"])
    except Exception:
        return False


def fetch_consensus(ticker, requests_mod=None, timeout=15, retries=3, backoff=1.5):
    """quoteSummary with retry + host rotation. Yahoo blocks/empties responses
    intermittently (esp. from datacenter IPs); we try query2 then query1, re-warm
    the session each round, and back off between rounds. Returns the last response
    (possibly an error dict) if every attempt is degraded — callers/normalize
    already flag a missing result block."""
    import requests as _r
    requests_mod = requests_mod or _r
    last = {"_error": "no attempt"}
    for attempt in range(1, retries + 1):
        s, crumb = _session(requests_mod)
        for host in ("query2", "query1"):
            try:
                r = s.get(f"https://{host}.finance.yahoo.com/v10/finance/quoteSummary/{ticker}",
                          params={"modules": _QS_MODULES, "crumb": crumb}, timeout=timeout)
                if r.status_code == 200:
                    j = r.json()
                    if _qs_ok(j):
                        return j                       # got real data — done
                    last = j
                else:
                    last = {"_error": f"http {r.status_code}"}
            except Exception as e:
                last = {"_error": str(e)[:120]}
        if attempt < retries:
            time.sleep(backoff * attempt)              # 1.5s, 3.0s, …
    return last


def fetch_chart(ticker, requests_mod=None, rng="3mo", interval="1d", timeout=20,
                retries=3, backoff=1.5):
    """Daily closes + volumes + dates for momentum. Retries + host rotation so a
    single throttled response doesn't blank out momentum."""
    import requests as _r
    requests_mod = requests_mod or _r
    import datetime as dt
    last_err = None
    res = None
    for attempt in range(1, retries + 1):
        for host in ("query1", "query2"):
            try:
                r = requests_mod.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}",
                                     params={"range": rng, "interval": interval,
                                             "events": "div,splits"},
                                     headers=_UA, timeout=timeout)
                r.raise_for_status()
                result = (r.json().get("chart") or {}).get("result")
                if result:
                    res = result[0]
                    break
            except Exception as e:
                last_err = e
        if res is not None:
            break
        if attempt < retries:
            time.sleep(backoff * attempt)
    if res is None:
        raise last_err or RuntimeError(f"chart unavailable for {ticker}")
    q = res["indicators"]["quote"][0]
    # adjusted close (split+dividend) for momentum; absent on some responses
    adj = None
    adj_node = res["indicators"].get("adjclose")
    if adj_node and isinstance(adj_node, list) and adj_node:
        adj = adj_node[0].get("adjclose")
    ts = res["timestamp"]
    closes, adj_closes, vols, dates = [], [], [], []
    for i in range(len(ts)):
        c, v = q["close"][i], q["volume"][i]
        if c is not None and v is not None:
            closes.append(float(c)); vols.append(float(v))
            a = adj[i] if (adj and i < len(adj) and adj[i] is not None) else c
            adj_closes.append(float(a))
            dates.append(dt.datetime.fromtimestamp(ts[i], dt.timezone.utc).strftime("%Y-%m-%d"))
    out = {"closes": closes, "volumes": vols, "dates": dates}
    if adj is not None:
        out["adj_closes"] = adj_closes          # present only when Yahoo returned adjclose
    return out


def pe_percentile_5y(eps_dated, closes, dates, price, current_eps):
    """Where the current trailing P/E sits in its OWN ~5y range: 0=cheapest, 1=richest.
    Re-rating / mean-reversion signal for the v8.2 Price pillar. None if not enough data.

      eps_dated = [[FY_end, diluted_EPS], …]  (from SEC, any order)
      closes/dates = ascending monthly price series (from Yahoo 5y chart)

    Builds a historical P/E point per fiscal year (year-end price ÷ that FY's EPS),
    adds the current P/E, and returns the current point's position in [min,max]. Pure."""
    if not eps_dated or not closes or not dates or not price or not current_eps or current_eps <= 0:
        return None
    paired = sorted(zip(dates, closes), key=lambda x: x[0])
    pes = []
    for end, eps in eps_dated:
        if not eps or eps <= 0:
            continue
        px = None
        for d, c in paired:                 # last close on/before the FY end
            if d <= end:
                px = c
            else:
                break
        if px:
            pes.append(px / eps)
    if len(pes) < 2:
        return None
    cur_pe = price / current_eps
    pes.append(cur_pe)
    lo, hi = min(pes), max(pes)
    if hi <= lo:
        return 0.5
    return max(0.0, min(1.0, (cur_pe - lo) / (hi - lo)))


def fetch_treasury_10y(requests_mod=None, timeout=12):
    """10-year US treasury yield as (decimal, live_bool). Uses ^TNX.
    live=False means Yahoo failed and the hardcoded 4.3% fallback is used —
    callers must surface this (DEEP principle: 'Risk is current')."""
    try:
        d = fetch_chart("%5ETNX", requests_mod=requests_mod, rng="5d", timeout=timeout)
        if d["closes"]:
            return d["closes"][-1] / 100.0, True
    except Exception:
        pass
    return 0.043, False


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
