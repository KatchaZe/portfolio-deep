# Portfolio DEEP v7.3 — Stock Screening App (v2)

A local web app that values your stocks with the **DEEP Framework v7.3** on
reliable, cross-checked free data, and manages a portfolio, watchlist, and
allocation view.

- **Data:** SEC EDGAR (primary financials, all US + 20-F filers) · FMP profile
  (sector/beta/price) · Yahoo (forward EPS, momentum, FX). All free.
- **Engine:** DEEP v7.3 (WACC, ROIC, Justified PEG, Future Value Projection,
  Terminal-Anchored Reverse DCF, weighted composite) — isolated & version-swappable.
- **Trust:** every value carries provenance + a confidence tier; a regression test
  suite locks correct numbers (the v1 extraction bugs can't come back).

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

**Allocation** — two cost-basis doughnut pies (by holding, by sector). **What-if**:
enter up to 5 (ticker, buy $) → **Calculate** to see the allocation before vs after.

---

## 3b. Earnings track record (beat / meet / miss)

The **Earnings** column shows how each company has done vs consensus, as a row of
coloured circles (oldest→newest, hover for the numbers). Threshold: surprise > +2%
🟢 beat · −2…+2% 🟡 meet · < −2% 🔴 miss.

- **EPS** — comes straight from Yahoo (last ~4 quarters), available immediately.
  A consistent beat/miss record also nudges the **confidence** score (bounded ±10;
  it never touches the DEEP valuation math).
- **Rev** — Yahoo only gives the *current-quarter* revenue estimate (no historical
  ones for free), so the app **builds this history forward**: each refresh it snapshots
  the estimate, and once the SEC actual for that quarter lands it grades beat/miss.
  → starts empty, fills one circle per reported quarter, ~4 quarters (≈1 year) to fill.
  To accumulate it, **Run Fundamental Refresh at least once per quarter, ideally before
  earnings** so the estimate is captured before it rolls over. With Google Drive
  persistence enabled (`GOOGLE_DRIVE_OAUTH_SETUP.md`) the snapshots are mirrored to your
  Drive, so revenue history now survives redeploys and accumulates on the deployed app
  too — not just when running locally.

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

## 5. Upgrade the framework (e.g. v7.4) — only touches the engine

The engine is isolated behind a contract (`domain/engine/contract.py`). To switch:
1. Add `domain/engine/deep_v74.py` with `class DeepV74Engine(DeepEngine)`.
2. `register(DeepV74Engine())` in `domain/engine/__init__.py`.
3. Set `DEEP_VERSION = "7.4"` in `config.py`.

Nothing in the data layer, store, API, or dashboard changes — they only speak
`FinancialFacts` (in) and `Valuation` (out).

---

## 6. Tests

```powershell
python capture.py        # one-time: fetch real fixtures (or commit them)
python run_tests.py      # FMP parser + SEC extraction + DEEP engine + earnings/rev track
```
The fixtures freeze real numbers for AVGO/ABBV/ORCL/NVO/MSFT so a data regression
fails the suite instead of shipping (e.g. AVGO net income must stay ≈ $25B).

---

## 7. Honest limitations
- **Non-GAAP EPS** isn't in SEC XBRL. For amortization-heavy names the app uses the
  analyst consensus (Yahoo) when plausible; otherwise SEC GAAP (which can understate).
- **Priced-for-perfection names** (e.g. ARM) get no point fair value by design —
  the framework defers to the Reverse DCF (shows the growth the market implies).
- **Sector** for never-seen tickers needs the FMP key; otherwise "Unknown".
- **EPS surprise** is EPS-only (~4 Q, street/adjusted basis). **Revenue surprise** is
  built forward (empty at first, ~1 yr to fill) and only accumulates when run locally —
  free data has no historical revenue estimates. Fiscal-Q4 (annual-only filings) may not
  grade since there's no standalone 90-day period.
- Not investment advice — a calculator that reproduces DEEP v7.3 rules on free data.
