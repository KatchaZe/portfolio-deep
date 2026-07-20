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
         "tests.test_cache_sync", "tests.test_prices_ladder"]
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
