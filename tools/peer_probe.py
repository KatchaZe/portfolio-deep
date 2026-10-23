"""Live run of the relative-valuation cross-check on every holding, with real network.

    python -m tools.peer_probe            # all holdings
    python -m tools.peer_probe NVDA UNH   # some

Reads the stored facts (no fundamentals refresh), fetches each FMP peer list (one call
per ticker unless cached < 30 days), SEC facts and T-1 closes for the peers, fits, and
writes data/peer_probe_<date>.json plus a table on screen. Changes nothing in the store.
"""
import datetime as dt
import json
import os
import sys
import time
from types import SimpleNamespace

import config
import store
from domain import pricing
from pipeline import batch, peers

FIELDS = ("sic", "ticker", "currency", "revenue", "operating_income", "total_debt", "operating_leases",
          "cash", "shares_diluted", "market_cap", "price", "provenance", "revenue_annuals_dated")


def _sic(t):
    import requests
    from pipeline.cik import resolve_cik
    from sources import sec_gap
    cik, _ = resolve_cik(t)
    if not cik:
        return None
    try:
        r = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json",
                         headers={"User-Agent": config.SEC_USER_AGENT}, timeout=30)
        return sec_gap.sic_of(r.json())
    except Exception:
        return None


def main(argv):
    s = store.load()
    tickers = [t.upper() for t in argv] or sorted(s.get("holdings", {}))
    today = dt.date.fromisoformat(pricing.ny_today())
    budget, memo, out = batch.Budget(len(tickers) + 1), peers.PointMemo(), {}
    print(f"FMP key: {'set' if config.FMP_API_KEY else 'MISSING (peer lists will be empty)'}")
    t0 = time.time()
    for t in tickers:
        f = s.get("facts", {}).get(t)
        if not f:
            print(f"{t:6} no stored facts"); continue
        ff = SimpleNamespace(**{k: f.get(k) for k in FIELDS})
        ff.ticker = t
        if not ff.sic:                         # stored facts predate the SIC field
            ff.sic = _sic(t)
        t1 = time.time()
        try:
            block, calls = peers.relative_value(ff, config.FMP_API_KEY, today, None, budget, memo)
        except Exception as e:
            print(f"{t:6} ERROR {e}"); continue
        block["_fmp_calls"], block["_secs"] = calls, round(time.time() - t1, 1)
        out[t] = block
        m = block.get("model")
        px = f.get("price")
        if m:
            vr = block.get("value_regression")
            print(f"{t:6} SIC {(ff.sic or ['?'])[0]} n={m['n']} R2={m['r2']} LOO={m['loo_median_err']} "
                  f"reg=${vr} median=${block.get('value_median')} price=${px} "
                  f"excl={len(block['excluded'])} {block['_secs']}s")
        else:
            print(f"{t:6} no fit: {'; '.join(n for n in block['notes'] if n)} excl={len(block['excluded'])}")
    path = os.path.join(config.DATA_DIR, f"peer_probe_{today.isoformat()}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"run": today.isoformat(), "secs": round(time.time() - t0, 1),
                   "fmp_calls": sum(b["_fmp_calls"] for b in out.values()), "results": out}, fh, indent=1)
    print(f"\n{len(out)} tickers in {time.time() - t0:.0f}s -> {path}")


if __name__ == "__main__":
    main(sys.argv[1:])
