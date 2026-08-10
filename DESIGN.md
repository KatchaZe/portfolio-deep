# Portfolio DEEP v8.2 App — Design Document (v2, as-built)

A local web app that values stocks with the **DEEP Framework v8.2** (v7.3 retained for
rollback) on reliable, cross-checked **free** data, and manages a portfolio, watchlist,
and allocation view.

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
portfolio-deep/
  config.py                  # tickers, CIKs, FMP base, SEC UA, DEEP_VERSION, ERP (+as-of/stale)
  app.py                     # FastAPI endpoints — thin routes only (risk payload builder → pipeline/risk_report.py, 2026-07-19)
  index.html                 # 6-tab dashboard (vanilla JS + Chart.js); Tab 3 = Risk Desk, Tab 6 = Correlation (share ONE /api/risk payload), Tab 4/5 = How to / Ref
  store.py                   # local JSON store: holdings, watchlist, facts, results, momentum, fmp_usage, rev_*
  store_sync.py              # Google-Drive mirror subsystem (2026-07-19 split): pull-on-cold-start · background push worker · push guard
                             #   + aux mirror (19b): risk_cache.json shared via Drive — one machine pays FMP, every instance reuses
  sources/                   # fetch only — no math, each mockable
    sec_edgar.py             #   PRIMARY financials: robust TTM, freshest-tag pick, currency-aware (+v8.2 fields)
    fmp.py                   #   profile (sector/beta/price) · earnings · quote · analyst-estimates · peers · adjClose
    yahoo.py                 #   forward EPS, beta, price, shares, growth, chart→momentum, 5y prices, ^TNX, FX
    stooq.py                 #   free daily OHLC (no key) — price/momentum + risk-return fallback
    finnhub.py               #   optional 3rd EPS-surprise cross-check (free key)
    alphavantage.py          #   optional 4th EPS-surprise cross-check (free key, 25/day)
    gdrive_store.py          #   OAuth Google-Drive mirror of portfolio.json (persistence)
  domain/                    # pure, deterministic, unit-tested
    facts.py                 #   FinancialFacts dataclass (+ provenance / confidence / tier; v8.2 fields)
    momentum.py              #   PRIMARY signal: MOM_12_1 · ROC_6M · SMA200 · RSI(14) → composite (QuantInsti)
                             #   + S9: reversal_flag (3-5y), market_state (SPY vs 200-DMA), crash_guard
    indicators.py            #   secondary: RSI / MACD / DBBMV mean-reversion + Action grid (S11 taxonomy)
    advice.py                #   v8.3 Thai action synthesis per row: VALUE → QUALITY → TIMING → Action
    pead.py                  #   S25 post-earnings drift bias from the latest surprise
    costs.py                 #   S6 net upside after round-trip trading cost + capital-gains tax
    philosophy.py            #   S1/S42 philosophy-fit: what the portfolio actually runs vs profile
    diversification.py       #   per-PORTFOLIO diversification philosophy (Correlation tab; S2-4/S35-41; gauge/pillars/story)
    engine/                  #   versioned DEEP engine
      contract.py            #     Valuation (output) + DeepEngine ABC  ← the stable contract
      deep_v82.py            #     DeepV82Engine ("8.2", ACTIVE): true WACC/Ke, R&D-ROIC, EQ, 2-stage PEG, EV RevDCF, rubric
      deep_v73.py            #     DeepV73Engine ("7.3"): kept for one-line rollback
      risk.py                #     Risk Desk math (pure): weights/cov/DR/RC/ENB/stress/VaR/sizing/rebalance
                             #     + Correlation Monitor: pair_corr/sector_corr/top_pairs/downside_corr/rolling_corr
      __init__.py            #     registry: register(), get_engine(version)
  pipeline/
    normalize.py             #   merge SEC + FMP + Yahoo → FinancialFacts (+FX, base confidence)
    validate.py              #   sanity rules + forward-EPS resolution + assumption flags + confidence tier
    consensus.py             #   blend forward-EPS (median + dispersion) · reconcile EPS-surprise sources
    rev_track.py             #   build-forward revenue beat/miss (snapshot estimate -> grade vs SEC actual)
    margin_track.py          #   operating-margin YoY trend per SEC quarter (expand/flat/contract)
    surprise_backfill.py     #   immediate EPS/Rev beat/miss from SEC actual × FMP estimate
    pricecache.py            #   disk cache for the long (2y/5y) adjusted price series (R4)
    market_valuation.py      #   S32/33 market overlay: implied-ERP band → cheap/fair/expensive + tilt
    screen.py                #   S13/S19 GARP screen: cheap × quality rank over stored subscores
    prices.py                #   ★2026-07-19★ THE price-series module — all 3 fetch ladders, one roof:
                             #     fetch_daily_series (yahoo→stooq) · fetch_daily_adjusted (yahoo→fmp→stooq +pricecache)
                             #     fetch_returns (risk desk): pricecache→yahoo→fmp→stooq→stale-cache→proxy — reuses the pool
                             #     momentum paid for + direct Yahoo for NEW holdings; no-poison daily cache (failures retried)
    risk_report.py           #   /api/risk payload builder (extracted from app.py) — injectable fetch_returns seam,
                             #     realized/mixed/proxy covariance (hybrid_cov) + meta.proxy_tickers
    risk_prices.py           #   DEPRECATED shim → pipeline/prices.py (kept for old imports)
    refresh.py               #   orchestration: fetch/commit_fundamentals · fetch/commit_daily (PARALLEL, 4 workers) ·
                             #                  watchlist · allocation · portfolio_view · _earn_status · resolve_cik
                             #                  (get_prices / get_prices_long = thin delegators → prices.py)
  tests/                     # 55 suites (run_tests.py) — see §10
    fixtures/                #   frozen real JSON: AVGO, ABBV, ORCL, NVO, MSFT (sec/yahoo/fmp profile)
    test_engine_v82.py · test_momentum.py · test_risk.py · test_no_regression.py · …
  capture.py                 # fetch real fixtures (SEC + FMP profile + Yahoo)
  verify.py                  # run SEC extraction on fixtures, compare to known-good
  run_tests.py               # run all 55 suites
  requirements.txt · render.yaml · Procfile · .gitignore
  data/portfolio.json        # created at runtime (+ data/risk_cache.json, data/cache/)
```

Reused from v1 (verified): the canonical DEEP math (ported into the versioned engine,
now `domain/engine/deep_v82.py` with `deep_v73.py` kept for rollback) and the mean-reversion
indicators (`domain/indicators.py`, now secondary). The **primary momentum signal**
(`domain/momentum.py`) and the **Risk Desk** (`domain/engine/risk.py`) were added later; the
whole data layer is new.

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
| price (daily) + momentum | **Yahoo** chart (→ **Stooq** fallback) | free, no FMP quota; **primary** = `momentum.py` composite (MOM_12_1/ROC/SMA200/RSI), RSI/MACD/DBBMV is secondary |
| EPS surprise history (last ~4 Q) | **Yahoo** earningsHistory | beat/meet/miss vs consensus (EPS only; street/adjusted basis) |
| revenue estimate (current quarter) | **Yahoo** earningsTrend `0q` | snapshotted each refresh; graded later vs SEC actual (build-forward) |
| revenue actuals (per quarter) | **SEC EDGAR** ~90-day | grades the snapshotted estimates |
| Rf (10y treasury) | **Yahoo** ^TNX | one fetch per refresh |
| **CFO, total assets, receivables, interest expense, R&D (+5y series), acquisitions, deferred revenue, prior-year balances** | **SEC EDGAR** | v8.2: earnings quality, true-WACC Kd, R&D capitalization, organic-growth/billings, 5y spread trend |
| **consensus path** (next/far-FY revenue growth + analyst count) | **FMP** analyst-estimates | v8.2 growth fade (Demand durability) + reliability gate |
| **peer set** | **FMP** stock-peers (helper) + **sector cohort** | peer-median growth computed free from the batch; FMP peers refine the cohort |
| **own 5y P/E percentile** | **Yahoo** 5y monthly prices + **SEC** annual EPS | re-rating signal; USD filers, fills on localhost (Yahoo blocks cloud IPs) |

**FMP usage (v8.2):** `profile` + `earnings` + `analyst-estimates` ≈ **3 calls per ticker**
(+ `quote`/`peers` on demand). Financials are still SEC, so the 250/day free budget holds.
Peer-median and own-P/E add **no FMP calls** (sector cohort + Yahoo). Inputs with no free
source (NRR, sentiment, terminal margin, survival prob) are **skipped + flagged**, never guessed.

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

## 6. DEEP engine — versioned & swappable (active: v8.2)

`domain/engine/` isolates the framework behind a contract:
- `contract.py` — `Valuation` (output dataclass) + `DeepEngine` ABC (`evaluate(facts, rf) → Valuation`).
  v8.x added optional fields (`cost_of_equity`, `eva`, `eq_verdict`, `subscores`) — additive only.
- `deep_v73.py` — `DeepV73Engine` ("7.3"): WACC = Rf+β·ERP (cost of equity), Justified PEG
  (heuristic), FVP, Reverse DCF. **Kept for instant rollback.**
- `deep_v82.py` — `DeepV82Engine` ("8.2", active). v8 finance-fidelity changes:
  - **True WACC** — Ke = Rf+β·ERP; WACC weights after-tax debt + market equity. FCFF→WACC, FCFE→Ke.
  - **ERP 4.23%** (Damodaran implied, Jan 2026) replaces the frozen 4.75%.
  - **EV-bridge Reverse DCF** — anchors on Enterprise Value (mktcap + debt − cash).
  - **R&D-capitalized ROIC** — research asset → adjusted operating income + invested capital.
  - **Earnings-quality screen** — cash conversion / accruals / SBC% → caps Execution.
  - **Fundamental 2-stage PEG** (payout, growth, Ke) with a low-beta sanity clamp on fair P/E.
  - **Numeric DEEP rubric** (deterministic 0–5 bands + bounded adjustments): organic growth,
    incremental ROIC, 5y spread trend, peer-median, own-5y-P/E percentile. `subscores.breakdown`
    carries the full audit trail. Weights unchanged (D .20 / E .20 / Ec .30 / P .30).
  - Adjustments with no free data input are **skipped + flagged**, never fabricated.
- `__init__.py` — registry; both engines registered; `get_engine()` reads `config.DEEP_VERSION`.

**Switch / rollback** = one line in `config.py` (`DEEP_VERSION = "8.2"` ⇄ `"7.3"`). The data
layer, store, API, and dashboard are untouched — they only speak `FinancialFacts` (in) and
`Valuation` (out). The upgrade how-to is `UPGRADE_ENGINE.md`; lessons from the real v7.3→v8.2
upgrade (signature ripple, new-data plumbing, output guards) are in `UPGRADE_ENGINE_REVIEW.md`.

Example verdict (v8.2):
> *BUY ★★★★☆ — Fundamental PEG $172 (+15% upside); range $150–$172. ROIC 28% vs WACC 8.7% (Ke 9.2%), growth 12%, EQ CLEAN.*
> Priced-for-perfection names (e.g. ARM) → anchor null by design; verdict shows the
> Reverse-DCF implied CAGR instead.

---

## 7. Dashboard — five tabs (`index.html`)

**Shared banner (all tabs)** — a **command strip** of four Damodaran tiles:
Regime (S9, SPY vs 200-DMA → per-card crash guard) · Market value (S32/33 implied-ERP
band + tilt) · vs S&P 12M (S16/S35, MV-weighted illustrative) · Philosophy fit (S1/S42),
plus a 🧭 one-line synthesis (`renderSynth`), a stale-assumption banner
(`renderAssumptions`, flags ERP / MARKET_PE older than 3 months), a stale-momentum
banner, the 💾 Drive-persistence badge (`/api/persist`), the FMP quota badge, and a
build-stamp guard (`DASH_BUILD` vs `config.BUILD` via `/healthz`). A keepalive pings
`/healthz` every 4 min while the tab is open (Render free tier spins down at ~15 min).

**Tab 1 · My Portfolio** (stored) — **card layout** (`pcard`), value-first (S5):
header (confidence dot + ⚑ flags + price/P&L as muted context) → **Price-vs-Value gauge**
(`valueGauge`; pre-profit names show implied-vs-actual CAGR bars) → DEEP★ + reco +
**Moat/EQ/GARP chips** → Momentum / cross-sectional Rank / Action (`dispAction` applies the
S9 WAIT·crash-guard display override) → **Anchor FV** (every computed method, ★ = anchor,
`EPS×n` blend badge) + upside gross → net (S6) → S9 guard badges → **Earnings** circles +
PEAD chip → verdict + 💡 Thai advice (`domain/advice.py`) → drawer (WACC/Ke/EVA +
sub-score audit trail). The **Earnings** block shows up to three rows of up to 5 circles
(oldest→newest) — 🟢 beat / 🟡 meet / 🔴 miss, hover for actual vs estimate:
- **EPS** (vs consensus, cross-checked across Yahoo + FMP + Finnhub + Alpha Vantage).
- **Rev** (immediate from FMP when available, else built forward — see §9).
- **Mgn** (operating-margin YoY trend from SEC: expand/flat/contract — fills immediately).
Empty rows are explained, never silent (`_earn_status`): `⟳ refresh` = pre-schema facts,
`× n/a` = primary and fallback sources both empty. Bond/gold/crypto tickers render a
reduced card tagged BOND (S3) / PRICING ASSET (S40-41) — no FV/GARP/EQ.

**Tab 2 · Watchlist** (names persisted, data ephemeral): add a ticker → **Run watchlist**
to analyse on demand (not stored). Same cards. Per card **+ Portfolio** (prompts for
shares + avg cost, then moves it to Tab 1) and ✕ remove.

**Tab 3 · Allocation** — opens with **Cash stance (S32/33)** and the **GARP screen +
quadrant** (S13/S19, `/api/screen`), then the **Risk Desk** (served by `GET /api/risk`)
headed by a **Portfolio Story** (main FINDING + 5 Damodaran principle rows, `riskStory`):
%Capital vs %Risk, concentration (HHI/Effective-N/ENB), correlation/diversification
(DR normal + crisis), stress tests (historical + hypothetical + VaR/CVaR + reverse stress),
**5b downside lens (S2/S4** incl. Portfolio MoS**)**, suitability vs your max-loss
tolerance, and a rebalancing plan (before→after). All math is pure Python
(`domain/engine/risk.py`); numbers carry epistemic tags. Below it (unchanged) are the
cost-basis doughnuts (by holding, by sector) and **What-if**: up to 5 (ticker, buy $) →
**Calculate** → before-vs-after pies. See `RISK_FEATURE.md` for the developer reference.

**Tab 4 · How to** — annotated SVG mockups (numbered callouts + Thai legend tables) of
the stock card, the top banner/command strip, and every Allocation section & graph.

**Tab 5 · Ref** — the full glossary: discount rates / quality / FV methods / DEEP scores /
portfolio-risk metrics, the complete S1–S42 session map, every verdict & Action with
thresholds, and all abbreviations & symbols.

---

## 8. Storage (`data/portfolio.json`)

```jsonc
{
  "holdings":  { "NVDA": { "shares": 40, "avg_cost": 110.5, "added": "2026-06-01" } },
  "watchlist": ["AMD", "CRM"],                 // names only
  "facts":     { "NVDA": { ...FinancialFacts.to_dict()... } },   // holdings only
  "results":   { "NVDA": { ...Valuation.to_dict()... } },
  "momentum":  { "NVDA": { ...momentum.compute() (primary) + indicators.compute() (secondary)... } },
  "market":    { "regime": "risk_on", "ret_12m": 0.15, "valuation": { ...S32/33 overlay + as-of dates... } },  // S9 regime (reserved key ^MARKET)
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
fills one circle per reported quarter (~1 yr for four); it accumulates wherever the store
persists — with Google Drive persistence on (`GOOGLE_DRIVE_OAUTH_SETUP.md`) that now includes
the deployed Render app, not only local runs; refresh at least once
per quarter, ideally *before* earnings, so the estimate is captured before it rolls over.
This is **display-only** — it never feeds the DEEP math (EPS surprise is the only earnings
signal wired into confidence).

---

## 9. Quota strategy (`store.py` counter + `app.py` guard)

- Budget **250 FMP calls/day**; a fundamentals refresh budgets a worst case of
  ~**5 calls/ticker** (profile + quote fallback + earnings + estimates + quarterly-est
  backfill) — the quota is **partitioned up front** (`_partition_by_quota`) so the
  pre-check stays exact under the parallel (4-worker) fetch.
- `store.add_fmp_calls` / `fmp_used_today` track usage; `app._quota` exposes
  used/cap/percent + **warns at 90%** and shows "~N ticker-fetches left".
  `refresh_fundamentals` skips any ticker that would exceed the cap.
- Because fundamentals change only on earnings, refresh per ticker after it reports
  (or monthly). Daily price/momentum is free (Yahoo) → run anytime.
- Practical capacity: dozens of tickers; the cap is essentially never hit in normal use.

---

## 10. Testing strategy (the trust layer)

- **Fixtures** — committed real SEC + Yahoo (+FMP profile) JSON for AVGO/ABBV/ORCL/NVO/MSFT.
- `run_tests.py` runs **55 suites**; `capture.py`/`verify.py` refresh + spot-check fixtures. Highlights:
  - `test_extract.py` — SEC robust extraction + normalize + validate vs known-good ranges
    (AVGO net ≈ $25B, ORCL rev in-range, NVO DKK→USD, AVGO forward-EPS corrected, no out-of-band).
  - `test_engine.py` / **`test_engine_v82.py`** — DEEP v7.3 / **v8.2** engine contract on fixtures.
  - `test_earnings.py` / `test_rev.py` / `test_margin.py` / `test_consensus.py` / `test_finnhub.py` /
    `test_fmp_rev.py` / `test_followups.py` — earnings/rev/margin track record + EPS blend + assumption flags.
  - `test_momentum.py` / `test_stooq.py` / `test_pricecache.py` — momentum composite + price sources/cache.
  - **`test_risk.py`** — risk-engine invariants (weights→1, RC→σp, signed %RC→100%, diversifier<0, DR≥1, score 0–100).
  - **`test_no_regression.py`** — `/api/risk` is additive and never writes `portfolio.json`.
  - `test_fmp_parse.py` / `test_hardening.py` / `test_app_fixes.py` / `test_gdrive.py` — parsers, freeze/crash fixes, Drive mirror.

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
| 11. v8.2 engine | true WACC/Ke, R&D-ROIC, earnings-quality, 2-stage PEG, EV reverse DCF, numeric DEEP rubric; centralised ERP; `test_engine_v82` | ✅ |
| 12. Momentum upgrade | `momentum.py` composite (MOM_12_1/ROC/SMA200/RSI) as primary; Stooq fallback + price cache; `test_momentum` | ✅ |
| 13. Risk Desk | Allocation tab → `domain/engine/risk.py` + `/api/risk` (read-only, isolated cache); stress/VaR/rebalance; `test_risk`, `test_no_regression` | ✅ |
| 14. Persistence + extra sources | Google Drive OAuth mirror; Finnhub/Alpha Vantage cross-check; margin track; consensus blend | ✅ |

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
