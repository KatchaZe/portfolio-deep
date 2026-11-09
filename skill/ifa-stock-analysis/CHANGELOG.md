# v9.0 — 2026-11-10 (one engine: the skill runs the portfolio-deep app's code)
- scripts/value.py runs pipeline.finish + the DEEP engine from scripts/app/, copied from the app
  by tools/build_skill.py. tests/test_skill_engine.py fails in CI when the copy is stale or when
  value.py and the app disagree on any valuation field (FCFF DCF, young DCF, excess return).
- Removed every v8.x formula script (wacc, roic, rd_capitalize, justified_peg, fcff_dcf,
  excess_return, young_company_dcf, reverse_dcf_terminal, earnings_quality, deep_metrics,
  deep_subscores, deep_score): each duplicated an engine function and had drifted from it.
- Brings in the app's P1/P2 rules: uncertainty rating -> MoS bands, implied return, moat-set
  DCF horizon, sector-level inputs from Damodaran's tables, DEEP vs action alignment.
- ERP is a run input (rf, erp, erp_as_of in input.json); no ERP figure is written in SKILL.md.
- examples/: standard firm, young firm, bank (illustrative numbers) — input shape + test fixtures.
- Stage 2 template reads the fields of `value.py --brief`; references/ are background, the engine wins.

# v8.4 — 2026-10-18 (parity with portfolio-app-v2 Damodaran rounds 1-3)
- Stage 1.8 rewritten: company-type router; FCFF DCF primary, excess return for financials.
- New scripts: fcff_dcf.py, excess_return.py (the app's own modules, with a CLI).
- wacc.py: --crp (country risk premium over the US); ERP default 4.20% (Oct 2026).
- justified_peg.py: forward P/E = trailing / (1+g) on forward EPS (round 1).
- deep_subscores.py: MoS = (FV − P)/FV; EQ "LOW QUALITY"/UNTESTED accepted; non-numeric inc. ROIC skipped.
- earnings_quality.py: SBC is a disclosure (no double count); UNTESTED when no test applies.
- reverse_dcf_terminal.py: no crash when terminal ROIC <= g; closed form labelled a cross-check.
- Weights unchanged: Demand .20 / Execution .20 / Economics .30 / Price .30 (same as the app).

# DEEP Framework — v7.3 → v8.0 Changelog

All changes are finance/Damodaran-fidelity fixes from the skill review (16 Jun 2026).
Grounded in Damodaran's NYU Stern data (implied ERP 4.23% Jan 2026; median US WACC 7.79%;
80% of US firms 5.26%–9.88%) and his young-company valuation framework.

## P0 — Correctness (fix first; affect every output)
- **True WACC.** `scripts/wacc.py` rewritten: `Rf+Beta×ERP` is cost of equity (Ke); WACC now weights
  after-tax cost of debt and E/(D+E), D/(D+E). New invariant: FCFF→WACC, FCFE→Ke.
- **EV bridge in reverse DCF.** `scripts/reverse_dcf_terminal.py` now anchors on Enterprise Value
  (mkt cap + debt − cash) instead of market cap — fixes distortion for cash-rich/levered firms.
- **Failure / survival probability for pre-profit.** New `scripts/young_company_dcf.py` + Stage 1.7:
  value/share = p_survival × going_concern + (1−p_survival) × distress. Never in the discount rate.

## P1 — Methodology fidelity
- **Live implied ERP** (default 4.23%) replaces the frozen 4.75% (Stage 0 Rule 5E; wacc.py default).
- **Forward intrinsic DCF (Stage 1.7)** gives pre-profit names an independent VALUE; Stage 1.6 reverse
  DCF demoted to a PRICING cross-check. Stage 1.8 routes pre-profit to 1.7 (value) + 1.6 (check).
- **Earnings-quality module** — new `scripts/earnings_quality.py` + Stage 0 Rule 6: cash conversion,
  accruals ratio, SBC% trend, AR-vs-revenue, deferred-revenue/billings. Caps the Execution score.
- **R&D capitalization** — new `scripts/rd_capitalize.py`: research asset → adjusted ROIC for tech/pharma.

## P2 — Refinements
- **Fundamental PEG** — `scripts/justified_peg.py` rewritten to a 2-stage P/E (payout, growth, Ke);
  old +0.25/−0.1 heuristic kept only as an optional sanity overlay.
- **Growth–reinvestment gate** — `g = reinvestment × ROIC` consistency check on locked growth.
- **Sales-to-capital reinvestment** — drives pre-profit reinvestment/burn in Stage 1.7.
- **Bottom-up beta default** — industry beta relevered to D/E; regression beta as cross-check.
- **Leading indicators** — RPO/backlog, deferred revenue/billings, NRR feed the Demand score (Stage 0 Rule 7).
- **Execution score** — quality/fundamental-trend based instead of "earnings-beat consistency".
- **Two-stage fade** + equity flows discounted at Ke in the 5Y Future Value Projection.

## Files
- Rewritten scripts: `wacc.py`, `reverse_dcf_terminal.py`, `justified_peg.py`
- New scripts: `young_company_dcf.py`, `earnings_quality.py`, `rd_capitalize.py`
- Unchanged scripts: `roic.py`, `deep_score.py`
- New reference: `references/stage1.7-young-company-dcf.md`
- Updated references: formulas-reference, stage0, stage1, stage1.5, stage1.6, stage1.8, stage2, stage3
- Updated: `SKILL.md` (v8.0, invariants 13–15)

## How to install
Replace your saved `ifa-stock-analysis` skill files with the contents of this folder via
Settings > Capabilities (skills are read-only at runtime, so they can't be hot-edited in a session).

---

# v8.0 → v8.1 Changelog (Bigdata integration + no-guess discipline)

Implements phases 4–6 of `BIGDATA-V8-INTEGRATION-PLAN.md`.

## Added
- **Stage 0.5 — Connector Data Pull** (`references/stage0.5-connector-data-pull.md`): Bigdata.com as the
  institutional data layer (verified to match SEC), one-call pull, cross-check vs SEC EDGAR, fallback chain.
- **Invariants 16–18 (No-Guess Guardrail):** (16) provenance tags + data-sources table on every number;
  (17) `⛔ MISSING` instead of inventing values; (18) no ad-hoc growth/assumptions — growth from consensus,
  assumptions labeled + sensitivity, arithmetic only in scripts.

## Changed
- **Stage 0 Rule 5 (growth lock → growth-from-consensus):** derive growth from the analyst-estimate PATH;
  for a scalar use the CAGR over the reliable window (#analysts ≥ 15), prefer revenue CAGR, cross-check EPS;
  fade per the consensus path; cap terminal g ≤ Rf. Replaces hand-picked round numbers.
- **Invariant 11** updated to reference the consensus-derived growth rule.
- **Stage 1.5 / 1.7:** Future Value and pre-profit revenue path now read the consensus per-FY path directly.
- **Stage 2 templates:** must render a provenance ("Data sources") table.
- **SKILL.md:** version v8.1, execution chain adds Stage 0.5, How-to-run step 2 = connector pull.

## Note on yfinance
- Tested as a cross-check source: blocked in this sandbox (proxy 403, Yahoo unreachable). Cross-check =
  Bigdata ↔ SEC EDGAR. Add yfinance only in environments where it can reach Yahoo.

---

# v8.1 → v8.2 Changelog (numeric DEEP rubric)

## Added
- **`scripts/deep_subscores.py`** — deterministic rubric turning raw metrics → 0-5 per pillar with a full
  audit breakdown (base + each adjustment shows its driving number). Removes the judgment step (inv. 18).
- **4 Damodaran/expert metrics (one per pillar):**
  - D — **organic growth** (M&A-intensity penalty)
  - Execution — **incremental ROIC** (ΔNOPAT/ΔIC vs WACC; value-destroying reinvestment penalized)
  - Economics — **5y spread trend** (durability of ROIC−WACC) + Economic Profit/EVA context
  - Price — **own-5y-multiple percentile** (re-rating / mean-reversion risk)

## Changed
- **`references/stage1-deep-scoring.md`** rewritten as a numeric rubric (thresholds per base + adjustments).
- **SKILL.md / README** → v8.2; Stage 1 now runs `deep_subscores.py` before `deep_score.py`.

## Validated (real data)
- NVDA → composite 4.75 (BUY); SNOW → 1.95 (SELL/AVOID). Both fully traceable from raw metrics.

## v8.2 addendum — `deep_metrics.py` (auto-computed rubric inputs)
- New `scripts/deep_metrics.py`: computes peer-median growth (from peer tearsheets), incremental ROIC,
  5y spread trend, margin trend, own-multiple percentile, organic acq-intensity — directly from Bigdata
  series. Removes the last manual-estimation step; full chain now data-derived & auditable.
- Validated NVDA: peer median 34.3% (AMD/AVGO/MRVL), incremental ROIC 76%, composite 4.75 (BUY) unchanged.

## v8.3 — Philosophy-2026 alignment (2026-07-10)
Source: *Investment Philosophies — The 2026 Edition* (Damodaran, 43-video playlist) + full-stack
code review of 2026-07-10. See `../Philosophy2026_Summary.md` and
`../Philosophy2026_CrossCheck_and_Updates.md` (repo root).

1. **Full-path reverse DCF (S19/S21)** — `reverse_dcf_terminal.py`: primary implied CAGR now solved
   by bisection over the WHOLE FCFF path (interim cash flows credited). Old terminal-only closed form
   assumed all of EV compounds into the year-H terminal value → implied CAGR overstated → verdicts
   skewed Aggressive/Exceptional. It remains as `implied_cagr_upper_pct` (labeled upper bound).
   Same fix mirrored in `portfolio-app-v2/domain/engine/deep_v82.py::reverse_dcf`.
2. **Stable-period exit P/E (S5/S19)** — FVP exit multiple = `payout_st×(1+g_st)/(Ke−g_st)`
   (`stable_exit_pe` in the app engine); "growth×100" (PEG=1) heuristic removed. Formulas-reference updated.
3. **ERP refresh** — 4.23% (Jan 2026) → **4.45% US** (Jul 2026; mature market 4.17%).
   `wacc.py` ERP_DEFAULT + `portfolio-app-v2/config.py` (SOURCE OF TRUTH) updated together.
4. **New invariants 19–20** — full-path reverse DCF mandatory; Stage 2 must show net-of-cost/tax MoS
   (S6) + active-vs-index humility disclaimer (S16/S35).
Validation: app suite `run_tests.py` → ALL TEST SUITES PASSED (incl. updated ERP locks); script
smoke test: implied 52.2% (full-path) vs 53.2% (upper bound) on the reference case.

### v8.3 addendum (2026-07-11) — P1/P2 completion pass
5. **NOL carryforward (P2-10)** — `young_company_dcf.py`: accumulated losses now shield the first
   profitable years from tax (`use_nol` default true, `nol_initial` optional). Full taxation from
   year-1-of-profit understated pre-profit values.
6. **Drift guard (P2-8)** — the app now ships `tests/test_skill_parity.py` which imports these
   scripts and asserts ERP default, two-stage PE, and full-path reverse DCF agree with the app
   engine. Formula drift between the two codebases now fails the app's test suite.
7. **Companion app changes** (portfolio-app-v2, same date): operating leases capitalized into
   IC+debt (S5), regression-beta cross-check (S4), historical VaR (S2/S4), pricing-asset cap 5%
   (S40-41), empirical bond duration override (S3), and a dashboard "Assumptions" form that
   persists monthly ERP / MARKET_PE to the store (Google-Drive mirrored) and applies them at
   call time (no restart).
