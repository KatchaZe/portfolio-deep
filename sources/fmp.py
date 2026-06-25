"""
FMP source adapter — fetch (network) separated from parse (pure).
"""
import time

import config

BASE = config.FMP_BASE

ENDPOINTS = {
    "profile": {},
    "quote": {},
    "income_annual": {"_ep": "income-statement", "period": "annual", "limit": 5},
    "income_ttm": {"_ep": "income-statement-ttm"},
    "balance_annual": {"_ep": "balance-sheet-statement", "period": "annual", "limit": 2},
    "cashflow_annual": {"_ep": "cash-flow-statement", "period": "annual", "limit": 5},
    "key_metrics_ttm": {"_ep": "key-metrics-ttm"},
    "estimates": {"_ep": "analyst-estimates", "period": "annual", "limit": 6},
}


def fetch(ticker, key, sleep=1.2, requests_mod=None):
    import requests as _r
    requests_mod = requests_mod or _r
    bundle, calls = {}, 0
    for name, spec in ENDPOINTS.items():
        ep = spec.get("_ep", name)
        params = {k: v for k, v in spec.items() if not k.startswith("_")}
        params.update({"symbol": ticker, "apikey": key})
        data = None
        for attempt in (1, 2):
            calls += 1
            try:
                r = requests_mod.get(f"{BASE}/{ep}", params=params, timeout=25)
            except Exception as e:
                data = {"_error": str(e)[:160]}
                break
            if r.status_code == 429:
                time.sleep(6)
                continue
            if r.status_code != 200:
                data = {"_status": r.status_code, "_body": r.text[:200]}
                break
            try:
                data = r.json()
            except Exception:
                data = {"_status": 200, "_body": r.text[:200]}
            break
        bundle[name] = data
        time.sleep(sleep)
    bundle["_calls"] = calls
    return bundle


def fetch_earnings(ticker, key, requests_mod=None, timeout=20, limit=8):
    import requests as _r
    requests_mod = requests_mod or _r
    attempts = [
        (f"{BASE}/earnings", {"symbol": ticker, "limit": limit, "apikey": key}),
        (f"{config.FMP_LEGACY}/earnings-surprises/{ticker}", {"apikey": key}),
        (f"{config.FMP_LEGACY}/historical/earning_calendar/{ticker}", {"apikey": key}),
    ]
    for url, params in attempts:
        try:
            r = requests_mod.get(url, params=params, timeout=timeout)
            if r.status_code != 200:
                continue
            j = r.json()
            if isinstance(j, list) and j:
                return j
        except Exception:
            continue
    return []


def fetch_quote(ticker, key, requests_mod=None, timeout=15):
    import requests as _r
    requests_mod = requests_mod or _r
    attempts = [
        (f"{BASE}/quote", {"symbol": ticker, "apikey": key}),
        (f"{config.FMP_LEGACY}/quote/{ticker}", {"apikey": key}),
    ]
    for url, params in attempts:
        try:
            r = requests_mod.get(url, params=params, timeout=timeout)
            if r.status_code != 200:
                continue
            j = r.json()
            if isinstance(j, list) and j:
                return j
        except Exception:
            continue
    return []


def fetch_estimates(ticker, key, requests_mod=None, timeout=20, limit=6):
    import requests as _r
    requests_mod = requests_mod or _r
    attempts = [
        (f"{BASE}/analyst-estimates", {"symbol": ticker, "period": "annual", "limit": limit, "apikey": key}),
        (f"{config.FMP_LEGACY}/analyst-estimates/{ticker}", {"period": "annual", "limit": limit, "apikey": key}),
    ]
    for url, params in attempts:
        try:
            r = requests_mod.get(url, params=params, timeout=timeout)
            if r.status_code != 200:
                continue
            j = r.json()
            if isinstance(j, list) and j:
                return j
        except Exception:
            continue
    return []


def fetch_estimates_quarter(ticker, key, requests_mod=None, timeout=20, limit=12):
    """Analyst estimates at QUARTERLY granularity — gives a per-quarter EPS &
    revenue estimate we can pair with SEC actuals (surprise_backfill). Same endpoint
    family as fetch_estimates, just period=quarter. Returns a raw list or []."""
    import requests as _r
    requests_mod = requests_mod or _r
    attempts = [
        (f"{BASE}/analyst-estimates", {"symbol": ticker, "period": "quarter", "limit": limit, "apikey": key}),
        (f"{config.FMP_LEGACY}/analyst-estimates/{ticker}", {"period": "quarter", "limit": limit, "apikey": key}),
    ]
    for url, params in attempts:
        try:
            r = requests_mod.get(url, params=params, timeout=timeout)
            if r.status_code != 200:
                continue
            j = r.json()
            if isinstance(j, list) and j:
                return j
        except Exception:
            continue
    return []


def parse_estimates_quarterly(estimates):
    """FMP quarterly analyst-estimates -> {quarter_end(YYYY-MM-DD): {eps_est, rev_est}}.
    Defensive about field names; skips rows missing a usable date. Pure.
    Paired with SEC actuals in pipeline.surprise_backfill."""
    out = {}
    for e in (estimates or []):
        if not isinstance(e, dict):
            continue
        d = e.get("date") or e.get("period")
        if not d:
            continue
        d = str(d)[:10]
        eps_est = _num(e, "epsAvg", "epsAvgEstimate", "estimatedEpsAvg", "estimatedEps")
        rev_est = _num(e, "revenueAvg", "estimatedRevenueAvg", "revenueAvgEstimate", "estimatedRevenue")
        if eps_est is None and rev_est is None:
            continue
        out[d] = {"eps_est": eps_est, "rev_est": rev_est}
    return out


def fetch_peers(ticker, key, requests_mod=None, timeout=15):
    import requests as _r
    requests_mod = requests_mod or _r
    attempts = [
        (f"{BASE}/stock-peers", {"symbol": ticker, "apikey": key}),
        (f"{config.FMP_LEGACY}/stock_peers", {"symbol": ticker, "apikey": key}),
    ]
    for url, params in attempts:
        try:
            r = requests_mod.get(url, params=params, timeout=timeout)
            if r.status_code != 200:
                continue
            j = r.json()
            if isinstance(j, list) and j:
                if isinstance(j[0], dict) and j[0].get("peersList"):
                    return list(j[0]["peersList"])
                return [e.get("symbol") for e in j if isinstance(e, dict)
                        and e.get("symbol") and e.get("symbol") != ticker]
        except Exception:
            continue
    return []


def _num(d, *names):
    for n in names:
        if isinstance(d, dict) and d.get(n) is not None:
            return d.get(n)
    return None


def parse_profile(profile_json):
    p = profile_json[0] if isinstance(profile_json, list) and profile_json else (profile_json or {})
    return {
        "sector": _num(p, "sector"),
        "beta": _num(p, "beta"),
        "price": _num(p, "price"),
        "company": _num(p, "companyName"),
        "currency": _num(p, "currency"),
    }


def _grade(surprise_pct):
    if surprise_pct is None:
        return None
    if surprise_pct > 2:
        return "beat"
    if surprise_pct < -2:
        return "miss"
    return "meet"


def parse_earnings(earn_json):
    rows = earn_json if isinstance(earn_json, list) else []
    out = []
    for e in rows:
        if not isinstance(e, dict):
            continue
        act = _num(e, "epsActual", "actualEarningResult", "eps", "epsActuals")
        est = _num(e, "epsEstimated", "estimatedEarning", "epsEstimate", "estimatedEps")
        if act is None:
            continue
        sp = None
        if est not in (None, 0):
            sp = (act - est) / abs(est) * 100
        out.append({"quarter": e.get("date") or e.get("fillingDate") or e.get("period"),
                    "eps_actual": act, "eps_estimate": est,
                    "surprise_pct": round(sp, 1) if sp is not None else None,
                    "grade": _grade(sp)})
    out.sort(key=lambda x: x["quarter"] or "")
    return out[-4:]


def parse_revenue_surprises(earn_json):
    """FMP earnings -> revenue beat/miss history (oldest->newest, last 4) from rows
    that carry BOTH a revenue actual and estimate. Same +/-2% grade as EPS."""
    rows = earn_json if isinstance(earn_json, list) else []
    out = []
    for e in rows:
        if not isinstance(e, dict):
            continue
        act = _num(e, "revenueActual", "revenue")
        est = _num(e, "revenueEstimated", "estimatedRevenue")
        if act is None or est in (None, 0):
            continue
        try:
            sp = (act - est) / abs(est) * 100
        except (TypeError, ZeroDivisionError):
            continue
        out.append({"quarter": e.get("date") or e.get("fillingDate")
                    or e.get("fiscalDateEnding") or e.get("period"),
                    "rev_actual": act, "rev_estimate": est,
                    "surprise_pct": round(sp, 1), "grade": _grade(sp)})
    out.sort(key=lambda x: x["quarter"] or "")
    return out[-4:]


def parse_quote(quote_json):
    q = quote_json[0] if isinstance(quote_json, list) and quote_json else (quote_json or {})
    return {
        "price": _num(q, "price"),
        "shares": _num(q, "sharesOutstanding", "sharesOutstandingDil"),
    }


def parse_estimate_path(estimates, latest_fy=None):
    rows = [e for e in (estimates or []) if isinstance(e, dict) and e.get("date")
            and _num(e, "revenueAvg") and _num(e, "revenueAvg") > 0]
    if len(rows) < 2:
        return {}
    rows.sort(key=lambda e: e["date"])
    future = [e for e in rows if (not latest_fy) or e["date"] > latest_fy] or rows
    if len(future) < 2:
        future = rows
    revs = [_num(e, "revenueAvg") for e in future]
    growths = [revs[i + 1] / revs[i] - 1 for i in range(len(revs) - 1) if revs[i]]
    out = {}
    if growths:
        out["fwd_growth_near"] = round(growths[0], 4)
        out["fwd_growth_far"] = round(growths[-1], 4)
    na = _num(future[0], "numberAnalystsEstimatedRevenue",
              "numberAnalystEstimatedRevenue", "numAnalystsRevenue")
    if na is not None:
        try:
            out["n_analysts"] = int(na)
        except (TypeError, ValueError):
            pass
    return out


def parse_forward_eps(estimates, latest_fy=None):
    """FMP analyst-estimates -> nearest future-year epsAvg, or None."""
    rows = [e for e in (estimates or []) if isinstance(e, dict) and e.get("date")]
    if not rows:
        return None
    rows.sort(key=lambda e: e["date"])
    future = [e for e in rows if (not latest_fy) or e["date"] > latest_fy]
    if not future:
        future = rows[-2:] if len(rows) >= 2 else rows
    eps = _num(future[0], "epsAvg", "epsAvgEstimate", "estimatedEpsAvg")
    try:
        return float(eps) if eps is not None else None
    except (TypeError, ValueError):
        return None


def _first(x):
    if isinstance(x, list) and x:
        return x[0]
    if isinstance(x, dict) and not any(k.startswith("_") for k in x):
        return x
    return {}


def parse(bundle, facts):
    SRC = "fmp"
    prof = _first(bundle.get("profile"))
    quote = _first(bundle.get("quote"))
    inc_list = bundle.get("income_annual") if isinstance(bundle.get("income_annual"), list) else []
    inc = inc_list[0] if inc_list else {}
    bal = _first(bundle.get("balance_annual"))
    cf = _first(bundle.get("cashflow_annual"))
    ttm = _first(bundle.get("income_ttm"))

    facts.set("company", _num(prof, "companyName"), SRC)
    facts.set("sector", _num(prof, "sector"), SRC)
    facts.set("beta", _num(prof, "beta"), SRC)
    facts.set("price", _num(quote, "price") or _num(prof, "price"), SRC)
    facts.set("currency", _num(inc, "reportedCurrency") or _num(prof, "currency") or "USD", SRC)
    facts.set("fiscal_year", _num(inc, "date", "fiscalYear"), SRC)

    src_inc = ttm if (ttm and _num(ttm, "revenue")) else inc
    tag = SRC + ("/ttm" if src_inc is ttm else "/annual")
    facts.set("revenue", _num(src_inc, "revenue", "revenueTTM"), tag)
    facts.set("operating_income", _num(src_inc, "operatingIncome", "operatingIncomeTTM"), tag)
    facts.set("net_income", _num(src_inc, "netIncome", "netIncomeTTM", "bottomLineNetIncome"), tag)
    facts.set("eps_gaap", _num(src_inc, "epsDiluted", "epsdiluted", "eps"), tag)
    facts.set("shares_diluted", _num(src_inc, "weightedAverageShsOutDil", "weightedAverageShsOut"), tag)
    facts.set("income_before_tax", _num(src_inc, "incomeBeforeTax"), tag)
    facts.set("tax_expense", _num(src_inc, "incomeTaxExpense"), tag)
    facts.set("dep_amort", _num(src_inc, "depreciationAndAmortization") or _num(cf, "depreciationAndAmortization"), tag)

    facts.set("revenue_annuals", [_num(x, "revenue") for x in inc_list if _num(x, "revenue")], SRC + "/annual")

    facts.set("total_debt", _num(bal, "totalDebt"), SRC)
    facts.set("cash", _num(bal, "cashAndCashEquivalents", "cashAndShortTermInvestments"), SRC)
    facts.set("equity", _num(bal, "totalStockholdersEquity", "totalEquity"), SRC)

    capex = _num(cf, "capitalExpenditure")
    facts.set("capex", abs(capex) if capex is not None else None, SRC)
    facts.set("sbc", _num(cf, "stockBasedCompensation"), SRC)

    fwd_eps, growth = _consensus(bundle.get("estimates"), facts.fiscal_year)
    facts.set("forward_eps", fwd_eps, SRC + "/estimates")
    facts.set("growth_lt", growth, SRC + "/estimates")
    return facts


def _consensus(estimates, latest_fy):
    if not isinstance(estimates, list) or not estimates:
        return None, None
    rows = [e for e in estimates if isinstance(e, dict) and e.get("date")]
    rows.sort(key=lambda e: e["date"])
    future = [e for e in rows if (not latest_fy) or e["date"] > latest_fy]
    if not future:
        future = rows[-2:] if len(rows) >= 2 else rows
    fwd_eps = _num(future[0], "epsAvg")
    growth = None
    revs = [(_num(e, "revenueAvg")) for e in future if _num(e, "revenueAvg")]
    if len(revs) >= 2 and revs[0] and revs[0] > 0:
        yrs = len(revs) - 1
        growth = (revs[-1] / revs[0]) ** (1 / yrs) - 1
    return fwd_eps, growth
