# Stage 1.6 — Terminal-Anchored Reverse DCF — v8.3  (PRICING cross-check)

> **v9.0:** background on the method. Every number comes from `scripts/value.py` (the portfolio-deep app's engine). Where this page and the engine disagree, the engine is right.

Helper script: `scripts/value.py`

> ★v8 ROLE: this is a **pricing** tool ("what growth is the market implying?"), **NOT** a valuation.
> For pre-profit **intrinsic value**, run **Stage 1.7** (`scripts/value.py`). Always run BOTH and
> compare: 1.7 says what it's worth; 1.6 says what the price assumes. The gap is the thesis.

## Trigger Conditions (run if ANY is true)
- TTM EPS < 0 (loss-making)
- TTM Free Cash Flow < 0
- Forward DCF result < 0.1× current price (validation gate fail)
- User asks: "what CAGR is priced in", "reverse DCF", "what growth does the market expect"

## ★v8 FIX — Enterprise Value bridge (P0)
FCFF is a whole-firm cash flow, so anchor on EV, not market cap:
```
enterprise_value = market_cap + total_debt − cash & ST investments
```
For cash-rich startups or levered firms, using market_cap directly distorts the implied revenue.

## Inputs to gather first
`market_cap, total_debt, cash, WACC, g (10Y), tax_rate, terminal_margin (peer mature median — SEARCH),
ROIC_terminal (15% tech / 12% capital-heavy), horizon (10; very early → 15), current_revenue,
revenue_1y_prior, revenue_3y_prior`

## Calculation chain — ★v8.3 FULL-PATH solve is PRIMARY
```
PRIMARY (v8.3, Philosophy-2026 S19/S21 — credit interim FCFF, bisection on x):
  Solve x so that  EV = Σ_{t=1..H} FCFF_t/(1+WACC)^t + TV/(1+WACC)^H
    FCFF_t = rev₀(1+x)^t × terminal_margin × (1−tax) × (1 − min(0.9, x/ROIC_terminal))
    TV     = FCFF_{H+1}/(WACC − g),  terminal reinvest = g/ROIC_terminal
  implied_CAGR = x

UPPER BOUND (legacy closed form — assumes ALL EV compounds into year-H TV):
  STEP 1  TV_needed     = enterprise_value × (1 + WACC)^horizon
  STEP 2  FCFF_terminal = TV_needed × (WACC − g)
  STEP 3  reinvestment  = g / ROIC_terminal
          Revenue_term  = FCFF_terminal / (terminal_margin × (1 − tax) × (1 − reinvestment))
  STEP 4  implied_CAGR_upper = (Revenue_term / current_revenue)^(1/horizon) − 1
```
Script outputs BOTH (`implied_cagr_pct` primary, `implied_cagr_upper_pct` upper bound).
Verdict/acceleration always uses the PRIMARY number.

## Sensitivity — 3 margin scenarios
| Scenario | Terminal margin | Required revenue | Implied CAGR |
|---|---|---|---|
| Bear | Peer median − 5% | computed | computed |
| Base | Peer median | computed | computed |
| Bull | Peer median + 5% | computed | computed |

## Reality check (from last 10-K/10-Q)
```
actual_growth_1y    = (current_revenue / revenue_1y_prior) − 1
actual_growth_3y    = (current_revenue / revenue_3y_prior)^(1/3) − 1
acceleration_needed = implied_CAGR (base) / actual_growth_1y
```

## Verdict logic
```
< 1.5×  Plausible | 1.5–3× Ambitious | 3–5× Aggressive | > 5× Exceptional
```

## Historical benchmarks (context)
| Company | Peak 10Y revenue CAGR | Context |
|---|---|---|
| Amazon 2000–2010 | ~28% | small base |
| Apple 2004–2014 | ~39% | iPhone era |
| Google 2004–2014 | ~32% | search dominance |
| Netflix 2012–2022 | ~22% | streaming scale-up |
| Very few companies | >50% | sustained, at >$10B base |

> **Mandatory:** output MUST include actual 1Y and 3Y revenue CAGR — the comparison is the point.
