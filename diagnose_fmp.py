"""
diagnose_fmp.py — probe every FMP endpoint the app uses (and likely alternates)
and print STATUS + response snippet, so we can see exactly why earnings /
quarterly-estimates come back empty (premium-gated? deprecated? renamed field?).

Usage (needs FMP_API_KEY in env; ~8 calls):
    $env:FMP_API_KEY="your_key"
    py diagnose_fmp.py NVDA
The API key is NEVER printed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import requests

KEY = config.FMP_API_KEY
S = config.FMP_BASE       # /stable
L = config.FMP_LEGACY     # /api/v3


def probe(label, url, params):
    params = dict(params, apikey=KEY)
    try:
        r = requests.get(url, params=params, timeout=20)
        body = r.text.replace(KEY, "***KEY***")[:260].replace("\n", " ")
        print(f"[{r.status_code}] {label}\n      {body}\n")
    except Exception as e:
        print(f"[ERR] {label}: {e}\n")


def main():
    t = (sys.argv[1] if len(sys.argv) > 1 else "NVDA").upper()
    if not KEY:
        print("FMP_API_KEY not set"); return
    print(f"=== FMP probe {t} ===\n")
    # what the app calls today
    probe("stable /earnings", f"{S}/earnings", {"symbol": t, "limit": 8})
    probe("legacy /earnings-surprises", f"{L}/earnings-surprises/{t}", {})
    probe("legacy /historical/earning_calendar", f"{L}/historical/earning_calendar/{t}", {})
    probe("stable /analyst-estimates (quarter)", f"{S}/analyst-estimates",
          {"symbol": t, "period": "quarter", "limit": 12})
    probe("legacy /analyst-estimates (quarter)", f"{L}/analyst-estimates/{t}",
          {"period": "quarter", "limit": 12})
    # likely alternates on the current free tier
    probe("stable /earnings-surprises", f"{S}/earnings-surprises", {"symbol": t})
    probe("stable /earnings-calendar", f"{S}/earnings-calendar", {"symbol": t})
    probe("stable /analyst-estimates (annual)", f"{S}/analyst-estimates",
          {"symbol": t, "period": "annual", "limit": 4})
    print("=== ส่ง output ทั้งหมดกลับมา (key ถูก mask แล้ว) ===")


if __name__ == "__main__":
    main()
