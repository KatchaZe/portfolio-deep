# Portfolio DEEP v7.3 App — Design Document (v2, as-built)

A local web app that values stocks with the **DEEP Framework v7.3** on reliable,
cross-checked **free** data, and manages a portfolio, watchlist, and allocation view.

> **Mid-build pivot (important).** The original plan made **FMP** the primary
> financial source. During Phase 1 we found the **FMP free tier blocks the
> statement endpoints for many symbols** (AVGO/ORCL/NVO returned "Premium")
> while only `profile` works for all. So the data strategy was changed to:
> **SEC EDGAR = primary financials (free, all US + 20-F filers)**, **FMP = profile
> only (sector/beta/price)**, **Yahoo = forward EPS + momentum + FX**. This doc
> reflects the as-built result.

---

## 1. Principles

1. **Free, authoritative-first data** — SEC EDGAR XBRL for financials; FMP profile
   for sector/beta/price; Yahoo for analyst forward EPS, momentum, and FX. No paid keys required.
2. **Separation of layers** — `sources → pipeline → domain → store → api → ui`.
   The domain (DEEP math) is pure and never touches the network.
3. **Provenance + confidence** — every value records its source; each ticker gets a
   confidence score + green/yellow/red tier so you know what to trust.
4. **Fixture tests** — real responses for the hard tickers (AVGO/ABBV/ORCL/NVO/MSFT)
   are frozen as regression tests, so a data regression fails a test instead of shipping.
5. **Version-swappable engine** — the DEEP framework lives behind a stable contract;
   upgrading (e.g. v7.4) touches only the engine.

---

## 2. Module structure (as-built)

```
portfolio-app-v2/
  config.py                  # tickers, CIKs, FMP base, SEC UA, DEEP_VERSION
  app.py                     # FastAPI endpoints
  index.html                 # 3-tab dashboard (vanilla JS + Chart.js)
  store.py                   # local JSON: holdings, watchlist, facts, results, momentum, fmp_usage
  sources/                   # fetch only — no math, each mockable
    sec_edgar.py             #   PRIMARY financials: robust TTM, freshest-tag pick, currency-aware
    fmp.py                   #   profile (sector/beta/price); parse_profile  [statements are premium-gated]
    yahoo.py                 #   forward EPS, beta, price, shares, growth, chart→momentum, ^TNX, FX
  domain/                    # pure, deterministic, unit-tested
    facts.py                 #   FinancialFacts dataclass (+ provenance / confidence / tier)
    indicators.py            #   RSI / MACD / DBBMV momentum + Action grid
    engine/                  #   versioned DEEP engine
      contract.py            #     Valuation (output) + DeepEngine ABC  ← the stable contract
      deep_v73.py            #     DeepV73Engine: canonical math + facts→Valuation wiring
      __init__.py            #     registry: register(), get_engine(version)
  pipeline/
    normalize.py             #   merge SEC + FMP profile + Yahoo → FinancialFacts (+FX, base confidence)
    validate.py              #   sanity rules + forward-EPS resolution + confidence tier (+earnings nudge)
    rev_track.py             #   build-forward revenue beat/miss (snapshot estimate -> grade vs SEC actual)
    refresh.py               #   orchestration: refresh_fundamentals / run_daily / analyze_row /
                             #                  watchlist_run / allocation / portfolio_view / resolve_cik
  tests/
    fixtures/                #   frozen real JSON: AVGO, ABBV, ORCL, NVO, MSFT (sec/yahoo/fmp profile)
    test_fmp_parse.py        #   FMP profile/parse on real schema (offline)
    test_extract.py          #   SEC extraction + normalize + validate vs known-good
    test_engine.py           #   DEEP engine contract on fixtures
  capture.py                 # fetch real fixtures (SEC + FMP profile + Yahoo)
  verify.py                  # run SEC extraction on fixtures, compare to known-good
  run_tests.py               # run all suites
  requirements.txt · render.yaml · Procfile · .gitignore
  data/portfolio.json        # created at runtime
```

Reused from v1 (verified): the canonical DEEP math (now in `domain/engine/deep_v73.py`)
and the momentum math (`domain/indicators.py`). Everything in the data layer is new.

---

## 3. Canonical data model — `FinancialFacts`

One normalized object per ticker (in reporting currency → converted to USD), consumed
by the engine. Plain values + a `provenance` map (`field → source`) keep it
JSON-serialisable for the store.

```
FinancialFacts:
  meta:        ticker, company, sector, currency, fiscal_year, as_of, price
  income:      revenue, operating_income, net_income, eps_gaap, shares_diluted,
               income_before_tax, tax_expense
  balance:     total_debt, cash, equity
  cashflow:    capex, dep_amort, sbc
  market:      beta
  consensus:   forward_eps (NTM, adjusted), forward_eps_raw, growth_lt (decimal)
  history:     revenue_annuals = [latest_FY, FY-1, FY-2, FY-3]   # clean CAGR
  earnings:    earnings_surprises = [{quarter, eps_actual, eps_estimate,
               surprise_pct, grade}]  # EPS, last ~4 Q, oldest->newest (Yahoo)
  rev-track:   rev_estimate_curq = {quarter_end, estimate}   # snapshotted each refresh
               revenue_quarters  = {end_date: actual}        # SEC ~90-day, to grade snapshots
               (graded revenue beat/miss HISTORY lives in the store, not here)
  quality:     confidence (0-100), confidence_tier (green/yellow/red), flags[]
  provenance:  { field: source }     # e.g. net_income: "sec", forward_eps: "yahoo"
  derived:     tax_rate (property)
```
(There is no separate `eps_adjusted` field — the adjusted number arrives as the
analyst `forward_eps` from Yahoo; `eps_gaap` is the SEC GAAP value.)

---

## 4. Data sources & field mapping (as-built)

| Field | Source (primary) | Notes / cross-check |
|---|---|---|
| revenue, operating income, net income (TTM) | **SEC EDGAR** | robust TTM = latest FY + current YTD − prior YTD; freshest-tag pick (handles tag switches, off-calendar FY) |
| eps_gaap, shares, debt, cash, equity, capex, D&A, tax | **SEC EDGAR** | freshness-guarded tag selection (e.g. ORCL debt) |
| revenue_annuals (CAGR) | **SEC EDGAR** | clean fiscal-year series |
| currency → USD | **SEC** reported currency + **Yahoo FX** | e.g. NVO DKK → USD |
| forward EPS (NTM, adjusted) | **Yahoo** quoteSummary | validated vs revenue ceiling (rejects bad/unsplit) |
| growth (long-term) | **Yahoo** est. → else SEC annual CAGR | clamped 0–30% in engine |
| sector, beta, price | **FMP** profile (all symbols, free) | Yahoo fallback for beta/price |
| price (daily) + momentum (RSI/MACD/DBBMV) | **Yahoo** chart | free, no FMP quota |
| EPS surprise history (last ~4 Q) | **Yahoo** earningsHistory | beat/meet/miss vs consensus (EPS only; street/adjusted basis) |
| revenue estimate (current quarter) | **Yahoo** earningsTrend `0q` | snapshotted each refresh; graded later vs SEC actual (build-forward) |
| revenue actuals (per quarter) | **SEC EDGAR** ~90-day | grades the snapshotted estimates |
| Rf (10y treasury) | **Yahoo** ^TNX | one fetch per refresh |

**FMP usage:** only the `profile` endpoint (sector/beta/price) ≈ **1 call per ticker**.
Financials are from SEC, so the 250/day free budget is rarely a constraint.

---

## 5. Validation & confidence (`pipeline/validate.py`)

- **Sanity rules** — operating margin in [-50%, 90%], ROIC in [-100%, 300%],
  WACC in [3%, 25%]; out-of-band → flag (this is how the ORCL bad-debt extraction
  was caught and then fixed).
- **Forward-EPS resolution** — keep the Yahoo analyst (adjusted) EPS when it sits
  under a revenue-capacity ceiling (`rev/share × 0.65`); otherwise replace with a
  SEC-derived forward EPS. (Keeps ABBV's adjusted $16, rejects AVGO's unsplit $19 → ~$6.)
- **Cross-source check** — when FMP statement data is available for a symbol, compare
  SEC vs FMP revenue/net income (≤5% → ✓). Mostly N/A on the free tier.
- **Currency** — non-USD filers converted via FX; flagged.
- **Earnings track record** — a *bounded* confidence nudge from the EPS-surprise
  history: `delta = round((beats − misses) / total × 10)`, clamped to ±10, needs
  ≥2 quarters; recorded as a flag (e.g. `earnings 3B/1E/0M (+8 conf)`). This adjusts
  **data confidence only** — it never enters the DEEP valuation math.
- **Confidence + tier** — score from completeness + flags (+ earnings nudge);
  🟢 ≥80 · 🟡 50–79 · 🔴 <50.

---

## 6. DEEP v7.3 engine — versioned & swappable

`domain/engine/` isolates the framework behind a contract:
- `contract.py` — `Valuation` (output dataclass) + `DeepEngine` ABC (`evaluate(facts, rf) → Valuation`).
- `deep_v73.py` — `DeepV73Engine` (version "7.3"): WACC = Rf+β·ERP, ROIC, Justified PEG,
  Future Value Projection, Terminal-Anchored Reverse DCF, weighted composite
  (D .20 / E .20 / Ec .30 / P .30) → stars + recommendation + Stage 1.8 anchor + verdict.
- `__init__.py` — registry; `get_engine()` reads `config.DEEP_VERSION`.

**Upgrade to v7.4** = add `deep_v74.py` (`class DeepV74Engine(DeepEngine)`), `register()` it,
set `DEEP_VERSION="7.4"`. The data layer, store, API, and dashboard are untouched —
they only speak `FinancialFacts` (in) and `Valuation` (out).

Example verdict:
> *HOLD/Accumulate ★★★★☆ — Justified PEG $53 (+24% upside); range $53–$66. ROIC 34% vs WACC 6%, growth 11%.*
> Priced-for-perfection names (e.g. ARM) → anchor null by design; verdict shows the
> Reverse-DCF implied CAGR instead.

---

## 7. Dashboard — three tabs (`index.html`)

**Tab 1 · My Portfolio** (stored): `Ticker(+✕) | Price | Chg | Shares | AvgCost | MktVal |
P/L $ | P/L % | DEEP★+reco | Momentum(+RSI/MACD/DBBMV tooltip) | Action | Anchor FV (or
RevDCF implies %) | Upside | Earnings | Verdict`, plus a TOTAL row and a confidence dot.
The **Earnings** cell shows two rows of up to 4 circles (oldest→newest) — 🟢 beat /
🟡 meet / 🔴 miss, hover for quarter/actual vs estimate/surprise%:
- **EPS** (from Yahoo, immediate).
- **Rev** (built forward — empty until snapshots are graded; see §9). Add/update a
holding with **shares + avg cost** (auto-fetches). **Run Fundamental Refresh** / **Run
Daily**. Remove with the red ✕ (deletes its cached data).

**Tab 2 · Watchlist** (names persisted, data ephemeral): add a ticker → **Run watchlist**
to analyse on demand (not stored). Same columns incl. the **Earnings** circles. Per row
**+ Portfolio** (prompts for shares + avg cost, then moves it to Tab 1) and ✕ remove.

**Tab 3 · Allocation** (cost basis): two Chart.js doughnuts (by holding, by sector).
**What-if**: up to 5 (ticker, buy $) → **Calculate** → before-vs-after pies.

---

## 8. Storage (`data/portfolio.json`)

```jsonc
{
  "holdings":  { "NVDA": { "shares": 40, "avg_cost": 110.5, "added": "2026-06-01" } },
  "watchlist": ["AMD", "CRM"],                 // names only
  "facts":     { "NVDA": { ...FinancialFacts.to_dict()... } },   // holdings only
  "results":   { "NVDA": { ...Valuation.to_dict()... } },
  "momentum":  { "NVDA": { ...indicators.compute()... } },
  "fmp_usage": { "2026-06-09": 12 },           // daily FMP-call counter (quota guard)
  "updated":   { "NVDA": "2026-06-09" },
  "rev_snapshots": { "NVDA": { "2026-07-31": {"est": 5.4e10, "captured": "2026-06-09"} } },  // pending estimates
  "rev_surprises": { "NVDA": [ {"quarter":"2026-04-30","rev_actual":..,"rev_estimate":..,"surprise_pct":..,"grade":".."} ] }  // graded, <=4
}
```
Removing a holding deletes it from `holdings`, `facts`, `results`, `momentum`,
`rev_snapshots`, `rev_surprises`. Watchlist tickers are never written to `facts`.

**Concurrency & durability.** `save()` is atomic (write temp → `fsync` → `os.replace`),
and a process-level `store.LOCK` (RLock) serializes every mutating request (held in `app.py`
around each job) so concurrent writes can't lose updates or corrupt the file. Reads stay
lock-free because the swap is atomic. (For true multi-user this would move to a DB — see `REVIEW.md`.)

**SEC fair-access.** `companyfacts` is cached to `data/cache/` with a 12h TTL and SEC requests
are throttled (`config.SEC_MIN_INTERVAL`); the ticker→CIK map is cached ~30 days. This avoids
re-downloading multi-MB JSON each refresh and respects SEC's rate limits.

**Revenue track record (build-forward, `rev_track.py`).** Free data has no *historical*
revenue estimates, so the app makes its own: on every fundamentals refresh it snapshots
the current-quarter consensus (`rev_estimate_curq`) keyed by quarter end; when the SEC
~90-day actual for a snapshotted quarter appears (`revenue_quarters`), it grades beat/meet/
miss (±2%) and appends to a rolling 4-quarter history. Consequences: it starts empty and
fills one circle per reported quarter (~1 yr for four); it only accumulates where the store
persists (**run locally** — Render free wipes the disk each deploy); refresh at least once
per quarter, ideally *before* earnings, so the estimate is captured before it rolls over.
This is **display-only** — it never feeds the DEEP math (EPS surprise is the only earnings
signal wired into confidence).

---

## 9. Quota strategy (`store.py` counter + `app.py` guard)

- Budget **250 FMP calls/day**; this app spends ~**1 call/ticker** (profile only).
- `store.add_fmp_calls` / `fmp_used_today` track usage; `app._quota` exposes
  used/cap/percent + **warns at 90%** and shows "~N ticker-fetches left".
  `refresh_fundamentals` skips any ticker that would exceed the cap.
- Because fundamentals change only on earnings, refresh per ticker after it reports
  (or monthly). Daily price/momentum is free (Yahoo) → run anytime.
- Practical capacity: dozens of tickers; the cap is essentially never hit in normal use.

---

## 10. Testing strategy (the trust layer)

- **Fixtures** — committed real SEC + Yahoo (+FMP profile) JSON for AVGO/ABBV/ORCL/NVO/MSFT.
- `test_fmp_parse.py` — FMP profile/parse on real schema (offline).
- `test_extract.py` — SEC robust extraction + normalize + validate vs known-good ranges
  (AVGO net ≈ $25B, ORCL rev ≈ $64B TTM / $57B annual, NVO DKK→USD, AVGO forward-EPS corrected, no out-of-band).
- `test_engine.py` — DEEP engine contract well-formedness on fixtures.
- `test_earnings.py` — EPS-surprise parsing/grading (beat/meet/miss, oldest→newest)
  + the bounded confidence nudge (synthetic; Yahoo earningsHistory isn't in fixtures).
- `test_rev.py` — revenue estimate parsing + the build-forward snapshot→grade→cap logic
  (synthetic; locks accumulation and the 4-quarter cap).
- `run_tests.py` runs all five; `capture.py`/`verify.py` refresh + spot-check fixtures.

---

## 11. Build plan — status (all phases complete)

| Phase | Deliverable | Status |
|---|---|---|
| 1. Data foundation | SEC adapter (robust TTM), FMP profile, Yahoo, `FinancialFacts`, `normalize`, fixtures | ✅ |
| 2. Validation + confidence | sanity rules, forward-EPS resolution, confidence tier, provenance | ✅ |
| 3. Engine (versioned) | `engine/contract` + `deep_v73` + registry, `DEEP_VERSION` switch | ✅ |
| 4. Tab 1 My Portfolio | store, holdings (shares+avg cost), P/L, momentum+breakdown, remove, daily | ✅ |
| 5. Tab 2 Watchlist | run-on-demand, persist names, +Portfolio (forces shares/avg cost), remove | ✅ |
| 6. Tab 3 Allocation | cost-basis pies, sector, what-if before/after (Chart.js) | ✅ |
| 7. Quota guard | daily counter, 90% warning + headroom, over-cap skip | ✅ |
| 8. Regression + deploy | `run_tests.py`, README, render.yaml/Procfile/.gitignore | ✅ |
| 9. Earnings track record | Yahoo EPS-surprise (4Q), beat/meet/miss circles, bounded confidence nudge, `test_earnings` | ✅ |
| 10. Revenue track record | build-forward (`rev_track`): snapshot Yahoo estimate → grade vs SEC actual, Rev circles, `test_rev` | ✅ |

---

## 12. Known limitations
- **Non-GAAP EPS** is not in SEC XBRL; the app uses the analyst (Yahoo) forward EPS when
  plausible, else SEC GAAP (can understate amortization-heavy names like ABBV).
- **Priced-for-perfection** names (ARM, TSLA) get no point fair value by design — the
  Reverse DCF "implied CAGR" is shown instead.
- **Sector** for never-seen tickers needs the FMP key, else "Unknown".
- **Non-USD filers** (NVO) are FX-converted; ADR per-share ratios may need a manual check.
- **Earnings surprise** is **EPS-only** (Yahoo's consensus/street basis) for the last
  **~4 quarters** — a clean GAAP-vs-Non-GAAP split isn't available free.
- **Revenue surprise** is **built forward** (no free historical estimates): empty at first,
  ~1 yr to fill, accumulates only when run locally, and fiscal-Q4 (annual-only filings) may
  not grade (no standalone 90-day period). Display-only — not in the DEEP math.

## 13. Future (not in scope)
Cloud storage + multi-device, auth, more sources for triangulation, earnings/price alerts.
