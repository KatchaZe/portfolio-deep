# Formulas Reference (Shared Math Contract) — v8.3

> **v9.0:** background on the method. Every number comes from `scripts/value.py` (the portfolio-deep app's engine). Where this page and the engine disagree, the engine is right.

Single source of truth for every formula in the skill. Scripts in `../scripts/` implement these.
**v8 changes are tagged inline.** Read the CHANGELOG-v8.md for the rationale.

## Constants
- **ERP** (Equity Risk Premium) = **live Damodaran IMPLIED ERP**. Current default **4.45% US** (Jul 2026;
  mature market 4.17% — US carries a small country premium this cycle).
  ★v8: NO LONGER frozen at 4.75%. Refresh monthly from Damodaran; pass to `scripts/value.py`.
  ★v8.3: keep in sync with `portfolio-app-v2/config.py` (SOURCE OF TRUTH).
- **Default tax rate** = 21% (fallback only; prefer last 10-K effective rate).
- **Default terminal ROIC** = 15% tech/growth, 12% capital-heavy (adjust by sector search).
- **Default horizon** = 10 years.
- **Terminal growth `g`** = current 10Y US Treasury yield (live); cap: g ≤ Rf.
- **US benchmark (Damodaran 2026):** median firm cost of capital ≈ 7.79%; 80% of US firms 5.26%–9.88%.

## Cost of Equity & WACC  (`scripts/value.py`)  ★v8 FIX (P0)
```
Cost of Equity  Ke   = Rf + Beta × ERP
Cost of Debt    Kd   = (Rf + default_spread) × (1 − tax)        # after tax
WACC                 = Ke × E/(D+E) + Kd × D/(D+E)
```
- The old skill called `Rf + Beta×ERP` "WACC" — that is **only Ke**. WACC must weight debt.
- **Discount-rate matching (MANDATORY):** FCFF → discount with **WACC**; FCFE → discount with **Ke**.
- `default_spread` from Damodaran synthetic-rating table (by interest-coverage ratio); table is in `scripts/value.py`.
- *Beta:* ★v8 prefer **bottom-up / industry beta relevered to the firm's D/E**; use 5Y regression beta only as a cross-check (regression betas are noisy).

## ROIC  (`scripts/value.py`)
```
NOPAT = Operating Income (normalized, one-time items stripped) × (1 − tax)
ROIC  = NOPAT / Invested Capital
Spread = ROIC − WACC
```
★v8: for R&D-heavy firms, FIRST run `scripts/value.py` and feed the **adjusted** OI and IC into ROIC.

## R&D Capitalization (Research Asset)  (`scripts/value.py`)  ★v8 NEW (P1)
```
research_asset       = Σ_{i=0..life} RD[-i] × (life − i)/life     # i=0 = current year (full)
amortization_this_yr = Σ_{i=1..life} RD[-i] / life
adj_operating_income = reported_OI + current_RD − amortization
adj_invested_capital = reported_IC + research_asset
adj_ROIC             = adj_OI × (1 − tax) / adj_invested_capital
```
life ≈ 5y tech/software, longer for pharma/hardware. Reported ROIC for tech is misleading without this.

## Fundamental (Justified) PEG  (`scripts/value.py`)  ★v8 FIX (P2)
Replaces the old arbitrary point system with Damodaran's 2-stage P/E (FCFE form):
```
PE = payout_h×(1+g_h)×(1 − (1+g_h)^n/(1+Ke)^n)/(Ke − g_h)
   + payout_st×((1+g_h)^n)×(1+g_st)/((Ke − g_st)×(1+Ke)^n)
  payout_h  = 1 − g_h/ROIC_h          payout_st = 1 − g_st/ROIC_st
Fundamental PEG = PE / (g_h × 100)
Fair Price      = PE × diluted_forward_EPS
```
Old heuristic kept only as an optional sanity overlay. Requires Ke > g_st.

## Growth–Reinvestment Consistency  ★v8 NEW (P2)
```
g = Reinvestment Rate × ROIC
```
Use as a gate: if consensus growth implies a reinvestment rate the firm is not making, flag
"growth not funded." For pre-profit firms reinvestment is driven by the sales-to-capital ratio (below).

## 5-Year Future Value Projection (profitable)
```
Future EPS (Y5) = EPS₀ × (1 + g)^5          [g = locked consensus median]
Future Price    = Future EPS × Exit P/E
Present Value   = Future Price / (1 + Ke)^5  ★v8: equity cash flow → discount with Ke (not WACC)
SBC: add unvested SBC shares to diluted shares; deduct from forward EPS.

★v8.3 Exit P/E = STABLE-period justified P/E (never "growth × 100" / PEG=1 heuristic):
  Exit P/E = payout_st × (1 + g_st) / (Ke − g_st),  payout_st = 1 − g_st/ROIC_st
  (clamp to a sane band, e.g. 12–25; g_st = terminal growth ≤ Rf)
```

## Young / Pre-Profit Forward Intrinsic DCF  (`scripts/value.py`)  ★v8 NEW (P0/P1) — Stage 1.7
```
per year t:
  rev_t   = rev_{t-1} × (1 + g_t)            # g fades linearly g_high → g_stable
  margin_t= current_margin → target_margin (linear)
  nopat_t = (rev_t × margin_t) × (1 − tax)   # = EBIT×(1−tax); if EBIT<0, no tax shield
  ★v8.3 NOL carryforward: losses accumulate as NOL; profitable years pay tax only on
    max(0, EBIT − NOL) until the carryforward is exhausted (use_nol=true default;
    nol_initial = existing NOL from the last 10-K)
  reinv_t = (rev_t − rev_{t-1}) / sales_to_capital      ★ sales-to-capital drives reinvestment
  fcff_t  = nopat_t − reinv_t
Firm Value = Σ fcff_t/(1+WACC)^t + TV/(1+WACC)^n ,  TV = mature FCFF/(WACC−g)
Equity     = Firm − Net Debt ; ÷ diluted shares (incl. future SBC dilution)

FAILURE RISK (separate, never in the discount rate):
  Value/share = p_survival × going_concern_per_share + (1 − p_survival) × distress_value_per_share
  p_survival from sector survival data (e.g. BLS); distress value = liquidation/recovery estimate.
SIMULATION: Monte Carlo over target_margin, sales_to_capital, g_high, p_survival → report p10/p50/p90.
```

## Terminal-Anchored Reverse DCF  (`scripts/value.py`)  ★v8 FIX (P0) · ★v8.3 FIX — Stage 1.6
PRICING cross-check only (NOT a valuation). Anchored on **Enterprise Value**, not market cap:
```
enterprise_value  = market_cap + total_debt − cash & ST investments     ★v8 EV bridge

★v8.3 PRIMARY — FULL-PATH solve (credits interim FCFF; bisection on x):
  EV = Σ_{t=1..H} FCFF_t/(1+WACC)^t + TV/(1+WACC)^H
  FCFF_t = rev₀(1+x)^t × terminal_margin × (1−tax) × (1 − min(0.9, x/ROIC_terminal))
  TV     = FCFF_{H+1}/(WACC − g),  terminal reinvest = g/ROIC_terminal
  implied_CAGR = x                          # solved

UPPER BOUND (legacy closed form — assumes ALL EV = year-H terminal value):
  TV_needed         = enterprise_value × (1 + WACC)^horizon
  FCFF_terminal     = TV_needed × (WACC − g)              [Gordon Growth inverted]
  Revenue_terminal  = FCFF_terminal / (terminal_margin × (1 − tax) × (1 − g/ROIC_terminal))
  implied_CAGR_upper= (Revenue_terminal / current_revenue)^(1/horizon) − 1

actual_growth_1y    = (current_revenue / revenue_1y_prior) − 1
actual_growth_3y    = (current_revenue / revenue_3y_prior)^(1/3) − 1
acceleration_needed = implied_CAGR_base / actual_growth_1y
```
*Constraint:* `WACC > g` must hold. Validate before use.
*Why v8.3:* the closed form ignored interim cash flows → implied CAGR overstated → verdicts skewed
Aggressive/Exceptional (Philosophy-2026, S19/S21: reverse DCF must discount the whole path).

## Verdict thresholds (acceleration_needed)
```
< 1.5×  Plausible | 1.5–3× Ambitious | 3–5× Aggressive | > 5× Exceptional
```

## Growth Derivation (consensus-based)  ★v8.1
```
Inputs: analyst_estimates = per-FY {revenue, eps, #analysts}  (Bigdata Stage 0.5)
reliable_window = FYs where #analysts >= 15           # drop thin-coverage out-years (flag)
g_scalar (if a method needs one):
    g_rev = (rev[last reliable FY] / rev0)^(1/n) - 1   # PREFERRED (revenue, more stable)
    g_eps = (eps[last reliable FY] / eps0)^(1/n) - 1   # cross-check
    g     = g_rev   (report both; if they diverge widely, lower confidence)
PATH use (preferred): feed per-FY growth directly into Future Value / DCF; fade to terminal g <= Rf.
Fallback (no estimates): historical reported CAGR, tagged [SEC].  NEVER a hand-picked round number.
```

## Provenance & No-Guess (contract)  ★v8.1
```
Every numeric input has a source tag: [BD] Bigdata | [SEC] EDGAR | [WS] WebSearch.
Missing input + no documented fallback  -> ⛔ MISSING: <field>  (do not invent / interpolate).
Assumptions (terminal margin, target P/E, p_survival) -> label + sector-anchor + sensitivity.
All arithmetic runs in scripts/, never in prose.
```
