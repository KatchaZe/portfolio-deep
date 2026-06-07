"""
Phase 1 probe v2 — capture real FMP responses (stable endpoints only).

v1 lesson: the legacy /api/v3 endpoints are now 403 ("no longer supported"), and
firing calls too fast rate-limits the free tier. This version uses ONLY the stable
API, spaces calls out, retries once on 429, and saves the real error body so we can
see exactly what each endpoint returns.

Run (with internet + your FMP key):
    $env:FMP_API_KEY="your_key"      # PowerShell
    pip install requests
    python fmp_probe.py
"""
import os
import json
import time

import requests

import config

FIX = config.FIXTURE_DIR
KEY = config.FMP_API_KEY
_calls = 0


def _save(ticker, name, data):
    d = os.path.join(FIX, ticker)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name + ".json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def fmp(endpoint, ticker, **params):
    """Stable endpoint with spacing + one retry on 429. Returns (data, ok)."""
    global _calls
    params = {**params, "symbol": ticker, "apikey": KEY}
    url = f"{config.FMP_BASE}/{endpoint}"
    for attempt in (1, 2):
        _calls += 1
        try:
            r = requests.get(url, params=params, timeout=25)
        except Exception as e:
            return {"_error": str(e)[:160]}, False
        if r.status_code == 429:          # rate limited -> wait and retry once
            time.sleep(6)
            continue
        if r.status_code != 200:
            return {"_status": r.status_code, "_body": r.text[:300]}, False
        try:
            j = r.json()
        except Exception:
            return {"_status": 200, "_body": r.text[:300]}, False
        if isinstance(j, dict) and j.get("Error Message"):
            return {"_status": 200, "_error_message": j["Error Message"][:200]}, False
        return j, True
    return {"_status": 429, "_body": "rate limited after retry"}, False


def _num(d, *names):
    for n in names:
        if isinstance(d, dict) and d.get(n) is not None:
            return d.get(n)
    return None


ENDPOINTS = [
    ("profile", {}),
    ("quote", {}),
    ("income-statement", {"period": "annual", "limit": 5}),
    ("income-statement-ttm", {}),                 # test if TTM is on free tier
    ("balance-sheet-statement", {"period": "annual", "limit": 2}),
    ("cash-flow-statement", {"period": "annual", "limit": 5}),
    ("key-metrics-ttm", {}),
    ("ratios-ttm", {}),
    ("analyst-estimates", {"period": "annual", "limit": 4}),
]


def probe(ticker):
    print(f"\n========== {ticker} ==========")
    cap = {}
    for ep, params in ENDPOINTS:
        data, ok = fmp(ep, ticker, **params)
        suffix = ep + ("_" + params["period"] if "period" in params else "")
        _save(ticker, suffix, data)
        cap[suffix] = data
        if ok:
            n = len(data) if isinstance(data, list) else "obj"
            print(f"  {suffix:34s} OK   ({n})")
        else:
            msg = data.get("_error_message") or data.get("_body") or data.get("_error") or data.get("_status")
            print(f"  {suffix:34s} FAIL  {str(msg)[:70]}")
        time.sleep(1.5)                            # be gentle with the free tier

    inc = cap.get("income-statement_annual")
    if isinstance(inc, list) and inc:
        a0 = inc[0]
        print(f"  latest annual ({_num(a0,'fiscalYear','date')}): cur={_num(a0,'reportedCurrency')} "
              f"rev={_bn(_num(a0,'revenue'))} opInc={_bn(_num(a0,'operatingIncome'))} "
              f"netInc={_bn(_num(a0,'netIncome'))} epsDil={_num(a0,'epsDiluted')} "
              f"shares={_bn(_num(a0,'weightedAverageShsOutDil'))}")
        print(f"  annual revenue series: {[_bn(_num(x,'revenue')) for x in inc]}")
    ttm = cap.get("income-statement-ttm")
    if isinstance(ttm, list) and ttm:
        print(f"  TTM endpoint: rev={_bn(_num(ttm[0],'revenue','revenueTTM'))} "
              f"netInc={_bn(_num(ttm[0],'netIncome','netIncomeTTM'))}")
    est = cap.get("analyst-estimates_annual")
    if isinstance(est, list) and est:
        nxt = est[0]
        print(f"  next-yr estimate: epsAvg={_num(nxt,'epsAvg')} revenueAvg={_bn(_num(nxt,'revenueAvg'))} date={_num(nxt,'date')}")
    bs = cap.get("balance-sheet-statement_annual")
    if isinstance(bs, list) and bs:
        b = bs[0]
        print(f"  balance: debt={_bn(_num(b,'totalDebt'))} cash={_bn(_num(b,'cashAndCashEquivalents'))} "
              f"equity={_bn(_num(b,'totalStockholdersEquity','totalEquity'))}")
    cf = cap.get("cash-flow-statement_annual")
    if isinstance(cf, list) and cf:
        c = cf[0]
        print(f"  cashflow: capex={_bn(_num(c,'capitalExpenditure'))} D&A={_bn(_num(c,'depreciationAndAmortization'))} "
              f"sbc={_bn(_num(c,'stockBasedCompensation'))}")

    kg = config.KNOWN_GOOD.get(ticker)
    if kg and isinstance(inc, list) and inc:
        ni = (_num(inc[0], "netIncome") or 0) / 1e9
        rev = (_num(inc[0], "revenue") or 0) / 1e9
        if "net_income_ttm_bn" in kg:
            lo, hi = kg["net_income_ttm_bn"]
            print(f"  CHECK net income {ni:.1f}B exp {lo}-{hi} -> {'OK' if lo<=ni<=hi else 'CHECK!'}")
        if "revenue_ttm_bn" in kg:
            lo, hi = kg["revenue_ttm_bn"]
            print(f"  CHECK revenue {rev:.1f}B exp {lo}-{hi} -> {'OK' if lo<=rev<=hi else 'CHECK!'}")


def _bn(x):
    try:
        return round(x / 1e9, 2)
    except Exception:
        return x


def main():
    if not KEY:
        print("ERROR: set FMP_API_KEY first.")
        return
    print("FMP probe v2 (stable only) -> fixtures in", FIX)
    for t in config.PROBE_TICKERS:
        try:
            probe(t)
        except Exception as e:
            print(f"  {t} FAILED: {str(e)[:120]}")
    print(f"\nTotal FMP calls: {_calls} (free budget 250/day)")


if __name__ == "__main__":
    main()
