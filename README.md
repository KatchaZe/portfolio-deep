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

See `DESIGN.md` for architecture and `DEEP_v7.3_AUDIT.md` for framework compliance.

---

## 1. Run locally

Python 3.9+.

```powershell
cd portfolio-app-v2
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
"RevDCF implies X%" for priced-for-perfection names), **P/L $ and %**, confidence dot,
and a one-line verdict. **Run Fundamental Refresh** (SEC+FMP+Yahoo) / **Run Daily**
(Yahoo price+momentum, free). Remove with the red ✕.

**Watchlist** — type a ticker → **Run watchlist** to analyse on demand (not stored;
names are remembered). Per row: **+ Portfolio** (prompts for shares + avg cost, then
moves it to My Portfolio) and ✕ remove.

**Allocation** — two cost-basis doughnut pies (by holding, by sector). **What-if**:
enter up to 5 (ticker, buy $) → **Calculate** to see the allocation before vs after.

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
1. Put `portfolio-app-v2/` in a GitHub repo (commit `tests/fixtures/` so tests run).
2. render.com → **New → Web Service** → connect the repo (uses `render.yaml`).
3. In the dashboard set env var **FMP_API_KEY** (optional).
4. Deploy. Build: `pip install -r requirements.txt`; Start: `uvicorn app:app --host 0.0.0.0 --port $PORT`.

> Free tier sleeps after ~15 min idle (first hit wakes it) and uses an ephemeral
> disk — `data/portfolio.json` resets on redeploy. For persistent holdings add a
> Render Disk mounted at `./data` (paid), or run locally.

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
python run_tests.py      # FMP parser + SEC extraction + DEEP engine contract
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
- Not investment advice — a calculator that reproduces DEEP v7.3 rules on free data.
