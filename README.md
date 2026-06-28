# Portfolio DEEP v8.2 — Stock Screening App (v2)

A local web app that values your stocks with the **DEEP Framework v8.2** on
reliable, cross-checked free data, and manages a portfolio, watchlist, and
allocation view. (v7.3 is retained for one-line rollback — see below.)

- **Data (all free):** SEC EDGAR (primary financials, all US + 20-F filers — incl.
  cash flow, R&D + history, interest, assets, receivables, acquisitions, deferred
  revenue, prior-year balances, **quarterly revenue & operating income**) · FMP
  (profile, earnings, quote, **analyst-estimate consensus path**, **revenue surprise**,
  stock-peers) · Yahoo (forward EPS, momentum, FX, **5y prices**) · **Finnhub /
  Alpha Vantage** (optional 3rd/4th EPS-surprise cross-check) · **Stooq** (price/momentum
  fallback when Yahoo is blocked on cloud hosts).
- **Engine:** DEEP **v8.2** — true WACC (FCFF→WACC, FCFE→Ke), EV-bridge reverse DCF,
  ERP centralised in `config` (flagged when stale), **data-anchored terminal margin**,
  R&D-capitalized ROIC, earnings-quality screen, fundamental 2-stage PEG, and a numeric
  DEEP rubric (organic growth, incremental ROIC, 5y spread trend, peer-median, own-5y
  P/E percentile). Isolated & version-swappable.
- **Momentum & cross-checks:** RSI / MACD / dynamic-Bollinger momentum; an **EPS +
  Revenue + operating-margin** quarterly track record; a **multi-source forward-EPS
  blend** (median + dispersion).
- **Trust:** every value carries provenance + a confidence tier; inputs with no free
  source are **skipped + flagged**, never guessed (incl. beta→1.0, tax→21%, growth→8%,
  ERP staleness, terminal-margin fallback); a 20-module regression suite locks numbers.
  See **`DATA_AUDIT.md`** for the full real-vs-assumed provenance audit.

See `DESIGN.md` for architecture and **`BEGINNER_GUIDE.md` for a step-by-step beginner guide** (run, modify, upgrade the engine).

---

## 1. Run locally

Python 3.9+.

```powershell
cd portfolio-deep
$env:FMP_API_KEY="your_key"     # optional (sector/beta from FMP profile; without it, Yahoo is used)
pip install -r requirements.txt
uvicorn app:app --port 8000
```
Open http://localhost:8000

---

## 2. The three tabs

**My Portfolio** — your holdings. Add a ticker with **shares + average cost** →
it fetches + analyses automatically. Shows price, momentum (hover for RSI/MACD/DBBMV
breakdown), DEEP★ score + recommendation, Action, anchor fair value + upside (or
"RevDCF implies X%" for priced-for-perfection names), **P/L $ and %**, an **Earnings**
track record (🟢 beat / 🟡 meet / 🔴 miss circles — see §3b), confidence dot, and a
one-line verdict. **Run Fundamental Refresh** (SEC+FMP+Yahoo) / **Run Daily**
(Yahoo price+momentum, free). Remove with the red ✕.

**Watchlist** — type a ticker → **Run watchlist** to analyse on demand (not stored;
names are remembered). Per row: **+ Portfolio** (prompts for shares + avg cost, then
moves it to My Portfolio) and ✕ remove.

**Allocation** — opens with the new **Risk Desk** (see §2b): %Capital vs %Risk,
concentration, correlation/diversification, stress tests, and a rebalancing plan.
Below it (unchanged) are the two cost-basis doughnut pies (by holding, by sector)
and **What-if**: enter up to 5 (ticker, buy $) → **Calculate** to see the
allocation before vs after.

---

## 2b. Allocation → Risk Desk (institutional risk view)

The Allocation tab now opens with a **Risk Desk** that answers what the cost-basis
pies cannot: **capital weight ≠ risk weight** (a 20% position in a high-vol name can
be 50%+ of portfolio risk). All math is computed server-side in **pure Python**
(`domain/engine/risk.py`, no numpy) and served by **`GET /api/risk`**. The cost-basis
pies + what-if below it are untouched.

**Controls:** *Max loss รับได้ (%)* (your tolerance), *Horizon*, and *data*:
`auto` = use **FMP dividend-adjusted** history for accurate vol/correlation when the
quota allows, else free **Stooq**; `free` = never spend FMP quota.

**Seven sections** (numbers carry epistemic tags):

1. **Snapshot** — value, #positions, est. annual vol, severe drawdown, portfolio β, **Diversification Score 0–100**, DR normal/crisis.
2. **Capital Allocation** — donuts by **risk sleeve** (Semiconductor / Growth-Tech / Defensive / …) and by **currency**.
3. **Capital Weight vs Risk Weight** — the headline bar: grey %capital vs red %risk (green = a diversifier that *reduces* risk; signed %RC sums to 100%).
4. **Concentration Map** — HHI, Effective N, Top-5/10, ENB, by-sector bar, hidden-concentration callouts.
5. **Stress Test** — historical replays (GFC / COVID / 2022 / 2024 carry) + hypothetical shocks + **VaR/CVaR** + **reverse stress**. All `[JUDG-SCENARIO]` illustrative, not forecasts.
6. **Suitability** — the headline finding: does the worst plausible drawdown exceed your max-loss tolerance? (green = within / red = exceeds).
7. **Rebalancing Plan** — caps each name, proposes trims/adds with priority, shows **before → after** risk metrics; can return **NO TRADE**.

Plus a plain-language **Bottom line** and an epistemic-tag legend.

**Data & quota:** vol/correlation use ~1.5y of daily returns, sourced accuracy-first
and confidence-tagged: FMP adjClose `[CALC]` → Stooq `[CALC]` → asset-class proxy
`[JUDG-PROXY]`. A hard pre-check keeps FMP usage **under the 250/day cap** and degrades
to free sources rather than erroring; beta/sector are read from stored facts `[STORED]`.

**Isolation — the My Portfolio / Watchlist tabs are unaffected:** `/api/risk` is
**read-only** on `portfolio.json`; risk results cache in a **separate
`data/risk_cache.json`**; the only write to the shared store is the FMP-usage counter,
via the same reload→commit-under-lock pattern as the what-if endpoint. The
`test_no_regression` suite locks this. Full rationale + audit: **`RISK_FEATURE.md`**
and `ALLOCATION_RISK_UPGRADE_PLAN.md` §10.

---

## 3b. Earnings track record (EPS / Rev / Margin)

The **Earnings** column shows up to **three** rows of coloured circles per company
(oldest→newest, hover for the numbers).

- **EPS** (beat/meet/miss vs consensus) — surprise > +2% 🟢 beat · −2…+2% 🟡 meet ·
  < −2% 🔴 miss. Cross-checked across **Yahoo + FMP + Finnhub + Alpha Vantage**: the
  reconcile picks a primary track record, marks the sources that confirm it, and flags
  a latest-quarter beat-vs-miss disagreement. A consistent record nudges **confidence**
  (bounded ±10; never touches valuation math).
- **Rev** (beat/meet/miss vs consensus) — two paths: (a) **immediate** from FMP when
  its earnings feed exposes `revenueActual`/`revenueEstimated`; (b) otherwise **built
  forward** — each refresh snapshots Yahoo's current-quarter estimate and grades it once
  the SEC actual lands (~1 year to fill). Build-forward history is preferred when present;
  with Google Drive persistence it survives redeploys.
- **Mgn** (operating-margin YoY trend) — operating income ÷ revenue per quarter from SEC,
  graded 🟢 expand · 🟡 flat · 🔴 contract (±0.5pp). Fills **immediately**, no estimate
  needed. (Empty for IFRS semi-annual filers with no standalone 90-day period, e.g. NVO.)

The **Anchor FV** cell also shows an `EPS×N` badge = how many sources the forward-EPS
**blend** used (hover for the median + min–max range; green = sources agree, red = wide).

---

## 3. Data refresh & FMP quota

- **Fundamentals change quarterly** (on earnings) — refresh after a company reports,
  or monthly. **Daily price/momentum** is free (Yahoo) — run it anytime.
- FMP free tier = **250 calls/day**; this app uses FMP only for the *profile*
  (sector/beta/price) ≈ **1 call per ticker**, because financials come from SEC.
  So you can hold/scan dozens of tickers comfortably. The header bar shows usage and
  warns at 90%; refreshes that would exceed the cap are skipped.

---

## 4. Deploy (access from anywhere)

Render (free) — gives a public URL:
1. Put this repo on GitHub (commit `tests/fixtures/` so tests run).
2. render.com → **New → Web Service** → connect the repo (uses `render.yaml`).
3. In the dashboard set env vars: **FMP_API_KEY** (optional), **APP_TOKEN**
   (recommended — protects your portfolio on the public URL; open `/?token=YOUR_TOKEN`
   once, then it is remembered via cookie), and the three **`GDRIVE_OAUTH_*`** vars
   for Google Drive persistence (see **`GOOGLE_DRIVE_OAUTH_SETUP.md`**).
4. Deploy. Build: `pip install -r requirements.txt`; Start: `uvicorn app:app --host 0.0.0.0 --port $PORT`.

> Free tier sleeps after ~15 min idle (first hit wakes it) and uses an ephemeral disk.
> **Persistence is handled via Google Drive (OAuth):** the app pushes `portfolio.json`
> to *your own* Drive on every save and pulls it back on startup, so holdings (and the
> revenue-snapshot history) survive every redeploy — **no paid Render Disk needed**.
> One-time setup: **`GOOGLE_DRIVE_OAUTH_SETUP.md`**. Without the `GDRIVE_OAUTH_*` vars the
> app still runs, just local-only. (A *service account* can't write to a personal Gmail
> Drive — no storage quota — so OAuth-as-yourself is the supported path.)

---

## 5. Upgrade / switch the framework — only touches the engine

The engine is isolated behind a contract (`domain/engine/contract.py`). Both v7.3
and v8.2 are registered; switch the active one in `config.py`:
```python
DEEP_VERSION = "8.2"   # or "7.3" to roll back instantly
```
To add a future version: add `domain/engine/deep_vNN.py` with `class DeepVNNEngine(DeepEngine)`,
`register()` it in `domain/engine/__init__.py`, and point `DEEP_VERSION` at it. Nothing in the
data layer, store, API, or dashboard changes — they only speak `FinancialFacts` (in) and
`Valuation` (out). See `UPGRADE_ENGINE.md` (the how-to) and `UPGRADE_ENGINE_REVIEW.md` (lessons
from the real v7.3→v8.2 upgrade: signature ripple, new-data plumbing, output sanity guards).

---

## 6. Tests

```powershell
python capture.py        # one-time: fetch real fixtures (or commit them)
python run_tests.py      # 20 modules: FMP/SEC/engine + momentum, consensus blend,
                         # margin trend, Stooq, revenue surprise, assumption flags,
                         # + risk engine + no-regression isolation
```
The fixtures freeze real numbers for AVGO/ABBV/ORCL/NVO/MSFT so a data regression
fails the suite instead of shipping (e.g. AVGO net income must stay ≈ $25B). New
modules: `test_margin`, `test_stooq`, `test_consensus`, `test_finnhub`, `test_fmp_rev`,
`test_followups`, **`test_risk`** (risk-engine invariants: weights sum to 1, RC sums
to σp, signed %RC sums to 100%, a diversifier gets negative RC, DR≥1, score 0–100),
**`test_no_regression`** (the Allocation upgrade is additive: the existing endpoints
keep their JSON contract and `/api/risk` never modifies `portfolio.json`).

---

## 7. Honest limitations
- **Non-GAAP EPS** isn't in SEC XBRL. For amortization-heavy names the app uses the
  analyst consensus (Yahoo) when plausible; otherwise SEC GAAP (which can understate).
- **Priced-for-perfection names** (e.g. ARM) get no point fair value by design —
  the framework defers to the Reverse DCF (shows the growth the market implies).
- **Sector** for never-seen tickers needs the FMP key; otherwise "Unknown".
- **EPS surprise** is EPS-only (~4 Q, street/adjusted basis), cross-checked across up to
  4 free sources. **Revenue surprise** is immediate when FMP exposes revenue actual+estimate,
  else built forward (empty at first, ~1 yr to fill). Fiscal-Q4 (annual-only filings) may
  not grade since there's no standalone 90-day period.
- **Forward EPS** is a **median blend** of Yahoo + FMP (+ Finnhub if keyed) with the
  min–max dispersion shown; a revenue-capacity ceiling still rejects an implausible value
  (e.g. AVGO's unsplit consensus) in favour of a SEC-derived one.
- **v8.2 inputs with no free source** are skipped + flagged (never guessed): NRR /
  management-tone / news-sentiment (bigdata.com's NLP edge) affect only bounded rubric
  nudges, not the core valuation. **Assumption-by-nature** inputs are made transparent:
  **terminal margin** is now anchored to the company's own SEC operating margin (table
  fallback only for pre-profit, flagged); **ERP** lives in `config` with an as-of date and
  is flagged once stale; **beta→1.0 / tax→21% / growth→8%** fallbacks are each flagged.
  See **`DATA_AUDIT.md`**. **Own-5y P/E percentile** needs Yahoo 5y prices, so it fills
  on localhost and skips on cloud hosts that Yahoo blocks.
- Not investment advice — a calculator that reproduces DEEP v8.2 rules on free data.
