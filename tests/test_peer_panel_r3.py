"""SPEC R20-R23 (2026-11-21): coverage counts balance-sheet filers, S4 needs 10% revenue
growth, two more source hints, a trend on every hint and a watch list. Display only."""
import datetime as dt
import json
import os
import tempfile
import time
import unittest

from domain import contracts
from domain import moat_trend as MT
from domain import peer_panel as PP
from pipeline import peers
from sources import damodaran_companies as dc
from tests import test_peer_panel as base
from tests.test_peer_panel import TODAY, Y5, snap, tfacts, universe

AS_OF = TODAY.isoformat()


def frames_with(rev_by_cik, years=Y5):
    return {y: {c: [v, f"{y}-12-31"] for c, v in rev_by_cik.items()} for y in years}


class R20Coverage(unittest.TestCase):
    MEMBERS = {f"M{i}": 100 + i for i in range(10)}

    def share(self, assets=None):
        rev = {100 + i: 10.0 * (i + 1) for i in range(6)}          # 6 of 10 book revenue
        return PP.industry_share(self.MEMBERS, frames_with(rev), 100, Y5, AS_OF, None, assets)

    def test_pre_revenue_filers_count_toward_coverage_not_the_shares(self):
        alone = self.share()
        self.assertAlmostEqual(alone["coverage"]["2025"], 0.6)
        self.assertIsNone(alone["target_share"]["2025"], "60% < 70%: share not shown")
        both = self.share({y: {106, 107, 999} for y in Y5})          # 999 is not a member
        self.assertEqual((both["n_with_revenue"]["2025"], both["n_covered"]["2025"]), (6, 8))
        self.assertAlmostEqual(both["coverage"]["2025"], 0.8)
        self.assertAlmostEqual(both["target_share"]["2025"], 10 / 210, msg="revenue 0 adds nothing to the total")
        self.assertAlmostEqual(both["top5_share"]["2025"], 200 / 210)
        self.assertEqual(both["matched_n"], 6, "the matched set stays the revenue filers (R13)")

    def test_one_balance_sheet_short_of_the_rule_still_hides_the_share(self):
        sh = self.share({y: {106} for y in Y5})
        self.assertAlmostEqual(sh["coverage"]["2025"], 0.7 - 0.0, places=6)
        self.assertIsNotNone(sh["target_share"]["2025"], "exactly 70% passes")
        none = self.share({y: set() for y in Y5})
        self.assertIsNone(none["target_share"]["2025"])
        self.assertTrue(any("report revenue or a balance sheet" in f for f in none["flags"]))

    def test_assets_frames_cache_30_days_and_fall_back(self):
        calls = []

        class R:
            def __init__(self, data, code=200):
                self.data, self.status_code = data, code

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise RuntimeError(self.status_code)

            def json(self):
                return {"data": self.data}

        class Rq:
            ok = True

            def get(self, url, **kw):
                calls.append(url)
                if not Rq.ok:
                    raise ConnectionError("down")
                return R([{"cik": 7, "val": 1.0}, {"cik": 8, "val": 2.0}])

        with tempfile.TemporaryDirectory() as d:
            now = time.time()
            ciks, note = dc._assets(d, 2024, "ua", Rq(), now=now)
            self.assertEqual((ciks, note), ({7, 8}, None))
            self.assertIn("Assets/USD/CY2024Q4I.json", calls[-1])
            with open(os.path.join(d, "sec_frames_assets_CY2024.json")) as fh:
                self.assertEqual(json.load(fh)["ciks"], [7, 8])
            n = len(calls)
            dc._assets(d, 2024, "ua", Rq(), now=now + 29 * 86400)
            self.assertEqual(len(calls), n, "fresh for 30 days")
            Rq.ok = False
            ciks, note = dc._assets(d, 2024, "ua", Rq(), now=now + 31 * 86400)
            self.assertEqual(ciks, {7, 8})
            self.assertIn("from cache", note)
            ciks, note = dc._assets(d, 2023, "ua", Rq(), now=now)
            self.assertEqual(ciks, set())
            self.assertIn("unavailable", note)
            out, notes = dc.load_assets_frames_years(d, TODAY, "ua", Rq(), now=now)
            self.assertEqual(sorted(out), [2024], "a failed year is absent and named")
            self.assertEqual(len(notes), 4)



class R20Wiring(unittest.TestCase):
    setUp, tearDown, val = base.Wiring.setUp, base.Wiring.tearDown, base.Wiring.val

    def test_the_wiring_passes_the_assets_frames_to_the_share(self):
        u = universe()                          # 10 Software members, CIKs 1..10, all with revenue
        u._frames = ({y: {c: v for c, v in yr.items() if c <= 6} for y, yr in u.frames.items()}, [], {})
        sh = peers.panel_and_moat(tfacts(), self.val(), None, u, TODAY)[0]["industry_share"]
        self.assertEqual((sh["n_covered"]["2025"], sh["target_share"]["2025"]), (6, None))
        u._frames = (u._frames[0], [], {y: {7, 8} for y in Y5})
        sh = peers.panel_and_moat(tfacts(), self.val(), None, u, TODAY)[0]["industry_share"]
        self.assertEqual(sh["n_covered"]["2025"], 8)
        self.assertAlmostEqual(sh["target_share"]["2025"], 1 / 6)

    def test_lazy_universe_loads_the_assets_frames(self):
        seen = []
        real = (dc.load_revenue_frames_years, dc.load_assets_frames_years)
        dc.load_revenue_frames_years = lambda *a, **k: ({2025: {1: [5.0, "2025-12-31"]}}, ["r"])
        dc.load_assets_frames_years = lambda *a, **k: (seen.append(a) or {2025: {2}}, ["a"])
        try:
            u = peers.Universe.lazy(TODAY, cache_dir=tempfile.gettempdir(), user_agent="ua")
            self.assertEqual((u.assets, u.frame_notes), ({2025: {2}}, ["r", "a"]))
        finally:
            dc.load_revenue_frames_years, dc.load_assets_frames_years = real
        self.assertEqual(len(seen), 1)


class R21IncrementalMargin(unittest.TestCase):
    def test_needs_ten_percent_revenue_growth_both_sides(self):
        sig, why = MT.incremental_margin(snap(109.9, 100, oi0=20, oi3=10))
        self.assertIsNone(sig)
        self.assertEqual(why, "S4: revenue grew 9.9% in 3 years (need ≥10%)")
        sig, _ = MT.incremental_margin(snap(110.0, 100, oi0=20, oi3=10))
        self.assertIsNotNone(sig)

    def test_abbv_tiny_delta_is_absent_not_minus_122pp(self):
        # ABBV 2026-11-20: revenue +0.3% in 3 years gave S4 = -1.226 and a -1 vote
        sig, why = MT.incremental_margin(snap(56.33e9, 56.20e9, oi0=9.1e9, oi3=12.0e9))
        self.assertIsNone(sig)
        self.assertIn("grew 0.2% in 3 years", why)


def point(gm0, gm3, opm_ttm, opm0=None, opm3=None, rev=100.0):
    p = snap(rev, rev * 0.8, oi0=None if opm0 is None else rev * opm0,
             oi3=None if opm3 is None else rev * 0.8 * opm3, gm0=gm0, gm3=gm3)
    p.update({"revenue_ttm": rev, "ebit_ttm": rev * opm_ttm})
    return p


def peers3(gm=0.40, opm=0.10, opm0=0.10, opm3=0.10):
    return [point(gm, gm, opm, opm0, opm3) for _ in range(3)]


def share(top5=0.85, chg=1.0, n=20, top5_chg=0.0, s=0.05):
    return {"years": Y5, "target_share": {"2025": s}, "top5_share": {"2025": top5},
            "share_change_pp": chg, "n_members": n, "top5_change_pp": top5_chg}


def kinds(hints):
    return {h["type"]: h for h in hints}


class R22Hints(unittest.TestCase):
    def test_efficient_scale_every_threshold_both_sides(self):
        cases = [(share(), "weak"), (share(top5=0.80), "weak"), (share(top5=0.7999), None),
                 (share(top5=0.90), "moderate"), (share(chg=2.0), "weak"), (share(chg=-2.0), "weak"),
                 (share(chg=2.01), None), (share(chg=-2.01), None), (share(chg=None), None),
                 (share(n=30), "weak"), (share(n=31), None), (share(top5=None), None)]
        for sh, want in cases:
            h = kinds(MT.source_hints({}, [], sh)).get("efficient_scale")
            self.assertEqual(h and h["strength"], want, sh)
        h = kinds(MT.source_hints({}, [], share(top5=0.93, chg=-0.4, n=12)))["efficient_scale"]
        self.assertEqual([(m["metric"], m["value"], m["period"]) for m in h["measures"]],
                         [("top5_share", 0.93, "CY2025"), ("share_change_pp", -0.4, "CY2022..CY2025"),
                          ("n_members", 12, "CY2025")])
        self.assertIn("consistent with", h["evidence"])

    def test_cost_advantage_both_sides(self):
        def hint(gm, opm, n=3):
            return kinds(MT.source_hints(point(gm, gm, opm), peers3()[:n], None)).get("cost_advantage")
        self.assertIsNotNone(hint(0.4199, 0.1501))                    # +5pp OPM, +2pp GM
        self.assertIsNone(hint(0.4199, 0.1499))
        self.assertIsNone(hint(0.4201, 0.1501))
        self.assertIsNotNone(hint(0.20, 0.30), "a GM below the median is a cost story")
        self.assertIsNone(hint(0.42, 0.15, n=2), "a median of two firms is not a median")
        h = hint(0.41, 0.20)
        self.assertEqual(h["strength"], "moderate")
        self.assertEqual([(m["metric"], m["period"]) for m in h["measures"]],
                         [("opm_gap_pp", "TTM"), ("gm_gap_pp", "FY2025")])
        self.assertAlmostEqual(h["measures"][0]["value"], 10.0)
        self.assertIn("consistent with", h["evidence"])

    def test_old_hints_unchanged(self):
        hs = MT.source_hints(point(0.65, 0.65, 0.10), peers3(), share(s=0.30, top5=0.5))
        self.assertEqual([(h["type"], h["strength"]) for h in hs], [("intangible", "moderate"), ("scale", "moderate")])
        self.assertEqual(hs[0]["evidence"], "gross margin 25pp above the peer median (FY0): "
                                            "consistent with pricing power from brand or IP")


class R23TrendAndWatch(unittest.TestCase):
    def test_each_hint_takes_its_related_signal(self):
        sigs = [{"name": "gm_gap_change", "value": .05, "direction": 1},
                {"name": "share_change", "value": -.2, "direction": -1}]
        hs = kinds(MT.source_hints(point(0.65, 0.65, 0.10), peers3(), share(s=0.30, top5_chg=-3), sigs))
        self.assertEqual((hs["intangible"]["trend"], hs["scale"]["trend"]), (1, -1))
        hs = kinds(MT.source_hints(point(0.65, 0.65, 0.10), peers3(), share(s=0.30)))
        self.assertEqual((hs["intangible"]["trend"], hs["scale"]["trend"]), (None, None),
                         "an absent signal is an unknown trend, not flat")

    def test_efficient_scale_trend_from_top5_change(self):
        for chg, want in ((2.01, 1), (2.0, 0), (-2.0, 0), (-2.01, -1), (None, None)):
            h = kinds(MT.source_hints({}, [], share(top5_chg=chg)))["efficient_scale"]
            self.assertEqual(h["trend"], want, chg)

    def test_cost_advantage_trend_from_the_fy_operating_margin_gap(self):
        def trend(t0, t3):
            tgt = point(0.40, 0.40, 0.20, opm0=t0, opm3=t3)
            return kinds(MT.source_hints(tgt, peers3(), None))["cost_advantage"]["trend"]
        self.assertEqual(trend(0.2201, 0.20), 1)        # gap 12.01pp from 10pp
        self.assertEqual(trend(0.2199, 0.20), 0)
        self.assertEqual(trend(0.1799, 0.20), -1)
        self.assertIsNone(trend(None, 0.20), "no FY operating income: trend unknown")

    def test_watch_is_the_three_signals_nearest_their_band(self):
        sigs = [{"name": "spread_slope", "value": 5.0, "direction": 1},           # 4.0 bands away
                {"name": "share_change", "value": 0.09, "direction": 0},          # 0.1
                {"name": "gm_gap_change", "value": -0.025, "direction": -1},      # 0.25
                {"name": "incremental_margin", "value": 0.0, "direction": 0}]     # 1.0
        self.assertEqual(MT.watch(sigs), [MT.SIGNAL_TH[n] for n in
                                          ("share_change", "gm_gap_change", "incremental_margin")])
        self.assertEqual(MT.watch([]), [])
        out = MT.build({}, snap(100, 80, oi0=30, oi3=10), [], {"years": Y5, "target_share": {},
                       "matched_share_then": .1, "matched_share_now": .2}, 8.0)
        self.assertEqual(len(out["watch"]), 2)
        self.assertTrue(all(any("฀" <= c <= "๿" for c in w) for w in out["watch"]), "Thai names")

    def test_every_new_key_declares_its_clock(self):
        sigs = [{"name": "gm_gap_change", "value": .05, "direction": 1, "window": "x", "unit": "fraction"}]
        m = {"source_hints": MT.source_hints(point(0.42, 0.42, 0.20, 0.2, 0.2), peers3(), share(s=.3), sigs)
             + MT.source_hints({}, [], share()), "watch": MT.watch(sigs)}
        self.assertEqual({h["type"] for h in m["source_hints"]}, {"scale", "cost_advantage", "efficient_scale"})
        self.assertEqual(contracts.undeclared_panel_keys(m), [])
        sh = PP.industry_share({"A": 1}, frames_with({1: 5.0}), 1, Y5, AS_OF, None, {2025: {1}})
        self.assertEqual(contracts.undeclared_panel_keys(sh), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
