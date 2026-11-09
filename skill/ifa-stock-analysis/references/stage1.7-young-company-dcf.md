# Stage 1.7 — Young / Pre-Profit Forward Intrinsic DCF — v8.0  ★ NEW (P0/P1)

> **v9.0:** background on the method. Every number comes from `scripts/value.py` (the portfolio-deep app's engine). Where this page and the engine disagree, the engine is right.

Helper script: `scripts/value.py`

This is the **valuation** half of the pre-profit toolkit (Stage 1.6 is only the pricing check).
It implements Damodaran's actual young-company method: value the firm **forward** from a story, then
adjust for **failure risk separately**, and express the answer as a **distribution**, not a point.

## Why it exists (gaps it fixes)
1. The old skill had NO independent intrinsic value for pre-profit names — only "what's priced in."
2. It had NO failure-risk modeling. Damodaran's #1 rule: most young firms don't survive, and you must
   **not** bury failure risk in the discount rate (a "blunt instrument"). Model it as a probability.

## Trigger
Run whenever Stage 1.8 routes to pre-profit / optionality (TTM EPS < 0, TTM FCF < 0, or forward gate fail).

## Inputs
`current_revenue, g_high, g_stable (≤Rf), horizon, current_margin (may be negative), target_margin
(sector mature median — SEARCH), sales_to_capital, tax, WACC, roic_stable, net_debt (=debt−cash),
shares, annual_dilution (future SBC), p_survival, distress_value_per_share`

## Build (per year, then terminal)
```
rev_t   = rev_{t-1} × (1 + g_t)              # g fades linearly g_high → g_stable
margin_t= current_margin → target_margin (linear over horizon)
nopat_t = (rev_t × margin_t) × (1 − tax)     # EBIT×(1−tax); negative early = burn, no tax shield
reinv_t = (rev_t − rev_{t-1}) / sales_to_capital     ★ sales-to-capital links growth to cash & dilution
fcff_t  = nopat_t − reinv_t
Firm = Σ fcff_t/(1+WACC)^t + [mature FCFF/(WACC−g)]/(1+WACC)^horizon
Equity = Firm − Net Debt ; ÷ shares×(1+annual_dilution)^horizon  → going_concern_per_share
```

## Failure-risk adjustment (MANDATORY — never in the discount rate)
```
value/share = p_survival × going_concern_per_share + (1 − p_survival) × distress_value_per_share
```
- `p_survival`: estimate from sector survival data (e.g. BLS business-survival rates by industry/age),
  adjusted for the firm's cash runway and access to capital. Record the source.
- `distress_value_per_share`: liquidation / recovery estimate (often near 0 for asset-light startups).

## Distribution, not a point (Damodaran: "less detail, more precision")
Run the built-in **Monte Carlo** (`"montecarlo": true`): samples target_margin, sales_to_capital,
g_high, and p_survival → reports p10 / p50 / p90 / mean per share. Report the **band**; the p50 is the
anchor, p10 is the downside the position size must survive.

## Hand-off to Stage 1.8 / Stage 2
- Primary value = failure-adjusted p50 (or point failure-adjusted value).
- Show going-concern value, failure-adjusted value, and the MC band.
- Compare to Stage 1.6 implied CAGR: "worth $X (1.7); price assumes Y% CAGR (1.6)."

---

## ★v8.1 — Inputs from consensus (no guessing)
- **Revenue path:** use the **consensus per-FY revenue path** (`analyst_estimates`, Stage 0.5) for the
  high-growth phase — do NOT hand-pick g_high. Reliable window = #analysts ≥ 15; fade to g_stable ≤ Rf.
- **Margin convergence:** anchor the start/target on the **quarterly operating-margin trajectory** (8q) +
  sector mature median — labeled as an assumption with sensitivity, not a guessed line.
- **Dilution:** drive `annual_dilution` from the historical SBC% and share-count growth (Stage 0.5).
- **p_survival:** derive from cash + FCF + current ratio (runway) and/or `risk-assessment`; it is an
  ASSUMPTION → show the Monte Carlo band and size to p10. Never a number picked by feel.
- Every input above carries a source tag; missing + no fallback → `⛔ MISSING` (inv. 17).
