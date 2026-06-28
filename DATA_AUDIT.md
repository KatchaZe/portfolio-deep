# Data Provenance Audit — portfolio-app-v2

Question answered: *which numbers are fetched from real sources, which are computed
from real data, and which are assumptions/guesses?* Every assumption is **flagged**
at runtime (DEEP invariant: "skip + flag, never fabricate"), so no guessed value is
silent.

Verified by `python run_tests.py` (20/20 modules) + an end-to-end run on the real
SEC/Yahoo/FMP fixtures (MSFT, AVGO, NVO). `tests/test_extract.py` checks every
primary value against known-good ranges; `tests/test_followups.py` locks the
assumption-handling below.

---

## 1. REAL — fetched from a source, traceable via `facts.provenance`

| Value(s) | Source | Notes |
|---|---|---|
| revenue, net income, operating income, EPS (GAAP), shares, debt, cash, equity, capex, D&A, SBC, R&D (+history), interest, total assets, receivables, deferred revenue, acquisitions, **quarterly revenue & operating income**, annual series | **SEC EDGAR** (companyfacts XBRL) | Primary. All US + 20-F filers. De-duplicated by latest filing. |
| beta, forward EPS, long-term growth, EPS-surprise history, FX rate, 5y prices, daily price/volume | **Yahoo** | forward EPS is **blended** (§2). |
| sector, beta, price, EPS surprises, analyst-estimate path (growth fade, # analysts, forward EPS), **revenue surprise (immediate)**, peers | **FMP** | profile works on free tier for all symbols. |
| EPS-surprise history (3rd/4th cross-check) | **Finnhub / Alpha Vantage** | optional; only if API key set. |
| risk-free rate (Rf) | **live 10Y Treasury** `^TNX` | falls back to 4.3% **and flags** it. |
| daily price/volume fallback | **Stooq** | when Yahoo is blocked (cloud). |

**Correctness check:** `test_extract` locks AVGO net ≈ $25B, MSFT rev ≈ $318B,
ORCL rev in-range, ABBV adjusted EPS, NVO DKK→USD ≈ $44.8B. A data regression fails
the suite instead of shipping.

---

## 2. DERIVED — computed from the real values above (not guessed)

ROIC, WACC, Ke, Kd (interest-coverage → synthetic rating), operating margins,
revenue CAGRs, **operating-margin YoY trend**, incremental ROIC, 5y ROIC-spread
trend, reverse-DCF implied CAGR, EV bridge, **blended forward-EPS median + min–max
dispersion**, **data-anchored terminal margin (§3)**, fundamental 2-stage PEG, DEEP
rubric scores. Each is a formula over sourced inputs — see `domain/engine/deep_v82.py`.

---

## 3. ASSUMPTIONS / FALLBACKS — NOT fetched. Every one is flagged or documented.

| Assumption | Value | When used | Transparency |
|---|---|---|---|
| **beta** | 1.0 | beta missing from Yahoo & FMP | 🟢 flagged "beta missing → defaults to 1.0" |
| **tax rate** | 21% | filing has no usable pre-tax/tax (e.g. NVO) | 🟢 flagged "tax rate defaults to 21%" |
| **long-term growth** | 8% | no Yahoo growth & no SEC CAGR | 🟢 flagged "growth missing → defaults to 8%" |
| **Rf** | 4.3% | live ^TNX fetch fails | 🟢 flagged "Rf fallback … unavailable" |
| **terminal margin (profitable)** | company's own current op margin, clamped 5–40% | reverse DCF | 🟢 **DATA-ANCHORED** to SEC (was a hand-set table) — shows "from current op margin" |
| **terminal margin (pre-profit)** | per-ticker table → generic 25% | reverse DCF when current margin ≤ 0 | 🟢 flagged "(assumed table)" / "(generic assumption)" |
| **ERP (equity risk premium)** | 4.23%, as-of 2026-01 | every WACC/Ke | 🟢 central in `config.ERP`; **flagged once older than 3 months**; shown in `key_metrics.erp_as_of` |
| **cost-of-debt spread** | 1.3% | no interest-expense data | 🟢 flagged "Kd via default spread" |
| **terminal ROIC** | 15% | terminal value, PEG fallback | 🟠 methodology constant (Damodaran) |
| **R&D amortization life** | 5 yrs | R&D capitalization | 🟠 methodology constant |
| synthetic-rating spread table, PE clamp 8–30, exit-PE 12–25, Ke floor (Rf+3.5%), growth cap 30% | — | guardrails | 🟠 methodology guardrails; flagged when they bind |

Legend: 🟢 data-anchored or flagged at runtime · 🟠 framework methodology (assumption by nature, no free source).

---

## 4. Conclusion

- **No silent guesses remain.** beta→1.0, tax→21%, growth→8% are flagged
  (`validate._assumption_flags`); ERP staleness and terminal-margin fallbacks are
  flagged in the engine. (Live: NVO flags the 21% tax default.)
- **All primary financials are real** (SEC/Yahoo/FMP) and regression-checked.
- **Terminal margin is now data-anchored** to the company's own SEC operating margin
  for profitable names; only pre-profit names use a flagged fallback.
- Remaining constants (terminal ROIC, R&D life, guardrails) are **assumption-by-nature**
  methodology with no free source — documented here.

### Follow-ups — DONE
1. ✅ **ERP centralised** in `config.ERP` / `ERP_AS_OF`; engine + validate read one
   value; the app flags it once older than `ERP_STALE_MONTHS` (3).
2. ✅ **Terminal margin anchored** to current SEC op margin (clamped 5–40%); the
   per-ticker table is now a *pre-profit-only* fallback, and the source is flagged.
3. ✅ **validate ERP aligned** to `config.ERP` (was 4.75% vs engine 4.23%).
