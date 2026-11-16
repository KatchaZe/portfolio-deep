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


def pp(sym, rev=10e9, ebit=2e9, ni=1.5e9, ev=30e9, mcap=25e9, fy=None, ccy="USD", std="us-gaap",
       period_end="2026-09-30", price_date="2026-11-13", source="damodaran"):
    """A stored peer point (PP.POINT_KEYS) with its FY legs."""
    x = ext() if fy is None else fy
    snap = PP.fy_snapshot(x["revenue_annuals_dated"], x.get("gross_profit_annuals_dated"), None,
                          x.get("operating_income_annuals_dated"))
    return dict({"symbol": sym, "source": source, "currency": ccy, "accounting_standard": std,
                 "period_end": period_end, "price_date": price_date, "revenue_ttm": rev,
                 "ebit_ttm": ebit, "net_income_ttm": ni, "ev": ev, "market_cap": mcap}, **snap)


AS_OF = "2026-11-16"


class Table(unittest.TestCase):
    def test_row_known_answer(self):
        r = PP.table_row(pp("A"))
        self.assertAlmostEqual(r["growth_fy"], 10 / 9 - 1)
        self.assertAlmostEqual(r["cagr3_fy"], (10 / 7) ** (1 / 3) - 1)
        self.assertAlmostEqual(r["gross_margin_fy"], .5)
        self.assertAlmostEqual(r["op_margin_ttm"], .2)
        self.assertAlmostEqual(r["ev_sales"], 3.0)
        self.assertAlmostEqual(r["ev_ebit"], 15.0)
        self.assertAlmostEqual(r["pe"], 25 / 1.5)
        self.assertEqual(r["fy0_end"], "2025-12-31")

    def test_E1_fy_metrics_never_read_ttm_and_ttm_never_read_fy(self):
        base = PP.table_row(pp("A"))
        moved_ttm = PP.table_row(pp("A", rev=40e9, ebit=9e9))
        for m in ("growth_fy", "cagr3_fy", "gross_margin_fy"):
            self.assertEqual(base[m], moved_ttm[m], f"{m} moved with TTM revenue")
        moved_fy = PP.table_row(pp("A", fy=ext(fy_rev=(50e9, 9e9, 8e9, 7e9, 6e9))))
        for m in ("op_margin_ttm", "ev_sales", "ev_ebit", "pe", "revenue_ttm"):
            self.assertEqual(base[m], moved_fy[m], f"{m} moved with a fiscal-year revenue")

    def test_E14_gross_margin_needs_both_legs_from_one_fiscal_year(self):
        x = ext()
        x["gross_profit_annuals_dated"] = [r for r in x["gross_profit_annuals_dated"] if r[0] != "2025-12-31"]
        x["cost_of_revenue_annuals_dated"] = None
        self.assertIsNone(PP.table_row(pp("A", fy=x))["gross_margin_fy"])

    def test_E6_multiples_on_a_non_positive_denominator_are_null(self):
        self.assertIsNotNone(PP.table_row(pp("A", ebit=1.0))["ev_ebit"])
        for ebit in (0, -1e9):
            r = PP.table_row(pp("A", ebit=ebit))
            self.assertIsNone(r["ev_ebit"])
            self.assertIsNotNone(r["op_margin_ttm"])            # a loss margin is still a margin
        self.assertIsNotNone(PP.table_row(pp("A", ni=1.0))["pe"])
        for ni in (0, -1e9):
            self.assertIsNone(PP.table_row(pp("A", ni=ni))["pe"])
        self.assertIsNone(PP.table_row(pp("A", ev=-1e9))["ev_sales"])
        for base, want_none in ((7e9, False), (0, True), (-1e9, True)):
            r = PP.table_row(pp("A", fy=ext(fy_rev=(10e9, 9e9, 8e9, base, 6e9))))
            self.assertEqual(r["cagr3_fy"] is None, want_none, f"FY3 revenue {base}")

    def test_stats_known_answer_and_missing_is_not_zero(self):
        st = PP.stats([4, None, 1, 3, 2])
        self.assertEqual(st, {"n": 4, "max": 4, "p75": 3.25, "median": 2.5, "p25": 1.75, "min": 1})
        self.assertEqual(PP.stats([None])["n"], 0)

    def test_iqr_fence_on_both_sides(self):
        def rows(v):
            return [dict(PP.table_row(pp(f"P{i}")), ev_sales=x) for i, x in enumerate(v)]
        # 1,2,3,4: p25 1.75, p75 3.25, fence [-0.5, 5.5]
        inside = rows([1, 2, 3, 4, 5.5])
        PP.flag_outliers(inside)
        self.assertFalse(any("ev_sales" in r["outlier"] for r in inside))
        out = rows([1, 2, 3, 4, 5.6])
        PP.flag_outliers(out)
        st = PP.stats([1, 2, 3, 4, 5.6])
        hi = st["p75"] + 1.5 * (st["p75"] - st["p25"])
        self.assertEqual(["ev_sales" in r["outlier"] for r in out], [False] * 4 + [5.6 > hi])

    def test_rank3_terciles(self):
        rows = [dict(PP.table_row(pp(f"P{i}", rev=r))) for i, r in enumerate([9e9, 8e9, 7e9, 6e9, 5e9, 4e9])]
        PP.rank_terciles(rows)
        self.assertEqual([r["rank3"]["scale"] for r in rows], [1, 1, 2, 2, 3, 3])
        two = [dict(PP.table_row(pp(f"P{i}", rev=r))) for i, r in enumerate([9e9, 8e9])]
        PP.rank_terciles(two)
        self.assertEqual([r["rank3"]["scale"] for r in two], [None, None])

    def test_target_vs_median_units(self):
        peers_ = [pp(f"P{i}", ebit=e, ev=v) for i, (e, v) in enumerate([(1e9, 20e9), (2e9, 30e9), (3e9, 40e9)])]
        tbl, _ = PP.table(pp("T", ebit=4e9, ev=60e9), peers_, AS_OF)
        tv = tbl["target_vs_median"]
        self.assertAlmostEqual(tv["op_margin_ttm"], (0.4 - 0.2) * 100)   # pp
        self.assertAlmostEqual(tv["ev_sales"], 6.0 - 3.0)                 # x
        self.assertEqual(tbl["stats"]["ev_sales"]["n"], 3, "the median is over the peers, not the target")
        self.assertTrue(tbl["rows"][0]["is_target"])

    def test_E8_foreign_currency_and_ifrs_peers_are_excluded_with_reason(self):
        tbl, excl = PP.table(pp("T"), [pp("A"), pp("B", ccy="EUR"), pp("C", std="ifrs-full"),
                                        pp("D", std=None)], AS_OF)
        self.assertEqual([r["symbol"] for r in tbl["rows"]], ["T", "A"])
        why = {e["symbol"]: e["reason"] for e in excl}
        self.assertIn("EUR", why["B"])
        self.assertIn("ifrs-full", why["C"])
        self.assertIn("unknown standard", why["D"])

    def test_E4_a_row_whose_newest_year_lags_is_dropped_with_a_flag(self):
        lag400 = ext(ends=("2024-11-26", "2023-11-26", "2022-11-26", "2021-11-26", "2020-11-26"))
        lag401 = ext(ends=("2024-11-25", "2023-11-25", "2022-11-25", "2021-11-25", "2020-11-25"))
        tbl, excl = PP.table(pp("T"), [pp("OK", fy=lag400), pp("OLD", fy=lag401)], AS_OF)
        self.assertEqual([r["symbol"] for r in tbl["rows"]], ["T", "OK"])
        self.assertIn("lags", excl[0]["reason"])
        panel = PP.build(pp("T"), [pp("OLD", fy=lag401)], AS_OF, "Software", "x")
        self.assertTrue(any(f.startswith("OLD:") and "lags" in f for f in panel["flags"]))

    def test_E22_peer_facts_after_as_of_are_excluded(self):
        _, excl = PP.table(pp("T"), [pp("A", price_date="2026-11-17")], AS_OF)
        self.assertIn("after 2026-11-16", excl[0]["reason"])
        tbl, excl = PP.table(pp("T"), [pp("A", price_date="2026-11-16")], AS_OF)
        self.assertEqual(excl, [])

    def test_E10_filters_run_before_anything_is_counted(self):
        tbl, _ = PP.table(pp("T"), [pp("X", ccy="EUR", rev=999e9)] + [pp(f"P{i}") for i in range(3)], AS_OF)
        self.assertEqual(tbl["stats"]["revenue_ttm"]["max"], 10e9)


def frames_for(years, rows):
    """{CY: {cik: [revenue, end]}} from {cik: [rev per year]}."""
    return {y: {c: [v[i], f"{y}-12-31"] for c, v in rows.items() if v[i] is not None}
            for i, y in enumerate(years)}


Y5 = [2021, 2022, 2023, 2024, 2025]


class Share(unittest.TestCase):
    def members(self, n=10):
        return {f"T{i}": str(i + 1).zfill(10) for i in range(n)}

    def test_known_answer_share_top5_hhi_and_change(self):
        rows = {i + 1: [10.0] * 5 for i in range(10)}
        rows[1] = [10.0, 10.0, 20.0, 30.0, 40.0]          # the target grows into the industry
        sh = PP.industry_share(self.members(), frames_for(Y5, rows), "0000000001", Y5, AS_OF)
        self.assertEqual(sh["n_members"], 10)
        self.assertEqual(sh["n_with_revenue"]["2025"], 10)
        self.assertAlmostEqual(sh["target_share"]["2025"], 40 / 130)
        self.assertAlmostEqual(sh["target_share"]["2022"], 10 / 100)
        self.assertAlmostEqual(sh["share_change_pp"], (40 / 130 - 10 / 100) * 100)
        self.assertAlmostEqual(sh["top5_share"]["2025"], 80 / 130)
        self.assertAlmostEqual(sh["hhi"]["2021"], 10 * 10 ** 2)
        self.assertEqual(sh["target_frames_end"]["2025"], "2025-12-31")

    def test_coverage_threshold_on_both_sides(self):
        rows = {i + 1: [10.0] * 5 for i in range(10)}
        for c in (8, 9, 10):
            rows[c] = [10.0, 10.0, 10.0, 10.0, None]           # 7 of 10 in CY2025: exactly 70%
        sh = PP.industry_share(self.members(), frames_for(Y5, rows), "0000000001", Y5, AS_OF)
        self.assertAlmostEqual(sh["target_share"]["2025"], 1 / 7)
        rows[7] = [10.0, 10.0, 10.0, 10.0, None]               # 6 of 10: 60%
        sh = PP.industry_share(self.members(), frames_for(Y5, rows), "0000000001", Y5, AS_OF)
        self.assertIsNone(sh["target_share"]["2025"])
        self.assertIsNone(sh["hhi"]["2025"])
        self.assertIsNone(sh["share_change_pp"], "a nulled newest year leaves no change")
        self.assertTrue(any("CY2025" in f and "60%" in f for f in sh["flags"]))
        # 69 of 100 just under the line, 70 of 100 on it
        big = {f"T{i}": str(i + 1).zfill(10) for i in range(100)}
        r69 = {i + 1: [1.0] * 5 if i < 69 else [None] * 5 for i in range(100)}
        self.assertIsNone(PP.industry_share(big, frames_for(Y5, r69), "0000000001", Y5, AS_OF)["hhi"]["2025"])
        r70 = {i + 1: [1.0] * 5 if i < 70 else [None] * 5 for i in range(100)}
        self.assertIsNotNone(PP.industry_share(big, frames_for(Y5, r70), "0000000001", Y5, AS_OF)["hhi"]["2025"])

    def test_share_classes_of_one_filer_count_once(self):
        m = dict(self.members(), GOOG="0000000001")
        rows = {i + 1: [10.0] * 5 for i in range(10)}
        sh = PP.industry_share(m, frames_for(Y5, rows), "0000000001", Y5, AS_OF)
        self.assertEqual(sh["n_members"], 10)
        self.assertAlmostEqual(sh["target_share"]["2025"], .1)

    def test_E11_only_damodaran_industry_members_enter_the_share(self):
        rows = {i + 1: [10.0] * 5 for i in range(10)}
        rows[999] = [1e6] * 5                                  # a filer outside the industry
        sh = PP.industry_share(self.members(), frames_for(Y5, rows), "0000000001", Y5, AS_OF)
        self.assertAlmostEqual(sh["target_share"]["2025"], .1)

    def test_E10_top5_is_taken_after_dropping_non_positive_revenue(self):
        rows = {i + 1: [10.0] * 5 for i in range(10)}
        rows[2] = [-50.0] * 5
        rows[3] = [0.0] * 5
        sh = PP.industry_share(self.members(), frames_for(Y5, rows), "0000000001", Y5, AS_OF)
        self.assertEqual(sh["n_with_revenue"]["2025"], 8)
        self.assertAlmostEqual(sh["top5_share"]["2025"], 50 / 80)

    def test_E6_share_change_with_a_missing_base_is_null(self):
        rows = {i + 1: [10.0] * 5 for i in range(10)}
        rows[1] = [10.0, None, 10.0, 10.0, 10.0]               # target absent in the base year
        sh = PP.industry_share(self.members(), frames_for(Y5, rows), "0000000001", Y5, AS_OF)
        self.assertIsNone(sh["target_share"]["2022"])
        self.assertIsNone(sh["share_change_pp"])
        self.assertIsNotNone(sh["top5_change_pp"])
        self.assertTrue(any("CY2022: no revenue frame for the target" in f for f in sh["flags"]))

    def test_E22_a_calendar_year_not_over_by_as_of_is_not_used(self):
        rows = {i + 1: [10.0] * 6 for i in range(10)}
        sh = PP.industry_share(self.members(), frames_for(Y5 + [2026], rows), "0000000001",
                               Y5 + [2026], AS_OF)
        self.assertEqual(sh["years"], Y5)
        sh = PP.industry_share(self.members(), frames_for(Y5, rows), "0000000001", Y5, "2025-06-30")
        self.assertEqual(sh["years"], [2021, 2022, 2023, 2024])

    def test_E21_the_target_frames_end_is_what_sec_assigned(self):
        fr = frames_for(Y5, {i + 1: [10.0] * 5 for i in range(10)})
        fr[2025][1] = [10.0, "2025-06-30"]                     # a June filer, as frames placed it
        sh = PP.industry_share(self.members(), fr, "0000000001", Y5, AS_OF)
        self.assertEqual(sh["target_frames_end"]["2025"], "2025-06-30")

    def test_E13_every_early_return_says_why(self):
        sh = PP.industry_share({"A": None}, {}, None, Y5, AS_OF)
        self.assertTrue(any("no industry members" in f for f in sh["flags"]))
        sh = PP.industry_share(self.members(), {}, "0000000001", Y5, AS_OF)
        self.assertEqual(sum("no SEC revenue frames" in f for f in sh["flags"]), 5)
        sh = PP.industry_share(self.members(), frames_for(Y5, {1: [1.0] * 5}), None, Y5, AS_OF)
        self.assertTrue(any("target has no SEC CIK" in f for f in sh["flags"]))


class Panel(unittest.TestCase):
    def test_E15_no_stored_points_is_a_flag_not_an_error(self):
        panel = PP.build(pp("T"), None, AS_OF, "Software", "Damodaran company list")
        self.assertIsNone(panel["table"])
        self.assertTrue(any("predates the panel" in f for f in panel["flags"]))

    def test_E13_every_missing_input_is_flagged(self):
        panel = PP.build(None, [], AS_OF)
        f = " | ".join(panel["flags"])
        for want in ("no Damodaran industry", "no usable peers", "no target row"):
            self.assertIn(want, f)
        panel = PP.build(pp("T"), [pp("B", ccy="EUR")], AS_OF, "x", "y",
                         excluded=[{"symbol": "Z", "reason": "not an SEC filer"}])
        f = " | ".join(panel["flags"])
        for want in ("every peer was excluded", "Z: not an SEC filer", "B: reports in EUR"):
            self.assertIn(want, f)

    def test_panel_shape_matches_the_spec(self):
        sh = PP.industry_share({"T": "1"}, frames_for(Y5, {1: [1.0] * 5}), "1", Y5, AS_OF)
        panel = PP.build(pp("T"), [pp(f"P{i}") for i in range(4)], AS_OF, "Software", "b", sh)
        self.assertEqual(set(panel), {"as_of", "version", "industry", "industry_basis", "table",
                                      "industry_share", "flags"})
        self.assertEqual(set(panel["table"]), {"rows", "stats", "target_vs_median"})
        row = set(panel["table"]["rows"][0])
        for k in ("symbol", "is_target", "period_end", "fy0_end", "price_date", "revenue_ttm",
                  "growth_fy", "cagr3_fy", "gross_margin_fy", "op_margin_ttm", "ev_sales", "ev_ebit",
                  "pe", "rank3", "outlier"):
            self.assertIn(k, row)
        for k in ("group", "years", "n_members", "n_with_revenue", "target_share", "top5_share",
                  "hhi", "share_change_pp", "top5_change_pp"):
            self.assertIn(k, panel["industry_share"])
        json.dumps(panel)


if __name__ == "__main__":
    unittest.main()
