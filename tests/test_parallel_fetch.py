"""
test_parallel_fetch — P1: quota partition + ThreadPool fetch integrity.
Offline: analyze / price ladders / treasury are monkeypatched — no network.
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import batch, fundamentals, daily


def test_partition():
    todo, errs = batch.partition_by_quota(["A", "B", "C", "D"], 5, 240, 250)
    assert todo == ["A", "B"] and errs == ["C (quota)", "D (quota)"]
    todo, errs = batch.partition_by_quota(["A", "B"], 0, 999, 250)   # no key -> no cost
    assert todo == ["A", "B"] and errs == []
    print("partition OK")


def test_parallel_fundamentals():
    orig_an, orig_ty = fundamentals.analyze, fundamentals.yahoo.fetch_treasury_10y
    seen = []
    lock = threading.Lock()

    class FF:
        def __init__(self, t):
            self.t = t
            self.revenue_annuals = []
            self.sector = "X"

        def set(self, *a, **k):
            pass

    def fake_analyze(t, rf, fmp_key="", rf_live=True, roc_table=None, quota_left=None):
        with lock:
            seen.append(t)
        if t == "BAD":
            raise RuntimeError("boom")
        return FF(t), object(), 3

    fundamentals.analyze = fake_analyze
    fundamentals.yahoo.fetch_treasury_10y = lambda **k: (0.043, True)
    fundamentals.damodaran.fetch_roc_table = lambda **k: {}   # no network in unit tests
    try:
        fetched, errors, calls, rf_pct, rf_live = fundamentals.fetch_fundamentals(
            ["AAA", "BAD", "CCC"], fmp_key="k", quota_used=0, quota_cap=250)
        assert sorted(seen) == ["AAA", "BAD", "CCC"]
        assert set(fetched) == {"AAA", "CCC"} and calls == 6
        assert any(e.startswith("BAD:") for e in errors)
        # quota partition: budget 7 with cost 5 -> only the first ticker runs
        seen.clear()
        fetched, errors, calls, *_ = fundamentals.fetch_fundamentals(
            ["AAA", "CCC"], fmp_key="k", quota_used=243, quota_cap=250)
        assert set(fetched) == {"AAA"} and "CCC (quota)" in errors
        print("parallel fundamentals OK")
    finally:
        fundamentals.analyze, fundamentals.yahoo.fetch_treasury_10y = orig_an, orig_ty


def test_parallel_daily_shape():
    orig_gp, orig_gpl = daily.prices.fetch_daily_series, daily.prices.fetch_daily_adjusted
    closes = [100 + i * 0.1 for i in range(300)]
    dates = [f"2025-{(i % 12) + 1:02d}-01" for i in range(300)]
    daily.prices.fetch_daily_series = lambda t, **k: {"closes": closes[-90:], "volumes": [1e6] * 90,
                                         "dates": dates[-90:]}
    daily.prices.fetch_daily_adjusted = lambda t, key="", **k: {"closes": closes, "volumes": [1e6] * 300,
                                                      "dates": dates, "dividend_adjusted": True,
                                                      "source": "test"}
    try:
        out = daily.fetch_daily(["AAA", "BBB"], fmp_key="")
        for t in ("AAA", "BBB"):
            assert t in out and "v2" in out[t], out.keys()
        assert "^MARKET" in out
        print("parallel daily OK")
    finally:
        daily.prices.fetch_daily_series, daily.prices.fetch_daily_adjusted = orig_gp, orig_gpl


if __name__ == "__main__":
    test_partition()
    test_parallel_fundamentals()
    test_parallel_daily_shape()
    print("\nALL PARALLEL-FETCH TESTS PASSED ✅")
