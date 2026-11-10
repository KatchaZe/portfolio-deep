# Stage 2 — Compact Output (300–500 words) — v9.0

The only stage shown by default. Explanations in Thai, labels in English. Every figure
comes from `python scripts/value.py input.json --brief` (field names in `code`); write
none of them by hand.

```
[TICKER] ([Company]) | DEEP Framework v9.0
📋 Data: price $`price` (close YYYY-MM-DD) | Rf X% | ERP X% (as of `erp_as_of`) | latest quarter QX YYYY
Fair Value (primary): $`fair_value` | Model: `model` (`company_type`)
Range: $`range[0]`–$`range[1]` | Uncertainty: `uncertainty.rating` (MoS for a BUY ≥ `uncertainty.bands_pct[1]`%)
Cross-check: implied return `expected_return.equity_irr_pct`% vs Ke `ke_pct`%
  (excess `expected_return.excess_pct`pp) · Bear/Base/Bull `scenarios.cases[*].value`
  · reverse DCF `reverse_dcf.implied_cagr_pct`% CAGR priced in
  A cross-check never replaces the fair value; explain any gap as the market's story.

📊 บทสรุปผู้บริหาร
Rating `stars` · `recommendation` · X% of portfolio
Thesis: D / E / Valuation in 2–3 lines of Thai

🎯 Scores (0–5)
D `scores.D` · E-Exec `scores.E_exec` · E-Econ `scores.E_econ` · P `scores.P` → composite `scores.composite`
Key metrics: growth `growth_pct`% · ROIC `roic_pct`% vs WACC `wacc_pct`% · peer median growth

💰 Fair value
Growth path used, WACC, terminal growth and ROIC (`valuation.dcf.sources`),
MoS gross and net of trading cost + capital-gains tax

💼 Action plan ($100K portfolio): size, entry, 12-month target, stop rule
   Young company (`young_dcf` present): ≤ 3%, sized to survive the p10, and show the value at
   p_survival ± 0.20 (`young_dcf`)
⚠️ Top 3 risks (impact / probability) — include every engine flag that changes the thesis (`flags`)
⛔ MISSING lines from `missing`, if any
Data sources: every input with its [BD] / [SEC] / [WS] tag and date (`sources`)
Standing note: most active strategies trail the index after costs.
```

Confidence below 50 (`confidence`) → say so in the first line and do not issue a BUY.
