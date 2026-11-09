# Stage 0 — Data Audit & Sanitization (Gatekeeper) — v8.1

> **v9.0:** background on the method. Every number comes from `scripts/value.py` (the portfolio-deep app's engine). Where this page and the engine disagree, the engine is right.

> ⚠️ TOTAL AMNESIA on current market data. Verify everything from a source. Nothing downstream runs
> until this stage passes. **Every value entering the model carries a source tag [BD]/[SEC]/[WS]** (inv. 16).

## Rule 1 — Kill Switch
If no source returns a valid price + date for the ticker:
→ **STOP.** Output exactly: `⛔ CRITICAL ERROR: Live Data Unavailable for [TICKER]`

## Rule 2 — Smart Search Protocol
- **Step A:** Resolve ticker → full company name (prefer `find_securities`, Stage 0.5).
- **Step B:** Target Date = T-1 (yesterday for Tue–Fri; last Friday for Sat–Mon).
- **Step C:** Earnings queries must use "announced", "reported", "actual" — NEVER "latest" or "estimates".
- **Step D:** Get the current 10-Year US Treasury yield → lock it as **Rf** for WACC.
- **Step E:** Get the current Damodaran **implied ERP** (monthly). Lock as `erp`. Default 4.23%
  (Jan 2026) if the month's value can't be retrieved. NEVER reuse the old frozen 4.75%.

## Rule 3 — Corporate Action & Data Sanitization
- Search stock splits in the past 12 months; adjust ALL historical per-share metrics to post-split.
- Strip non-recurring one-time items from Operating Income → **True Normalized NOPAT**.
- For R&D-heavy firms, gather **3–6 years of R&D** so `scripts/value.py` can build the research asset.

## Rule 4 — Verification Logic
- Confirm fiscal year matches the current system year (or T-1). Forward EPS = **NTM consensus**.
- Keep GAAP vs non-GAAP consistent (if base EPS is non-GAAP ex-SBC, SBC must still be re-added as
  dilution downstream — never drop it twice or count it twice).
- **Cross-check:** price + core financials from ≥ 2 sources (Bigdata ↔ SEC EDGAR). Gate: price ≤ 2%,
  financials ≤ 1%. Fail → flag.

## Rule 5 — Growth from Consensus (NOT a hand-picked number) ★ v8.1
Growth is the highest-sensitivity input. It must be **derived from analyst estimates**, fully shown,
and reproducible — never a round number chosen by feel.
- **Use the consensus PATH, not one compressed number.** Pull per-FY consensus revenue & EPS
  (`analyst_estimates`, Stage 0.5). Feed the per-FY path directly into Future Value (1.5) / DCF (1.7).
- **Reliable window only.** Use FYs where **#analysts ≥ 15**; drop thin-coverage out-years (flag them).
- **If a single scalar `g` is required** (e.g. for PEG): `g` = CAGR over the reliable window.
  Prefer **revenue CAGR** (more stable); cross-check with EPS CAGR. Show the formula + the values.
- **Fade & cap:** let growth decelerate along the consensus path; terminal `g ≤ Rf`.
- **Consistency gate:** cross-check with `g = reinvestment × ROIC`. If growth isn't fundable, flag
  "growth not funded" and lean low.
- **Confidence tag:** by #analysts and the consensus high–low dispersion → wide → widen scenarios.
- **Fallback (no estimates):** historical reported CAGR, tagged `[SEC]`. ❌ Never a hand-picked round number.

> Example (NVDA, 17 Jun 2026, all computed by script): consensus path FY27 +82% → FY28 +40% → FY29 +18%;
> reliable-window CAGR ≈ 44–46% (3y) / revenue 5y 34.5%. A flat "30%" would understate and violate inv. 18.

## Rule 6 — Earnings-Quality Capture
From the latest 10-K/10-Q (or Bigdata): Net income, CFO, FCF, total assets → cash conversion + accruals;
SBC (this & prior yr) + revenue → SBC% trend; receivables → AR-vs-revenue; deferred revenue / RPO → billings.

## Rule 7 — Leading Indicators Capture
RPO / backlog, deferred revenue / billings, Net Revenue Retention (NRR), segment revenue & margin, KPIs
(units, ARPU, churn). Hand to the Demand score.

## Checkpoints (all must pass)
| Checkpoint | Criteria |
|---|---|
| **0.1 Price** | ≥ 2 sources, deviation ≤ 2%, date = T-1, post-split adjusted |
| **0.2 Financial Integrity** | SBC (Cash Flow Statement), R&D history, diluted shares, debt & cash |
| **0.3 Financials** | TTM revenue, normalized margins, ROIC components |
| **0.4 Peers** | 4–6 comparable peers (terminal margin / multiples) |
| **0.5 Risk inputs** | live Rf, live implied ERP, beta (bottom-up + regression), debt/cash for WACC |
| **0.6 Quality** | earnings-quality inputs gathered (Rule 6) |
| **0.7 Provenance ★v8.1** | every field tagged [BD]/[SEC]/[WS]; no untagged numbers |

## Output of this stage (handed to Stage 1)
```
price_T1, market_cap, diluted_shares, total_debt, cash, Rf (live), erp (live implied),
TTM_revenue, normalized_NOPAT, R&D_history[], SBC, forward_estimates_path[] (per-FY rev & EPS + #analysts),
growth_g (consensus-derived, reliable window), beta_bottom_up, beta_reg, peer_set[], tax_rate,
earnings_quality_inputs{}, leading_indicators{}, source_tags{}
```
Missing item with no documented fallback → `⛔ MISSING: <field>` (inv. 17). Never guess.
