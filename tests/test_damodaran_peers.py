"""Damodaran company list (indname) peers, SEC-frames size matching, the psdata
industry multiple, and the watchlist's cache. Fixture: the US rows of indname.xls for
the holdings' industries, SEC revenue frames CY2025 and the stored facts, all live
answers of 2026-10-23/24."""
import datetime as dt
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from domain import relative as R
from pipeline import batch, lastgood, peers
from sources import damodaran, damodaran_companies as dc
import store

TODAY = dt.date(2026, 10, 24)
FIX = os.path.join(os.path.dirname(__file__), "fixtures", "universe", "universe_2026-10-24.json")
PS = {f: damodaran._psdata_fallback(f) for f in damodaran.PSDATA_COLUMNS}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FIX, encoding="utf-8") as fh:
            cls.fx = json.load(fh)
        cls.u = peers.Universe(cls.fx["rows"], {int(k): v for k, v in cls.fx["frames"].items()},
                               PS, as_of="2026-01")
        cls._cik = peers.resolve_cik
        peers.resolve_cik = lambda t: ((str(cls.fx["ticker_cik"][t]).zfill(10), t)
                                       if t in cls.fx["ticker_cik"] else (None, None))

    @classmethod
    def tearDownClass(cls):
        peers.resolve_cik = cls._cik

    def target(self, t):
        d = dict(self.fx["targets"][t])
        d["provenance"] = {"price": "yahoo close 2026-10-22 (T-1)"}
        return SimpleNamespace(ticker=t, **d)


class Industry(Base):
    def test_damodaran_classification_wins_for_us_listings(self):
        self.assertEqual(self.u.industry_of(self.target("GOOGL")),
                         ("Software (Entertainment)", "Damodaran company list"))
        self.assertEqual(self.u.industry_of(self.target("ABBV"))[0], "Drugs (Biotechnology)")
        self.assertEqual(self.u.industry_of(self.target("HIMS"))[0], "Healthcare Support Services")

    def test_adrs_fall_back_to_the_app_mapping_and_say_so(self):
        ind, basis = self.u.industry_of(self.target("TSM"))
        self.assertEqual(ind, "Semiconductor")
        self.assertIn("app industry mapping", basis)

    def test_googl_known_answer_at_the_industry_multiple(self):
        f = self.target("GOOGL")
        tp, nd, _ = peers._target(f, TODAY)
        b = self.u.industry_block(f, "Software (Entertainment)", "x", tp, nd)
        self.assertEqual((b["ev_sales"], b["n"]), (9.13, 77))
        self.assertAlmostEqual(b["value_avg"], (9.13 * f.revenue - nd) / f.shares_diluted, places=1)
        self.assertAlmostEqual(b["value_avg"], 341.57, delta=0.05)
        self.assertAlmostEqual(b["margin_ratio"], 0.98, delta=0.01)
        self.assertAlmostEqual(b["value_margin_adj"], 336.06, delta=0.1)

    def test_margin_adjustment_guards(self):
        tp = {"ev_sales": 5, "margin": 0.33, "revenue": 100}
        out = R.industry_implied(tp, 2.12, 0.1005, 0, 10)        # GOOGL vs Advertising: 3.3x
        self.assertIsNone(out["value_margin_adj"])
        self.assertIn("not margin-adjusted", out["flags"][0])
        out = R.industry_implied(dict(tp, margin=-0.06), 0.46, 0.03, 0, 10)
        self.assertIn("negative margin", out["flags"][0])
        out = R.industry_implied(tp, 1.0, 0.3, 1000, 10)          # net debt above EV
        self.assertIsNone(out["value_avg"])


class Candidates(Base):
    def test_orcl_gets_nine_software_peers_by_size(self):
        c, note = self.u.candidates(self.target("ORCL"), "Software (System & Application)")
        self.assertIsNone(note)
        self.assertEqual(set(c), {"CRM", "ADBE", "INTU", "MSFT", "NOW", "WDAY", "PANW", "ROP", "ADSK"})

    def test_hims_gets_health_services_not_cosmetics(self):
        c, _ = self.u.candidates(self.target("HIMS"), "Healthcare Support Services")
        self.assertEqual(len(c), 12)
        self.assertFalse({"COTY", "EL", "ELF"} & set(c))
        self.assertTrue({"AMN", "ALHC", "ADUS"} <= set(c))

    def test_a_giant_widens_the_band_and_says_so(self):
        c, note = self.u.candidates(self.target("GOOGL"), "Software (Entertainment)")
        self.assertIn("META", c)
        self.assertTrue(note)

    def test_target_itself_and_non_us_rows_are_never_peers(self):
        c, _ = self.u.candidates(self.target("NVDA"), "Semiconductor")
        self.assertNotIn("NVDA", c)
        self.assertTrue(all(self.fx["rows"][t][2] == "United States" for t in c))

    def test_foreign_domicile_rows_are_excluded_even_with_sec_revenue(self):
        self.assertEqual(dc.members({"AAA": ["Semiconductor", "3674", "Israel"],
                                     "BBB": ["Semiconductor", "3674", "United States"]}, "Semiconductor"),
                         ["BBB"])

    def test_every_candidate_is_inside_the_band(self):
        for t in ("ORCL", "HIMS", "CELH", "AVGO"):
            f = self.target(t)
            ind = self.u.industry_of(f)[0]
            c, note = self.u.candidates(f, ind)
            lo, hi = (0.02, 50) if note else (0.1, 10)
            for p in c:
                r = self.fx["frames"][str(self.fx["ticker_cik"][p])] / f.revenue
                self.assertTrue(lo <= r <= hi, (t, p, r))


class Flow(Base):
    """relative_value: Damodaran peers first, FMP only below five."""

    def setUp(self):
        self.calls = {"list": 0}
        self._pp = peers.peer_point

        def fake_point(sym, today, **_):
            m = 0.05 + (hash(sym) % 30) / 100
            return {"symbol": sym, "ev_sales": 1 + 20 * m, "margin": m, "growth": (hash(sym) % 7) / 20,
                    "revenue": 1e9, "period_end": "2026-06-30", "price_date": "2026-10-22",
                    "sic": ["7372", "x"], "addback": 0.0}, None
        peers.peer_point = fake_point

    def tearDown(self):
        peers.peer_point = self._pp

    def fetch_list(self, t, key):
        self.calls["list"] += 1
        return ["FMP1", "FMP2", "FMP3", "FMP4", "FMP5"]

    def test_enough_damodaran_peers_spend_no_fmp_call(self):
        block, calls = peers.relative_value(self.target("ORCL"), "k", TODAY, None, batch.Budget(5),
                                            None, self.u, cache_dir=tempfile.mkdtemp(),
                                            fetch_list=self.fetch_list)
        self.assertEqual((calls, self.calls["list"]), (0, 0))
        self.assertTrue(all(p["source"] == "damodaran" for p in block["model"]["peers"]))
        self.assertEqual(block["industry"]["industry"], "Software (System & Application)")

    def test_below_five_fills_from_fmp_and_tags_them(self):
        rows = {t: self.fx["rows"][t] for t in ("GM", "F", "TSLA")}      # two Damodaran peers
        u = peers.Universe(rows, self.u.rev, PS, as_of="2026-01")
        block, calls = peers.relative_value(self.target("TSLA"), "k", TODAY, None, batch.Budget(5),
                                            None, u, cache_dir=tempfile.mkdtemp(),
                                            fetch_list=self.fetch_list)
        self.assertEqual(calls, 1)
        srcs = {p["source"] for p in block["model"]["peers"]}
        self.assertEqual(srcs, {"damodaran", "fmp"})
        self.assertTrue(any("filled from the FMP list" in n for n in block["notes"]))

    def test_industry_block_survives_when_no_fit(self):
        empty = peers.Universe({"ZZZZ": ["Semiconductor", "3674", "United States"]}, {}, PS, as_of="2026-01")
        # an empty cache dir: a peer list cached by an earlier live run must not leak in
        block, _ = peers.relative_value(self.target("NVDA"), "", TODAY, None, None, None, empty,
                                        cache_dir=tempfile.mkdtemp())
        self.assertIsNone(block["model"])
        self.assertIsNotNone(block["industry"]["value_avg"])

    def test_target_without_fy_series_still_gets_the_industry_multiple(self):
        """TSM, live 2026-10-24: the FMP fallback cleared its fiscal-year series."""
        f = self.target("TSM")
        f.revenue_annuals_dated = []
        block, _ = peers.relative_value(f, "", TODAY, None, None, None, self.u, cache_dir=tempfile.mkdtemp())
        self.assertIsNotNone(block["industry"]["value_avg"])
        self.assertIn("app industry mapping", block["industry"]["basis"])
        if block["model"] and "growth" in block["model"]["terms"]:
            self.assertIsNone(block["value_regression"])
            self.assertIn("no fiscal-year growth", block["flags"][0])

    def test_a_fit_from_the_old_method_is_not_reused(self):
        old = {"model": {"as_of": "2026-10-23", "n": 7, "peers": [], "coef": [1, 1], "terms": ["margin"]},
               "excluded": [], "notes": []}                 # no version: the FMP-only fit
        block, _ = peers.relative_value(self.target("ORCL"), "k", TODAY, old, batch.Budget(5), None,
                                        self.u, cache_dir=tempfile.mkdtemp(), fetch_list=self.fetch_list)
        self.assertEqual(block["model"]["version"], peers.MODEL_VERSION)
        again, _ = peers.relative_value(self.target("ORCL"), "k", TODAY, block, batch.Budget(5), None,
                                        self.u, cache_dir=tempfile.mkdtemp(), fetch_list=self.fetch_list)
        self.assertIs(again["model"], block["model"])        # same method, same week: reused

    def test_financial_firms_get_no_ev_multiple(self):
        u = peers.Universe({"SOFI": ["Financial Svcs. (Non-bank & Insurance)", "6199", "United States"]},
                           {}, PS, as_of="2026-01")
        f = self.target("ORCL")
        f.ticker = "SOFI"
        block, calls = peers.relative_value(f, "k", TODAY, None, None, None, u, cache_dir=tempfile.mkdtemp(),
                                            fetch_list=self.fetch_list)
        self.assertEqual((block["model"], block["industry"], calls, self.calls["list"]), (None, None, 0, 0))
        self.assertIn("does not apply to a financial firm", block["notes"][0])

    def test_universe_failure_never_breaks_the_refresh(self):
        def boom():
            raise OSError("stern.nyu.edu down")
        u = peers.Universe(loader=boom)
        block, _ = peers.relative_value(self.target("ORCL"), "", TODAY, None, None, None, u,
                                        cache_dir=tempfile.mkdtemp())
        self.assertIsNone(block["industry"])
        self.assertTrue(any("peer universe unavailable" in n for n in block["notes"]))


class Sources(unittest.TestCase):
    def test_row_parser_keeps_us_listings_only(self):
        head = ["Company Name", "Exchange:Ticker", "Industry Group", "Primary Sector", "SIC Code",
                "Country", "Broad Group", "Sub Group"]
        rows = [head] + [["x", f"NYSE:T{i}", "Semiconductor", "", 3674.0, "United States", "", ""]
                         for i in range(dc.MIN_ROWS)] + [["y", "LSE:FOUR", "Advertising", "", 7330.0, "UK", "", ""]]
        t = dc.rows_to_table(rows)
        self.assertEqual(t["T0"], ["Semiconductor", "3674", "United States"])
        self.assertNotIn("FOUR", t)
        with self.assertRaises(ValueError):
            dc.rows_to_table(rows[:10])                           # a layout change, not data

    def test_indname_failure_falls_back_to_the_cache_with_a_date(self):
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, dc.INDNAME_CACHE), "w") as fh:
            json.dump({"at": 0, "fetched": "2026-02-01", "rows": {"A": ["x", "1", "United States"]}}, fh)

        class Down:
            def get(self, *a, **k):
                raise ConnectionError("down")
        rows, note = dc.load_indname(tmp, requests_mod=Down())
        self.assertEqual(rows, {"A": ["x", "1", "United States"]})
        self.assertIn("2026-02-01", note)
        rows, note = dc.load_indname(tempfile.mkdtemp(), requests_mod=Down())
        self.assertIsNone(rows)
        self.assertIn("unavailable", note)

    def test_frames_take_the_larger_revenue_concept_and_cache(self):
        tmp = tempfile.mkdtemp()
        answers = {"Revenues": [{"cik": 1, "val": 10}, {"cik": 2, "val": 5}],
                   "RevenueFromContractWithCustomerExcludingAssessedTax": [{"cik": 1, "val": 7}, {"cik": 3, "val": 9}]}

        class R:
            def get(self, url, **k):
                c = url.split("/us-gaap/")[1].split("/")[0]
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": answers[c]})
        rev, note = dc.load_revenue_frames(tmp, 2025, "ua", requests_mod=R())
        self.assertEqual(rev, {1: 10, 2: 5, 3: 9})
        self.assertEqual(dc.frames_year(dt.date(2026, 3, 1)), 2024)
        self.assertEqual(dc.frames_year(dt.date(2026, 10, 1)), 2025)

    def test_psdata_label_follows_where_the_table_came_from(self):
        tmp = tempfile.mkdtemp()

        class Down:
            def get(self, *a, **k):
                raise ConnectionError("down")
        self.assertTrue(damodaran.fetch_psdata(tmp, requests_mod=Down())["snapshot"])
        with open(os.path.join(tmp, "damodaran_psdata_ev_sales.json"), "w") as fh:
            json.dump({"fetched": 0, "table": {"semiconductor": 15.7}}, fh)
        self.assertFalse(damodaran.fetch_psdata(tmp, requests_mod=Down())["snapshot"])

    def test_psdata_snapshot_is_labelled_as_bundled(self):
        ps = {f: damodaran._psdata_fallback(f) for f in damodaran.PSDATA_COLUMNS}
        ps["snapshot"] = True
        self.assertEqual(damodaran.industry_multiple(ps, "Software (Entertainment)"),
                         {"ev_sales": 9.13, "margin": 0.3367, "n": 77.0})
        self.assertIsNone(damodaran.industry_multiple(ps, "No Such Industry"))


class Watchlist(unittest.TestCase):
    def test_subset_keeps_what_last_good_reads(self):
        facts = {"rev_growth_path": [[0.1, 20]], "consensus_as_of": "2026-10-20",
                 "revenue_annuals_dated": [["2025-12-31", 100], ["2024-12-31", 90]],
                 "provenance": {"rev_growth_path": "consensus fmp", "forward_eps": "consensus yahoo",
                                "price": "x"},
                 "forward_eps": 5.0, "forward_eps_as_of": "2026-10-20", "fwd_growth_y1": 0.1,
                 "big_series": list(range(1000))}
        sub = lastgood.subset(facts)
        ff = SimpleNamespace(revenue_annuals_dated=[["2025-12-31", 100]])
        self.assertEqual(lastgood.consensus_path(sub, ff, TODAY)[:2],
                         lastgood.consensus_path(facts, ff, TODAY)[:2])
        self.assertEqual(lastgood.forward_eps(sub, TODAY), lastgood.forward_eps(facts, TODAY))
        self.assertNotIn("big_series", sub)

    def test_cache_drops_names_no_longer_watched(self):
        s = {"watchlist": ["AAA"], "watch_cache": {"OLD": {"at": "x"}}}
        store.commit_watch_cache(s, {"AAA": {"at": "2026-10-24"}})
        self.assertEqual(set(s["watch_cache"]), {"AAA"})


if __name__ == "__main__":
    unittest.main()
