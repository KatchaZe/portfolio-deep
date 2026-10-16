"""Every FMP-spending path keeps the same reserve below the daily cap, because FMP
also counts calls our counter never sees (a request that failed after reaching FMP).
Before, only the Risk Desk kept it; fundamentals and the watchlist spent the cap."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from pipeline import batch


def test_one_rule_for_the_usable_budget():
    assert config.QUOTA_RESERVE == 1
    assert batch.usable_quota(0, 250) == 249
    assert batch.usable_quota(249, 250) == 0
    assert batch.usable_quota(300, 250) == 0


def test_fundamentals_partition_keeps_the_reserve():
    # 5 calls left before the cap, a ticker costs 5: the reserve leaves room for none
    todo, errors = batch.partition_by_quota(["A", "B"], 5, 245, 250)
    assert todo == [] and errors == ["A (quota)", "B (quota)"], (todo, errors)
    todo, _ = batch.partition_by_quota(["A", "B"], 5, 244, 250)
    assert todo == ["A"], todo


def test_quota_badge_reports_the_reserve():
    import app
    q = app._quota({"holdings": {}, "fmp_usage": {}})
    assert q["reserved"] == 1 and q["headroom_tickers"] == 249, q


if __name__ == "__main__":
    from tests._harness import run_suite
    run_suite(globals(), "ALL test_quota_reserve OK")
