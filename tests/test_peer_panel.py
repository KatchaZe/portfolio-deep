"""Peer panel and moat trend (docs/peer-panel/SPEC.md): five-year SEC revenue frames,
the extra fields on stored peer points, the pure panel and moat modules, and their
wiring. Offline: every fetch is faked, store_sync is stubbed to raise.

Tests named test_E<n>_... are the guards for docs/peer-panel/PAST_ERRORS.md row E<n>."""
import datetime as dt
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

import store_sync
from domain import peer_panel as PP
from pipeline import peers
from sources import damodaran_companies as dc


def _refuse(*a, **k):
    raise AssertionError("a peer-panel test reached store_sync (real storage)")


for _name in dir(store_sync):
    if not _name.startswith("_") and callable(getattr(store_sync, _name)) \
            and getattr(getattr(store_sync, _name), "__module__", None) == "store_sync":
        setattr(store_sync, _name, _refuse)

TODAY = dt.date(2026, 11, 16)


class FakeFrames:
    """SEC frames by (concept, year); counts calls."""

    def __init__(self, answers, fail_years=()):
        self.answers, self.fail_years, self.calls = answers, set(fail_years), 0

    def get(self, url, **k):
        self.calls += 1
        concept = url.split("/us-gaap/")[1].split("/")[0]
        year = int(url.split("/CY")[1].split(".")[0])
        if year in self.fail_years:
            raise ConnectionError("down")
        data = self.answers.get((concept, year), [])
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": data})


class Frames(unittest.TestCase):
    def test_five_calendar_years_oldest_first(self):
        self.assertEqual(dc.panel_years(TODAY), [2021, 2022, 2023, 2024, 2025])
        self.assertEqual(dc.panel_years(dt.date(2026, 3, 31)), [2020, 2021, 2022, 2023, 2024])

    def test_E21_frames_keep_each_filers_period_end_and_the_larger_concept(self):
        ans = {}
        for y in dc.panel_years(TODAY):
            ans[("Revenues", y)] = [{"cik": 1, "val": 10 + y, "end": f"{y}-06-30"},
                                    {"cik": 2, "val": 5, "end": f"{y}-12-31"}]
            ans[("RevenueFromContractWithCustomerExcludingAssessedTax", y)] = [
                {"cik": 1, "val": 7, "end": f"{y}-06-29"}, {"cik": 2, "val": 9, "end": f"{y}-12-30"}]
        tmp = tempfile.mkdtemp()
        rq = FakeFrames(ans)
        got, notes = dc.load_revenue_frames_years(tmp, TODAY, "ua", requests_mod=rq)
        self.assertEqual(notes, [])
        self.assertEqual(sorted(got), [2021, 2022, 2023, 2024, 2025])
        self.assertEqual(got[2025][1], [2035, "2025-06-30"])      # Revenues won, its end kept
        self.assertEqual(got[2025][2], [9, "2025-12-30"])         # the other concept won
        calls = rq.calls
        again, _ = dc.load_revenue_frames_years(tmp, TODAY, "ua", requests_mod=rq)
        self.assertEqual(again, got)
        self.assertEqual(rq.calls, calls, "a second load inside the TTL is served from cache")
        rev, note = dc.load_revenue_frames(tmp, 2025, "ua", requests_mod=rq)
        self.assertEqual(rev, {1: 2035, 2: 9})                   # the old reader is unchanged

    def test_E13_a_failed_year_is_named_not_silent(self):
        ans = {("Revenues", y): [{"cik": 1, "val": 1, "end": f"{y}-12-31"}] for y in dc.panel_years(TODAY)}
        got, notes = dc.load_revenue_frames_years(tempfile.mkdtemp(), TODAY, "ua",
                                                  requests_mod=FakeFrames(ans, fail_years={2022}))
        self.assertNotIn(2022, got)
        self.assertEqual(len(notes), 1)
        self.assertIn("CY2022", notes[0])

    def test_E15_a_cache_written_before_end_dates_reads_with_no_end(self):
        tmp = tempfile.mkdtemp()
        import time
        with open(os.path.join(tmp, "sec_frames_revenue_CY2025.json"), "w") as fh:
            json.dump({"at": time.time(), "rev": {"1": 5}}, fh)
        rev, end, note = dc._frames(tmp, 2025, "ua", requests_mod=FakeFrames({}))
        self.assertEqual((rev, end, note), ({1: 5}, {}, None))


YEARS = ("2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31", "2021-12-31")


def ext(rev=10e9, oi=2e9, ni=1.5e9, fy_rev=(10e9, 9e9, 8e9, 7e9, 6e9), gm=(.5, .48, .46, .44, .42),
        fy_oi=(2e9, 1.7e9, 1.4e9, 1.1e9, .8e9), ends=YEARS, ccy="USD", std="us-gaap", **kw):
    """A sec_edgar.extract()-shaped dict, fiscal years newest first."""
    x = {"currency": ccy, "accounting_standard": std, "revenue": rev, "operating_income": oi,
         "net_income": ni, "total_debt": 1e9, "operating_leases": 0, "cash": .5e9,
         "shares_diluted": 1e9, "latest_period_end": "2026-09-30",
         "revenue_annuals_dated": [[d, v] for d, v in zip(ends, fy_rev)],
         "gross_profit_annuals_dated": [[d, v * g] for d, v, g in zip(ends, fy_rev, gm)],
         "operating_income_annuals_dated": [[d, v] for d, v in zip(ends, fy_oi)]}
    x.update(kw)
    return x


class Snapshot(unittest.TestCase):
    def test_fy0_fy1_fy3_from_the_contiguous_dated_run(self):
        s = PP.fy_snapshot(ext()["revenue_annuals_dated"], ext()["gross_profit_annuals_dated"],
                           None, ext()["operating_income_annuals_dated"])
        self.assertEqual((s["fy0"]["end"], s["fy1"]["end"], s["fy3"]["end"]),
                         ("2025-12-31", "2024-12-31", "2022-12-31"))
        self.assertEqual((s["fy3"]["revenue"], s["fy3"]["gross_profit"], s["fy3"]["operating_income"]),
                         (7e9, 7e9 * .44, 1.1e9))

    def test_E2_gross_profit_is_joined_to_revenue_by_date_not_position(self):
        x = ext()
        # gross profit filed for FY2025, FY2023, FY2022 only: an index zip would put
        # FY2023's profit beside FY2024's revenue
        x["gross_profit_annuals_dated"] = [r for r in x["gross_profit_annuals_dated"] if r[0] != "2024-12-31"]
        s = PP.fy_snapshot(x["revenue_annuals_dated"], x["gross_profit_annuals_dated"])
        self.assertIsNone(s["fy1"]["gross_profit"])
        self.assertEqual(s["fy3"]["gross_profit"], 7e9 * .44)

    def test_E7_newest_first_and_oldest_first_give_the_same_snapshot(self):
        x = ext()
        a = PP.fy_snapshot(x["revenue_annuals_dated"], x["gross_profit_annuals_dated"], None,
                           x["operating_income_annuals_dated"])
        b = PP.fy_snapshot(list(reversed(x["revenue_annuals_dated"])),
                           list(reversed(x["gross_profit_annuals_dated"])), None,
                           list(reversed(x["operating_income_annuals_dated"])))
        self.assertEqual(a, b)

    def test_E3_fy3_never_reaches_across_a_filing_gap(self):
        x = ext()
        x["revenue_annuals_dated"] = [r for r in x["revenue_annuals_dated"] if r[0] != "2023-12-31"]
        s = PP.fy_snapshot(x["revenue_annuals_dated"])
        self.assertEqual(s["fy1"]["end"], "2024-12-31")
        self.assertIsNone(s["fy3"], "2022 is behind a gap: no three-year comparison")

    def test_fy1_must_be_about_one_year_back_by_date(self):
        s = PP.fy_snapshot([["2025-12-31", 10], ["2024-01-31", 9]])
        self.assertIsNone(s["fy1"], "consecutive calendar labels 700 days apart are not one year")

    def test_E22_a_fiscal_year_ending_after_as_of_is_not_used(self):
        s = PP.fy_snapshot(ext()["revenue_annuals_dated"], as_of="2025-06-30")
        self.assertEqual(s["fy0"]["end"], "2024-12-31")
        s = PP.fy_snapshot(ext()["revenue_annuals_dated"], as_of="2025-12-31")
        self.assertEqual(s["fy0"]["end"], "2025-12-31")


class PeerPoint(unittest.TestCase):
    def setUp(self):
        self._cik, self._extract = peers.resolve_cik, peers.sec_edgar.extract
        peers.resolve_cik = lambda s: ("0000000001", s)
        peers.sec_edgar.extract = lambda cf: {k: v for k, v in cf.items() if not k.startswith("_")}

    def tearDown(self):
        peers.resolve_cik, peers.sec_edgar.extract = self._cik, self._extract

    def point(self, x, complete=None):
        return peers.peer_point("P", TODAY, fetch_facts=lambda c: x, complete=complete,
                                fetch_price=lambda s, d: (20.0, "2026-11-13", "yahoo"))

    def test_stored_point_keeps_the_panel_fields(self):
        p, why = self.point(ext())
        self.assertIsNone(why)
        c = PP.compact(p)
        self.assertEqual(set(c), set(PP.POINT_KEYS))
        self.assertEqual((c["revenue_ttm"], c["ebit_ttm"], c["net_income_ttm"]), (10e9, 2e9, 1.5e9))
        self.assertEqual(c["market_cap"], 20e9)
        self.assertEqual(c["ev"], 20e9 + 1e9 - .5e9)
        self.assertEqual((c["fy0"]["end"], c["fy3"]["end"]), ("2025-12-31", "2022-12-31"))
        self.assertEqual(c["accounting_standard"], "us-gaap")

    def test_E12_peer_ebit_on_the_panel_is_after_the_one_time_addback(self):
        x = ext(rev=30e9, oi=-2.4e9, acquired_iprd_ttm=10e9,
                acquired_iprd_annuals_dated=[["2025-12-31", .1e9], ["2024-12-31", .2e9]])
        p, _ = self.point(x)
        self.assertEqual(p["ebit_ttm"], -2.4e9 + 7.5e9)          # capped at 25% of revenue
        self.assertEqual(p["ebit_ttm"], p["margin"] * p["revenue"])

    def test_E9_sec_gap_completion_stays_on_the_peer_path(self):
        seen = []

        def complete(cik, cf):
            seen.append(cik)
            return dict(cf, _sec_gap={"form": "10-Q", "filed": "2026-11-01", "read": False, "checked": True})
        p, why = self.point(ext(), complete=complete)
        self.assertEqual(seen, ["0000000001"])
        self.assertIsNone(p)
        self.assertIn("lacks the 10-Q", why)
        p, _ = self.point(ext(), complete=lambda cik, cf: dict(cf, _sec_gap={"read": True, "form": "10-Q"}))
        self.assertIsNotNone(p["fy0"])

    def test_fit_stores_every_usable_point_outliers_included(self):
        pts = []
        for i, m in enumerate([.10, .15, .20, .25, .30, .35, .2]):
            p = {"symbol": f"P{i}", "ev_sales": 1 + 20 * m if i < 6 else 90, "margin": m,
                 "growth": .1, "revenue": 1e9, "period_end": "2026-09-30",
                 "price_date": "2026-11-13", "source": "damodaran", "revenue_ttm": 1e9}
            pts.append(p)
        model, _, why = peers.fit_points(pts, [], TODAY)
        self.assertIsNone(why)
        self.assertEqual(model["version"], 3)
        self.assertEqual(model["outliers"], ["P6"])
        self.assertEqual([p["symbol"] for p in model["panel_points"]], [f"P{i}" for i in range(7)])
        self.assertEqual(set(model["panel_points"][0]), set(PP.POINT_KEYS))


if __name__ == "__main__":
    unittest.main()
