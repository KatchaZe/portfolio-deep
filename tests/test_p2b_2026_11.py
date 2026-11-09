"""
P2 risk desk, 2026-11 (methodology review against Barra / Aladdin / Bridgewater).

P2-1  Fama-French 5 + momentum exposures and the factor / specific risk split.
P2-2  historical episodes replayed on what each holding (or its sector ETF) did.
P2-5  the book's risk in baht beside dollars.
"""
import datetime as dt
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain import risk as R
from domain.risk import factors as F
from pipeline import factors as factors_mod
from pipeline import replay, risk_report
from sources import french
from tests.test_alloc_corr import _dates, _provider, _store


def _factor_days(n, seed=7):
    random.seed(seed)
    out = {}
    for d in _dates(n):
        out[d] = {k: random.gauss(0, 0.01) for k in F.FACTORS}
        out[d]["RF"] = 0.0001
    return out


def test_french_csv_parses_the_daily_table_only():
    text = ("This file was created using the 202509 CRSP database.\n"
            "The 1-month TBill rate data until 202405 ...\n\n"
            ",Mkt-RF,SMB,HML,RMW,CMA,RF\n"
            "20250102,  -0.21,  0.35, -0.10,  0.05, 0.12, 0.017\n"
            "20250103,   1.10, -0.20,  0.00, -0.08, 0.01, 0.017\n\n"
            "Copyright 2025 Eugene F. Fama and Kenneth R. French\n")
    got = french.parse_csv(text)
    assert list(got) == ["2025-01-02", "2025-01-03"], got
    assert abs(got["2025-01-03"]["Mkt-RF"] - 0.011) < 1e-12 and got["2025-01-02"]["RF"] == 0.00017
    print("P2-1 Ken French CSV parse OK")


def test_regression_recovers_the_true_exposures():
    fac = _factor_days(400)
    true = {"MKT": 1.2, "SMB": -0.3, "HML": 0.5, "RMW": 0.0, "CMA": 0.2, "MOM": 0.4}
    random.seed(3)
    dates = sorted(fac)
    rets = [fac[d]["RF"] + sum(true[k] * fac[d][k] for k in F.FACTORS) + random.gauss(0, 0.002)
            for d in dates]
    fit = F.regress(rets, dates, fac)
    for k, v in true.items():
        assert abs(fit["betas"][k] - v) < 0.05, (k, fit["betas"])
    assert fit["r2"] > 0.95
    print("P2-1 OLS recovers betas OK", {k: round(v, 2) for k, v in fit["betas"].items()})


def test_factor_vs_specific_split():
    fac = _factor_days(400)
    dates = sorted(fac)
    random.seed(5)
    factor_name = [fac[d]["RF"] + fac[d]["MKT"] for d in dates]
    idio_name = [fac[d]["RF"] + random.gauss(0, 0.03) for d in dates]
    out = F.decompose({"A": 0.5, "B": 0.5}, {"A": (factor_name, dates), "B": (idio_name, dates)}, fac)
    assert out["status"] == "ok" and abs(out["exposures"]["MKT"] - 0.5) < 0.1, out["exposures"]
    assert out["specific_share_pct"] > out["factor_share_pct"], out
    one = F.decompose({"A": 1.0}, {"A": (factor_name, dates)}, fac)
    assert one["factor_share_pct"] > 95, one
    short = F.decompose({"A": 1.0}, {"A": (factor_name[:50], dates[:50])}, fac)
    assert short["status"] == "insufficient" and short["missing"] == ["A"], short
    print("P2-1 factor/specific split OK")


def test_factor_cache_refreshes_monthly_and_survives_a_failed_fetch():
    with tempfile.TemporaryDirectory() as d:
        factors_mod.PATH = os.path.join(d, "ff.json")
        calls = []

        def ok():
            calls.append(1)
            return {"2025-01-02": {"MKT": 0.01}}
        today = dt.date(2026, 11, 6)
        assert factors_mod.factors(ok, today) and len(calls) == 1
        assert factors_mod.factors(ok, today + dt.timedelta(days=10)) and len(calls) == 1
        assert factors_mod.factors(None, today + dt.timedelta(days=90))          # cache only

        def boom():
            raise RuntimeError("down")
        assert factors_mod.factors(boom, today + dt.timedelta(days=40)) == {"2025-01-02": {"MKT": 0.01}}
    print("P2-1 factor cache OK")


def test_replay_uses_own_then_proxy_then_beta():
    got = {"covid_2020": {"OLD": -0.40, "XLK": -0.30}}
    rows = R.historical_replay({"OLD": 0.5, "NEW": 0.3, "ODD": 0.2}, got,
                               betas={"OLD": 1.0, "NEW": 1.5, "ODD": 1.0},
                               sectors={"OLD": "Healthcare", "NEW": "Technology", "ODD": "Unknown"},
                               scenarios=[s for s in R.HISTORICAL if s["key"] == "covid_2020"])
    r = rows[0]
    assert r["basis"] == {"OLD": "own", "NEW": "proxy XLK", "ODD": "beta"}, r["basis"]
    expected = 0.5 * -0.40 + 0.3 * -0.30 + 0.2 * (1.0 * -0.34)
    assert abs(r["loss_pct"] - round(expected * 100, 1)) < 1e-9, (r["loss_pct"], expected)
    assert r["method"] == "replay + proxy" and r["window"] == "2020-02-19 → 2020-03-23"
    print("P2-2 replay own/proxy/beta OK:", r["loss_pct"])


def test_replay_cache_keeps_answers_not_failures():
    with tempfile.TemporaryDirectory() as d:
        replay.PATH = os.path.join(d, "rw.json")
        seen = []

        def fetch(sym, start, end):
            seen.append((sym, start))
            if sym == "DOWN":
                raise RuntimeError("timeout")
            return None if sym == "IPO" else -0.2
        scn = [R.HISTORICAL[0]]
        out = replay.window_returns(["A", "IPO", "DOWN"], fetch=fetch, scenarios=scn)
        assert out == {"gfc_2008": {"A": -0.2}}, out
        seen.clear()
        replay.window_returns(["A", "IPO", "DOWN"], fetch=fetch, scenarios=scn)
        assert seen == [("DOWN", "2007-10-09")], seen          # A and IPO came from the cache
        assert replay.window_returns(["A"], scenarios=scn) == {"gfc_2008": {"A": -0.2}}
    print("P2-2 replay cache OK")


def test_a_window_before_the_listing_is_not_a_failure():
    import requests
    from sources import yahoo

    class Resp:
        def __init__(self, code):
            self.status_code = code

        def raise_for_status(self):
            raise requests.HTTPError(f"{self.status_code}", response=self)

    class Req:
        def __init__(self, code):
            self.code = code

        def get(self, *a, **k):
            return Resp(self.code)
    assert yahoo.fetch_window_return("ABBV", "2007-10-09", "2009-03-09", requests_mod=Req(400),
                                     retries=1) is None
    try:
        yahoo.fetch_window_return("ABBV", "2007-10-09", "2009-03-09", requests_mod=Req(503), retries=1)
        raise AssertionError("a 503 must raise, so it is retried next build")
    except requests.HTTPError:
        pass
    print("P2-2 HTTP 400 = did not trade then (cached), 503 = retry OK")


def test_thb_view_nets_the_currency():
    dates = _dates(120)
    port = [-0.01 if i % 2 else 0.012 for i in range(120)]
    pts, level = [], 35.0
    prev_day = (dt.date.fromisoformat(dates[0]) - dt.timedelta(days=1)).isoformat()
    pts.append({"d": prev_day, "v": level})
    for d, r in zip(dates, port):
        level *= (1 / (1 + r))          # the baht moves exactly against the book
        pts.append({"d": d, "v": level})
    v = R.thb_view(port, dates, pts)
    assert v["status"] == "ok" and v["n"] == 120, v
    assert v["vol_thb_pct"] < 0.5 and v["vol_usd_pct"] > 10, v
    assert v["corr_port_fx"] < -0.9, v
    assert R.thb_view(port[:10], dates[:10], pts)["status"] == "insufficient"
    print("P2-5 THB view OK:", v["vol_usd_pct"], "->", v["vol_thb_pct"])


def test_build_carries_factors_replay_and_thb():
    with tempfile.TemporaryDirectory() as d:
        replay.PATH = os.path.join(d, "rw.json")
        factors_mod.PATH = os.path.join(d, "ff.json")
        spec = {"AAA": dict(sector="Technology"), "BBB": dict(sector="Healthcare")}
        fac = _factor_days(300)
        fx = [{"d": x, "v": 35 + i * 0.01} for i, x in enumerate(_dates(300))]
        p, _ = risk_report.build(_store(spec), "", 0, 250, 20.0, 1.0, False,
                                 fetch_returns=_provider({}), fetch_long=lambda t: {},
                                 fetch_fx=lambda: fx,
                                 fetch_window=lambda sym, a, b: -0.5 if sym == "AAA" else None,
                                 fetch_factors=lambda: fac)
        hist = {r["key"]: r for r in p["stress"]["historical"]}
        assert hist["gfc_2008"]["basis"]["AAA"] == "own", hist["gfc_2008"]
        assert hist["gfc_2008"]["basis"]["BBB"] == "beta", hist["gfc_2008"]
        assert p["factors"]["status"] == "ok", p["factors"]
        assert p["thb_view"]["status"] == "ok", p["thb_view"]
    print("P2-1/2/5 wired into the risk payload OK")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ALL P2B 2026-11 TESTS PASSED")
