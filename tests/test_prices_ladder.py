"""test_prices_ladder — C1 (2026-07-20a): the risk-returns ladder reuses the
pricecache pool momentum already fetched, tries Yahoo directly for NEW holdings
(not yet in the pool), and falls back to a stale cached series before proxy.
Offline — pricecache / yahoo / fmp / stooq are monkeypatched."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import prices, pricecache
from sources import yahoo, fmp, stooq


def _series(n=100):
    return {"closes": [100.0 + i for i in range(n)],
            "volumes": [1.0] * n,
            "dates": ["2026-01-%02d" % (i % 28 + 1) for i in range(n)]}


class _Lad:
    """Monkeypatch every ladder collaborator; restore on exit."""

    def __init__(self, fresh=None, any_=None, yahoo_ok=False, fmp_ok=False, stooq_ok=False):
        self.fresh, self.any_ = fresh, any_
        self.yahoo_ok, self.fmp_ok, self.stooq_ok = yahoo_ok, fmp_ok, stooq_ok
        self.wrote, self.calls = [], []

    def __enter__(self):
        self._orig = (pricecache.read_fresh, pricecache.read_any, pricecache.write,
                      yahoo.fetch_chart, fmp.fetch_history, fmp.parse_history,
                      stooq.fetch_chart)
        pricecache.read_fresh = lambda t, **k: self.fresh
        pricecache.read_any = lambda t, **k: self.any_
        pricecache.write = lambda t, p, **k: self.wrote.append(t) or True

        def y(t, **k):
            self.calls.append("yahoo")
            if not self.yahoo_ok:
                raise RuntimeError("yahoo blocked")
            d = _series()
            return {**d, "adj_closes": d["closes"]}
        yahoo.fetch_chart = y

        def fh(t, key, **k):
            self.calls.append("fmp")
            return {"historical": []}
        fmp.fetch_history = fh
        fmp.parse_history = (lambda j: _series() if self.fmp_ok
                             else {"closes": [], "volumes": [], "dates": []})

        def sq(t, **k):
            self.calls.append("stooq")
            if not self.stooq_ok:
                raise RuntimeError("stooq down")
            return _series()
        stooq.fetch_chart = sq
        return self

    def __exit__(self, *a):
        (pricecache.read_fresh, pricecache.read_any, pricecache.write,
         yahoo.fetch_chart, fmp.fetch_history, fmp.parse_history,
         stooq.fetch_chart) = self._orig


def test_tier0_pricecache_first():
    with _Lad(fresh=_series(120)) as L:
        pay, src, used = prices._fetch_one("NVDA", "key", True, 400)
        assert src == "yahoo-cache" and used is False
        assert len(pay["closes"]) == 120
        assert L.calls == [], "cache hit must cost ZERO network/quota"
    print("tier0 pricecache OK")


def test_tier1_yahoo_new_holding():
    """A NEW holding (not in pricecache yet) must be fetched from Yahoo directly
    and written back into pricecache for the next Run Daily."""
    with _Lad(fresh=None, yahoo_ok=True) as L:
        pay, src, used = prices._fetch_one("NEWSTK", "key", True, 400)
        assert src == "yahoo" and used is False
        assert L.wrote == ["NEWSTK"], "yahoo series must be cached for momentum"
        assert "fmp" not in L.calls, "yahoo success must not spend FMP quota"
    print("tier1 yahoo new-holding OK")


def test_tier4_stale_cache_beats_proxy():
    with _Lad(fresh=None, any_=_series(90)) as L:
        pay, src, used = prices._fetch_one("OLD", "key", True, 400)
        assert src == "yahoo-cache-stale" and used is False
        assert pay and len(pay["closes"]) == 90
        assert L.calls == ["yahoo", "fmp", "stooq"], "live tiers tried first"
    print("tier4 stale-cache OK")


def test_tier5_proxy_last():
    with _Lad() as L:
        pay, src, used = prices._fetch_one("DEAD", "key", True, 400)
        assert pay is None and src == "proxy" and used is False
    print("tier5 proxy OK")


def test_short_cache_skipped():
    # <61 closes cannot give 60 returns -> must NOT satisfy tier 0/4
    with _Lad(fresh=_series(30), any_=_series(30)) as L:
        pay, src, used = prices._fetch_one("TINY", "", False, 400)
        assert src == "proxy", src
    print("short-cache skipped OK")


if __name__ == "__main__":
    test_tier0_pricecache_first()
    test_tier1_yahoo_new_holding()
    test_tier4_stale_cache_beats_proxy()
    test_tier5_proxy_last()
    test_short_cache_skipped()
    print("ALL test_prices_ladder OK")
