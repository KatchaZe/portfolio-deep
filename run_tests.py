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

TESTS = ["tests.test_fmp_parse", "tests.test_extract", "tests.test_engine", "tests.test_earnings"]
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
    print("ALL TEST SUITES PASSED ✅")


if __name__ == "__main__":
    main()
