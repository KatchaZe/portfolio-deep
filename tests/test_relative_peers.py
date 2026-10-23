"""Relative valuation on the firm's own peers (Damodaran item 4): domain/relative.py
(pure fit) and pipeline/peers.py (fetch, guards, weekly reuse). Network is faked."""
import datetime as dt
import json
import os
import random
import tempfile
import unittest
from types import SimpleNamespace

from domain import relative as R
from pipeline import batch, peers

TODAY = dt.date(2026, 10, 21)
FIX = os.path.join(os.path.dirname(__file__), "fixtures", "peers", "sec_points_2026-10-19.json")


def facts(rev=100e9, oi=30e9, debt=10e9, cash=5e9, shares=1e9, end="2026-06-30",
          fy=(("2025-12-31", 90e9), ("2024-12-31", 75e9)), ccy="USD"):
    return {"currency": ccy, "revenue": rev, "operating_income": oi, "total_debt": debt,
            "operating_leases": 0, "cash": cash, "shares_diluted": shares,
            "latest_period_end": end, "revenue_annuals_dated": [list(r) for r in fy]}


def pt(sym, ev_sales, margin, growth):
    return {"symbol": sym, "ev_sales": ev_sales, "margin": margin, "growth": growth,
            "revenue": 1e9, "period_end": "2026-06-30", "price_date": "2026-10-19"}


class Fit(unittest.TestCase):
    def test_exact_plane_is_recovered(self):
        pts = [pt(f"P{i}", 1 + 20 * m + 4 * g, m, g)
               for i, (m, g) in enumerate([(.1, .05), (.2, .1), (.3, .02), (.15, .3), (.25, .2),
                                           (.35, .15), (.05, .25), (.4, .08)])]
        model, why = R.fit(pts)
        self.assertIsNone(why)
        self.assertEqual(model["terms"], ["margin", "growth"])
        for got, want in zip(model["coef"], (1, 20, 4)):
            self.assertAlmostEqual(got, want, places=3)
        self.assertAlmostEqual(model["r2"], 1.0, places=6)
        self.assertAlmostEqual(model["loo_median_err"], 0.0, places=6)

    def test_noisy_synthetic_recovery(self):
        rnd = random.Random(7)
        pts = []
        for i in range(30):
            m, g = rnd.uniform(0, .45), rnd.uniform(-.05, .4)
            pts.append(pt(f"P{i}", (2 + 15 * m + 6 * g) * rnd.uniform(.95, 1.05), m, g))
        model, _ = R.fit(pts)
        a, b, c = model["coef"]
        self.assertAlmostEqual(b, 15, delta=1.5)
        self.assertAlmostEqual(c, 6, delta=1.5)
        self.assertGreater(model["r2"], 0.9)
        self.assertLess(model["loo_median_err"], 0.05)

    def test_margin_only_below_eight_peers(self):
        pts = [pt(f"P{i}", 2 + 10 * m, m, .1 * i) for i, m in enumerate([.1, .2, .3, .4, .5, .15])]
        model, _ = R.fit(pts)
        self.assertEqual(model["terms"], ["margin"])

    def test_too_few_peers_is_a_reason_not_a_number(self):
        model, why = R.fit([pt(f"P{i}", 3, .1 * i, .1) for i in range(4)])
        self.assertIsNone(model)
        self.assertIn("need 5", why)

    def test_iqr_outlier_dropped_and_named(self):
        pts = [pt(f"P{i}", 4 + i * .2, .1 + .02 * i, .1) for i in range(7)] + [pt("RICH", 60, .2, .1)]
        model, _ = R.fit(pts)
        self.assertEqual(model["outliers"], ["RICH"])
        self.assertNotIn("RICH", [p["symbol"] for p in model["peers"]])

    def test_loo_matches_brute_force(self):
        xs, ys = [[.1], [.2], [.3], [.4], [.25]], [3., 5.2, 6.8, 9.1, 6.]
        got = R.loo_errors(xs, ys)
        for i in range(5):
            c = R.ols(xs[:i] + xs[i + 1:], ys[:i] + ys[i + 1:])
            self.assertAlmostEqual(got[i], abs(c[0] + c[1] * xs[i][0] - ys[i]) / ys[i])

    def test_implied_value_and_extrapolation_flag(self):
        pts = [pt(f"P{i}", 1 + 20 * m, m, .1) for i, m in enumerate([.1, .15, .2, .25, .3])]
        model, _ = R.fit(pts)
        tgt = {"ev_sales": 5, "margin": .2, "growth": .1, "revenue": 100}
        out = R.implied(model, tgt, net_debt=50, shares=10)
        self.assertAlmostEqual(out["implied_ev_sales"], 5.0, places=3)
        self.assertAlmostEqual(out["value_regression"], (5 * 100 - 50) / 10, places=2)
        self.assertEqual(out["flags"], [])
        out = R.implied(model, dict(tgt, margin=.6), 50, 10)
        self.assertIn("extrapolated", out["flags"][0])

    def test_negative_value_is_withheld(self):
        pts = [pt(f"P{i}", 1 + 20 * m, m, .1) for i, m in enumerate([.1, .15, .2, .25, .3])]
        model, _ = R.fit(pts)
        out = R.implied(model, {"ev_sales": 1, "margin": .1, "growth": .1, "revenue": 10},
                        net_debt=1000, shares=10)
        self.assertIsNone(out["value_regression"])
        self.assertTrue(any("not positive" in f for f in out["flags"]))


class PointGuards(unittest.TestCase):
    def ok(self, **kw):
        return R.point("X", facts(**kw), 50, "2026-10-19", TODAY)

    def test_good_point_on_one_clock(self):
        p, why = self.ok()
        self.assertIsNone(why)
        self.assertAlmostEqual(p["ev_sales"], (50e9 + 10e9 - 5e9) / 100e9)
        self.assertAlmostEqual(p["margin"], .3)
        self.assertAlmostEqual(p["growth"], 90 / 75 - 1)

    def test_each_guard_names_its_reason(self):
        cases = {
            "reports in EUR": dict(ccy="EUR"),
            "older than 6 months": dict(end="2025-12-31"),
            "not positive": dict(cash=100e9),
            "below -50%": dict(oi=-60e9),
            "one year apart": dict(fy=(("2025-12-31", 90e9), ("2023-12-31", 75e9))),
            "fewer than two": dict(fy=(("2025-12-31", 90e9),)),
            "no TTM revenue": dict(rev=0),
        }
        for want, kw in cases.items():
            p, why = self.ok(**kw)
            self.assertIsNone(p, want)
            self.assertIn(want, why)

    def test_no_close_is_excluded(self):
        self.assertIn("T-1", R.point("X", facts(), None, None, TODAY)[1])


class RealDataBacktest(unittest.TestCase):
    """Frozen SEC extracts of the 21 holdings (closes of 2026-10-19)."""

    @classmethod
    def setUpClass(cls):
        with open(FIX, encoding="utf-8") as fh:
            cls.raw = json.load(fh)
        cls.pts, cls.why = {}, {}
        for t, v in cls.raw.items():
            p, why = R.point(t, v["facts"], v["price"], v["price_date"], TODAY)
            (cls.pts.__setitem__(t, p) if p else cls.why.__setitem__(t, why))

    def test_foreign_and_stale_filers_excluded(self):
        self.assertIn("DKK", self.why["NVO"])
        self.assertIn("EUR", self.why["ASML"])
        self.assertIn("older than 6 months", self.why["TSM"])

    def test_known_answer_msft_ev_sales(self):
        f = self.raw["MSFT"]["facts"]
        ev = 533.52 * f["shares_diluted"] + f["total_debt"] + f["operating_leases"] - f["cash"]
        self.assertAlmostEqual(self.pts["MSFT"]["ev_sales"], ev / f["revenue"], places=9)
        self.assertAlmostEqual(self.pts["MSFT"]["ev_sales"], 12.14, delta=0.01)

    def test_regression_beats_peer_median_out_of_sample(self):
        kept, dropped = R.drop_outliers(list(self.pts.values()))
        ys = [p["ev_sales"] for p in kept]
        xs = [[p["margin"]] for p in kept]
        reg = R._median(R.loo_errors(xs, ys))
        med = R._median([abs(R._median(ys[:i] + ys[i + 1:]) - y) / y for i, y in enumerate(ys)])
        self.assertLess(reg, med)
        self.assertEqual([p["symbol"] for p in dropped], ["RKLB"])

    def test_margin_is_priced(self):
        model, _ = R.fit(list(self.pts.values()))
        self.assertGreater(model["coef"][1], 0)


class FakeF(SimpleNamespace):
    pass


def target(**kw):
    base = dict(ticker="TGT", currency="USD", revenue=100e9, operating_income=25e9,
                total_debt=10e9, operating_leases=0, cash=5e9, shares_diluted=1e9,
                market_cap=None, price=60.0, provenance={"price": "yahoo close 2026-10-19 (T-1)"},
                revenue_annuals_dated=[["2025-12-31", 90e9], ["2024-12-31", 80e9]])
    base.update(kw)
    return FakeF(**base)


PEER_FACTS = {f"P{i}": facts(rev=10e9, oi=m * 10e9, shares=1e9) for i, m in
              enumerate([.10, .15, .20, .25, .30, .35])}
PEER_PRICE = {f"P{i}": 10 * (1 + 20 * m) - .5 for i, m in enumerate([.10, .15, .20, .25, .30, .35])}


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.calls = {"list": 0, "sec": 0, "price": 0}
        peers.resolve_cik, self._cik = (lambda s: ("0000000001", s)), peers.resolve_cik
        self._extract = peers.sec_edgar.extract
        peers.sec_edgar.extract = lambda cf: cf
        self._pp = peers.peer_point

    def tearDown(self):
        peers.resolve_cik = self._cik
        peers.sec_edgar.extract = self._extract
        peers.peer_point = self._pp

    def kw(self, lag=None):
        def fetch_list(t, key):
            self.calls["list"] += 1
            return list(PEER_FACTS) + [t]

        def fetch_facts(cik):
            self.calls["sec"] += 1
            return PEER_FACTS[self.cur]

        def fetch_price(sym, today_iso):
            self.calls["price"] += 1
            return PEER_PRICE[sym], lag or "2026-10-19", "yahoo"

        # the fake SEC fetch needs the symbol: route through resolve_cik's name
        def facts_by_sym(cik):
            return fetch_facts(cik)
        orig = self._pp

        def peer_point(sym, today, **_):
            self.cur = sym
            return orig(sym, today, fetch_facts=facts_by_sym, fetch_price=fetch_price, complete=None)
        peers.peer_point = peer_point
        return {"cache_dir": self.tmp, "fetch_list": fetch_list}

    def test_fresh_run_fits_and_values_target(self):
        block, calls = peers.relative_value(target(), "k", TODAY, None, batch.Budget(5), **self.kw())
        self.assertEqual(calls, 1)
        m = block["model"]
        self.assertEqual(m["n"], 6)
        self.assertAlmostEqual(m["coef"][1], 20, delta=1)
        # peers sit on EV/Sales = 1.45 + 20 x margin (net cash 0.5x sales);
        # the target's 25% margin reads 6.45x
        self.assertAlmostEqual(block["implied_ev_sales"], 6.45, delta=0.01)
        self.assertEqual(m["as_of"], "2026-10-21")

    def test_week_old_model_reused_without_network(self):
        block, _ = peers.relative_value(target(), "k", TODAY, None, batch.Budget(5), **self.kw())
        self.calls = {"list": 0, "sec": 0, "price": 0}
        later = TODAY + dt.timedelta(days=6)
        again, calls = peers.relative_value(target(revenue=120e9), "k", later, block, batch.Budget(5), **self.kw())
        self.assertEqual((calls, self.calls["sec"], self.calls["price"]), (0, 0, 0))
        # the target side moved with the new revenue
        self.assertNotEqual(again["value_regression"], block["value_regression"])

    def test_stale_peer_price_excluded(self):
        block, _ = peers.relative_value(target(), "k", TODAY, None, batch.Budget(5), **self.kw(lag="2026-10-09"))
        self.assertIsNone(block["model"])
        self.assertTrue(all("not within 5 days" in e["reason"] for e in block["excluded"]))

    def test_failed_week_reuses_last_fit_up_to_30_days_with_note(self):
        good, _ = peers.relative_value(target(), "k", TODAY, None, batch.Budget(5), **self.kw())
        later = TODAY + dt.timedelta(days=20)
        bad, _ = peers.relative_value(target(), "k", later, good, batch.Budget(5), **self.kw(lag="2026-10-01"))
        self.assertEqual(bad["model"]["as_of"], "2026-10-21")
        self.assertTrue(any("reused" in n for n in bad["notes"]))
        gone, _ = peers.relative_value(target(), "k", TODAY + dt.timedelta(days=45), good,
                                       batch.Budget(5), **self.kw(lag="2026-10-01"))
        self.assertIsNone(gone["model"])

    def test_peer_list_cache_and_budget(self):
        kw = self.kw()
        syms, as_of, note, calls = peers.peer_list("TGT", "k", TODAY, batch.Budget(5), kw["cache_dir"], kw["fetch_list"])
        self.assertEqual((calls, note), (1, None))
        self.assertNotIn("TGT", syms)
        _, _, _, calls = peers.peer_list("TGT", "k", TODAY + dt.timedelta(days=29), batch.Budget(5),
                                         kw["cache_dir"], kw["fetch_list"])
        self.assertEqual(calls, 0)
        # expired and no budget left -> the old list, with a dated note
        syms, _, note, calls = peers.peer_list("TGT", "k", TODAY + dt.timedelta(days=31), batch.Budget(0),
                                               kw["cache_dir"], kw["fetch_list"])
        self.assertEqual(calls, 0)
        self.assertIn("2026-10-21", note)
        self.assertEqual(len(syms), 6)

    def test_no_key_spends_nothing(self):
        block, calls = peers.relative_value(target(), "", TODAY, None, None, **self.kw())
        self.assertEqual((calls, self.calls["list"]), (0, 0))
        self.assertIsNone(block["model"])
        self.assertIn("no FMP key", block["notes"][0])

    def test_stale_sec_cache_excluded(self):
        p, why = self._pp("P1", TODAY, fetch_facts=lambda c: dict(PEER_FACTS["P1"], _stale_cache=True), complete=None,
                                  fetch_price=lambda s, d: (5, "2026-10-19", "yahoo"))
        self.assertIsNone(p)
        self.assertIn("stale", why)


class IndustryAndOneTime(unittest.TestCase):
    def test_peer_ebit_gets_the_targets_one_time_addback(self):
        x = facts(rev=30e9, oi=-2.4e9)                         # GILD-like: -8% margin
        x.update(acquired_iprd_ttm=10e9, impairment_ttm=None, restructuring_ttm=None,
                 acquired_iprd_annuals_dated=[["2025-12-31", 0.1e9], ["2024-12-31", 0.2e9]],
                 restructuring_annuals_dated=None,
                 revenue_annuals_dated=[["2025-12-31", 29e9], ["2024-12-31", 28e9]])
        y, add = peers.normalized(x)
        self.assertEqual(add, 7.5e9)                           # capped at 25% of revenue
        self.assertAlmostEqual(y["operating_income"] / y["revenue"], 0.17)
        x["acquired_iprd_annuals_dated"] = [["2025-12-31", 3e9], ["2024-12-31", 3e9],
                                            ["2023-12-31", 3e9], ["2022-12-31", 3e9]]
        x["revenue_annuals_dated"] = [[d, 29e9] for d, _ in x["acquired_iprd_annuals_dated"]]
        y, add = peers.normalized(x)
        self.assertEqual((add, y["operating_income"]), (0.0, -2.4e9))   # recurs: an operating cost

    def test_mixed_industry_fit_is_marked_weak(self):
        pts = [dict(pt(f"C{i}", 1 + 10 * m, m, .1), sic=["2844", "cosmetics"])
               for i, m in enumerate([.05, .1, .15, .2, .25, .3])]
        memo = peers.PointMemo()
        for p in pts:
            memo.get(p["symbol"], lambda s, p=p: (p, None))
        model, _, _ = peers.build_model("HIMS", [p["symbol"] for p in pts], TODAY, memo,
                                        target_sic=["8011", "doctors"])
        self.assertAlmostEqual(model["r2"], 1.0)
        self.assertTrue(model["weak"])
        self.assertIn("only 0 of 6", model["industry_note"])

    def test_sic_level(self):
        self.assertEqual(peers.sic_level(["7372", ""], ["7370", ""]), 3)
        self.assertEqual(peers.sic_level(["2844", ""], ["8000", ""]), 0)
        self.assertIsNone(peers.sic_level(None, ["7372", ""]))

    def test_other_industries_dropped_only_when_enough_remain(self):
        soft = [dict(pt(f"S{i}", 5, .2, .1), sic=["7372", "software"]) for i in range(5)]
        cosm = [dict(pt(f"C{i}", 3, .1, .1), sic=["2844", "perfumes, cosmetics"]) for i in range(3)]
        kept, dropped, note = peers.match_industry(soft + cosm, ["7372", "software"])
        self.assertEqual(len(kept), 5)
        self.assertIsNone(note)
        self.assertTrue(all("other industry: SIC 2844" in d["reason"] for d in dropped))
        kept, dropped, note = peers.match_industry(soft[:3] + cosm, ["7372", "software"])
        self.assertEqual((len(kept), dropped), (6, []))
        self.assertIn("only 3 of 6", note)
        self.assertIn("SIC unknown", peers.match_industry(soft, None)[2])


if __name__ == "__main__":
    unittest.main()
