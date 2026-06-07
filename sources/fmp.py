"""
FMP source adapter — fetch (network) is separated from parse (pure) so the
parser can be unit-tested offline against saved fixtures.

Field names below are the REAL ones confirmed from the probe's captured
responses (income-statement, profile, analyst-estimates). Access is defensive
(several name variants) so a minor FMP rename doesn't silently null a field.
"""
import time

import config

BASE = config.FMP_BASE


# --------------------------------------------------------------------------- #
#  FETCH  (runs on a machine with internet + FMP key)                          #
# --------------------------------------------------------------------------- #
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
    """Return a raw bundle {name: json}. Spaced + one retry on 429."""
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


# --------------------------------------------------------------------------- #
#  PARSE  (pure — unit-tested against fixtures)                                 #
# --------------------------------------------------------------------------- #
def _num(d, *names):
    for n in names:
        if isinstance(d, dict) and d.get(n) is not None:
            return d.get(n)
    return None


def parse_profile(profile_json):
    """FMP /profile -> sector, beta, price, company, currency (works on free tier
    for ALL symbols, unlike the statement endpoints)."""
    p = profile_json[0] if isinstance(profile_json, list) and profile_json else (profile_json or {})
    return {
        "sector": _num(p, "sector"),
        "beta": _num(p, "beta"),
        "price": _num(p, "price"),
        "company": _num(p, "companyName"),
        "currency": _num(p, "currency"),
    }


def _first(x):
    if isinstance(x, list) and x:
        return x[0]
    if isinstance(x, dict) and not any(k.startswith("_") for k in x):
        return x
    return {}


def parse(bundle, facts):
    """Populate `facts` (FinancialFacts) from an FMP bundle. Source tag 'fmp'."""
    SRC = "fmp"
    prof = _first(bundle.get("profile"))
    quote = _first(bundle.get("quote"))
    inc_list = bundle.get("income_annual") if isinstance(bundle.get("income_annual"), list) else []
    inc = inc_list[0] if inc_list else {}
    bal = _first(bundle.get("balance_annual"))
    cf = _first(bundle.get("cashflow_annual"))
    ttm = _first(bundle.get("income_ttm"))

    # meta
    facts.set("company", _num(prof, "companyName"), SRC)
    facts.set("sector", _num(prof, "sector"), SRC)
    facts.set("beta", _num(prof, "beta"), SRC)
    facts.set("price", _num(quote, "price") or _num(prof, "price"), SRC)
    facts.set("currency", _num(inc, "reportedCurrency") or _num(prof, "currency") or "USD", SRC)
    facts.set("fiscal_year", _num(inc, "date", "fiscalYear"), SRC)

    # income — prefer the TTM endpoint if present, else latest annual
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

    # annual revenue series for CAGR (clean fiscal-year values)
    facts.set("revenue_annuals", [_num(x, "revenue") for x in inc_list if _num(x, "revenue")], SRC + "/annual")

    # balance
    facts.set("total_debt", _num(bal, "totalDebt"), SRC)
    facts.set("cash", _num(bal, "cashAndCashEquivalents", "cashAndShortTermInvestments"), SRC)
    facts.set("equity", _num(bal, "totalStockholdersEquity", "totalEquity"), SRC)

    # cash flow
    capex = _num(cf, "capitalExpenditure")
    facts.set("capex", abs(capex) if capex is not None else None, SRC)
    facts.set("sbc", _num(cf, "stockBasedCompensation"), SRC)

    # consensus (analyst estimates) — adjusted forward EPS + implied growth
    fwd_eps, growth = _consensus(bundle.get("estimates"), facts.fiscal_year)
    facts.set("forward_eps", fwd_eps, SRC + "/estimates")
    facts.set("growth_lt", growth, SRC + "/estimates")
    return facts


def _consensus(estimates, latest_fy):
    """NTM adjusted EPS = nearest future-year epsAvg; growth = revenueAvg CAGR
    across the estimate horizon."""
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
