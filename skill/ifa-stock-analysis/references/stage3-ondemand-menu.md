# Stage 3 — On-Demand Deep-Dive Menu — v8.0

> **v9.0:** background on the method. Every number comes from `scripts/value.py` (the portfolio-deep app's engine). Where this page and the engine disagree, the engine is right.

Display only when the user types the command. Each expands a part of the silent analysis.

### `show:fundamental_peg`  ★v8 (was show:justified_peg)
2-stage P/E from payout, growth, Ke (`scripts/value.py`). Show payouts, fair P/E, fundamental PEG.
Note SBC handled via EPS dilution, not a PEG penalty. Old heuristic shown only as a sanity overlay.

### `show:wacc`  ★v8
Ke = Rf + Beta×ERP, then true WACC with after-tax cost of debt and E/D weights. Show live Rf, live
implied ERP, bottom-up vs regression beta, and the FCFF→WACC / FCFE→Ke matching rule.

### `show:roic_formula`
Step-by-step ROIC. ★v8 if R&D-heavy, run `scripts/value.py` first; show research asset, adjusted vs
unadjusted ROIC, and spread vs WACC.

### `show:earnings_quality`  ★v8 NEW
Cash conversion, accruals ratio, FCF margin, SBC% trend, AR-vs-revenue, deferred-revenue/billings.
Red-flag list + CLEAN/REVIEW/LOW verdict (`scripts/value.py`).

### `show:young_company_dcf`  ★v8 NEW
Full forward intrinsic DCF (`scripts/value.py`): year-by-year revenue/margin/FCFF, terminal value,
equity bridge, failure-probability adjustment, and the Monte Carlo p10/p50/p90 band.

### `show:valuation_methods`
Fundamental PEG | ROIC-PE | FCF Yield | Reverse DCF (1.6, pricing) | Forward DCF (1.7, value, pre-profit).
Show weighting logic and when each applies.

### `show:deep_scores`
Full D-E-E-P breakdown: Base → Adjustments → Peer Adj → Final. Explain normalized margins, one-time
sanitization, and the earnings-quality cap on Execution.

### `show:verification`
3 price sources + timestamps (T-1). Financial sources. Corporate-action / split checks. ★v8 live Rf
and live implied ERP sources.

### `show:sensitivity`
Fair-value sensitivity: Growth ±5%, ROIC ±5%, terminal margin ±5%. ★v8 Monte Carlo band for pre-profit.

### `show:full_risks`
10–15 risks expanded, impact on fair value quantified, probability-weighted scenarios. ★v8 pre-profit:
dilution/financing risk and runway explicitly.

### `show:reverse_dcf_terminal`
Full EV-anchored reverse DCF: 4 steps with complete arithmetic, WACC ±1% / margin ±5% / ROIC ±5%
sensitivity, revenue checkpoints at Y2/Y5/Y8, historical analog CAGRs, burn-rate adjustment.
