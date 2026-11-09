# Stage 1.5 — Growth Valuation Engine (Damodaran Extension) — v8.1

> **v9.0:** background on the method. Every number comes from `scripts/value.py` (the portfolio-deep app's engine). Where this page and the engine disagree, the engine is right.

Forward-looking value for **profitable** companies.

> **Growth is consensus-derived (Stage 0 Rule 5).** Use the per-FY analyst-estimate PATH directly where
> possible; use a single scalar `g` (reliable-window CAGR) only where a method needs one. Never hand-pick.

## 0. Growth–Reinvestment gate
Before projecting, confirm the growth is fundable: `implied_reinvestment = g / ROIC`. If the firm's actual
reinvestment is far below this, flag "growth not funded" and bias low.

## 1. Future Value Projection (5-Year)
- **Base EPS:** verified TTM (GAAP/non-GAAP consistent), tagged.
- **Growth:** prefer the **consensus per-FY EPS path** (`analyst_estimates`); else compound by `g`.
  When a Y5 EPS is published by consensus with adequate coverage, use it directly (more faithful than
  compounding a single rate).
- **Exit multiple:** dynamic (expected post-Y5 growth + risk penalty); label it as an ASSUMPTION and
  show a target-P/E sensitivity (e.g. 20/25/30). ❌ Not a hand-picked single P/E without sensitivity.
- **SBC dilution:** add unvested SBC shares to diluted shares; deduct from forward EPS.
```
Future EPS (Y5) = consensus FY(Y5) EPS  [preferred]   OR   EPS₀ × Π(1+g_FY)   [path]   OR   EPS₀ × (1+g)^5
Future Price    = Future EPS × Dynamic Target P/E      (assumption → sensitivity)
Present Value   = Future Price / (1 + Ke)^5            ★ equity flow → discount with Ke, not WACC
```

## 2. Two-stage fade
Don't hold growth/margin flat. High-growth phase fades to stable (terminal g ≤ Rf, terminal margin =
sector mature median). Reuse the same g_high / years / g_stable / Ke as the fundamental PEG.

## 3. Reverse DCF Scenarios (profitable)
3 scenario tables (Bear consensus−5% / Base consensus / Bull consensus+5%) → compare each PV to price to
read what growth the market is pricing (margin of safety vs priced-for-perfection).

> Loss-making / cash-burning → skip this stage; use **Stage 1.7 (value)** + **Stage 1.6 (pricing check)**.
