"""Alpha Vantage's estimates come in the reporting currency with no label, and AV
carries the same consensus as Yahoo. tools/consensus_check 2026-10-25: ASML read
53.23 on AV (EUR) beside Yahoo's 59.80 (USD); AV's growth matched Yahoo's to the
4th decimal on NVDA, ABBV, GOOGL and UNH."""
import unittest

import config
import store
from domain.facts import FinancialFacts
from pipeline import fundamentals as fu

ASML_AV = {"estimates": [
    {"date": "2026-12-31", "horizon": "fiscal year", "eps_estimate_average": "47.10"},
    {"date": "2027-12-31", "horizon": "fiscal year", "eps_estimate_average": "57.90"}]}


def facts(t, price, fy, fx=None):
    ff = FinancialFacts(t)
    ff.price, ff.fiscal_year = price, fy
    if fx:
        ff.set("fx", fx, "yahoo")
    return ff


class AlphaVantageBackup(unittest.TestCase):
    def setUp(self):
        self.keep = (config.ALPHAVANTAGE_API_KEY, config.FINNHUB_API_KEY, fu._av_estimates, store.today)
        config.ALPHAVANTAGE_API_KEY, config.FINNHUB_API_KEY = "K", ""
        store.today = lambda: "2026-10-25"
        self.asked = []
        fu._av_estimates = lambda t: (self.asked.append(t) or ASML_AV, 1)

    def tearDown(self):
        config.ALPHAVANTAGE_API_KEY, config.FINNHUB_API_KEY, fu._av_estimates, store.today = self.keep

    def test_non_usd_filer_never_takes_av_eps(self):
        ff = facts("ASML", 1571.8, "2026-09-30", fx=1.1204)
        fu._blend_forward_eps(ff, "ASML", None)
        self.assertIsNone(ff.forward_eps)
        self.assertEqual(self.asked, [])                       # no AV call spent either
        self.assertTrue(any("non-USD filer" in x for x in ff.flags), ff.flags)

    def test_usd_filer_av_eps_is_labelled_single_feed(self):
        ff = facts("ORCL", 129.59, "2026-08-31")
        fu._blend_forward_eps(ff, "ORCL", None)
        self.assertEqual(ff.provenance.get("forward_eps"), "consensus-blend(alphavantage)")
        self.assertTrue(any(fu.AV_SAME_FEED in x for x in ff.flags), ff.flags)

    def test_non_usd_falls_to_last_good(self):
        prev = {"forward_eps": 59.80, "forward_eps_as_of": "2026-10-20",
                "provenance": {"forward_eps": "consensus-blend(yahoo)"}}
        ff = facts("ASML", 1571.8, "2026-09-30", fx=1.1204)
        fu._blend_forward_eps(ff, "ASML", None, prev)
        self.assertEqual(ff.forward_eps, 59.80)
        self.assertTrue(ff.provenance["forward_eps"].startswith("last-good"))


if __name__ == "__main__":
    unittest.main()
