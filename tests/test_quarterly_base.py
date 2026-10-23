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
    def test_run_spends_nothing_without_a_key_or_when_current(self):
        ff, *_ = load("ASML")
        self.assertEqual(quarterly.run(ff, "ASML", "EUR", 1.1199, "", "/nonexistent", lambda c: 1), 0)
        ff.fiscal_year = "2026-09-30"           # current base: no interim warning
        self.assertEqual(quarterly.run(ff, "ASML", "EUR", 1.1199, "KEY", "/nonexistent", lambda c: 1), 0)

    def test_throttle_reply_is_never_cached_as_data(self):
        class Fake:
            status_code = 200
            def json(self):
                return {"Information": "rate limit"}
        class C:
            def get(self, url, **kw):
                return Fake()
        self.assertEqual(av.fetch_statement("INCOME_STATEMENT", "ASML", "k", requests_mod=C()), {})


if __name__ == "__main__":
    unittest.main()
