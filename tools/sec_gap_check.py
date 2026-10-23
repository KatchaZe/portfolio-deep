"""Live check of sources/sec_gap.py: for each ticker, the base period before and after
filling a filing companyfacts has not ingested.

    python -m tools.sec_gap_check                 # HUM F CTSH BIIB NXPI UNH ELV
    python -m tools.sec_gap_check HUM
"""
import logging
import sys

import requests

import config
from pipeline.cik import resolve_cik
from sources import sec_edgar, sec_gap

logging.basicConfig(level=logging.INFO, format="      log %(name)s: %(message)s")

for t in (sys.argv[1:] or ["HUM", "F", "CTSH", "BIIB", "NXPI", "UNH", "ELV"]):
    cik, _ = resolve_cik(t)
    cf = sec_edgar.fetch_companyfacts(cik, config.SEC_USER_AGENT, cache_dir=None)
    before = sec_edgar.extract(cf)
    sub = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json",
                       headers={"User-Agent": config.SEC_USER_AGENT}, timeout=30).json()
    counts = sec_gap.fact_counts(cf)
    for f in sec_gap.periodic_filings(sub)[:4]:
        print(f"      {f['form']:5} filed {f['filed']} period {f['period']} {f['accn']} "
              f"facts in companyfacts {counts.get(f['accn'], 0)}")
    out = sec_gap.complete(cik, cf, config.SEC_USER_AGENT, cache_dir=None)
    after = sec_edgar.extract(out)
    g = out.get("_sec_gap")
    q = (after.get("revenue_quarters") or {})
    last_q = max(q.items()) if isinstance(q, dict) and q else None
    print(f"{t:5} before {before['latest_period_end']} TTM rev {before['revenue']/1e9:.2f}B | "
          f"after {after['latest_period_end']} TTM rev {after['revenue']/1e9:.2f}B "
          f"EBIT {after['operating_income']/1e9 if after.get('operating_income') else float('nan'):.2f}B "
          f"last quarter {last_q} | gap {g}")
