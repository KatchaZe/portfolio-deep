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


class OneTimeGains(unittest.TestCase):
    """E79. The add-back added costs back and never took a gain out."""

    def test_E79_hca_fy2021_facility_gain_is_inside_ebit_and_comes_out(self):
        # HCA release 2022-01-27 (dollars in millions): "Losses (gains) on sales of
        # facilities (1,620)" among the expense lines above income before income taxes.
        from domain import one_time
        from domain.facts import FinancialFacts
        d = extract("HCA_gains")
        self.assertIn(["2021-12-31", 1620000000], d["gain_annuals_dated"])
        ff = FinancialFacts(ticker="HCA")
        ff.revenue_annuals_dated = d["revenue_annuals_dated"]
        ff.gain_annuals_dated = d["gain_annuals_dated"]
        self.assertEqual(one_time.annual_addbacks(ff)["2021-12-31"], -1620000000)

    def test_E79_a_gain_the_filers_lines_do_not_place_is_named_not_subtracted(self):
        d = extract("SNPS_gains")
        self.assertIsNone(d["gain_ttm"])
        self.assertTrue(any("GainLossOnSaleOfBusiness" in f and "EBIT keeps it" in f for f in d["xbrl_flags"]))

    def test_E79_a_ttm_gain_comes_out_capped_like_an_addback(self):
        # NVST Q3 2026 (release 2026-11-04): a $428.1M realized cumulative translation gain
        # inside operating profit; TTM EBIT read $757M in the scout run.
        from domain import one_time
        from domain.facts import FinancialFacts
        ff = FinancialFacts(ticker="NVST")
        ff.set("revenue", 2600e6, "sec")
        ff.set("operating_income", 757e6, "sec")
        ff.gain_ttm = 428.1e6
        self.assertAlmostEqual(one_time.normalize_operating_income(ff), -428.1e6)
        self.assertAlmostEqual(ff.operating_income, 328.9e6)
        self.assertEqual(ff.operating_income_gaap, 757e6)
        self.assertTrue(any("-$0.43B one-time gain" in f for f in ff.flags))
        ff2 = FinancialFacts(ticker="X")
        ff2.set("revenue", 1000e6, "sec")
        ff2.set("operating_income", 400e6, "sec")
        ff2.gain_ttm = 390e6
        self.assertAlmostEqual(one_time.normalize_operating_income(ff2), -250e6)

    def test_E88_nvst_sum_of_lines_places_the_gain_with_no_total_opex_line(self):
        # NVST Q3 2026 (release 2026-11-04, $M): gross profit 400.6, SG&A 284.2, R&D 29.3,
        # operating profit 515.2 with no total-opex line. 400.6 - 284.2 - 29.3 = 87.1, so
        # 515.2 - 87.1 = 428.1 is the realized translation gain: inside operating income.
        M = 1e6
        lines = S._opex_line_sums(lambda c: {"SellingGeneralAndAdministrativeExpense": 284.2 * M,
                                             "ResearchAndDevelopmentExpense": 29.3 * M}.get(c))
        self.assertTrue(S._gain_inside(428.1 * M, 515.2 * M, 650 * M, 400.6 * M, None, None, lines))
        # the same through the reader, the quarter filed as one annual period
        cf = {"cik": 1757073, "facts": {"us-gaap": {
            c: {"units": {"USD": [dict(_inst("2025-12-31", v * M, form="10-K", filed="2026-02-20"),
                                       start="2025-01-01", fy=2025, fp="FY")]}}
            for c, v in (("Revenues", 650.0), ("GrossProfit", 400.6), ("OperatingIncomeLoss", 515.2),
                         ("SellingGeneralAndAdministrativeExpense", 284.2),
                         ("ResearchAndDevelopmentExpense", 29.3),
                         ("OtherComprehensiveIncomeReclassificationAdjustmentForSaleOfForeignEntitiesBeforeTax", 428.1),
                         ("Liabilities", 900.0))}}}
        self.assertIn(["2025-12-31", 428.1 * M], S.extract(cf)["gain_annuals_dated"])

    def test_E88_sum_of_lines_never_places_a_gain_the_lines_already_explain(self):
        M = 1e6
        get = {"SellingGeneralAndAdministrativeExpense": 284.2 * M, "ResearchAndDevelopmentExpense": 29.3 * M}.get
        lines = S._opex_line_sums(get)
        # operating income equals GP - SG&A - R&D: the gain is below the line, not inside
        self.assertFalse(S._gain_inside(428.1 * M, 87.1 * M, 650 * M, 400.6 * M, None, None, lines))
        # a residual 20% off the gain is not the gain
        self.assertFalse(S._gain_inside(428.1 * M, (87.1 + 1.2 * 428.1) * M, 650 * M, 400.6 * M, None, None, lines))
        # no listed opex line at all: nothing to sum, nothing placed
        self.assertEqual(S._opex_line_sums(lambda c: None), [])
        self.assertFalse(S._gain_inside(428.1 * M, 515.2 * M, 650 * M, 400.6 * M, None, None, []))
        # SG&A filed as a total AND as its parts is counted once (the total)
        both = S._opex_line_sums({"SellingGeneralAndAdministrativeExpense": 284.2 * M,
                                  "SellingAndMarketingExpense": 200 * M,
                                  "GeneralAndAdministrativeExpense": 84.2 * M,
                                  "ResearchAndDevelopmentExpense": 29.3 * M}.get)
        self.assertAlmostEqual(both[0], 313.5 * M)

    def _hxl(self, with_ooi=True):
        # HXL FY2022 10-K (accn 0000950170-23-002099, $M): gross profit 357.1, SG&A 148.0,
        # R&D 45.8, other operating income 11.9, operating income 175.2; notes: amortization
        # 6.8, restructuring 7.6, gain on disposition of assets 19.4. The face reconciles
        # through the other-operating line (357.1 - 148.0 - 45.8 + 11.9 = 175.2), and that
        # line is the gain net of restructuring (19.4 - 7.6 = 11.8 ~ 11.9).
        M = 1e6
        vals = [("Revenues", 1577.7), ("GrossProfit", 357.1), ("OperatingIncomeLoss", 175.2),
                ("SellingGeneralAndAdministrativeExpense", 148.0), ("ResearchAndDevelopmentExpense", 45.8),
                ("AmortizationOfIntangibleAssets", 6.8), ("RestructuringCharges", 7.6),
                ("GainLossOnDispositionOfAssets1", 19.4), ("Liabilities", 900.0)]
        if with_ooi:
            vals.append(("OtherOperatingIncomeExpenseNet", 11.9))
        cf = {"cik": 717605, "facts": {"us-gaap": {
            c: {"units": {"USD": [dict(_inst("2022-12-31", v * M, form="10-K", filed="2023-02-10"),
                                       start="2022-01-01", fy=2022, fp="FY")]}}
            for c, v in vals}}}
        return S.extract(cf)["gain_annuals_dated"]

    def test_E88_hxl_gain_placed_through_its_other_operating_line(self):
        self.assertIn(["2022-12-31", 19.4e6], self._hxl())

    def test_E88_hxl_without_the_line_is_declined_not_matched_by_amortization(self):
        # SG&A + R&D + amortization leave 18.7 against 19.4 (3.6% off): a notes-only line
        # summed to a near miss is a coincidence, so the gain is not placed
        self.assertEqual(self._hxl(with_ooi=False), [])
        M = 1e6
        lines = S._opex_line_sums({"SellingGeneralAndAdministrativeExpense": 148.0 * M,
                                   "ResearchAndDevelopmentExpense": 45.8 * M,
                                   "AmortizationOfIntangibleAssets": 6.8 * M}.get)
        self.assertFalse(S._gain_inside(19.4 * M, 175.2 * M, 1577.7 * M, 357.1 * M, None, None, lines))
        # the other-operating line reconciles the face but does not hold the gain: declined
        self.assertFalse(S._gain_inside(19.4 * M, 175.2 * M, 1577.7 * M, 357.1 * M, None, None, lines,
                                        11.9 * M, []))


def _inst(end, val, form="10-Q", filed="2026-05-07"):
    return {"end": end, "val": val, "form": form, "filed": filed, "accn": "x"}


class DebtMisses(unittest.TestCase):
    """E80. Debt the reader missed or read short."""

    def test_E80_qcom_reads_its_balance_sheet_lines_not_the_notes_only_total(self):
        # 10-K FY2026 (2026-09-27, $M): long-term debt 12,620, short-term debt 2,492 (1,993
        # current notes + 499 commercial paper); DebtLongtermAndShorttermCombinedAmount 14,613.
        self.assertEqual(extract("QCOM_debt")["total_debt"], 15112e6)

    def test_E80_acu_mortgage_in_an_extension_concept_leaves_debt_unknown(self):
        # ACU 10-Q Q1 2026: 'Long-term debt' 33,030 and 'Current portion of mortgage payable'
        # 458 are us-gaap; 'Mortgage payable, net of current portion' 9,362 ($K) is not.
        cf = {"cik": 2098, "facts": {"us-gaap": {
            "Revenues": {"units": {"USD": [dict(_inst("2026-03-31", 45e6), start="2026-01-01")]}},
            "OperatingIncomeLoss": {"units": {"USD": [dict(_inst("2026-03-31", 2e6), start="2026-01-01")]}},
            "LongTermDebtNoncurrent": {"units": {"USD": [_inst("2026-03-31", 33030e3)]}},
            "SecuredDebtCurrent": {"units": {"USD": [_inst("2026-03-31", 458e3)]}},
            "Liabilities": {"units": {"USD": [_inst("2026-03-31", 90e6)]}}}}}
        d = S.extract(cf)
        self.assertIsNone(d["total_debt"])
        self.assertTrue(any("ACU 10-Q Q1 2026" in f for f in d["xbrl_flags"]))
        cf["cik"] = 1                              # the same facts at any other filer are read
        self.assertEqual(S.extract(cf)["total_debt"], 33488e3)


class CashDefinition(unittest.TestCase):
    """E81. Cash = cash and equivalents + current marketable securities, for every filer."""

    def test_E81_intu_current_investments_count_as_cash(self):
        # INTU's 10-Q for 2026-10-31 files CashAndCashEquivalentsAtCarryingValue 4,042M and
        # its current AFS debt only as ...ExcludingAccruedInterestCurrent 1,279M ($M, as filed).
        self.assertEqual(extract("INTU_cash")["cash"], 5321e6)

    def test_E81_noncurrent_and_equity_investments_are_not_cash(self):
        d = extract("INTU_cash")
        with open(os.path.join(FIX, "INTU_cash.json")) as fh:
            g = json.load(fh)["facts"]["us-gaap"]
        lti = max(g["LongTermInvestments"]["units"]["USD"], key=lambda e: e["end"])["val"]
        self.assertNotEqual(d["cash"], 5321e6 + lti)
        self.assertNotIn("EquitySecuritiesFvNi", S.STI_TAGS)


if __name__ == "__main__":
    unittest.main()
