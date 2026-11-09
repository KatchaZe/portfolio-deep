# Stage 1 — DEEP Calculation (Numeric Rubric) — v8.2

> **v9.0:** background on the method. Every number comes from `scripts/value.py` (the portfolio-deep app's engine). Where this page and the engine disagree, the engine is right.

Four sub-scores, each 0–5.0, now produced by a **coded, deterministic rubric** (no judgment step):
`scripts/value.py` (raw metrics → 0-5 with full breakdown), aggregated by `scripts/value.py`.
Math contract in `../formulas-reference.md`. **Every input carries a source tag; missing → ⛔ MISSING (inv. 16-18).**

> v8.2 adds 4 Damodaran/expert metrics (one per pillar) + turns metric→score from judgment into a rubric.
> Pattern per pillar: **BASE (primary metric) + bounded ADJUSTMENTS, clamp [0,5]**. Missing adjustment input → skipped + noted.

## D — Demand (weight 0.20)
- **Base = revenue growth (YoY):** ≥35%→5 · 25–35%→4 · 15–25%→3 · 8–15%→2 · 0–8%→1 · <0→0
- **Organic adj ★v8.2** (acq_intensity = |acquisitions_net|/revenue): >10%→−1.0 · 5–10%→−0.5 · else 0
  *(can't strip exact acquired revenue → penalize M&A intensity; Damodaran wants organic)*
- **Peer adj:** g > peer median → +0.5 · g < 0.8×peer → −0.5
- **Leading adj** (RPO/billings/NRR): strong +0.5 · weak −0.5
- **Durability adj ★v8.2** (forward fade = fwd_far/fwd_near): <0.40 → −0.5 (fragile) · >0.70 → +0.25

## E — Execution (weight 0.20)
- **Base = earnings-quality verdict** (`scripts/value.py`): CLEAN→4.0 · REVIEW→3.0 · LOW→1.5
- **Margin-trend adj:** up +0.5 · flat 0 · down −0.5
- **Incremental ROIC adj ★v8.2** (inc = ΔNOPAT/ΔIC − WACC): ≥+10%→+1.0 · 0–10%→+0.5 · −5–0%→−0.5 · <−5%→−1.0
  *(quality of capital allocation; value-destroying reinvestment penalized even if EQ clean)*
- **Insider adj:** buy +0.25 · sell −0.25
- SBC NOT penalized here (handled as dilution in 1.5/1.7).

## E — Economics (weight 0.30)
- **Base = spread (ROIC − WACC)**, ROIC R&D-adjusted (`scripts/value.py`):
  ≥20%→5 · 10–20%→4 · 5–10%→3 · 0–5%→2 · −5–0%→1 · <−5%→0
- **Spread-trend adj ★v8.2** (Δ vs prior period): widening (>+2pp)→+0.5 · stable→0 · narrowing(<−2pp)→−0.5
  (−1.0 if narrowing AND spread now <5% — moat eroding)
- **Context:** Economic Profit (EVA) = spread × Invested Capital (reported, shows absolute moat size)

## P — Price (weight 0.30)
- Fair value comes from the blended methods (PEG 20% · ROIC-PE 30% · FCF-yield 20% · DCF 30%;
  pre-profit → Stage 1.7 forward DCF + 1.6 reverse check) → **anchor**.
- **Base = Margin of Safety** MoS = (fair_value_anchor − price)/price:
  ≥+40%→5 · +15–40%→4 · −15–+15%→3 · −40–−15%→2 · <−40%→1
- **Own-5y-multiple adj ★v8.2** (percentile of current EV/Sales or EV/EBITDA in its own 5y range):
  ≤33% (cheap vs history) → +0.5 · ≥67% (re-rating risk) → −0.5

## Aggregate (`scripts/value.py`)
```
composite = D×0.20 + Execution×0.20 + Economics×0.30 + Price×0.30
stars = nearest half · recommendation: ≥4 BUY | ≥3 HOLD/Accumulate | ≥2 HOLD | <2 SELL/AVOID
```

## Worked outputs (real data, fully traceable)
- **NVDA:** D 5.0 (growth 66%, peer+, leading+, fade−) · Exec 4.5 (EQ REVIEW + inc-ROIC +1.0) ·
  Econ 5.0 (spread 57%, widening; EVA $96.7B) · Price 4.5 (MoS +21%, multiple at 5y low) → **4.75 BUY**
- **SNOW:** D 5.0 · Exec 2.5 (inc-ROIC −1.0) · Econ 0.0 (negative spread, narrowing) · Price 1.5
  (priced ~−87% vs conservative forward-DCF anchor) → **1.95 SELL/AVOID**

## Auto-computed inputs (`scripts/value.py`) ★v8.2 — no manual estimation
The rubric's secondary metrics are computed by script from Bigdata series (newest-first), not estimated:
```
revenue_growth_1y / 3y CAGR · acq_intensity (organic) · margin_trend (op-margin now vs 3y) ·
incremental_roic = ΔNOPAT/ΔIC · roic_now & 3y-ago (→ spread trend, consistent reported series) ·
own multiple percentile (EV/EBITDA in 5y range) · peer_median_growth (from peer tearsheets)
```
Chain: `bigdata_company_tearsheet` (company + peers) → `scripts/value.py`.
Worked (NVDA, real Bigdata): peer-median growth 34.3% (AMD 34/AVGO 24/MRVL 42), incremental ROIC 76%,
ROIC trend widening 11.7%→62.9%, EV/EBITDA at 5y low (pct 0%) → D5.0 / Exec4.5 / Econ5.0 / Price4.5 → 4.75 BUY.
