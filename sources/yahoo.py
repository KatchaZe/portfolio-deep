"""
Yahoo source adapter — forward EPS (adjusted consensus), beta, price, shares,
growth, FX rates, and (later) daily momentum. Parse is separated from fetch so
it is unit-testable against saved quoteSummary fixtures.
"""
import time
import threading

from sources.surprise import grade


def _raw(node):
    if isinstance(node, dict):
        return node.get("raw")
    return node


def parse_consensus(qs_json):
    """From a Yahoo quoteSummary response -> dict of consensus/market fields."""
    out = {"forward_eps": None, "beta": None, "price": None, "shares": None,
           "growth_lt": None, "revenue_growth": None, "market_cap": None}
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
    # quoted for the US-listed security, so already ADR-ratio consistent
    out["market_cap"] = _raw(price.get("marketCap"))
    out["revenue_growth"] = _raw(fin.get("revenueGrowth"))
    annual = []
    for tr in res.get("earningsTrend", {}).get("trend", []):
        if tr.get("period") == "+5y":
            out["growth_lt"] = _raw(tr.get("growth"))
        elif tr.get("period") in ("0y", "+1y") and tr.get("endDate"):
            avg = _raw((tr.get("earningsEstimate") or {}).get("avg"))
            if avg is not None:
                annual.append((tr["endDate"], avg))
    # NTM from this year's and next year's consensus, the clock every source is put
    # on (pipeline/consensus.ntm_eps); forwardEps (next fiscal year) when absent
    if len(annual) == 2:
        from pipeline.consensus import ntm_eps
        out["forward_eps"] = ntm_eps(annual) or out["forward_eps"]
    return out


def parse_revenue_path(qs_json, latest_fy=None):
    """Consensus revenue growth for the fiscal years that end after `latest_fy` (the
    last reported period): [[growth, analysts], ...], nearest year first. Yahoo's "0y"
    row is the current fiscal year, which may already be reported (MSFT's FY ending
    2026-06-30 read as "0y" in July), so rows are kept by date, not by label. Growth is
    Yahoo's own year-over-year figure, a ratio, so a DKK filer needs no FX."""
    try:
        res = qs_json["quoteSummary"]["result"][0]
    except Exception:
        return []
    rows = []
    for tr in (res.get("earningsTrend") or {}).get("trend", []):
        if tr.get("period") not in ("0y", "+1y") or not tr.get("endDate"):
            continue
        if latest_fy and str(tr["endDate"]) <= str(latest_fy)[:10]:
            continue
        rev = tr.get("revenueEstimate") or {}
        g, n = _raw(rev.get("growth")), _raw(rev.get("numberOfAnalysts"))
        if g is None:
            continue
        rows.append((tr["endDate"], float(g), int(n) if n else 0))
    rows.sort()
    return [[round(g, 4), n] for _, g, n in rows]


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
        # Yahoo's surprisePercent raw is a FRACTION (0.041 = +4.1%), not a percent.
        # Compute from act/est (unit-unambiguous); use the fraction x100 only when
        # the estimate is missing.
        sp = None
        if act is not None and est not in (None, 0):
            sp = (act - est) / abs(est) * 100
        else:
            raw_sp = _raw(h.get("surprisePercent"))
            if raw_sp is not None:
                sp = raw_sp * 100
        if act is None and est is None:
            continue
        out.append({"quarter": h.get("quarter", {}).get("fmt") if isinstance(h.get("quarter"), dict)
                    else _raw(h.get("quarter")),
                    "eps_actual": act, "eps_estimate": est,
                    "surprise_pct": round(sp, 1) if sp is not None else None,
                    "grade": grade(sp)})
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


# Cache (session, crumb) per process: the warm-up costs 2 requests + a crumb, too
# much per attempt per ticker. requests.Session is thread-safe for concurrent GETs
# (urllib3 pool), so the parallel refresh can share it.
_sess_lock = threading.Lock()
_sess_cached = None


def _get_session(requests_mod, force_new=False):
    global _sess_cached
    with _sess_lock:
        if _sess_cached is None or force_new:
            _sess_cached = _session(requests_mod)
        return _sess_cached


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
    injected = requests_mod is not None and requests_mod is not _r   # tests inject fakes
    requests_mod = requests_mod or _r
    last = {"_error": "no attempt"}
    for attempt in range(1, retries + 1):
        # reuse the cached session on the first attempt; re-warm on retries
        # (a blocked/expired crumb is the usual reason the first round failed).
        s, crumb = (_session(requests_mod) if injected
                    else _get_session(requests_mod, force_new=(attempt > 1)))
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


# Yahoo also files price adjustments for spin-offs and small stock dividends as
# "splits" (PFE 2020-11-17 x1.054 for the Viatris spin-off, TSM 2009 x1.005). Filers do
# not restate per-share history for those, so only real splits (>= 5:4, <= 4:5) count.
MIN_SPLIT_RATIO = 1.25


def parse_splits(chart_result):
    """[(ex-date "YYYY-MM-DD", ratio)] oldest first from a chart result's split events
    (TSLA: 2020-08-31 5:1, 2022-08-25 3:1). A reverse split has a ratio below 1."""
    import datetime as dt
    out = []
    events = ((chart_result or {}).get("events") or {}).get("splits") or {}
    for ev in events.values():
        try:
            num, den = float(ev["numerator"]), float(ev["denominator"])
            day = dt.datetime.fromtimestamp(int(ev["date"]), dt.timezone.utc).strftime("%Y-%m-%d")
        except (KeyError, TypeError, ValueError):
            continue
        r = num / den if num > 0 and den > 0 else 1.0
        if r >= MIN_SPLIT_RATIO or r <= 1 / MIN_SPLIT_RATIO:
            out.append((day, r))
    return sorted(out)


def fetch_splits(ticker, requests_mod=None, timeout=15):
    """Full split history of a listing, from one quarterly max-range chart call."""
    import requests as _r
    requests_mod = requests_mod or _r
    last_err = None
    for host in ("query1", "query2"):
        try:
            r = requests_mod.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}",
                                 params={"range": "max", "interval": "3mo", "events": "splits"},
                                 headers=_UA, timeout=timeout)
            r.raise_for_status()
            result = (r.json().get("chart") or {}).get("result")
            if result:
                return parse_splits(result[0])
        except Exception as e:
            last_err = e
    raise last_err or RuntimeError(f"split history unavailable for {ticker}")


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
