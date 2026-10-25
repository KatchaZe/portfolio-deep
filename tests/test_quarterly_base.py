"""pipeline/quarterly.py: a TTM base for 20-F filers from Alpha Vantage quarterly
statements, checked by Yahoo. Fixtures are the live answers of 2026-10-24 (first 8
quarters) and the stored facts of 2026-10-23 for ASML, NVO and TSM."""
import copy
import dataclasses
import json
import os
import unittest

from domain.facts import FinancialFacts
from pipeline import dataquality, quarterly
from sources import alphavantage as av, yahoo

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "quarterly")
RATE = {"EUR": 1.1199, "DKK": 0.1498, "TWD": 0.0307}
FIELDS = {f.name for f in dataclasses.fields(FinancialFacts)}


def load(t):
    with open(os.path.join(FIX, f"{t}.json"), encoding="utf-8") as fh:
        raw = json.load(fh)
    ff = FinancialFacts(t)
    for k, v in raw["facts"].items():
        if k in FIELDS:
            setattr(ff, k, v)
    inc, bs = av.parse_quarters(raw["INCOME_STATEMENT"]), av.parse_quarters(raw["BALANCE_SHEET"])
    return ff, inc, bs, yahoo.parse_quarterly_timeseries(raw["yahoo"])


def run(t, mutate=None):
    ff, inc, bs, yq = load(t)
    if mutate:
        mutate(ff, inc, bs, yq)
    ccy = next(q["currency"] for _, q in sorted(inc.items(), reverse=True) if q.get("currency"))
    alt, notes = quarterly.build(ff, inc, bs, {}, yq, ccy, RATE[ccy])
    applied = alt is not None and quarterly.apply(ff, alt, notes, ff.currency, None, lambda c: RATE[c])
    return ff, alt, notes, applied


class RealData(unittest.TestCase):
    def test_asml_ttm_to_q3_2026(self):
        ff, alt, notes, ok = run("ASML")
        self.assertTrue(ok)
        self.assertEqual(ff.fiscal_year, "2026-09-30")
        # 11.662 + 9.3265 + 8.7669 + 9.7181 = 39.4735B EUR (the four reported quarters)
        self.assertAlmostEqual(ff.revenue, 39.4735e9 * 1.1199, delta=1e6)
        self.assertAlmostEqual(ff.operating_income, (5.1728 + 3.4561 + 3.1578 + 3.4311) * 1e9 * 1.1199, delta=1e6)
        # Q3's balance sheet leaves the debt lines blank: the filed year-end debt stays
        self.assertAlmostEqual(ff.total_debt, 4936051064.25, delta=1)
        self.assertTrue(any("balance as filed at 2025-12-31" in n and "total_debt" in n for n in notes))
        self.assertTrue(any("2026-09-30 from Alpha Vantage only" in n for n in notes))

    def test_tsm_blank_currency_quarter_named(self):
        ff, alt, notes, ok = run("TSM")
        self.assertTrue(ok)
        self.assertAlmostEqual(ff.revenue / 1e9, (1493.425963 + 1270.38025 + 1134.10344 + 1055.976591) * 0.0307, delta=0.01)
        self.assertTrue(any("no currency for 2026-09-30; read as TWD" in n for n in notes))

    def test_nvo_one_off_quarter_named_not_removed(self):
        ff, alt, notes, ok = run("NVO")
        self.assertTrue(ok)
        self.assertEqual(ff.fiscal_year, "2026-06-30")
        self.assertTrue(any("2026-03-31" in n and "one-time gain is not removed" in n for n in notes))
        # NVO's filed debt + IFRS 16 leases is reproduced by "total debt + leases"
        self.assertIn("total debt + leases", ff.provenance["total_debt"])
        self.assertIsNone(ff.operating_leases)

    def test_series_kept_and_interim_warning_gone(self):
        ff0, *_ = load("ASML")
        series = copy.deepcopy(ff0.revenue_annuals_dated)
        self.assertTrue(any(d["code"] == "interim_not_used" for d in dataquality.assess(ff0)))
        ff, *_ = run("ASML")
        self.assertEqual(ff.revenue_annuals_dated, series)
        self.assertFalse(any(d["code"] == "interim_not_used" for d in dataquality.assess(ff)))

    def test_fields_that_do_not_reproduce_are_cleared_not_mixed(self):
        ff, alt, notes, ok = run("TSM")
        # FMP's FY2025 tax line is not what Alpha Vantage sums to: not moved, cleared
        self.assertIsNone(ff.tax_expense)
        self.assertIsNone(ff.eps_gaap)          # a FY per-share figure beside a TTM base
        self.assertTrue(any("cleared" in n and "tax_expense" in n for n in notes))


class Refusals(unittest.TestCase):
    def assertRefused(self, t, mutate, why):
        seen = {}

        def m(ff, inc, bs, yq):
            mutate(ff, inc, bs, yq)
            seen.update(fy=ff.fiscal_year, revenue=ff.revenue)
        ff, alt, notes, ok = run(t, m)
        self.assertIsNone(alt)
        self.assertIn(why, notes)
        self.assertEqual((ff.fiscal_year, ff.revenue), (seen["fy"], seen["revenue"]))

    def test_calibration_must_reproduce_the_filed_year(self):
        self.assertRefused("ASML", lambda ff, i, b, y: setattr(ff, "revenue", ff.revenue * 1.03),
                           "does not reproduce the filed")

    def test_wrong_currency_quarter(self):
        def m(ff, inc, bs, yq):
            inc["2026-06-30"]["currency"] = "USD"
        self.assertRefused("ASML", m, "reports USD, not EUR")

    def test_two_blank_currencies(self):
        def m(ff, inc, bs, yq):
            inc["2026-06-30"]["currency"] = None
            inc["2026-03-31"]["currency"] = None
        self.assertRefused("ASML", m, "2 quarters carry no currency")

    def test_yahoo_disagreement_refuses_the_base(self):
        def m(ff, inc, bs, yq):
            yq["2026-06-30"]["revenue"] *= 1.03
        self.assertRefused("ASML", m, "2026-06-30 revenue")

    def test_missing_quarter_breaks_the_window(self):
        def m(ff, inc, bs, yq):
            del inc["2026-03-31"]
        self.assertRefused("NVO", m, "four consecutive")

    def test_nothing_after_the_filed_year(self):
        def m(ff, inc, bs, yq):
            for e in [e for e in inc if e > "2025-12-31"]:
                del inc[e]
        self.assertRefused("NVO", m, "no quarter after 2025-12-31")


class Plumbing(unittest.TestCase):
    def test_run_spends_nothing_when_current_and_says_so_without_a_key(self):
        import tempfile
        ff, *_ = load("ASML")
        self.assertEqual(quarterly.run(ff, "ASML", "EUR", 1.1199, "", tempfile.mkdtemp(), lambda c: 1), 0)
        self.assertTrue(any("ALPHAVANTAGE_API_KEY is not set" in f for f in ff.flags))
        ff2, *_ = load("ASML")
        ff2.fiscal_year = "2026-09-30"          # current base: nothing to do, no flag
        self.assertEqual(quarterly.run(ff2, "ASML", "EUR", 1.1199, "KEY", "/nonexistent", lambda c: 1), 0)
        self.assertFalse(any(f.startswith("QUARTERLY") for f in ff2.flags))

    def test_a_cached_answer_serves_without_a_key(self):
        import tempfile
        tmp = tempfile.mkdtemp()
        with open(os.path.join(FIX, "ASML.json"), encoding="utf-8") as fh:
            raw = json.load(fh)
        for fn, name in (("INCOME_STATEMENT", "q_income_statement"), ("BALANCE_SHEET", "q_balance_sheet")):
            with open(os.path.join(tmp, f"av_{name}_ASML.json"), "w") as fh:
                json.dump({"at": 9e12, "data": raw[fn]}, fh)
        data, calls = av.cached("q_income_statement", "ASML", "", lambda s, k: {}, tmp)
        self.assertEqual((bool(data.get("quarterlyReports")), calls), (True, 0))

    def test_throttle_reply_is_never_cached_as_data(self):
        """Live 2026-10-24: 12 tickers had AV's '1 request per second' reply cached as
        their EARNINGS history for 7 days."""
        import tempfile
        tmp = tempfile.mkdtemp()
        info = {"Information": "Please consider spreading out your free API requests (1 request per second)."}
        data, calls = av.cached("earnings", "ABBV", "k", lambda s, k: info, tmp, sleep=lambda x: None)
        self.assertIn("1 request per second", data["_refused"])
        self.assertFalse(os.path.exists(os.path.join(tmp, "av_earnings_ABBV.json")))
        # an old cache holding a refusal is ignored and refetched
        with open(os.path.join(tmp, "av_earnings_PFE.json"), "w") as fh:
            json.dump({"at": 9e12, "data": info}, fh)
        good = {"quarterlyEarnings": [{"fiscalDateEnding": "2026-06-30"}]}
        data, calls = av.cached("earnings", "PFE", "k", lambda s, k: good, tmp, sleep=lambda x: None)
        self.assertEqual((data, calls), (good, 1))

    def test_the_api_key_never_reaches_the_card(self):
        key = "AQLRI7NYDZVDC1FH"
        msg = {"Information": f"We have detected your API key as {key} and our standard API "
                              "rate limit is 25 requests per day."}
        why = av.refusal(msg)
        self.assertEqual(why, "Alpha Vantage daily limit reached")
        self.assertNotIn(key, why)

    def test_a_base_older_than_15_months_also_gets_the_quarterly_path(self):
        """TSM, live 2026-10-24: FY2024 SEC base (stale_financials, not interim_not_used)."""
        ff, *_ = load("TSM")
        ff.fiscal_year = "2024-12-31"
        orig, seen = av.cached, []
        av.cached = lambda *a, **k: (seen.append(a[0]) or {"_refused": "Alpha Vantage daily limit reached"}, 0)
        try:
            quarterly.run(ff, "TSM", "USD", None, "KEY", "/tmp", lambda c: 0.0307)
        finally:
            av.cached = orig
        self.assertTrue(seen)
        self.assertTrue(any("daily limit" in f for f in ff.flags))

    def test_calls_are_spaced_and_capped_per_day(self):
        import tempfile
        tmp, waits = tempfile.mkdtemp(), []
        good = {"quarterlyReports": [{}]}
        for i in range(3):
            av.cached("q", f"S{i}", "k", lambda s, k: good, tmp, sleep=waits.append)
        self.assertEqual(len(waits), 2)                 # the 2nd and 3rd waited
        self.assertTrue(all(0 < w <= av.MIN_GAP_SECS for w in waits))
        with open(os.path.join(tmp, "av_usage.json")) as fh:
            u = json.load(fh)
        u["n"] = av.DAILY_CAP
        with open(os.path.join(tmp, "av_usage.json"), "w") as fh:
            json.dump(u, fh)
        data, calls = av.cached("q", "LATE", "k", lambda s, k: good, tmp, sleep=lambda x: None,
                                priority=True)
        self.assertEqual(calls, 0)
        self.assertIn("daily limit", data["_refused"])
        # a cross-check stops earlier, leaving the reserve for the statements
        u["n"] = av.DAILY_CAP - av.RESERVED_FOR_PRIORITY
        with open(os.path.join(tmp, "av_usage.json"), "w") as fh:
            json.dump(u, fh)
        self.assertIn("_refused", av.cached("earnings", "X", "k", lambda s, k: good, tmp,
                                            sleep=lambda x: None)[0])
        self.assertEqual(av.cached("q", "Y", "k", lambda s, k: good, tmp, sleep=lambda x: None,
                                   priority=True), (good, 1))

    def test_refused_statement_says_why_on_the_card(self):
        ff, *_ = load("ASML")
        orig = av.cached
        av.cached = lambda *a, **k: ({"_refused": "Alpha Vantage daily limit (24) reached"}, 0)
        try:
            quarterly.run(ff, "ASML", "EUR", 1.1199, "KEY", "/tmp", lambda c: 1.1199)
        finally:
            av.cached = orig
        self.assertTrue(any("daily limit" in f for f in ff.flags if f.startswith("QUARTERLY")))
        self.assertEqual(ff.fiscal_year, "2025-12-31")


class FmpFallbackCache(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pipeline import fundamentals as fu
        self.fu, self.tmp = fu, tempfile.mkdtemp()
        self._cd, fu.config.CACHE_DIR = fu.config.CACHE_DIR, self.tmp
        self._fetch, self._parse, self._apply = fu.fmp.fetch, fu.fmp.parse, fu.dataquality.apply_stale_fallback
        self.fetched = 0

        def fetch(t, key):
            self.fetched += 1
            return {"income_annual": [{"revenue": 1}], "balance_annual": [{}], "cashflow_annual": [{}],
                    "income_ttm": {"_status": 402, "_body": "premium"}, "_calls": 8}
        fu.fmp.fetch, fu.fmp.parse = fetch, lambda b, alt: None
        fu.dataquality.apply_stale_fallback = lambda *a, **k: (True, ["applied"])

    def tearDown(self):
        fu = self.fu
        fu.config.CACHE_DIR = self._cd
        fu.fmp.fetch, fu.fmp.parse, fu.dataquality.apply_stale_fallback = self._fetch, self._parse, self._apply

    def stale(self):
        ff, *_ = load("TSM")
        ff.fiscal_year = "2024-12-31"
        return ff

    def test_budget_short_is_said_not_silent(self):
        from pipeline import batch
        ff = self.stale()
        calls = self.fu._stale_financials_fallback(ff, "TSM", "TSMC", "k", "USD", None, batch.Budget(0))
        self.assertEqual((calls, self.fetched), (0, 0))
        self.assertTrue(any("โควตา FMP" in f for f in ff.flags))

    def test_bundle_cached_for_a_week_so_no_budget_is_needed(self):
        from pipeline import batch
        # 8 calls really made are counted, and a premium-refused TTM endpoint does not
        # stop the core statements being cached
        self.assertEqual(self.fu._stale_financials_fallback(self.stale(), "TSM", "TSMC", "k", "USD", None,
                                                            batch.Budget(9)), 8)
        ff = self.stale()
        self.assertEqual(self.fu._stale_financials_fallback(ff, "TSM", "TSMC", "k", "USD", None,
                                                            batch.Budget(0)), 0)
        self.assertEqual(self.fetched, 1)
        self.assertIn("applied", ff.flags)

    def test_an_error_bundle_is_not_cached(self):
        self.fu._store_fallback_bundle("X", {"income_annual": {"_status": 402, "_body": "limit"},
                                             "balance_annual": [{}], "cashflow_annual": [{}]})
        self.assertIsNone(self.fu._cached_fallback_bundle("X"))


if __name__ == "__main__":
    unittest.main()
