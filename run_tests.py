"""
Run the full v2 test suite.  python run_tests.py

  tests.test_fmp_parse  — FMP parser on real ABBV schema (offline, synthetic)
  tests.test_extract    — SEC robust extraction + normalize on real fixtures
  tests.test_engine     — DEEP engine contract on real fixtures

test_extract / test_engine need the captured fixtures in tests/fixtures/
(run `python capture.py` once, or commit the fixtures to the repo).
"""
import os
import sys
import subprocess

TESTS = ["tests.test_fmp_parse", "tests.test_extract", "tests.test_engine",
         "tests.test_engine_v82", "tests.test_earnings", "tests.test_fmp_earnings",
         "tests.test_rev", "tests.test_margin", "tests.test_stooq", "tests.test_momentum",
         "tests.test_pricecache",
         "tests.test_consensus", "tests.test_finnhub", "tests.test_fmp_rev",
         "tests.test_followups",
         "tests.test_hardening", "tests.test_app_fixes", "tests.test_gdrive",
         "tests.test_risk", "tests.test_no_regression", "tests.test_costs", "tests.test_downside", "tests.test_pead", "tests.test_screen", "tests.test_market_valuation", "tests.test_assetclass", "tests.test_philosophy", "tests.test_frontend", "tests.test_correlation",
         "tests.test_advice", "tests.test_earn_status", "tests.test_sec_stale_cache",
         "tests.test_price_fullbars", "tests.test_parallel_fetch",
         "tests.test_fmp_freetier", "tests.test_surprise_backfill",
         "tests.test_philosophy2026", "tests.test_skill_parity",
         "tests.test_quickpatch_corr", "tests.test_risk_report",
         "tests.test_cache_sync", "tests.test_prices_ladder",
         "tests.test_damodaran_roc", "tests.test_young_dcf",
         "tests.test_young_panel_render",
         # round-3 review guard: period alignment (a "1-year" growth rate must span
         # one year), missing-pillar inflation, and a screen that shows everything
         # it screened.
         "tests.test_review3", "tests.test_review3d",
         # T5: the 5-year performance strip — date alignment, gaps, staleness,
         # watchlist/holdings parity, and the E_exec cash-durability leg.
         "tests.test_trend",
         # A1-A4: Price gains an FCF-yield leg, D loses two sign bugs, Economics is
         # normalised one-sidedly, incremental ROIC moves to the capital delta.
         "tests.test_pillars_a",
         # B5-B8: growth consistency, beat/miss record, sector-relative ROIC, CAP,
         # the surprise-ordering fix, and the per-pillar adjustment budget.
         "tests.test_pillars_b",
         # --- the safety net (2026-08-09). These are not unit tests: each one checks a
         # property of the SYSTEM, and each was built after a defect that 46 green unit
         # suites had failed to see. See REVIEW_PROCESS.md.
         #
         # dataquality  the DATA, not the code — staleness, series gaps, unconverted
         #              currency, and that the stale-data fallback does not rebuild a
         #              clock mismatch while fixing one. TSM (SEC stops at FY2024) is live.
         "tests.test_dataquality",
         # contracts    every fact field declares a CLOCK and a UNIT; an AST scan rejects
         #              dividing, subtracting or comparing across them, and rejects a
         #              money field that normalize never FX-converts. 1,128 possible
         #              mispairings — past what review by eye can cover.
         "tests.test_contracts",
         # invariants   identities that must hold across TWO code paths (the two "actual
         #              growth" figures agree; spread = ROIC - WACC; watchlist and
         #              holdings rows carry the same fields). Mutation-tests itself.
         "tests.test_invariants",
         # replay       re-scores every stored ticker against a committed baseline and
         #              fails when a REAL company's score moves. Intentional moves are
         #              accepted with --update, and the baseline diff is reviewed with
         #              the change. This is the harness that found most of the defects.
         "tests.replay_snapshot",
         "tests.audit2"]
HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    failed = []
    for t in TESTS:
        print("=" * 60)
        print("RUN", t)
        print("=" * 60)
        r = subprocess.run([sys.executable, "-m", t], cwd=HERE)
        if r.returncode != 0:
            failed.append(t)
    print("\n" + "=" * 60)
    if failed:
        print("FAILED:", ", ".join(failed))
        sys.exit(1)
    print("ALL TEST SUITES PASSED OK")


if __name__ == "__main__":
    main()
