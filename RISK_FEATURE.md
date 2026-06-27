# Risk Desk — developer reference

What the Allocation-tab risk upgrade added, how it works, and how to extend it.
User-facing overview is in `README.md` §2b; the design rationale and isolation audit
are in `ALLOCATION_RISK_UPGRADE_PLAN.md` (esp. §10).

---

## 1. File map (what was added / touched)

| File | Type | Role |
|---|---|---|
| `domain/engine/risk.py` | **new** | Pure-Python risk math (no I/O, no numpy). The whole 10-step workflow lives here as small testable functions. |
| `pipeline/risk_prices.py` | **new** | The only networked part: fetch dividend-adjusted daily history (FMP→Stooq→proxy), build returns, cache. |
| `app.py` | edited | Added `GET /api/risk` + `_build_risk()` + `_sleeve()`/`_proxy_vol()` helpers. Nothing existing changed. |
| `index.html` | edited | Added the Risk Desk UI **inside `#p3` only** + new JS (`loadRisk`, `renderRisk`, chart builders). The only shared edit is the `n===3` branch of `showTab`. |
| `data/risk_cache.json` | **new (runtime)** | Separate cache for return series — never `portfolio.json`. |
| `tests/test_risk.py` | **new** | Risk-math invariants (offline). |
| `tests/test_no_regression.py` | **new** | Endpoint contracts + `portfolio.json` isolation. |
| `run_tests.py` | edited | Registered the two new test modules (now 19). |

No existing module's behavior or return shape was modified, so My Portfolio and
Watchlist are byte-for-byte unchanged.

---

## 2. Data flow

```
GET /api/risk?risk_tolerance_pct=20&horizon_years=1&enrich=auto
  app.api_risk()                      # sync def — FastAPI threadpool
   ├─ st.load()                       # READ-ONLY (holdings + facts + momentum)
   ├─ _build_risk(s, ...)             # runs OUTSIDE st.LOCK
   │    ├─ build positions (mv = shares × (momentum.price or facts.price))
   │    ├─ risk_prices.fetch_returns()        # NETWORK: FMP adj → Stooq → proxy
   │    │     └─ caches in data/risk_cache.json (key = date + holdings hash)
   │    └─ risk.*()                            # all math (pure)
   └─ if FMP calls > 0:               # commit ONLY the quota counter, safely
        with st.LOCK: reload → add_fmp_calls → save     # == /api/allocation/whatif
```

`_build_risk` is read-only on the main store; the single permitted `portfolio.json`
write is the FMP-usage counter, committed with the proven reload-then-save-under-lock
pattern so a concurrent holding edit cannot be clobbered.

---

## 3. The math (`domain/engine/risk.py`)

Pure functions, grouped by workflow phase. Key formulas:

- **Weights** `wᵢ = mvᵢ / Σmv` · **HHI** `Σwᵢ²` · **Effective N** `1/Σwᵢ²`
- **Covariance** from daily returns (annualized ×252); **realized** when every name has
  ≥60 aligned points, else **`proxy_cov`** from asset-class vols + assumed correlation.
- **Portfolio vol** `σp = √(wᵀΣw)` · **Diversification Ratio** `DR = (Σwᵢσᵢ)/σp`
- **Risk contribution** `MCRᵢ = (Σⱼwⱼ·Σᵢⱼ)/σp`, `RCᵢ = wᵢ·MCRᵢ`, `Σ RCᵢ = σp`;
  **Signed %RC** `= RCᵢ/σp` (negative ⇒ diversifier); **Absolute share** `qᵢ = |RCᵢ|/Σ|RC|`;
  **ENB_abs** `= 1/Σqᵢ²`.
- **Crisis regime** (`crisis_cov`): keep each vol, floor equity↔equity correlations to
  `CRISIS_EQUITY_CORR` (0.90) — diversification fades in a crash.
- **Diversification Score** (0–100, `[JUDG]`): `0.35·TrueDiv + 0.30·RiskBalance +
  0.20·GapCoverage + 0.15·Concentration` (sub-scores use `clamp((x−1)/range,0,1)·100`).
- **Stress** (`stress_test`): per-name loss ≈ `β × market_shock` with sector multipliers
  + a high-beta extra; portfolio loss `= Σ wᵢ·lossᵢ`. `HISTORICAL` + `SCENARIOS` tables.
- **VaR/CVaR** parametric-normal over the horizon. **Reverse stress**: market move `m`
  solving `Σwβ·m = −tolerance`.
- **Position sizing / rebalance**: bind each name to the tightest of single-name cap /
  sector cap / risk-share budget; `rebalance()` trims over-cap names, redistributes to
  under-weight ones, and the caller re-validates the proposed weights (Step 7).

**Why pure Python (no numpy):** the app ships without numpy/pandas; a portfolio is tiny,
so loops are clear, fast, beginner-readable, and add zero deploy risk.

---

## 4. Epistemic tags (returned in `meta`)

| Tag | Meaning |
|---|---|
| `[STORED]` | straight from the data model (beta, sector, shares) |
| `[CALC]` | computed (weights, realized vol/corr, HHI, RC) |
| `[JUDG-PROXY]` | asset-class proxy used (thin history) — see `meta.cov_mode = "proxy"` |
| `[JUDG-SCENARIO]` | stress numbers — illustrative, not forecasts |

`meta.sources` shows the price source per ticker (`fmp`/`stooq`/`proxy`);
`meta.quota_degraded` is `true` when FMP was wanted but the cap forced a free source.

---

## 5. Quota & isolation guarantees

- **FMP cap:** `risk_prices.fetch_returns` pre-checks `quota_used + planned < QUOTA_CAP`
  before every FMP call and **degrades to Stooq** instead of erroring. Each ticker costs
  FMP at most once per day (cache). `enrich=free` spends **zero** quota.
- **No clobber:** risk data → `risk_cache.json` (separate file). `/api/risk` never writes
  holdings/facts/momentum. Locked by `tests/test_no_regression.py`.
- **No event-loop freeze:** `api_risk` is a sync `def`; the network runs outside `st.LOCK`.

---

## 6. How to extend

- **Add a stress scenario:** append to `SCENARIOS` (or `HISTORICAL`) in `risk.py`
  (`{key,label,mkt, sector_mult?, highbeta_extra?}`). The UI bar picks it up automatically.
- **Add an asset class / ballast** (bond, gold): set `asset_class[t]` in `_build_risk`
  (instead of hard-coded `"equity"`), add a `PROXY_VOL` entry; `gap_coverage` and the
  crisis-correlation logic already handle non-equity.
- **ETF look-through (X-ray/overlap):** the hooks exist conceptually; needs an ETF-holdings
  source. Until then single names pass through and overlap is N/A (documented).
- **Options/futures:** not in the data model — add a holding type with notional/delta first.
- **Add a UI panel:** add a `rsec(...)` block in `renderRisk()` and (if charted) a
  `rcChart(...)` call in `drawRiskCharts()`; keep everything inside `#riskBody`.

---

## 7. Tests

```powershell
python -m tests.test_risk            # pure math, offline
python -m tests.test_no_regression   # endpoint contracts + portfolio.json isolation (offline, monkeypatched)
python run_tests.py                  # full suite (19 modules)
```

`test_risk` locks the invariants (weights→1, RC→σp, signed %RC→100%, diversifier<0,
DR≥1, score∈[0,100]). `test_no_regression` proves the feature is additive and isolated.
