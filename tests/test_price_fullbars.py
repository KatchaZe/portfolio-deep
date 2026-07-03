"""
test_price_fullbars — H3 fix for get_prices_long's cache policy:
  * a same-day cache SHORT-CIRCUITS only when it has >= full_bars
  * a PARTIAL same-day cache no longer hides a longer series (free tiers retry),
    but FMP quota is NOT re-spent while such a cache exists
  * when every live source fails, the partial same-day cache is still served
Offline/synthetic — sources + pricecache are monkeypatched, no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import refresh


def _series(n, start="2020-01-01"):
    import datetime as dt
    d0 = dt.date.fromisoformat(start)
    return {"closes": [100.0 + i * 0.1 for i in range(n)],
            "volumes": [1000.0] * n,
            "dates": [(d0 + dt.timedelta(days=i)).isoformat() for i in range(n)]}


class _Patch:
    """Monkeypatch refresh's collaborators; restore on exit."""
    def __init__(self, fresh=None, yahoo_bars=0, stooq_bars=0, fmp_bars=0):
        self.fresh = fresh
        self.yahoo_bars, self.stooq_bars, self.fmp_bars = yahoo_bars, stooq_bars, fmp_bars
        self.fmp_called = False

    def __enter__(self):
        self._orig = (refresh.pricecache.read_fresh, refresh.pricecache.read_any,
                      refresh.pricecache.write, refresh.yahoo.fetch_chart,
                      refresh.stooq.fetch_chart, refresh.fmp.fetch_history,
                      refresh.fmp.parse_history)
        refresh.pricecache.read_fresh = lambda t, **k: self.fresh
        refresh.pricecache.read_any = lambda t, **k: self.fresh
        refresh.pricecache.write = lambda t, p, **k: True

        def yahoo_chart(t, **k):
            if not self.yahoo_bars:
                raise RuntimeError("yahoo down")
            d = _series(self.yahoo_bars)
            d["adj_closes"] = d["closes"]
            return d
        refresh.yahoo.fetch_chart = yahoo_chart

        def stooq_chart(t, **k):
            if not self.stooq_bars:
                raise RuntimeError("stooq down")
            return _series(self.stooq_bars)
        refresh.stooq.fetch_chart = stooq_chart

        def fmp_hist(t, key, **k):
            self.fmp_called = True
            return {"historical": []} if not self.fmp_bars else {
                "historical": [{"date": d, "adjClose": c, "volume": v} for d, c, v in
                               zip(_series(self.fmp_bars)["dates"],
                                   _series(self.fmp_bars)["closes"],
                                   _series(self.fmp_bars)["volumes"])]}
        refresh.fmp.fetch_history = fmp_hist
        return self

    def __exit__(self, *a):
        (refresh.pricecache.read_fresh, refresh.pricecache.read_any,
         refresh.pricecache.write, refresh.yahoo.fetch_chart,
         refresh.stooq.fetch_chart, refresh.fmp.fetch_history,
         refresh.fmp.parse_history) = self._orig


def test_full_cache_short_circuits():
    with _Patch(fresh=_series(300), yahoo_bars=1000) as p:
        out = refresh.get_prices_long("XX", "key", full_bars=250)
        assert out.get("from_cache") is True and len(out["closes"]) == 300
        assert p.fmp_called is False
    print("full-cache-short-circuit OK")


def test_partial_cache_no_longer_blocks_longer_fetch():
    # cache 300 bars, request full_bars=780 -> free Yahoo (1000 bars) must win
    with _Patch(fresh=_series(300), yahoo_bars=1000) as p:
        out = refresh.get_prices_long("XX", "key", rng="5y", full_bars=780)
        assert not out.get("from_cache") and len(out["closes"]) == 1000
        assert p.fmp_called is False, "FMP must NOT be re-spent while a same-day partial cache exists"
    print("partial-cache-retries-free-tiers OK")


def test_partial_cache_served_when_live_fails():
    with _Patch(fresh=_series(300), yahoo_bars=0, stooq_bars=0) as p:
        out = refresh.get_prices_long("XX", "key", rng="5y", full_bars=780)
        assert out.get("from_cache") is True and len(out["closes"]) == 300
        assert p.fmp_called is False
    print("partial-cache-fallback OK")


def test_cache_kept_when_still_longest():
    # live sources answer but SHORTER than today's cache -> keep the cache
    with _Patch(fresh=_series(500), yahoo_bars=400) as p:
        out = refresh.get_prices_long("XX", "key", rng="5y", full_bars=780)
        assert out.get("from_cache") is True and len(out["closes"]) == 500
    print("cache-kept-when-longest OK")


def test_no_cache_spends_fmp():
    # first call of the day (no cache), Yahoo short -> FMP is allowed
    with _Patch(fresh=None, yahoo_bars=100, fmp_bars=1000) as p:
        out = refresh.get_prices_long("XX", "key", rng="5y", full_bars=780)
        assert p.fmp_called is True
        assert len(out["closes"]) == 1000 and out["source"] == "fmp"
    print("no-cache-spends-fmp OK")


if __name__ == "__main__":
    test_full_cache_short_circuits()
    test_partial_cache_no_longer_blocks_longer_fetch()
    test_partial_cache_served_when_live_fails()
    test_cache_kept_when_still_longest()
    test_no_cache_spends_fmp()
    print("\nALL PRICE-FULLBARS TESTS PASSED ✅")
