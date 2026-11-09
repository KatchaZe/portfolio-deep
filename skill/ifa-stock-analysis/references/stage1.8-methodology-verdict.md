# Stage 1.8 — Methodology Verdict (model follows the business) — v8.4

> **v9.0:** background on the method. Every number comes from `scripts/value.py` (the portfolio-deep app's engine). Where this page and the engine disagree, the engine is right.

The anchor is chosen by WHAT THE COMPANY IS, the same router the portfolio app runs
(`portfolio-app-v2/domain/company_type.py`). Decide the type once, then value it.

```
TYPE = FINANCIAL  if industry is a bank, insurer, reinsurer, broker or consumer lender
       CYCLICAL   if industry is commodity / heavy-cycle (oil & gas, metals & mining, steel,
                  basic chemicals, autos, homebuilding, airlines, shipping, trucking, farming)
                  AND >= 5 years of operating margins AND their median > 0
                  (checked BEFORE the loss test: a steel maker in a loss year is cyclical)
       YOUNG      if operating income <= 0
       STANDARD   otherwise

FINANCIAL → Excess-return model (`scripts/value.py`)
            value = book equity + PV((ROE − Ke) × book equity) + terminal excess return
            ROE fades halfway to Ke (floor Ke − 3%); stable g <= stable ROE; WACC = Ke
            Loss-making with no value → "No point valuation" (never a young-company DCF)
STANDARD → FCFF DCF (`scripts/value.py`) — PRIMARY anchor
            revenue growth: consensus revenue growth (avg of next and far year), 5 yrs, fade to g_stable by yr 10
            margin: own operating margin (US GAAP: + lease interest), held to <= 2x industry;
                    under 5% → the Damodaran industry margin
            reinvestment = Δrevenue / sales-to-capital (own, held to 0.5–2x industry)
            WACC fades to the stable WACC in yrs 6–10; tax → marginal 25% in yrs 6–10
            terminal = NOPAT₁₁ × (1 − g/ROIC_stable) / (WACC_stable − g)
            equity = PV + cash − debt (incl. leases) ÷ shares in the QUOTE's unit
CYCLICAL → same FCFF DCF, target margin = the firm's own median margin (reached in 3 yrs)
YOUNG    → Stage 1.7 Forward Intrinsic DCF (failure-adjusted) + Stage 1.6 reverse DCF

Fallback: DCF/excess-return value <= 0 or < 10% of price → treat as a broken input,
          anchor on Future Value Projection (spread > 10% and growth > 15%) else Fundamental PEG,
          and say so. PEG and FVP are always reported as cross-checks.
```

## Cost of capital (all types)
`Ke = Rf + β × (ERP + CRP)` — CRP = revenue-weighted Damodaran country risk premium minus the
US's own 0.23% (ctryprem.html). β = industry unlevered β (cash-corrected) re-levered at the
firm's market D/E; financial firms use the regression β (deposits are not capital).
`python scripts/value.py input.json`

## Margin of safety (one definition)
`MoS = (FV − P) / FV`. The bands depend on the uncertainty rating (Morningstar-style: Low 20%
… Extreme 75% for very cheap); read them from `valuation.uncertainty.bands_pct`, never a fixed table.
Upside (FV − P)/P is the return if price converges; report it beside MoS, never instead of it.

## Fair Value as a RANGE (not a point)
Range = [min, max] of the methods that ran (DCF or excess return, FVP, PEG; pre-profit: Stage 1.7
p10/p50/p90). A DCF discarded as a broken input is left out of the range. When the ends are
≥ 2x apart, label it a **method conflict**, not a confidence interval, and lean on the anchor.
