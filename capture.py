"""
Phase 1 capture — save real fixtures from the FREE stack:
  SEC EDGAR (full company facts, primary financials)  +  FMP profile (sector/beta/price)
  +  Yahoo (forward EPS consensus + beta + price)

Run on your machine (internet; FMP key optional for the profile):
    $env:FMP_API_KEY="your_new_key"   # optional
    python capture.py
Then run:  python verify.py
"""
import os
import sys
import json
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from sources import sec_edgar

FIX = config.FIXTURE_DIR
UA = config.SEC_USER_AGENT


def save(ticker, name, data):
    d = os.path.join(FIX, ticker)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name + ".json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def yahoo_consensus(ticker):
    """forward EPS + beta + price via Yahoo quoteSummary (crumb handshake)."""
    s = requests.Session()
    ua = {"User-Agent": "Mozilla/5.0"}
    s.headers.update(ua)
    try:
        s.get("https://fc.yahoo.com", timeout=8)
    except Exception:
        pass
    crumb = ""
    try:
        crumb = s.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=8).text.strip()
    except Exception:
        pass
    out = {}
    try:
        r = s.get(f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}",
                  params={"modules": "defaultKeyStatistics,financialData,earningsTrend,price", "crumb": crumb}, timeout=15)
        out = r.json()
    except Exception as e:
        out = {"_error": str(e)[:120]}
    return out


def main():
    print("Capturing free-stack fixtures ->", FIX)
    for t in config.PROBE_TICKERS:
        print(f"\n{t}:")
        cik = config.CIKS.get(t)
        # 1) SEC company facts (authoritative financials)
        try:
            facts = sec_edgar.fetch_companyfacts(cik, UA)
            save(t, "sec_companyfacts", facts)
            print(f"  SEC companyfacts OK ({facts.get('entityName')})")
        except Exception as e:
            print(f"  SEC FAIL {str(e)[:80]}")
        time.sleep(0.5)
        # 2) FMP profile (sector/beta/price) — works on the free tier for all symbols
        key = config.FMP_API_KEY
        if key:
            try:
                r = requests.get(f"{config.FMP_BASE}/profile", params={"symbol": t, "apikey": key}, timeout=15)
                save(t, "fmp_profile", r.json())
                print(f"  FMP profile OK")
            except Exception as e:
                print(f"  FMP profile FAIL {str(e)[:60]}")
            time.sleep(0.5)
        # 3) Yahoo consensus
        save(t, "yahoo_quotesummary", yahoo_consensus(t))
        print(f"  Yahoo consensus saved")
        time.sleep(0.5)
    print("\nDone. Now run:  python verify.py")


if __name__ == "__main__":
    main()
