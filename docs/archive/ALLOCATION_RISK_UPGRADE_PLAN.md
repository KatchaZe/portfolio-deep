# Allocation Tab → Institutional Risk-Desk Upgrade — Work Plan

**Status:** ✅ Implemented (v1, all phases) — code shipped; see `RISK_FEATURE.md` for the developer reference. Run `python run_tests.py` to verify. This plan is retained as the design rationale + isolation audit (§10).
**Target app:** `portfolio-app-v2` (FastAPI + single `index.html`)
**Engine decision:** risk math computed **natively in the Python backend** (not LLM)
**Input source:** the existing **My Portfolio** data (`/api/portfolio` + `data/portfolio.json`), **not** an uploaded image
**Scope:** full 10-step risk-desk workflow + Heuristic Diversification Score + 6-panel Risk Dashboard

> Companion file: `ALLOCATION_RISK_DESK_PROMPT.md` — the risk-desk methodology prompt, rewritten to read from My Portfolio instead of an image. That prompt is the **spec/source-of-truth** for what the backend below must compute.

---

## 0. TL;DR — what changes

Today the **Allocation** tab shows two cost-basis donuts (by holding, by sector) + a "what-if buy" calculator. We upgrade it into a **risk-desk panel** that answers the question the donuts cannot: *"Capital weight ≠ Risk weight — which small position actually drives the portfolio?"*

All 10 workflow stages are computed server-side in a new `domain/engine/risk.py`, exposed via a new `GET /api/risk` endpoint, and rendered in `tab3` with Chart.js. We reuse the data already in My Portfolio and fetch only **free** historical prices (stooq) for correlation/volatility — **zero extra FMP quota**.

---

## 1. Current state (verified against code)

| Piece | Where | What it does today |
|---|---|---|
| Tabs | `index.html` L71-75 | `My Portfolio` (tab1), `Watchlist` (tab2), `Allocation` (tab3) |
| Allocation UI | `index.html` L89-107 | 2 donuts (`pieTicker`, `pieSector`) + what-if buy (5 rows) + after-donuts |
| Allocation JS | `index.html` L307-328 | `loadAllocation()` → `/api/allocation`; `calcWhatif()` → `/api/allocation/whatif` |
| Allocation API | `app.py` L238-265 | `GET /api/allocation`, `POST /api/allocation/whatif` |
| Allocation calc | `pipeline/refresh.py` L572-632 | `allocation()` → cost-basis pies only (by_ticker, by_sector) |
| Chart helper | `index.html` L295-302 | `makePie(id, data)` — reusable doughnut |
| Store | `store.py`, `data/portfolio.json` | `holdings`, `facts`, `momentum`, `results`, `watchlist` |

**Key gap:** allocation today is **capital-weight only** (cost basis). No volatility, correlation, risk contribution, concentration index, or stress test anywhere in the codebase.

---

## 2. Data inventory — what we have vs what we must derive

This is the make-or-break section. The engine quality is capped by data quality, so every metric is tagged with its epistemic source (matching the prompt's `[STORED]/[CALC]/[PROXY]` tags).

### 2a. Already in the store (`facts[ticker]` + `holdings[ticker]`) — `[STORED]`
- `holdings`: `shares`, `avg_cost`
- `facts`: `beta`, `sector`, `currency`, `price`, `company`, `forward_eps`, `growth_lt`, `revenue`, `net_income`, `shares_diluted`, `total_debt`, `cash`, `key_metrics` (WACC/Ke), `confidence_tier`
- Derived in `portfolio_view()`: `market_value`, `pl`, `pl_pct`, totals

### 2b. Computable from stored data — `[CALC]`
- **Capital weight** `wᵢ = market_valueᵢ / Σ market_value`  ✅ trivial
- **Concentration**: HHI = Σwᵢ², Effective N = 1/Σwᵢ², Top-5/Top-10
- **Sector / Currency exposure**: group by `facts.sector`, `facts.currency`
- **Beta-weighted portfolio beta** = Σ wᵢ·βᵢ (one-factor risk model fallback)

### 2c. NOT stored — must fetch or proxy
**Source tiering (accuracy-first, quota-guarded).** Per your call, the risk path **may use FMP** when it raises data confidence — guarded by the existing `QUOTA_CAP=250` pre-check and graceful fallback. Each input is sourced in this order and **confidence-tagged**: `[FACT]` FMP/adjusted → `[CALC]` stooq realized → `[JUDG-PROXY]` asset-class default.

| Need | Tier 1 (high-confidence) | Tier 2 (free) | Tier 3 (fallback) |
|---|---|---|---|
| Per-asset **volatility** (σᵢ) | FMP **dividend-adjusted** history → realized σ `[FACT]` | stooq daily closes → realized σ `[CALC]` | asset-class proxy table `[JUDG-PROXY]` |
| **Correlation** matrix (ρᵢⱼ) | FMP adjusted history → realized ρ `[FACT]` | stooq → realized ρ `[CALC]` | beta-implied / crisis proxy `[JUDG-PROXY]` |
| **Crisis-regime** corr | realized over crisis windows (2020-03, 2022, 2008 if data) `[CALC]` | — | equity-pairs ≈ 0.9 proxy `[JUDG-PROXY]` |
| **Beta** (one-factor model) | FMP `profile` fresh beta `[FACT]` | cached `facts.beta` `[STORED]` | industry default `[JUDG-PROXY]` |
| **Sector / asset class** | FMP `profile` for any gap `[FACT]` | cached `facts.sector` `[STORED]` | infer/classifier map `[DERIVED]` |
| **Factor exposure** (growth/momentum/quality) | momentum_v2 + sector + beta heuristics | — | `[JUDG]` label |
| **ETF look-through** (X-ray, overlap) | FMP ETF-holdings endpoint when an ETF is held `[FACT]` | — | **N/A today** — all holdings are single names; build the hook |
| **Options / futures** | not in data model | — | **out of scope** — future field |

> **Why FMP helps here:** stooq closes can be *split-only* (the momentum module already flags this) → realized vol/corr understate dividend effects. FMP's **dividend-adjusted** series removes that bias, so the correlation matrix — the weakest link in the whole engine — gets materially more trustworthy. That is the single best use of the relaxed quota rule.
>
> **Already built, just reused:** `sources/fmp.py` `fetch_history()` + `parse_history()` already pull daily history and use **`adjClose`** (the momentum composite uses them), with stooq as the free fallback. So `risk_prices.py` reuses tested code — accuracy goes up with near-zero new fetch logic and no new failure surface.
>
> **Honest caveat (unchanged):** even FMP gives *realized* history; past correlation ≠ crisis correlation. The prompt's "Crisis Correlation > Normal Correlation" rule still holds — report **both** regimes and fall back to conservative proxies for the crisis column, always tagged.

**Quota discipline (so Portfolio/Watchlist never starve):** pre-check `fmp_used_today + planned_calls ≤ QUOTA_CAP` *before* spending (same guard as `fetch_watchlist`); if it would exceed, **degrade to stooq/proxy** for the remaining tickers instead of erroring; cache adjusted history in `risk_cache.json` so each ticker costs FMP **once per refresh**, not per tab open; return the live `quota` block in the `/api/risk` response so the badge updates exactly like the other tabs.

---

## 3. Architecture

Keep the app's existing pattern (sync endpoints, slow network outside the lock, store.LOCK only for fast save).

```
NEW  domain/engine/risk.py        # pure functions, no I/O — fully unit-testable
NEW  pipeline/risk_prices.py      # adjusted daily closes (FMP→stooq fallback) + return matrix; cached
NEW  GET /api/risk                # app.py — READ-ONLY on main store; sync def; net outside LOCK
EDIT index.html (tab3 only)       # 6-panel dashboard, reuse makePie + add bar/scenario charts
NEW  data/risk_cache.json         # SEPARATE cache file — never write portfolio.json (see §10)
NEW  tests/test_risk.py           # math correctness on a fixed fixture portfolio
NEW  tests/test_no_regression.py  # Portfolio/Watchlist endpoints unchanged (see §10)
```

> Note: `asset_class` may optionally be added to `facts[t]` (ignored by `portfolio_view()`), but the **price/vol/corr cache lives in its own `data/risk_cache.json`** so the shared `portfolio.json` is never rewritten by the risk feature.

Why a separate `risk.py` of **pure functions**: the user is a Python beginner — pure functions (inputs → numbers, no network, no files) are the easiest thing to read, test, and trust. All fetching lives in `risk_prices.py`; all math lives in `risk.py`.

**Caching:** realized vol/corr change slowly. Cache the return matrix + computed corr in a **separate `data/risk_cache.json`** (its own tiny load/save), keyed by a **hash of the holdings set + as-of date**, so `/api/risk` is instant on reload and only recomputes when holdings change or daily prices refresh. Keeping the cache out of `portfolio.json` is a deliberate isolation choice (see §10) — the risk feature must never rewrite the file the Portfolio/Watchlist tabs depend on.

---

## 4. The 10-step workflow → concrete Python

Each row: what the prompt asks → the formula → the data source → the epistemic tag in output.

| # | Stage | Computation (in `risk.py`) | Source / tag |
|---|---|---|---|
| 1 | **Extraction** | read `holdings` + `facts`; build per-asset table (qty, MV, weight, currency) | `[STORED]/[CALC]` |
| 2 | **X-ray / look-through** | single names pass through; ETF→underlying only if ETF present (hook now, data later) | `[STORED]`; ETF = N/A today |
| 3 | **Overlap** | pairwise fund overlap `Σ min(wᵢᴬ,wᵢᴮ)` — only when ≥2 funds exist | N/A today (no funds) |
| 4 | **Concentration** | Top-5/10, HHI=Σwᵢ², EffN=1/Σwᵢ², sector/currency/factor HHI | `[CALC]` |
| 5 | **Correlation & true diversification** | σₚ=√(wᵀΣw); Diversification Ratio DR=(Σwᵢσᵢ)/σₚ; normal + crisis regimes | realized `[CALC]` or `[PROXY]` |
| 6 | **Risk contribution** | MCRᵢ=(Σⱼwⱼσᵢσⱼρᵢⱼ)/σₚ; RCᵢ=wᵢ·MCRᵢ; Signed %RC; Absolute share qᵢ=|RCᵢ|/Σ|RCᵢ|; ENB=1/Σqᵢ² | `[CALC]` |
| 7 | **Tail / stress test** | (A) historical replay scalars (GFC/COVID/2022/2024 carry) applied via beta+sector sensitivities; (B) hypothetical shocks (Nasdaq −35%, semis −45%, BTC −60%, +200bps, FX ±15%); (C) reverse stress = solve loss=tolerance; VaR95/99 + CVaR parametric/MC | `[JUDG-SCENARIO]` |
| 8 | **Suitability** | compare stress drawdown vs Risk Tolerance / Capacity / Need (3 inputs from user) | `[JUDG]` |
| 9 | **Position sizing** | per-asset current vs target band from min(conviction, single-name cap, sector cap, RC budget, maxloss/stress, liquidity) | `[CALC]/[JUDG]` |
| 10 | **Rebalancing engine** | policy range → drift decomposition → trigger hierarchy → action (NO TRADE / cashflow / partial / full / exit / emergency) → tax/cost-aware trade list → post-trade re-validation | `[CALC]/[JUDG]` |

### Heuristic Diversification Score (0–100)
Implement exactly as the prompt specifies so the number is reproducible:
`0.35·TrueDiv (0.4·normalDR + 0.6·crisisDR) + 0.30·RiskBalance(ENB_abs) + 0.20·GapCoverage + 0.15·Concentration(EffN)`.
Each sub-score uses the `clamp((x−1)/range,0,1)·100` forms from the prompt. Label the whole thing `[JUDG] diagnostic heuristic, not an industry standard`.

---

## 5. The 6-panel Risk Dashboard (tab3 UI)

Reuse `makePie`; add two new chart helpers (`makeBar`, `makeScenarioBar`). Mobile-readable, color rules from the prompt (red=risk/breach, yellow=warning, green=diversifier/within-policy, grey=neutral/missing).

1. **Portfolio Snapshot** — KPI cards: value, # positions, cash %, equity %, est. volatility, est. severe drawdown, Diversification Score.
2. **Capital Allocation** — donut by asset-class / sector / risk-sleeve (keep current donuts, regroup by risk driver e.g. Semiconductor / AI-Growth / Defensive / Cash).
3. **Capital Weight vs Risk Weight** — horizontal bar, %Capital vs Signed %RC, highlight the "small money, big risk" names with a one-line caption.
4. **Concentration Map** — sector / factor / currency / Top-10 + hidden-concentration callouts (e.g. semiconductor cycle, USD).
5. **Stress Test** — scenario bar of estimated loss % per shock, each tagged `Illustrative, not a forecast`.
6. **Current vs Proposed** — before→after bars when a rebalance is proposed (vol, drawdown, top risk contributor, DR, cash, turnover).

Plus an 8–12 line **plain-language explanation** under the dashboard (the prompt's "Visual Explanation" section).

---

## 6. Phased roadmap (ship value early, hardest last)

Each phase is independently shippable and testable.

**Phase 0 — Plumbing (½ day)**
Create `risk.py` skeleton + `GET /api/risk` returning capital weights only; render panel 1 (snapshot KPIs) + panel 2 (regrouped donut). *Verifies the wiring before any hard math.*

**Phase 1 — Deterministic metrics, no new data (1 day)**
Concentration (HHI, EffN, Top-N), sector/currency exposure, portfolio beta, Capital-vs-Risk **using beta as the single risk factor** (no correlation matrix yet). Panels 3 + 4. → `[CALC]`.

**Phase 2 — Real volatility & correlation (1–2 days)**
`risk_prices.py`: fetch **dividend-adjusted** daily closes (FMP first for accuracy, stooq fallback, proxy last), build return matrix, realized σ + ρ, σₚ, DR, full MCR/RC, ENB, Diversification Score. Normal regime first, crisis regime via realized-window-or-proxy. Quota pre-check + degrade-to-free; cache in `risk_cache.json`. Confidence-tag each input. → upgrades panel 3 from beta-proxy to full covariance.

**Phase 3 — Stress test (1 day)**
Historical replay + hypothetical shocks + reverse stress + VaR/CVaR. Panel 5. All `[JUDG-SCENARIO]`.

**Phase 4 — Suitability + Position sizing + Rebalancing engine (2 days)**
Add a small intake (3 fields: risk tolerance %, horizon, cash needed by 1/3/5y) — reuse the `addform` style. Drift decomposition, trigger hierarchy, trade list, post-trade validation, panel 6 (before→after).

**Phase 5 — Polish (½ day)**
Plain-language explanation generator, epistemic-tag legend, mobile layout, empty/edge states (single holding, no cost basis, stale prices).

> Total ≈ 6–7 working days. Phases 0–2 already deliver the headline insight (capital vs risk). Stop after Phase 2 if time-boxed — it's the 80/20.

---

## 7. Testing plan

- `tests/test_risk.py` on a **fixed fixture portfolio** with hand-checked answers: weights sum to 1.0, HHI/EffN against a known case, RC sums to σₚ, signed %RC sums to 100%, DR≥1, score in 0–100.
- Edge cases: 1 holding (EffN=1, DR=1), holding with no cost basis, missing beta (→ proxy + `[PROXY]` tag), all-cash.
- Reuse existing `run_tests.py` runner; keep `risk.py` pure so tests need no network.
- Verification step before sign-off: re-run the full suite + spot-check one real portfolio's numbers by hand.

---

## 8. Risks & honest caveats (read before building)

1. **Proxy correlation is the weakest link.** Realized history ≠ next crisis. Always show both regimes; never present `[PROXY]` numbers as facts. Decision rule: if two options differ by less than estimation error, say "not materially different."
2. **Only 7 single-name holdings today**, all equity, mostly US + semis. X-ray/overlap (steps 2–3) and the multi-asset diversifier story are partly inert until ETFs/bonds/gold are added — build the hooks, don't fake the output.
3. **No options/futures** in the data model → the prompt's notional/delta/max-loss section is documented but deferred (needs a new holding type).
4. **Stress test is illustrative**, not a forecast — enforce the label in code (cannot render a scenario without the tag).
5. **Beta quality**: `facts.beta` source/recency varies; flag stale beta.
6. Keep the existing **what-if buy** feature working — the risk panel is additive, not a replacement.

---

## 9. Open decisions for you

- **Crisis regime**: realized crisis-window correlation (needs ≥3y daily history) vs fixed proxy table — start with proxy, upgrade later?
- **Intake placement**: inline mini-form in tab3 vs a small settings drawer reused across tabs?
- **Risk sleeves**: do you want a fixed mapping (Semiconductor / AI-Growth / Defensive / Financial / Gold / Cash-Bond) or auto-group by sector?
- **Where rebalancing "targets" come from**: you set them once, or the engine proposes ranges from the risk budget?

---

## 10. Regression safety — Portfolio & Watchlist stay byte-for-byte identical

Goal: the risk upgrade is **purely additive**. The My Portfolio and Watchlist tabs must behave exactly as today. Below is the impact audit (verified against the current code) and the rules that guarantee it.

### 10a. Shared-resource impact audit

| Shared resource | Used by Portfolio/Watchlist | Risk feature touches it? | Verdict |
|---|---|---|---|
| `data/portfolio.json` | read+write (holdings, facts, momentum, fmp_usage) | **No** — risk uses its own `data/risk_cache.json` | ✅ isolated |
| `store.load()` / `save()` | every endpoint | risk reads via `load()`; only write = `fmp_usage` counter via reload→add→save-under-LOCK (same as `/api/allocation/whatif`) | ✅ no lost-update |
| `store.LOCK` | held for fast saves | risk does network **outside** lock; only the tiny counter save is serialized | ✅ no contention |
| `portfolio_view()` | builds tab1/tab2 rows | risk **reuses read-only**, signature/return unchanged | ✅ contract intact |
| `allocation()` | tab3 donuts + what-if | unchanged; new code is separate functions | ✅ |
| FMP quota (`fmp_usage`) | quota badge, refresh, watchlist | risk **may use FMP** (opt-in, accuracy) with `QUOTA_CAP` pre-check + degrade-to-stooq; counter committed via the proven pattern; cached once per refresh | ✅ shared safely, never starves other tabs |
| `index.html` `#p1`/`#p2` | tab1/tab2 DOM | risk writes only into `#p3` | ✅ |
| `showTab(n)` | all tabs | edit **only the `n===3` branch**; `n===1/2` untouched | ⚠️ confine edit |
| `makePie`, `f`, `money`, `esc`, `dot`, `tag` | shared helpers | **read-only reuse**; no signature change | ✅ |
| `load()`, `renderPortfolio()`, `loadWatchlist()` | tab1/tab2 | **not modified** | ✅ |

### 10b. Hard rules the implementation must follow

1. **Risk data (return matrix, corr) goes only to `risk_cache.json`.** The risk feature must **never write holdings/facts/momentum**. The *only* permitted write to `portfolio.json` is bumping the `fmp_usage` counter — and only via the proven `with st.LOCK: s = st.load(); st.add_fmp_calls(s, calls); st.save(s)` pattern (reload-fresh-then-save), identical to `/api/allocation/whatif`, so a concurrent holding edit can't be clobbered.
2. **FMP is allowed in the risk path for accuracy (your call), but quota-guarded.** Pre-check `fmp_used_today + planned ≤ QUOTA_CAP` before spending; if it would exceed, **degrade to stooq/proxy** rather than block; do FMP fetches **outside** `st.LOCK`; cache adjusted history so a ticker costs FMP once per refresh, not per tab open; return the `quota` block so the badge stays accurate. Confidence-tag every input (`[FACT]` FMP → `[CALC]` stooq → `[JUDG-PROXY]`).
3. **Cache keyed by holdings-hash**, so adding/removing a holding auto-invalidates it. This sidesteps `remove_holding()` (which only cleans a fixed key set and would otherwise leave stale risk entries).
4. **Sync `def` endpoint; network outside `st.LOCK`.** `app.py` documents a past bug where an `async def` what-if blocked the event loop and froze the whole server — follow the established sync pattern.
5. **Additive code only.** New functions in `risk.py`/`risk_prices.py`; do **not** change the return shape of `portfolio_view()` or `allocation()` (tab1/tab2 and the what-if calculator parse those exact shapes).
6. **`index.html` edits confined to `#p3` + the `showTab` `n===3` branch.** Add new JS functions (`makeBar`, `loadRisk`, …); don't alter `load`, `renderPortfolio`, `loadWatchlist`, or `makePie`.

### 10c. Regression test checklist (run before/after each phase)

- `python run_tests.py` — full existing suite stays green (no edits to its covered modules).
- New `tests/test_no_regression.py`: assert `/api/portfolio`, `/api/allocation`, `/api/allocation/whatif`, `/api/watchlist*` return the **same JSON keys** as a saved baseline snapshot.
- Manual smoke: add a holding, remove a holding, run Daily, run Watchlist, run what-if — all unchanged; quota badge increments only on FMP actions (not on opening the risk tab).
- Diff guard: opening the Allocation/Risk tab must **not** modify `data/portfolio.json` (compare mtime/hash before and after).
- Concurrency: open risk tab while a fundamental refresh runs → holdings/facts intact afterward.

### 10d. Honest residual risks

- **One unavoidable shared edit**: the single `showTab` line. Mitigation: change only its `n===3` branch; covered by a manual tab-switch smoke test.
- **Google Drive sync**: keeping the cache in a separate file means `risk_cache.json` is *not* auto-mirrored to Drive (unlike `portfolio.json`). That's acceptable — the cache is rebuildable from stooq on demand; document it so a cold-start instance simply recomputes.
