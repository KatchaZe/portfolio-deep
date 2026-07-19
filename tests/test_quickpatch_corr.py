"""test_quickpatch_corr — 2026-07-19 quick patches for the 0.60-everywhere bug:
  A) prices.fetch_returns cache never persists/serves failed (n=0) fetches
  B) risk.hybrid_cov: realized pairs keep real corr; only thin names get proxy
Pure/offline (monkeypatched fetcher, tmp cache path)."""
import os
import sys
import json
import math
import random
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.engine import risk


def test_hybrid_cov_mixed():
    random.seed(7)
    a = [random.gauss(0, 0.02) for _ in range(120)]
    b = [x + random.gauss(0, 0.005) for x in a]          # highly correlated with a
    rets = {"AAA": a, "BBB": b, "THIN": [0.01, -0.01]}   # THIN: 2 points only
    tks = ["AAA", "BBB", "THIN"]
    cov, realized = risk.hybrid_cov(rets, tks, [0.3, 0.3, 0.5],
                                    {t: "equity" for t in tks})
    assert realized == ["AAA", "BBB"]
    corr = risk.corr_from_cov(cov)
    assert corr[0][1] > 0.9, corr[0][1]          # realized pair keeps REAL corr
    assert abs(corr[0][2] - 0.6) < 1e-9          # proxy pair = assumed 0.6
    assert abs(math.sqrt(cov[2][2]) - 0.5) < 1e-9  # THIN keeps proxy vol
    print("hybrid_cov mixed OK")


def test_hybrid_cov_all_proxy():
    cov, realized = risk.hybrid_cov({"AAA": [], "GLD": []}, ["AAA", "GLD"],
                                    [0.3, 0.15], {"AAA": "equity", "GLD": "gold"})
    assert realized == []
    corr = risk.corr_from_cov(cov)
    assert abs(corr[0][1] - 0.2) < 1e-9          # cross-class proxy
    print("hybrid_cov all-proxy OK")


def test_cache_no_poison():
    from pipeline import prices as rp
    with tempfile.TemporaryDirectory() as td:
        old_path, old_fetch = rp.RISK_CACHE_PATH, rp._fetch_one
        rp.RISK_CACHE_PATH = os.path.join(td, "risk_cache.json")
        try:
            calls = {"n": 0}
            good_series = {"closes": [100.0 + i for i in range(80)],
                           "dates": ["2026-01-%02d" % (i + 1) for i in range(80)]}

            def fake_fetch(t, key, may, days):
                calls["n"] += 1
                if t == "BAD" and calls["n"] <= 2:      # BAD fails on run 1 only
                    return None, "proxy", False
                return dict(good_series), "stooq", False

            rp._fetch_one = fake_fetch
            # run 1: GOOD ok, BAD fails -> BAD must NOT be cached
            d1, _c1, m1 = rp.fetch_returns(["GOOD", "BAD"])
            assert d1["GOOD"]["n"] > 0 and d1["BAD"]["n"] == 0
            saved = json.load(open(rp.RISK_CACHE_PATH))
            assert "BAD" not in saved["data"], "failed fetch must not be cached"
            assert "GOOD" in saved["data"]
            # run 2 (same day): GOOD from cache, BAD retried -> now succeeds
            d2, _c2, m2 = rp.fetch_returns(["GOOD", "BAD"])
            assert d2["BAD"]["n"] > 0, "BAD must be retried, not served from cache"
            assert m2["cache"] == "partial", m2
            # run 3: full good hit -> no fetch at all
            n3 = calls["n"]
            d3, _c3, m3 = rp.fetch_returns(["GOOD", "BAD"])
            assert calls["n"] == n3 and m3["cache"] == "hit", m3
            assert d3["BAD"]["n"] > 0
        finally:
            rp._fetch_one, rp.RISK_CACHE_PATH = old_fetch, old_path
    print("cache no-poison OK")


if __name__ == "__main__":
    test_hybrid_cov_mixed()
    test_hybrid_cov_all_proxy()
    test_cache_no_poison()
    print("ALL test_quickpatch_corr OK")
