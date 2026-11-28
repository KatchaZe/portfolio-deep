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
                  OR, on the firm's own last 5 FISCAL years (today's margin plays no part), ALL of:
                  a revenue fall >= 15% with the margin falling that year; a margin < -2% or a range
                  > 30 points; a later fiscal year back at or above its 10-year median (median >= 5%).
                  MU passes; NVDA, INTC, REGN (a fall that never came back) do not.
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
CYCLICAL → same FCFF DCF, starting at today's margin held to the peak line (1.5x normal),
            target margin = the firm's own median margin over up to 10 fiscal
            years (min 7; 5-6 flagged), reached in 3 yrs, on TODAY's revenue (Damodaran's
            scaled normalization). ONE fair value: card, scenarios, Monte Carlo, P, the MoS cap,
            reverse DCF and cross-checks all use that start (MU 2026-11: $440.32). ROIC for
            Economics, terminal ROIC, CAP, moat and incremental ROIC is measured on EBIT held
            to the same line. Growth = through-cycle organic (geometric mean of the
            non-acquisition years in the window, annualized over the real years between
            observations), never above g_stable. With consensus, at the peak line the path is 2
            years of consensus at the start margin, then back to the normal margin and
            through-cycle growth ("consensus ถึงปีที่ 2 แล้วกลับสู่ค่าปกติของรอบ"); at or below
            the normal margin consensus is read as for any firm; between, the two paths in
            proportion (peak weight), so nothing jumps at the line. Bear / Bull margin = 25th /
            75th percentile of the window (Bear floor 2%); the Monte Carlo of every cyclical
            runs from the window's trough to the Bull margin. A bigger peak never raises the
            value or the score. Cross-checks (never the FV): revenue to its log-linear trend,
            and average EBIT on trend revenue, labelled Bear only below the FV. PEG / FVP on
            normalized EPS.
            Reverse DCF: price implies a margin on today's revenue < today / 1.5 →
            "ตลาดตั้งราคาว่ารอบกำลังจะลง".
Scenarios → Bear / Bull shift growth only when that moves the value their way (Bear <= Base
            <= Bull). Primary FV more than 1.5x (log, either way) from the median of PEG, FV/P
            (not for a young primary), relative (not when its fit is weak or loo error > 0.40)
            and cycle checks, with 2 methods beyond 1.5x on the same side → flag
            "ค่าหลักต่างจากวิธีเทียบอื่นมาก ตรวจสมมติฐาน" (display only). A DCF row with no
            scenario range is NOT RATED.
Rating cap → MoS = (FV − price) / FV below −25% caps at HOLD / Accumulate, below −50% at
            HOLD, one notch per step ("MoS ต่ำกว่า −50% จำกัดคำแนะนำไม่เกิน HOLD: MoS −50.3%").
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
