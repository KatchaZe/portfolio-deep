"""IFRS members' calendar-year revenue from their own companyfacts (E76, SPEC R48, display only).

SEC's frames API has no ifrs-full frames from CY2022 (frames/ifrs-full/Revenue/USD/CY2025 answers
NoSuchKey), so GlobalFoundries, a 20-F filer reporting IFRS in USD, sat in NVDA's SIC 3674
sub-group as a member with no revenue. Its revenue is in its own companyfacts with SEC's frame
tags. Red on 0f529d0 (no frames_revenue.cy_rows_from_facts, no Universe IFRS reader)."""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain import frames_revenue as FR, peer_panel as PP  # noqa: E402

GFS_CF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "ifrs", "GFS_companyfacts.json")
GFS, NVDA, AMD = 1709048, 1045810, 2488
YEARS = [2021, 2022, 2023, 2024, 2025]
M = 1e6


def gfs_cf():
    with open(GFS_CF) as fh:
        return json.load(fh)


def frames():
    """us-gaap frames for NVDA and AMD only, as SEC serves them: GFS is in none."""
    nv = {2021: 16675, 2022: 26974, 2023: 60922, 2024: 130497, 2025: 215938}
    am = {2021: 16434, 2022: 23601, 2023: 22680, 2024: 25785, 2025: 34000}
    return {y: {NVDA: [nv[y] * M, "%d-01-25" % (y + 1), "us-gaap:Revenues", None],
                AMD: [am[y] * M, "%d-12-27" % y, "us-gaap:Revenues", None]} for y in YEARS}


class CompanyfactsRows(unittest.TestCase):
    def test_gfs_cy_revenue_is_read_by_frame_tag(self):
        rows = FR.cy_rows_from_facts(gfs_cf())
        self.assertEqual({y: rows[y][0] / M for y in YEARS},
                         {2021: 6585, 2022: 8108, 2023: 7392, 2024: 6750, 2025: 6791})
        self.assertEqual(rows[2025][2], "ifrs-full:Revenue")

    def test_a_fact_filed_after_as_of_is_left_out(self):
        self.assertNotIn(2025, FR.cy_rows_from_facts(gfs_cf(), as_of="2026-03-01"))

    def test_currency(self):
        self.assertEqual(FR.ifrs_currency(gfs_cf()), "USD")
        eur = {"facts": {"ifrs-full": {"Revenue": {"units": {"EUR": [{"val": 1}]}}}}}
        self.assertEqual(FR.ifrs_currency(eur), "EUR")
        self.assertIsNone(FR.ifrs_currency({"facts": {"us-gaap": {}}}))


class Loader(unittest.TestCase):
    def test_one_request_per_filer_then_cached(self):
        from sources import damodaran_companies as dc
        calls = []

        def fetch(cik):
            calls.append(cik)
            if cik == 404:
                raise IOError("blocked")
            return gfs_cf() if cik == GFS else {"facts": {"us-gaap": {}}}
        with tempfile.TemporaryDirectory() as d:
            got, failed, n = dc.load_ifrs_member_revenue(d, [GFS, 27367, 404], "ua", "2026-11-24", fetch=fetch)
            self.assertEqual((n, failed), (3, {404}))
            self.assertEqual(got[GFS]["cy"][2025][0], 6791 * M)
            self.assertIsNone(got[27367]["currency"])
            got2, failed2, n2 = dc.load_ifrs_member_revenue(d, [GFS, 27367, 404], "ua", "2026-11-24", fetch=fetch)
            self.assertEqual(n2, 1)                       # only the failed filer is asked again
            self.assertEqual(got2[GFS]["cy"][2025][0], 6791 * M)


class IndustryShare(unittest.TestCase):
    def share(self, ifrs, failed=None):
        members = {"NVDA": NVDA, "AMD": AMD, "GFS": GFS}
        fr, other = PP.with_ifrs(frames(), {}, ifrs)
        return PP.industry_share(members, fr, NVDA, YEARS, "2026-11-24", None, {}, {}, other, [], failed)

    def test_candidates_are_the_members_no_frame_holds(self):
        self.assertEqual(PP.ifrs_candidates({"NVDA": NVDA, "AMD": AMD, "GFS": GFS, "X": None}, {},
                                            frames(), {}, YEARS), {GFS})
        self.assertEqual(PP.ifrs_candidates({"GFS": GFS}, {}, frames(), {2024: {GFS}}, YEARS), set())

    def test_an_ifrs_member_with_companyfacts_revenue_is_counted(self):
        before, after = self.share({}), self.share({GFS: {"currency": "USD", "cy": FR.cy_rows_from_facts(gfs_cf())}})
        self.assertEqual([r[0] for r in before["no_revenue"]], ["GFS"])
        self.assertEqual(after["no_revenue"], [])
        self.assertEqual(after["n_with_revenue"]["2025"], 3)
        self.assertAlmostEqual(after["target_share"]["2025"], 215938 / (215938 + 34000 + 6791), places=6)

    def test_a_non_usd_ifrs_member_is_named_not_counted(self):
        out = self.share({GFS: {"currency": "EUR", "cy": {}}})
        self.assertEqual(out["members_ifrs_other"], [["EUR", 1]])
        self.assertEqual(out["no_revenue"], [])
        self.assertEqual(out["n_with_revenue"]["2025"], 2)
        self.assertTrue(any("IFRS สกุล EUR" in f for f in out["flags"]))

    def test_a_failed_request_says_so(self):
        out = self.share({}, {GFS})
        self.assertIn("companyfacts", out["no_revenue"][0][2])


class Pipeline(unittest.TestCase):
    def test_the_panel_reads_each_ifrs_member_once_per_run(self):
        from tests import _harness
        _harness.cut_network()
        from pipeline import peers
        asked = []

        def loader(ciks):
            asked.append(set(ciks))
            return {GFS: {"currency": "USD", "cy": FR.cy_rows_from_facts(gfs_cf())}}, set(), len(ciks)
        u = peers.Universe(frames=frames(), assets={}, ifrs_loader=loader)
        members = {"NVDA": NVDA, "AMD": AMD, "GFS": GFS}
        for _ in range(2):                                # two holdings of one industry in one run
            fr, other, failed = peers.share_frames(u, members, {}, YEARS)
        share = PP.industry_share(members, fr, NVDA, YEARS, "2026-11-24", {}, {}, {}, other, [], failed)
        self.assertEqual(asked, [{GFS}])
        self.assertEqual(u.ifrs_requests, 1)
        self.assertEqual(share["no_revenue"], [])
        self.assertEqual(fr[2025][GFS][0], 6791 * M)

    def test_a_universe_without_an_ifrs_source_changes_nothing(self):
        from pipeline import peers
        u = peers.Universe(frames=frames(), assets={})
        fr, other, failed = peers.share_frames(u, {"GFS": GFS}, {}, YEARS)
        self.assertEqual((fr, other, failed), (frames(), {}, set()))


if __name__ == "__main__":
    unittest.main()
