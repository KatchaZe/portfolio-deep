"""
fmp_budget: count the FMP requests one ticker's refresh really sends, by step.

Offline. It runs the app's own code for one ticker, the way Fundamental Refresh and the
watchlist do (fundamentals.analyze, the peer list, the momentum ladder), with SEC,
Yahoo and FMP answered from tests/fixtures/MSFT and every other host cut. Each FMP
request is attributed to the sources.fmp function that sent it, so a new FMP call
site shows up as its own step instead of hiding inside another.

Scenarios:
  typical  SEC current, Yahoo up (price, consensus, beat/miss history, long chart)
  worst    SEC stale (statements fallback), Yahoo down (no price, no history),
           FMP /earnings empty (quarterly-estimates backfill)
Each runs cold (empty caches), then warm (the same day's second refresh), then
warm with a new SEC filing listed today.

    python -m tools.fmp_budget          # table
    python -m tools.fmp_budget --json   # the measurement, for tests/test_fmp_budget.py
"""
import contextlib
import functools
import datetime as dt
import json
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                    # noqa: E402
from sources import fmp, sec_edgar, sec_gap, yahoo              # noqa: E402
from pipeline import batch, daily, dataquality, fmp_cache, fundamentals, peers  # noqa: E402

FIX = os.path.join(config.FIXTURE_DIR, "MSFT")
TICKER, CIK = "MSFT", "0000789019"

# sources.fmp function -> the step it is in batch.FMP_STEPS
STEP_OF = {"fetch": "statements fallback", "fetch_profile": "profile",
           "fetch_quote": "quote fallback", "fetch_earnings": "earnings",
           "fetch_estimates": "estimates", "fetch_estimates_quarter": "quarterly estimates",
           "fetch_peers": "peer list", "fetch_history": "price history"}
RUNS = ("cold", "warm", "new filing")


def _fix(name):
    with open(os.path.join(FIX, name + ".json"), encoding="utf-8") as fh:
        return json.load(fh)


def _bars(n=1300):
    end = dt.date.today() - dt.timedelta(days=1)
    dates = [(end - dt.timedelta(days=n - 1 - i)).isoformat() for i in range(n)]
    closes = [100.0 + (i % 17) for i in range(n)]
    return dates, closes


class _R:
    def __init__(self, body):
        self.status_code, self._body, self.text = 200, body, json.dumps(body)[:300]

    def json(self):
        return self._body


class FakeFmp:
    """Answers FMP from the fixtures; records the step of every request it gets."""

    def __init__(self, scenario):
        self.scenario, self.sent = scenario, []
        dates, closes = _bars()
        earnings = [] if scenario == "worst" else [
            {"date": "2026-07-29", "epsActual": 3.1, "epsEstimated": 3.0,
             "revenueActual": 7.6e10, "revenueEstimated": 7.4e10}]
        self.body = {
            ("profile", None): _fix("profile"), ("quote", None): _fix("quote"),
            ("income-statement", "annual"): _fix("income-statement_annual"),
            ("income-statement-ttm", None): _fix("income-statement-ttm"),
            ("balance-sheet-statement", "annual"): _fix("balance-sheet-statement_annual"),
            ("cash-flow-statement", "annual"): _fix("cash-flow-statement_annual"),
            ("key-metrics-ttm", None): _fix("key-metrics-ttm"),
            ("analyst-estimates", "annual"): _fix("analyst-estimates_annual"),
            ("analyst-estimates", "quarter"): [],          # premium on the free tier
            ("earnings", None): earnings,
            ("stock-peers", None): [{"symbol": "AAPL"}, {"symbol": "GOOGL"}, {"symbol": "ORCL"}],
            ("historical-price-eod/full", None): [
                {"date": d, "adjClose": c, "volume": 1e6} for d, c in zip(dates, closes)],
        }

    def get(self, url, params=None, timeout=None):
        ep = url[len(config.FMP_BASE) + 1:]
        self.sent.append(_STEP.get())
        return _R(self.body.get((ep, (params or {}).get("period")), []))


class _Step(threading.local):
    name = None

    def get(self):
        return self.name or "unattributed"


_STEP = _Step()


def _attributed(name, fn):
    def wrapped(*a, **k):
        outer, _STEP.name = _STEP.name, STEP_OF[name]
        try:
            return fn(*a, **k)
        finally:
            _STEP.name = outer
    return wrapped


@contextlib.contextmanager
def _patched(obj, **attrs):
    old = {k: getattr(obj, k) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(obj, k, v)


def _sources(scenario, filed):
    """Every non-FMP input of the refresh, fixed by the scenario."""
    up = scenario == "typical"
    cf = _fix("sec_companyfacts")
    qs = _fix("yahoo_quotesummary") if up else {"_error": "blocked"}
    dates, closes = _bars()

    def chart(t, rng="3mo", interval="1d", **k):
        if not up:
            raise OSError("yahoo blocked")
        return {"closes": closes, "adj_closes": closes, "dates": dates,
                "volumes": [1e6] * len(dates)}

    class Quality:
        """fundamentals' view of pipeline.dataquality, with the stale verdict fixed."""

        def __getattr__(self, name):
            return getattr(dataquality, name)

        @staticmethod
        def assess(ff, *a, **k):
            out = [d for d in dataquality.assess(ff, *a, **k) if d.get("code") != "stale_financials"]
            return out + ([{"code": "stale_financials", "severity": "block",
                            "message": "forced by fmp_budget"}] if scenario == "worst" else [])

    return [
        (fundamentals, dict(resolve_cik=lambda t: (CIK, "Microsoft Corp"), dataquality=Quality())),
        (sec_edgar, dict(fetch_companyfacts=lambda *a, **k: cf)),
        (sec_gap, dict(complete=lambda cik, c, *a, **k: dict(c, _latest_filed=filed))),
        (yahoo, dict(fetch_consensus=lambda t: qs, fetch_chart=chart,
                     fetch_splits=lambda t: [], fetch_fx_to_usd=lambda c: 1.0)),
    ]


def _refresh_one(scenario, filed, budget):
    """What one ticker costs on Fundamental Refresh: analyze, its peer list, momentum."""
    fundamentals.analyze(TICKER, 0.045, "key", True, None, budget)
    peers.peer_list(TICKER, "key", dt.date.today(), budget)
    try:
        daily.composite_momentum(TICKER, "key")
    except Exception:
        pass


def measure():
    """{scenario: {run: {step: calls}}} on the real code paths."""
    out = {}
    for scenario in ("typical", "worst"):
        tmp = tempfile.mkdtemp(prefix="fmp_budget_")
        fake = FakeFmp(scenario)
        old_filed = (dt.date.today() - dt.timedelta(days=40)).isoformat()
        out[scenario] = {}
        with contextlib.ExitStack() as st:
            st.enter_context(_patched(config, CACHE_DIR=tmp))
            st.enter_context(_patched(fmp, _session_cached=fake,
                                      LEDGER=fmp.Ledger(os.path.join(tmp, "ledger.json")),
                                      **{n: _attributed(n, getattr(fmp, n)) for n in STEP_OF
                                         if n != "fetch"},
                                      fetch=_attributed("fetch", functools.partial(fmp.fetch, sleep=0))))
            st.enter_context(_patched(fmp_cache, CACHE=fmp_cache.FmpCache(
                os.path.join(tmp, "fmp_cache.json"))))
            st.enter_context(_patched(fundamentals, refresh_implied_erp=lambda: None))
            for run in RUNS:
                filed = dt.date.today().isoformat() if run == "new filing" else old_filed
                for obj, attrs in _sources(scenario, filed):
                    st.enter_context(_patched(obj, **attrs))
                fake.sent.clear()
                _refresh_one(scenario, filed, batch.Budget(10 ** 6))
                counts = {}
                for step in fake.sent:
                    counts[step] = counts.get(step, 0) + 1
                out[scenario][run] = counts
    return out


def upfront(counts):
    """Calls a run sends from steps partition_by_quota admits up front."""
    budgeted = {s for s, _n, b in batch.FMP_STEPS if b}
    return sum(n for s, n in counts.items() if s not in budgeted)


def main():
    from tests import _harness
    _harness.cut_network()
    import logging
    logging.disable(logging.CRITICAL)
    m = measure()
    if "--json" in sys.argv:
        print(json.dumps(m))
        return
    steps = [s for s, _n, _b in batch.FMP_STEPS] + ["unattributed"]
    cols = [(sc, r) for sc in m for r in RUNS]
    print(f"{'step':22}" + "".join(f"{sc[:4]}/{r:>10}" for sc, r in cols))
    for s in steps:
        print(f"{s:22}" + "".join(f"{m[sc][r].get(s, 0):>15}" for sc, r in cols))
    print(f"{'TOTAL':22}" + "".join(f"{sum(m[sc][r].values()):>15}" for sc, r in cols))
    print(f"{'up-front part':22}" + "".join(f"{upfront(m[sc][r]):>15}" for sc, r in cols))
    print(f"\nbatch.FMP_CALLS_PER_TICKER = {batch.FMP_CALLS_PER_TICKER}")


if __name__ == "__main__":
    main()
