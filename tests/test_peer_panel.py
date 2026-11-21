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
from domain import moat_trend as MT
from domain import peer_panel as PP
from pipeline import fundamentals, peers
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
        # written on the two-concept ladder too, so it is refreshed; the refresh finds
        # nothing and the old cache is served with a note, never an error
        self.assertEqual((rev, end), ({1: 5}, {}))
        self.assertIn("from cache", note)


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
        peers.sec_edgar.extract = lambda cf, **k: {a: v for a, v in cf.items() if not a.startswith("_")}

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
        self.assertEqual(model["version"], peers.MODEL_VERSION)
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

    def test_R3_the_fence_and_stats_are_the_peers_alone(self):
        rows = [dict(PP.table_row(pp("T"), True), ev_sales=3)] + \
            [dict(PP.table_row(pp(f"P{i}")), ev_sales=x) for i, x in enumerate([1, 1, 1, 1, 3])]
        PP.flag_outliers(rows)
        self.assertEqual([("ev_sales" in r["outlier"]) for r in rows], [True, False, False, False, False, True])

    def test_R10_frames_cy_follows_sec_not_majority_of_days(self):
        self.assertEqual(PP.frames_cy("2025-06-30"), 2025)          # MSFT
        self.assertEqual(PP.frames_cy("2025-05-31"), 2024)          # ORCL
        self.assertEqual(PP.frames_cy("2025-12-31"), 2025)
        self.assertEqual(PP.frames_cy("2025-05-31", {"2025-05-31": 2025}), 2025, "SEC's tag wins")
        r = PP.table_row(pp("ORCL", fy=ext(ends=("2025-05-31", "2024-05-31", "2023-05-31", "2022-05-31", "2021-05-31"))))
        self.assertEqual((r["fy0_end"], r["frames_cy"]), ("2025-05-31", 2024))

    def test_R10_R11_frame_tags_take_the_latest_filing_on_or_before_as_of(self):
        def e(end, frame, filed, start="2024-07-01"):
            return {"start": start, "end": end, "frame": frame, "filed": filed, "val": 1}
        cf = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
            e("2025-06-30", "CY2024", "2025-07-30"),
            e("2025-06-30", "CY2025", "2025-09-01"),           # a later filing re-tags it
            e("2025-06-30", "CY2025Q2", "2025-07-30", "2025-04-01"),
            e("2024-06-30", None, "2024-07-30")]}}}}}
        self.assertEqual(PP.frame_tags(cf), {"2025-06-30": 2025})
        self.assertEqual(PP.frame_tags(cf, as_of="2025-08-01"), {"2025-06-30": 2024})
        self.assertEqual(PP.frame_tags(cf, as_of="2025-07-29"), {})

    def test_R2_row_keeps_the_addback_and_margins_use_the_addback_ebit(self):
        r = PP.table_row(dict(pp("A", ebit=3e9), addback=0.1))
        self.assertEqual(r["addback"], 0.1)
        self.assertAlmostEqual(r["op_margin_ttm"], 0.3)
        self.assertAlmostEqual(r["ev_ebit"], 10.0)

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
        self.assertAlmostEqual(sh["hhi"]["2021"], 10 * 0.1 ** 2)                 # 0-1 (R1)
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
        for k in ("symbol", "is_target", "period_end", "fy0_end", "frames_cy", "addback", "price_date", "revenue_ttm",
                  "growth_fy", "cagr3_fy", "gross_margin_fy", "op_margin_ttm", "ev_sales", "ev_ebit",
                  "pe", "rank3", "outlier"):
            self.assertIn(k, row)
        for k in ("group", "years", "n_members", "n_with_revenue", "target_share", "top5_share",
                  "hhi", "share_change_pp", "top5_change_pp"):
            self.assertIn(k, panel["industry_share"])
        json.dumps(panel)


def roic(vals, ends=None):
    ends = ends or ["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]
    return {d: {"roic": v} for d, v in zip(ends, vals)}


def snap(rev0, rev3, oi0=None, oi3=None, gm0=None, gm3=None):
    leg = lambda end, r, o, g: {"end": end, "revenue": r, "operating_income": o,
                                "gross_profit": None if g is None else r * g}
    return {"fy0": leg("2025-12-31", rev0, oi0, gm0), "fy3": leg("2022-12-31", rev3, oi3, gm3)}


def share_of(old, new):
    return {"years": Y5, "target_share": {"2022": old, "2025": new},
            "matched_share_then": old, "matched_share_now": new}


class Moat(unittest.TestCase):
    def test_provisional_thresholds_are_declared(self):
        self.assertEqual((MT.SPREAD_SLOPE_PP, MT.SHARE_REL, MT.GM_GAP_PP, MT.INC_PP), (1.0, 0.10, 2.0, 5.0))
        out = MT.build({}, None, [], None, None)
        self.assertEqual(out["thresholds"], "provisional")

    def test_S1_slope_known_answer_and_both_sides_of_the_band(self):
        dates = ["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]
        span = MT._years(dates[0], dates[-1]) / 4                  # ~1 year between ends
        for per_year, want in ((1.01, 1), (0.99, 0), (-0.99, 0), (-1.01, -1)):
            m = roic([10 + per_year * MT._years(dates[0], d) for d in dates])
            sig, _ = MT.spread_slope(m, 8.0)
            self.assertAlmostEqual(sig["value"], per_year, places=2)
            self.assertEqual(sig["direction"], want, f"slope {per_year}")
        self.assertAlmostEqual(span, 1.0, places=2)
        sig, _ = MT.spread_slope(roic([10, 11, 12, 13, 14]), 30.0)
        self.assertEqual((sig["window"], sig["unit"]), ("FY2021..FY2025", "pp/yr"))

    def test_E3_S1_reads_only_the_contiguous_recent_run(self):
        ends = ["2018-12-31", "2019-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]
        sig, _ = MT.spread_slope(roic([50, 50, 10, 12, 14], ends), 8.0)
        self.assertEqual(sig["window"], "FY2023..FY2025")
        self.assertAlmostEqual(sig["value"], 2.0, places=1)       # not the fall from 2019's 50
        sig, why = MT.spread_slope(roic([50, 50, 10, 12], ends[:2] + ends[3:]), 8.0)
        self.assertIsNone(sig)
        self.assertIn("2 contiguous", why)

    def test_E5_S1_without_wacc_is_absent(self):
        sig, why = MT.spread_slope(roic([10, 11, 12]), None)
        self.assertIsNone(sig)
        self.assertIn("no WACC", why)

    def test_S2_relative_share_change_both_sides(self):
        for new, want in ((0.1101, 1), (0.1099, 0), (0.0901, 0), (0.0899, -1)):
            sig, _ = MT.share_change(share_of(0.10, new))
            self.assertEqual(sig["direction"], want, f"share 0.10 -> {new}")
        self.assertEqual(MT.share_change(share_of(0.10, 0.12))[0]["window"], "CY2022..CY2025")

    def test_E6_S2_with_a_zero_or_missing_base_is_absent(self):
        for old in (0.0, None):
            sig, why = MT.share_change(share_of(old, 0.05))
            self.assertIsNone(sig, f"base {old}")
            self.assertTrue(why.startswith("S2"))
        self.assertIsNone(MT.share_change(None)[0])

    def test_S3_gm_gap_change_both_sides(self):
        peers_ = [snap(10, 8, gm0=.40, gm3=.40) for _ in range(3)]
        for t0, want in ((.4202, 1), (.4198, 0), (.3802, 0), (.3798, -1)):
            sig, _ = MT.gm_gap_change(snap(10, 8, gm0=t0, gm3=.40), peers_)
            self.assertEqual(sig["direction"], want, f"target GM {t0}")
        sig, _ = MT.gm_gap_change(snap(10, 8, gm0=.50, gm3=.45), [snap(10, 8, gm0=.30, gm3=.35)] * 3)
        self.assertAlmostEqual(sig["value"], (.50 - .30) - (.45 - .35))      # a fraction (R1)
        self.assertEqual(sig["unit"], "fraction")

    def test_S3_needs_three_peers_with_both_years(self):
        peers_ = [snap(10, 8, gm0=.4, gm3=.4)] * 2 + [snap(10, 8, gm0=.4, gm3=None)]
        sig, why = MT.gm_gap_change(snap(10, 8, gm0=.5, gm3=.4), peers_)
        self.assertIsNone(sig)
        self.assertIn("2 peers", why)

    def test_S4_incremental_margin_both_sides(self):
        # FY0 margin 20%, FY3 revenue 80: incremental - average = (16 - o3) / 20
        for o3, want in ((14.98, 1), (15.02, 0), (16.98, 0), (17.02, -1)):
            sig, _ = MT.incremental_margin(snap(100, 80, oi0=20, oi3=o3))
            self.assertEqual(sig["direction"], want, f"FY3 OI {o3} -> {sig['value']}")

    def test_E6_S4_only_when_revenue_grew(self):
        for r3, why_ in ((100, "grew 0.0% in 3 years"), (120, "grew -16.7% in 3 years"),
                         (0, "not positive"), (-5, "not positive")):
            sig, why = MT.incremental_margin(snap(100, r3, oi0=20, oi3=10))
            self.assertIsNone(sig, f"FY3 revenue {r3}")
            self.assertIn(why_, why)

    def test_E5_label_is_null_under_two_signals_and_missing_is_not_zero(self):
        up = {"direction": 1}
        self.assertIsNone(MT.label([up]))
        self.assertEqual(MT.label([up, up]), "improving")           # two votes, two absent
        self.assertEqual(MT.label([up, up, {"direction": -1}]), "stable")
        self.assertEqual(MT.label([{"direction": -1}] * 2), "eroding")
        self.assertEqual(MT.label([up, {"direction": 0}]), "stable")
        out = MT.build({}, snap(100, 80, oi0=30, oi3=10), [], share_of(.10, .20), 8.0)
        self.assertEqual((out["n_signals"], out["label"]), (2, "improving"))
        self.assertEqual({s["name"] for s in out["signals"]}, {"share_change", "incremental_margin"})
        self.assertEqual(len(out["flags"]), 2, "each absent signal says why")
        one = MT.build({}, None, [], share_of(.10, .20), 8.0)
        self.assertIsNone(one["label"])
        self.assertTrue(any("no label" in f for f in one["flags"]))

    def test_S1_from_stored_facts(self):
        f = {"operating_income_annuals_dated": [[f"{y}-12-31", 10 + 2 * i] for i, y in enumerate(range(2021, 2026))],
             "ic_components_dated": {f"{y}-12-31": {"equity": 100, "cash": 0, "debt": 0} for y in range(2021, 2026)},
             "income_before_tax": 100, "tax_expense": 20}
        out = MT.build(f, None, [], None, 5.0)
        self.assertEqual(out["signals"][0]["name"], "spread_slope")
        self.assertAlmostEqual(out["signals"][0]["value"], 1.6, places=1)   # 2 x (1 - 20%) per year

    def test_source_hints_say_consistent_with_never_a_finding(self):
        peers_ = [snap(10, 8, gm0=.30, gm3=.30)] * 3
        h = MT.source_hints(snap(10, 8, gm0=.55, gm3=.5), peers_, share_of(.1, .3))
        self.assertEqual([(x["type"], x["strength"]) for x in h], [("intangible", "moderate"), ("scale", "moderate")])
        self.assertTrue(all("consistent with" in x["evidence"] for x in h))
        h = MT.source_hints(snap(10, 8, gm0=.399, gm3=.5), peers_, share_of(.1, .099))
        self.assertEqual(h, [])
        h = MT.source_hints(snap(10, 8, gm0=.40, gm3=.5), peers_, share_of(.1, .10))
        self.assertEqual([(x["type"], x["strength"]) for x in h], [("intangible", "weak"), ("scale", "weak")])


def tfacts(**kw):
    """The target as FinancialFacts-shaped attributes (EBIT already normalized)."""
    x = ext()
    base = dict(ticker="T0", currency="USD", accounting_standard="us-gaap", revenue=10e9,
                operating_income=2.5e9, operating_income_gaap=2e9, net_income=1.5e9,
                total_debt=1e9, operating_leases=0, cash=.5e9, minority_interest=0,
                shares_diluted=1e9, price=30.0, revenue_quarters={"2026-09-30": 2.6e9, "2026-06-30": 2.5e9},
                revenue_annuals_dated=x["revenue_annuals_dated"],
                gross_profit_annuals_dated=x["gross_profit_annuals_dated"],
                cost_of_revenue_annuals_dated=None,
                operating_income_annuals_dated=x["operating_income_annuals_dated"],
                ic_components_dated={d: {"equity": 5e9, "cash": 0, "debt": 0} for d in YEARS},
                tax_rate=0.2, damodaran_industry=None)
    base.update(kw)
    return SimpleNamespace(**base)


def universe(n=10, years=Y5):
    rows = {f"T{i}": ["Software", "7372", "United States"] for i in range(n)}
    rows["FOREIGN"] = ["Software", "7372", "Israel"]
    rows["OTHER"] = ["Retail", "5311", "United States"]
    fr = frames_for(years, {i + 1: [10.0] * len(years) for i in range(n)})
    return peers.Universe(rows, {}, {}, as_of="2026-01", frames=fr)


class Wiring(unittest.TestCase):
    def setUp(self):
        self._cik = peers.resolve_cik
        peers.resolve_cik = lambda t: ((str(int(t[1:]) + 1).zfill(10), t) if t[1:].isdigit() else (None, None))
        self._fmp = {n: getattr(peers.fmp, n) for n in dir(peers.fmp) if n.startswith("fetch")}
        for n in self._fmp:
            setattr(peers.fmp, n, _refuse)

    def tearDown(self):
        peers.resolve_cik = self._cik
        for n, fn in self._fmp.items():
            setattr(peers.fmp, n, fn)

    def block(self, pts):
        return {"model": {"panel_points": pts, "version": peers.MODEL_VERSION}, "excluded": [],
                "notes": []}

    def val(self):
        return SimpleNamespace(key_metrics={"wacc_pct": 8.0})

    def test_panel_and_moat_end_to_end_without_fmp(self):
        pts = [PP.compact(dict(pp(f"P{i}", ebit=e), addback=0.0)) for i, e in enumerate([1e9, 2e9, 3e9, 4e9])]
        panel, moat = peers.panel_and_moat(tfacts(), self.val(), self.block(pts), universe(), TODAY)
        self.assertEqual(panel["industry"], "Software")
        rows = panel["table"]["rows"]
        self.assertEqual(rows[0]["symbol"], "T0")
        self.assertEqual((rows[0]["period_end"], rows[0]["addback"]), ("2026-09-30", 0.05))
        self.assertAlmostEqual(rows[0]["op_margin_ttm"], 0.25)
        sh = panel["industry_share"]
        self.assertEqual(sh["n_members"], 10, "Israel-domiciled and other-industry rows are out")
        self.assertAlmostEqual(sh["target_share"]["2025"], .1)
        self.assertEqual(moat["thresholds"], "provisional")
        self.assertIn("spread_slope", {s["name"] for s in moat["signals"]})
        json.dumps([panel, moat])

    def test_the_target_row_carries_the_closes_own_date_not_the_as_of(self):
        pts = [PP.compact(dict(pp(f"P{i}"), addback=0.0)) for i in range(4)]
        f = tfacts(provenance={"price": "yahoo close 2026-11-13 (T-1)"})
        panel, _ = peers.panel_and_moat(f, self.val(), self.block(pts), universe(), TODAY)
        self.assertEqual(panel["table"]["rows"][0]["price_date"], "2026-11-13")

    def test_E15_a_v2_fit_without_panel_points_gives_a_flagged_panel(self):
        block = {"model": {"version": 2}, "excluded": [], "notes": []}
        panel, moat = peers.panel_and_moat(tfacts(), self.val(), block, universe(), TODAY)
        self.assertIsNone(panel["table"])
        self.assertTrue(any("predates the panel" in f for f in panel["flags"]))
        self.assertIsNotNone(panel["industry_share"])
        self.assertTrue(any(f.startswith("S3") for f in moat["flags"]))

    def test_E13_no_industry_and_no_frames_are_flagged(self):
        u = peers.Universe({}, {}, {}, frames={})
        panel, _ = peers.panel_and_moat(tfacts(), self.val(), None, u, TODAY)
        self.assertIsNone(panel["industry_share"])
        self.assertTrue(any("no Damodaran industry" in f for f in panel["flags"]))
        u = universe()
        u._frames = ({}, ["CY2025: SEC revenue frames unavailable (ConnectionError)"])
        panel, _ = peers.panel_and_moat(tfacts(), self.val(), None, u, TODAY)
        self.assertTrue(any("unavailable" in f for f in panel["flags"]))
        self.assertTrue(any("no SEC revenue frames" in f for f in panel["flags"]))

    def test_a_panel_failure_leaves_relative_and_the_valuation_alone(self):
        val = SimpleNamespace(relative={"model": 1}, peer_panel={}, moat_trend={}, P=3.0)
        orig = peers.panel_and_moat
        peers.panel_and_moat = lambda *a, **k: 1 / 0
        try:
            fundamentals.attach_panel(tfacts(), val, val.relative, universe(), TODAY)
        finally:
            peers.panel_and_moat = orig
        failed = {"flags": ["peer panel failed: ZeroDivisionError"]}
        self.assertEqual((val.relative, val.peer_panel, val.moat_trend, val.P), ({"model": 1}, failed, failed, 3.0),
                         "E13: a failed build is told apart from a row that predates the panel ({})")

    def test_E13_relative_value_raising_leaves_a_flagged_panel_not_an_empty_one(self):
        val = SimpleNamespace(relative=None, peer_panel={}, moat_trend={}, P=3.0)
        orig, lazy = peers.relative_value, peers.Universe.lazy
        peers.relative_value = lambda *a, **k: [][0]
        peers.Universe.lazy = classmethod(lambda cls, today, **k: universe())
        try:
            fundamentals._attach_relative({"T0": (tfacts(), val)}, None, None, {}, TODAY)
        finally:
            peers.relative_value, peers.Universe.lazy = orig, lazy
        failed = {"flags": ["peer panel failed: IndexError"]}
        self.assertEqual((val.relative, val.peer_panel, val.moat_trend, val.P), (None, failed, failed, 3.0))

    def test_E18_attaching_the_panel_writes_only_the_two_display_blocks(self):
        pts = [PP.compact(dict(pp(f"P{i}"), addback=0.0)) for i in range(4)]
        val = SimpleNamespace(D=3.0, E_exec=2.0, E_econ=4.0, P=1.5, composite=2.7, recommendation="HOLD",
                              anchor_value=50.0, key_metrics={"wacc_pct": 8.0}, relative=self.block(pts),
                              peer_panel={}, moat_trend={})
        before = json.dumps({k: v for k, v in vars(val).items() if k not in ("peer_panel", "moat_trend")},
                            sort_keys=True)
        f = tfacts()
        facts_before = json.dumps(vars(f), sort_keys=True, default=str)
        fundamentals.attach_panel(f, val, val.relative, universe(), TODAY)
        self.assertTrue(val.peer_panel.get("table") and val.moat_trend.get("signals"))
        self.assertEqual(json.dumps({k: v for k, v in vars(val).items() if k not in ("peer_panel", "moat_trend")},
                                    sort_keys=True), before)
        self.assertEqual(json.dumps(vars(f), sort_keys=True, default=str), facts_before)

    def test_the_valuation_carries_the_two_blocks(self):
        from domain.engine.contract import Valuation
        d = Valuation(version="x").to_dict()
        self.assertEqual((d["peer_panel"], d["moat_trend"]), ({}, {}))


class Guards(unittest.TestCase):
    def built(self):
        sh = PP.industry_share({f"T{i}": str(i + 1) for i in range(10)},
                               frames_for(Y5, {i + 1: [10.0] * 5 for i in range(10)}), "1", Y5, AS_OF)
        panel = PP.build(pp("T"), [pp(f"P{i}", ebit=e) for i, e in enumerate([1e9, 2e9, 3e9, 4e9])],
                         AS_OF, "Software", "b", sh)
        moat = MT.build({}, snap(100, 80, oi0=30, oi3=10, gm0=.5, gm3=.4),
                        [snap(10, 8, gm0=.3, gm3=.3)] * 3, sh, 8.0)
        return panel, moat

    def test_E1_every_new_field_declares_a_clock(self):
        from domain import contracts as C
        panel, moat = self.built()
        point = PP.compact(dict(pp("P"), addback=0.0, sic=["7372", "x"], price=20.0))
        for name, obj in (("peer_panel", panel), ("moat_trend", moat), ("point", point)):
            self.assertEqual(C.undeclared_panel_keys(obj), [], name)
        for m in ("growth_fy", "cagr3_fy", "gross_margin_fy"):
            self.assertEqual(C.PANEL_CLOCK[m], C.FY)
        for m in ("revenue_ttm", "op_margin_ttm", "ev_sales", "ev_ebit", "pe", "ebit_ttm", "net_income_ttm"):
            self.assertEqual(C.PANEL_CLOCK[m], C.TTM)
        for m in ("target_share", "top5_share", "hhi", "share_change_pp"):
            self.assertEqual(C.PANEL_CLOCK[m], C.CY)
        for m in PP.METRICS:
            self.assertIn(m, C.PANEL_UNIT, f"{m} has no unit")
        self.assertEqual(C.undeclared_panel_keys({"rows": [{"new_metric": 1}]}), [".rows[0].new_metric"])

    def test_E16_no_new_global_allowed_entries(self):
        from domain import contracts as C
        self.assertEqual(list(C.ALLOWED), [("revenue", "revenue_annuals", "reverse_dcf")])
        self.assertTrue(all(k[2] for k in C.ALLOWED), "every exception is scoped to a function")

    def test_E17_every_test_here_asserts(self):
        import ast
        with open(__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        bare = [f.name for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name.startswith("test_")
                and not any(isinstance(n, ast.Attribute) and n.attr.startswith("assert") for n in ast.walk(f))]
        self.assertEqual(bare, [])

    def test_E19_this_suite_cannot_reach_real_storage(self):
        for name in ("ensure_pull", "mark_local_edit"):
            with self.assertRaises(AssertionError, msg=name):
                getattr(store_sync, name)("x")


class Probe(unittest.TestCase):
    def test_probe_runs_offline_read_only_with_zero_fmp_calls(self):
        import config
        from tools import panel_probe
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "portfolio.json")
        f = {k: v for k, v in vars(tfacts()).items() if k not in ("ticker", "tax_rate")}
        with open(path, "w") as fh:
            json.dump({"holdings": {"T0": {}}, "facts": {"T0": f},
                       "results": {"T0": {"key_metrics": {"wacc_pct": 8.0}}}}, fh)
        def state():
            with open(path) as fh:
                return os.path.getmtime(path), fh.read()
        before = state()
        cache = config.CACHE_DIR
        try:
            rep = panel_probe.main([], path=path, today=TODAY)
        finally:
            config.CACHE_DIR = cache
        self.assertEqual(rep["T0"].get("fmp_calls"), 0, rep)
        self.assertEqual(state(), before)
        self.assertTrue(rep["T0"]["flags"], "offline, every missing input is named")


if __name__ == "__main__":
    unittest.main()


FIX_AS_OF = "2026-05-15"
FIX_SYMS = ("MSFT", "ORCL", "AVGO", "ABBV")


def fixture_cf(sym):
    import config
    with open(os.path.join(config.FIXTURE_DIR, sym, "sec_companyfacts.json"), encoding="utf-8") as fh:
        return json.load(fh)


def fixture_frames(syms=FIX_SYMS, as_of=FIX_AS_OF):
    """({CY: {cik: [revenue, end]}}, {sym: cik}) from the fixtures' own CY-tagged annual
    revenue, filed on or before as_of, the larger concept per filer."""
    import re
    out, ciks = {}, {}
    for s in syms:
        cf = fixture_cf(s)
        ciks[s] = int(cf["cik"])
        ns = cf["facts"]["us-gaap"]
        for c in PP.REVENUE_CONCEPTS:
            for e in ((ns.get(c) or {}).get("units") or {}).get("USD") or []:
                m = re.match(r"^CY(\d{4})$", str(e.get("frame") or ""))
                if m and e.get("filed", "") <= as_of:
                    row = out.setdefault(int(m.group(1)), {})
                    if e["val"] > row.get(ciks[s], [0])[0]:
                        row[ciks[s]] = [e["val"], e["end"]]
    return out, ciks


class MatchedShare(unittest.TestCase):
    """SPEC R13: the share change is measured on the filers reporting in both years."""

    def test_R13_share_change_on_the_matched_set_fixture(self):
        fr, ciks = fixture_frames()
        self.assertNotIn(ciks["ORCL"], fr[2025], "fixture premise: ORCL (May FY) has no CY2025 frame yet")
        self.assertIn(ciks["ORCL"], fr[2022])
        sh = PP.industry_share({s: str(c) for s, c in ciks.items()}, fr, ciks["MSFT"], Y5, FIX_AS_OF)
        m = ciks["MSFT"]
        self.assertAlmostEqual(sh["target_share"]["2025"], fr[2025][m][0] / sum(v[0] for v in fr[2025].values()),
                               msg="the per-year share stays on that year's own filers (display)")
        sig, _ = MT.share_change(sh)
        self.assertAlmostEqual(sig["value"], 0.0114, places=4)
        self.assertEqual(sig["direction"], 0, "+1.1% on the matched set is inside the 10% band")
        both = [c for c in fr[2025] if c in fr[2022]]
        then = fr[2022][m][0] / sum(fr[2022][c][0] for c in both)
        now = fr[2025][m][0] / sum(fr[2025][c][0] for c in both)
        self.assertEqual(sh["matched_n"], 3)
        self.assertAlmostEqual(sh["matched_share_then"], then)
        self.assertAlmostEqual(sh["matched_share_now"], now)
        self.assertAlmostEqual(sh["share_change_pp"], (now - then) * 100)
        self.assertTrue(any("CY2022..CY2025" in f and "both years" in f for f in sh["flags"]))

    def test_R13_matched_set_below_70pct_of_the_older_year_nulls_the_change(self):
        members = {f"T{i}": str(i + 1).zfill(10) for i in range(10)}
        rows = {i + 1: [10.0] * 5 for i in range(10)}
        for c in (9, 10):
            rows[c] = [10.0, None, 10.0, 10.0, 10.0]                # 8 filers in CY2022
        for c in (6, 7, 8):
            rows[c] = [10.0, 10.0, 10.0, 10.0, None]
        rows[9] = rows[10] = [10.0, None, 10.0, 10.0, 10.0]          # CY2025: 1-5, 9, 10 (7 of 10)
        sh = PP.industry_share(members, frames_for(Y5, rows), "0000000001", Y5, AS_OF)
        self.assertIsNotNone(sh["target_share"]["2025"], "each year passes its own coverage")
        self.assertEqual(sh["matched_n"], 5)                         # 5 of 8 = 62.5%
        self.assertIsNone(sh["share_change_pp"])
        self.assertIsNone(sh["top5_change_pp"])
        self.assertTrue(any("5 of the 8 CY2022 filers" in f for f in sh["flags"]))
        rows[6] = [10.0] * 5                                         # 6 of 8 = 75%
        sh = PP.industry_share(members, frames_for(Y5, rows), "0000000001", Y5, AS_OF)
        self.assertEqual(sh["matched_n"], 6)
        self.assertIsNotNone(sh["share_change_pp"])


class NullFlags(unittest.TestCase):
    def test_F3_a_null_metric_names_row_metric_and_reason_ORCL(self):
        cf = fixture_cf("ORCL")
        cik, ext_ = peers.resolve_cik, peers.sec_edgar.extract
        peers.resolve_cik = lambda s: ("0001341439", s)
        try:
            p, why = peers.peer_point("ORCL", dt.date(2026, 5, 15), fetch_facts=lambda c: cf, complete=None,
                                      fetch_price=lambda s, d: (160.0, "2026-05-14", "fixed"))
        finally:
            peers.resolve_cik, peers.sec_edgar.extract = cik, ext_
        self.assertIsNone(why)
        panel = PP.build(pp("T"), [p], FIX_AS_OF, "x", "y")
        row = next(r for r in panel["table"]["rows"] if r["symbol"] == "ORCL")
        self.assertIsNone(row["gross_margin_fy"])
        end = p["fy0"]["end"]
        self.assertIn(f"ORCL gross_margin_fy: no GrossProfit or CostOfRevenue for FY0 ({end})", panel["flags"])
        nulls = [(r["symbol"], m) for r in panel["table"]["rows"] for m in PP.METRICS if r[m] is None]
        for s, m in nulls:
            self.assertTrue(any(f.startswith(f"{s} {m}: ") for f in panel["flags"]), (s, m))

    def test_F3_each_reason_names_the_missing_leg(self):
        t = pp("T", ni=-1.0, ebit=-1.0)
        panel = PP.build(t, [pp(f"P{i}") for i in range(3)], AS_OF, "x", "y")
        self.assertIn("T pe: TTM net income not positive", panel["flags"])
        self.assertIn("T ev_ebit: TTM EBIT not positive", panel["flags"])


class FiledAfterAsOf(unittest.TestCase):
    def test_F4_a_filing_after_as_of_says_so(self):
        from domain import relative
        x = ext(latest_period_end="2027-03-31")
        p, why = relative.point("P", x, 20.0, "2026-11-13", TODAY)
        self.assertIsNone(p)
        self.assertIn("after as-of", why)
        self.assertNotIn("older than", why)
        p, why = relative.point("P", ext(latest_period_end="2025-12-31"), 20.0, "2026-11-13", TODAY)
        self.assertIn("older than 6 months", why)
