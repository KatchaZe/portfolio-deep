"""Shared-reader fixes from the Sector scout audit (E77-E81, 2026-11-25).

Each test reads a trimmed real companyfacts file (tests/fixtures/sec_shares, cut from the
2026-11-21 SEC download) or a scout part read by the user's own run, through the real code.
All were seen red on 809b652. Expected values are the filer's own release, cited per test."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from domain import relative  # noqa: E402
from sources import sec_edgar as S  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "sec_shares")


def extract(name, folder=FIX):
    with open(os.path.join(folder, name + ".json")) as fh:
        return S.extract(json.load(fh))


class ShareScale(unittest.TestCase):
    """E77. A share count tagged with its table's unit (thousands, millions)."""

    def test_E77_loar_count_filed_in_thousands_is_restated(self):
        # Q3 2026 release, 2026-11-05 ("amounts in thousands"): diluted weighted shares
        # 95,647 for the nine months. The 10-Q (0001193125-26-448708) tags 95,647 shares.
        d = extract("LOAR")
        self.assertEqual(d["shares_diluted"], 95647000)
        self.assertTrue(any("in thousands" in f and "95,647" in f for f in d["xbrl_flags"]))

    def test_E77_mcd_count_filed_in_millions_is_restated(self):
        # Q2 2026 release, 2026-08-04 ("shares in millions"): diluted 712.3 for six months.
        d = extract("MCD")
        self.assertAlmostEqual(d["shares_diluted"], 712.3e6, delta=1)
        self.assertTrue(any("in millions" in f for f in d["xbrl_flags"]))

    def test_E77_a_count_that_disagrees_with_the_cover_page_is_unknown(self):
        with open(os.path.join(FIX, "LOAR.json")) as fh:
            cf = json.load(fh)
        for e in cf["facts"]["us-gaap"]["WeightedAverageNumberOfDilutedSharesOutstanding"]["units"]["shares"]:
            e["val"] = e["val"] * 31          # neither the count nor a unit slip of it
        d = S.extract(cf)
        self.assertIsNone(d["shares_diluted"])
        self.assertIn("disagree with cover-page shares", d["shares_unknown_reason"])

    def test_E77_net_income_over_eps_alone_never_blanks_a_count(self):
        # a two-class filer's EPS is per Class A share; with no cover page it decides nothing
        with open(os.path.join(FIX, "LOAR.json")) as fh:
            cf = json.load(fh)
        cf["facts"].pop("dei")
        for e in cf["facts"]["us-gaap"]["WeightedAverageNumberOfDilutedSharesOutstanding"]["units"]["shares"]:
            e["val"] = e["val"] * 2500
        self.assertEqual(S.extract(cf)["shares_diluted"], 95647 * 2500)


class ImplausibleMarketCap(unittest.TestCase):
    """E77. The scout's own run read NTNX at 292,183 shares (its 10-K table figure, in
    thousands: Q3 FY2026 release 2026-05-27 guides ~292 million diluted shares)."""

    X = {"currency": "USD", "revenue": 2853545000, "operating_income": 273970000,
         "latest_period_end": "2026-07-31", "shares_diluted": 292183, "total_debt": 1351445000,
         "operating_leases": 170023000, "cash": 2361463000,
         "revenue_annuals_dated": [["2026-07-31", 2853545000], ["2025-07-31", 2537927000]]}

    def test_E77_ntnx_market_cap_is_refused_with_the_reason(self):
        p, why = relative.point("NTNX", self.X, 57.56, "2026-11-23", "2026-11-24", check_base=False)
        self.assertIsNone(p)
        self.assertTrue(why.startswith("share count implausible"))

    def test_E77_a_real_count_passes(self):
        x = dict(self.X, shares_diluted=292183000)
        p, why = relative.point("NTNX", x, 57.56, "2026-11-23", "2026-11-24", check_base=False)
        self.assertIsNone(why)
        self.assertAlmostEqual(p["market_cap"] / 1e9, 16.82, places=2)


class TwoClassShares(unittest.TestCase):
    """E78. VERX and YOU (Up-C) tag diluted counts per class; companyfacts keeps no
    dimensioned fact, so the scout read no share count and no reason. Constructed from the
    LOAR fixture with its weighted count removed (no cached filer lacks one)."""

    def _cf(self):
        with open(os.path.join(FIX, "LOAR.json")) as fh:
            cf = json.load(fh)
        cf["facts"]["us-gaap"].pop("WeightedAverageNumberOfDilutedSharesOutstanding")
        return cf

    def test_E78_an_undimensioned_cover_count_is_the_total(self):
        cf = self._cf()
        cover = cf["facts"]["dei"]["EntityCommonStockSharesOutstanding"]["units"]["shares"]
        top = max(cover, key=lambda e: (e.get("filed", ""), e["end"]))
        cover.append(dict(top, val=1000000))          # a second class on the same cover
        d = S.extract(cf)
        self.assertEqual(d["shares_diluted"], top["val"] + 1000000)
        self.assertTrue(any("every class filed on that cover summed" in f for f in d["xbrl_flags"]))

    def test_E78_no_count_at_all_says_why(self):
        cf = self._cf()
        cf["facts"].pop("dei")
        d = S.extract(cf)
        self.assertIsNone(d["shares_diluted"])
        self.assertIn("two-class or Up-C", d["shares_unknown_reason"])
        p, why = relative.point("VERX", dict(ImplausibleMarketCap.X, shares_diluted=None,
                                             shares_unknown_reason=d["shares_unknown_reason"]),
                                17.92, "2026-11-23", "2026-11-24", check_base=False)
        self.assertIsNone(p)
        self.assertIn("two-class or Up-C", why)


if __name__ == "__main__":
    unittest.main()
