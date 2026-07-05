# Code Review 2026-07-04 — full-stack pass (build 2026-07-04a)

Senior full-stack review of the whole repo (app.py, store.py, config.py, sources/,
pipeline/, domain/, index.html, tests). Scope agreed with owner: **safe fixes only**
— no behavior changes, all 34 test suites must stay green. Verified by rebuilding a
clean copy from git HEAD, replaying every edit, then `python run_tests.py` (ALL
PASSED) + `node --check` on the extracted dashboard JS (OK).

## Verdict

The codebase is in very good shape. It has already absorbed multiple hardening
rounds (P1–P5 perf, R1–R4 data quality, H1–H5 reliability, C1–C2 concurrency) and
the architecture discipline is real: sources fetch-only, domain pure, engine behind
a contract, store isolated, quota partitioned up front for the parallel fetch.
No correctness bugs found in this pass.

## Changes applied (safe)

| File | Change | Why |
|---|---|---|
| `app.py` | docstring + `FastAPI(title=…)` now use `config.DEEP_VERSION` | stale "v7.3" label; one source of truth |
| `index.html` | `<title>` → "Portfolio DEEP Dashboard" | stale "v7.3"; header already shows the live version from the API |
| `sources/sec_edgar.py` | `extract()` uses the existing `CASH_TAGS` / `EQUITY_TAGS` constants in the two `fresh()` calls | removes duplicated tag lists (identical behavior) |
| `config.py` + `index.html` | BUILD / DASH_BUILD → **2026-07-04a** | UI changed (How to / Ref rebuild) — test_frontend enforces the pair |
| `index.html` | How to tab rebuilt with 4 annotated SVG mockups (card / banner / allocation top / risk desk) + numbered legend tables; Ref tab expanded (full S1–S42 map, verdict thresholds, abbreviations & symbols) | docs task (see below) |
| docs | `architecture.mermaid`, `DESIGN.md`, `README.md`, `BEGINNER_GUIDE.md` brought in line with the current build (5 tabs, cards + command strip, advice/earn_status, market_valuation/screen, parallel fetch, push guard, ~5 FMP calls/ticker, 34 suites) | were describing the pre-card, 3-tab, 1-call-per-ticker era |

## Reviewed and deliberately NOT changed

- **`refresh._reconcile_earnings`** looks superseded by `consensus.reconcile_earnings`
  but is still imported and exercised by `tests/test_fmp_earnings.py` — kept.
- **`grade()` duplicated across 6 adapters** (yahoo/fmp/finnhub/alphavantage/rev_track/
  surprise_backfill). A shared helper would be tidier, but each copy is 8 lines, pure,
  and pinned by its own tests; consolidation risk > benefit. Documented instead.
- **`showTab(3)` re-runs loadRisk/loadScreen on every visit** — acceptable: risk
  returns are cached per holdings-set per day (`risk_cache.json`), so repeat visits
  cost no quota.
- **`indicators.ACTION_MAP` can downgrade BUY→WAIT on bearish momentum** — slight
  tension with "momentum never overrides value", but it defers entry rather than
  reversing the verdict, and `advice.py` (the layer users read) is strictly ordered.
  Known observation #5 in `Damodaran2026_Philosophies_Summary_and_Code_Audit.md`.
- **Known maintenance duty (unchanged):** `config.ERP` (as-of 2026-01) and
  `config.MARKET_PE` (as-of 2026-06) are manual monthlies; the UI banner now shouts
  when they go stale — update them, don't code around them.

## Follow-ups worth considering (not done — behavior changes)

1. Wire `risk.effective_duration()` (already written) to override `DURATION_PROXY`.
2. Class-specific weight cap for pricing assets (e.g. crypto 5%) in `position_sizing`.
3. `/api/assumptions` + `/api/profile` endpoints so ERP/profile edits don't need a
   redeploy (UI banners currently point at config.py / philosophy_profile.json).
