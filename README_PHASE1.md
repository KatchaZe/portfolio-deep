# Phase 1 — Data Foundation (start here)

Goal: prove the new data layer reads **correct** numbers before we build anything
on top of it. We do this by capturing real FMP responses for the tickers that
broke v1 (AVGO, ABBV, ORCL, NVO) and checking them against known-good values.

## Run the probe (on your computer, with internet)

1. Set your FMP key (one time, in the same PowerShell window you'll run from):

   ```powershell
   $env:FMP_API_KEY="paste_your_key_here"
   ```

2. Install requests and run the probe:

   ```powershell
   cd "C:\Users\Katcha\Documents\Claude\Projects\Stock Screening\portfolio-app-v2"
   pip install requests
   python fmp_probe.py
   ```

## What you'll see

For each ticker it prints which endpoints returned data, the real **field names**,
the key values, a TTM net income / revenue, and a known-good check, e.g.:

```
========== AVGO ==========
  income-statement FIELDS: date, revenue, netIncome, epsDiluted, ...
  TTM (sum 4 quarters): netIncome=25.0B  revenue=68.3B
  KNOWN-GOOD CHECK: net income TTM 25.0B  expected 22-28B  -> OK
```

It also saves every raw response under `tests/fixtures/<TICKER>/` — these become
our permanent test fixtures.

## Then

Paste me the printout (or just tell me it ran) — I'll read the saved fixtures,
confirm the exact field names, and finalize the FMP adapter + `FinancialFacts` +
the fixture tests. Uses ~45 FMP calls total (well under the 250/day free budget).

> If any endpoint shows `[error]` or a `CHECK` instead of `OK`, that's exactly
> what we want to catch now — send it over and I'll adjust.
