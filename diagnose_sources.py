"""
diagnose_sources.py — pinpoint WHY the EPS/Rev circles are empty for a ticker.

Runs every source path the app uses for the earnings circles and prints what
each one returns (counts + first row), so we can see which link in the chain
is broken (Yahoo blocked? FMP endpoint empty? SEC missing eps? pairing failed?).

Usage (uses your real env keys, ~5 FMP calls):
    py diagnose_sources.py NVDA
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from sources import sec_edgar, yahoo, fmp, finnhub
from pipeline import refresh, surprise_backfill


def peek(label, rows):
    n = len(rows) if rows else 0
    first = (rows[-1] if isinstance(rows, list) and rows else
             (list(rows.items())[:1] if isinstance(rows, dict) and rows else None))
    print(f"  {label:38} n={n}   latest={first}")


def main():
    t = (sys.argv[1] if len(sys.argv) > 1 else "NVDA").upper()
    key = config.FMP_API_KEY
    print(f"=== diagnose {t} | FMP key set: {bool(key)} | Finnhub: {bool(config.FINNHUB_API_KEY)} | AV: {bool(config.ALPHAVANTAGE_API_KEY)}\n")

    # 1) Yahoo quoteSummary (primary EPS-surprise source)
    yq = yahoo.fetch_consensus(t)
    ok = False
    try:
        ok = bool(yq["quoteSummary"]["result"])
    except Exception:
        pass
    print(f"[1] Yahoo quoteSummary reachable: {ok}   (_error={yq.get('_error') if isinstance(yq, dict) else '?'})")
    peek("yahoo parse_earnings_history", yahoo.parse_earnings_history(yq))
    print(f"  yahoo revenue_estimate (cur q):      {yahoo.parse_revenue_estimate(yq)}")

    # 2) FMP earnings (EPS + revenue actual/estimate)
    if key:
        raw = fmp.fetch_earnings(t, key)
        print(f"\n[2] FMP fetch_earnings raw rows: {len(raw)}")
        if raw:
            print(f"  raw keys of first row: {sorted((raw[0] or {}).keys())}")
        peek("fmp parse_earnings (EPS)", fmp.parse_earnings(raw))
        peek("fmp parse_revenue_surprises (Rev)", fmp.parse_revenue_surprises(raw))

        # 3) FMP quarterly analyst-estimates (backfill source)
        rawq = fmp.fetch_estimates_quarter(t, key)
        print(f"\n[3] FMP fetch_estimates_quarter raw rows: {len(rawq)}")
        if rawq:
            print(f"  raw keys of first row: {sorted((rawq[0] or {}).keys())}")
        est_q = fmp.parse_estimates_quarterly(rawq)
        peek("fmp parse_estimates_quarterly", est_q)
    else:
        est_q = {}
        print("\n[2/3] FMP skipped — no key in env")

    # 3b) Finnhub (free EPS-surprise cross-check / fallback)
    if config.FINNHUB_API_KEY:
        raw_fh = finnhub.fetch_earnings(t, config.FINNHUB_API_KEY)
        print(f"\n[3b] Finnhub fetch_earnings raw rows: {len(raw_fh)}")
        peek("finnhub parse_earnings (EPS)", finnhub.parse_earnings(raw_fh))
        fh_fwd = finnhub.parse_eps_estimate(
            finnhub.fetch_eps_estimate(t, config.FINNHUB_API_KEY))
        print(f"  finnhub forward EPS estimate:        {fh_fwd}  (None = premium/ไม่มี — ไม่กระทบ)")
    else:
        print("\n[3b] Finnhub skipped — no key in env")

    # 4) SEC actuals (the free side of the backfill pairing)
    cik, _ = refresh.resolve_cik(t)
    print(f"\n[4] SEC cik={cik}")
    if cik:
        cf = sec_edgar.fetch_companyfacts(cik, config.SEC_USER_AGENT,
                                          cache_dir=config.CACHE_DIR,
                                          ttl_hours=config.SEC_CACHE_TTL_HOURS)
        d = sec_edgar.extract(cf)
        peek("sec eps_quarters", d.get("eps_quarters"))
        peek("sec revenue_quarters", d.get("revenue_quarters"))
        peek("sec operating_income_quarters", d.get("operating_income_quarters"))

        # 5) the pairing itself
        if est_q:
            peek("\n[5] backfill build_eps", surprise_backfill.build_eps(d.get("eps_quarters"), est_q))
            peek("[5] backfill build_rev", surprise_backfill.build_rev(d.get("revenue_quarters"), est_q))
        else:
            print("\n[5] backfill skipped — no quarterly estimates from FMP")

    print("\n=== ส่ง output ทั้งหมดนี้กลับมา เพื่อชี้จุดที่ chain ขาด ===")


if __name__ == "__main__":
    main()
