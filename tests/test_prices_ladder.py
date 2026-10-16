"""test_prices_ladder — C1 (2026-07-20a): the risk-returns ladder reuses the
pricecache pool momentum already fetched, tries Yahoo directly for NEW holdings
(not yet in the pool), and falls back to a stale cached series before proxy.
Offline — pricecache / yahoo / fmp / stooq are monkeypatched."""
import os
import sys
import json

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


def test_short_live_answers_fall_through():
    """A Yahoo series too short for 60 returns is not written to the pool, and an
    empty Stooq answer is a miss, not a success."""
    with _Lad() as L:
        yahoo.fetch_chart = lambda t, **k: (L.calls.append("yahoo") or
                                            {**_series(30), "adj_closes": _series(30)["closes"]})
        stooq.fetch_chart = lambda t, **k: L.calls.append("stooq") or {"closes": []}
        pay, src, used = prices._fetch_one("SHORT", "key", True, 400)
        assert (pay, src, used) == (None, "proxy", False)
        assert L.wrote == [], "a short yahoo series must not enter the pool"
        assert L.calls == ["yahoo", "fmp", "stooq"], L.calls
    print("short live answers fall through OK")


class _Desk:
    """fetch_returns with a scripted _fetch_one, a temp risk cache, no Drive."""

    def __init__(self, td, answers):
        self.td, self.answers, self.asked = td, answers, []

    def __enter__(self):
        import time
        import store_sync
        self._store_sync = store_sync
        self._orig = (prices.RISK_CACHE_PATH, prices._fetch_one,
                      prices._last_remote_pull, store_sync.schedule_push_named)
        prices.RISK_CACHE_PATH = os.path.join(self.td, "risk_cache.json")
        prices._last_remote_pull = time.time()          # throttled: no Drive pull
        store_sync.schedule_push_named = lambda *a, **k: None

        def fetch_one(t, key, may, days):
            self.asked.append((t, key, may, days))
            series, source = self.answers[t]
            if source == "fmp" and not may:
                source = "stooq"                        # the ladder skips FMP
            return series, (source if series else "proxy"), source == "fmp"
        prices._fetch_one = fetch_one
        return self

    def __exit__(self, *a):
        (prices.RISK_CACHE_PATH, prices._fetch_one, prices._last_remote_pull,
         self._store_sync.schedule_push_named) = self._orig


def _closes(n, dates=True, ndates=None):
    s = {"closes": [100.0 + i * (1 if i % 2 else -0.5) for i in range(n)]}
    if dates:
        s["dates"] = ["2026-03-%02d" % (i % 28 + 1) for i in range(ndates or n)]
    return s


def test_fetch_returns_quota_records_and_meta():
    """Pins the quota rule (spend only while used+spent+1 < cap), the per-ticker
    record (dates dropped when they do not pair with the returns), and the meta."""
    import tempfile
    from domain import risk
    answers = {"A": (_closes(70), "fmp"), "B": (_closes(65), "fmp"),
               "C": (_closes(64, ndates=10), "fmp"), "D": (_closes(62, dates=False), "stooq"),
               "E": (None, "proxy")}
    with tempfile.TemporaryDirectory() as td, _Desk(td, answers) as desk:
        data, calls, meta = prices.fetch_returns(["A", "B", "", "C", "A", "D", "E"],
                                                 fmp_key="k", quota_used=247,
                                                 quota_cap=250, days=50)
        assert [a[2] for a in desk.asked] == [True, True, False, False, False], desk.asked
        assert all(a[1] == "k" and a[3] == 50 for a in desk.asked)
        assert calls == 2, calls
        assert list(data) == ["A", "B", "C", "D", "E"]
        for t in "ABCD":
            series = answers[t][0]
            rets = risk.daily_returns(series["closes"])
            dates = series.get("dates")
            rdates = list(dates[1:]) if dates and len(dates) - 1 == len(rets) else []
            assert data[t] == {"returns": rets, "dates": rdates,
                               "vol": round(risk.annualized_vol(rets), 4),
                               "source": "fmp" if t in "AB" else "stooq",
                               "as_of": dates[-1] if dates else None,
                               "n": len(rets)}, t
        assert data["C"]["dates"] == [] and data["C"]["as_of"] == "2026-03-10"
        assert data["E"] == {"returns": [], "dates": [], "vol": None,
                             "source": "proxy", "as_of": None, "n": 0}
        assert meta == {"cache": "miss", "key": prices.holdings_key(list("ABCDE")),
                        "quota_degraded": True, "drive_cache_merged": 0}, meta
        saved = json.load(open(prices.RISK_CACHE_PATH))
        assert sorted(saved["data"]) == list("ABCD"), "only GOOD series persist"

    with tempfile.TemporaryDirectory() as td, _Desk(td, answers) as desk:
        _d, calls, meta = prices.fetch_returns(list("AB"), fmp_key="k", prefer_fmp=False)
        assert [a[2] for a in desk.asked] == [False, False] and calls == 0
        assert meta["quota_degraded"] is True

    with tempfile.TemporaryDirectory() as td, _Desk(td, answers) as desk:
        _d, calls, meta = prices.fetch_returns(list("AB"), use_cache=False)
        assert [a[2] for a in desk.asked] == [False, False] and calls == 0
        assert meta == {"cache": "miss", "key": prices.holdings_key(list("AB")),
                        "quota_degraded": False, "drive_cache_merged": 0}, meta
        assert not os.path.exists(prices.RISK_CACHE_PATH), "use_cache=False never saves"
    print("fetch_returns quota + records + meta OK")


if __name__ == "__main__":
    from tests._harness import run_suite
    run_suite(globals(), "ALL test_prices_ladder OK")
