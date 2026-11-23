"""Debt read off one balance-sheet date (E60-E63, 2026-11-23). Each test
reads a trimmed real companyfacts file (tests/fixtures/sec_debt, cut from the 2026-11-21 SEC
download by build.py) through the real sources.sec_edgar.extract. E60-E62 were seen red on
68e908a, E63 on the first draft of this fix (it dropped UTHR's FY2014). Expected values are the 10-K's balance-sheet lines, written out by hand."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sources import sec_edgar as S  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sec_debt")
M = 1e6


def extract(t):
    with open(os.path.join(FIX, t + ".json")) as fh:
        return S.extract(json.load(fh))


def ic_debt(t):
    return {d: v["debt"] for d, v in extract(t)["ic_components_dated"].items()}


class Debt(unittest.TestCase):
    def test_E60_tmdx_convertible_years_stay_in_invested_capital(self):
        # FY2024 10-K balance sheet: convertible senior notes, net 449,939 / 447,140 and
        # long-term debt, net 59,372 / 59,064 ($K, 2024 / 2023). The FY2023 switch from
        # LongTermDebtNoncurrent to ConvertibleDebtNoncurrent dropped FY2023-FY2025.
        d = ic_debt("TMDX")
        self.assertEqual(d["2023-12-31"], 447140000 + 59064000)
        self.assertEqual(d["2024-12-31"], 449939000 + 59372000)
        self.assertIn("2025-12-31", d)
        self.assertIn("2022-12-31", d)

    def test_E61_a_combined_total_is_never_summed_with_its_current_part(self):
        # LLY FY2024 10-K: short-term borrowings and current maturities 5,117.1 + long-term
        # debt 28,527.1 = 33,644.2 ($M); DebtLongtermAndShorttermCombinedAmount 33,644.0.
        # The base added DebtCurrent on top: 38,761.
        d = ic_debt("LLY")
        self.assertAlmostEqual(d["2024-12-31"] / M, 5117.1 + 28527.1, delta=1.0)
        self.assertAlmostEqual(d["2023-12-31"] / M, 6904.5 + 18320.8, delta=1.0)

    def test_E61_notes_payable_current_is_not_added_beside_the_current_maturities(self):
        # AXON 2025-09-30: LongTermDebt 2,008.9 = noncurrent 1,729.4 + current 279.6, and
        # NotesPayableCurrent repeats the 279.6. The base counted it twice (2,288.5).
        self.assertAlmostEqual(extract("AXON")["total_debt_prior"] / M, 1729.4 + 279.6, delta=0.2)

    def test_E62_current_maturities_are_read_beside_the_noncurrent_line(self):
        # ABBV FY2024: long-term debt and finance lease obligations 60,340 + current portion
        # 6,804 ($M). The base read the noncurrent line alone for invested capital.
        d = ic_debt("ABBV")
        self.assertEqual(d["2024-12-31"], (60340 + 6804) * M)

    def test_E62_a_total_below_its_own_part_is_not_the_total(self):
        # BKD 2025-12-31: ...IncludingCurrentMaturities 357.8 beside LongTermDebtNoncurrent
        # 4,215.0, current 77.5 and finance leases 25.6 ($M)
        self.assertAlmostEqual(ic_debt("BKD")["2025-12-31"] / M, 4215.0 + 77.5 + 25.6, delta=0.2)

    def test_E63_a_year_with_only_current_debt_keeps_its_debt(self):
        # UTHR 2014-12-31: convertible notes payable, current 126.4 ($M) and no long-term line
        self.assertAlmostEqual(ic_debt("UTHR")["2014-12-31"] / M, 126.4, delta=0.1)


    # ---- E64-E68 (round 3, 2026-11-23): each was seen red on a604340 ----

    def test_E64_finance_leases_inside_the_debt_lines_are_not_added_again(self):
        # HCA 10-K FY2024: the lease note classifies finance lease liabilities under "Long-term
        # debt due within one year" and "Long-term debt"; total debt 43,031 / 39,593 ($M,
        # 2024 / 2023). a604340 added FinanceLeaseLiability 786 / 707 on top.
        d = ic_debt("HCA")
        self.assertEqual(d["2024-12-31"], 43031 * M)
        self.assertEqual(d["2023-12-31"], 39593 * M)

    def test_E64_finance_leases_outside_the_debt_lines_are_still_added(self):
        # AAPL FY2025 10-K: term debt 78,328 + 12,350, commercial paper 7,979; finance lease
        # liabilities 1,230 sit in other current / non-current liabilities ($M)
        self.assertEqual(ic_debt("AAPL")["2025-09-27"], (78328 + 12350 + 7979 + 1230) * M)
        # MSFT FY2026: long-term debt 31,067 + current 9,227; finance leases 66,594 outside it
        self.assertEqual(ic_debt("MSFT")["2026-06-30"], (31067 + 9227 + 66594) * M)

    def test_E64_an_equal_lease_inclusive_twin_is_no_sign_of_leases_inside(self):
        # ASML 2024-12-31 files LongTermDebtNoncurrent and LongTermDebtAndCapitalLeaseObligations
        # both at 3,677.3 (EUR M). Its 20-F note 16 totals long-term debt at 4,687.6 (current
        # 1,010.3) with no lease line, and note 14 puts leases in accrued and other liabilities:
        # the 16.9 finance lease is outside the debt lines and is added.
        self.assertAlmostEqual(ic_debt("ASML")["2024-12-31"] / M, 3677.3 + 1010.3 + 16.9, delta=0.2)

    def test_E65_a_year_with_a_balance_sheet_and_no_debt_is_zero_debt(self):
        # AXON had no borrowings from FY2019 to FY2021 (its 0.50% convertible notes were issued
        # in December 2022); TTWO had none at FY2020-21 (March year ends)
        d = ic_debt("AXON")
        for y in ("2019-12-31", "2020-12-31", "2021-12-31"):
            self.assertIn(y, d)
            self.assertEqual(d[y], 0)
        t = ic_debt("TTWO")
        self.assertEqual(t["2020-03-31"], 0)
        self.assertEqual(t["2021-03-31"], 0)

    def test_E66_lease_only_debt_is_the_same_in_total_debt_and_invested_capital(self):
        # INCY has no borrowings; finance leases 4.1 + 27.6 at 2026-09-30, 38.0 at 2024-12-31
        # ($M). a604340 booked them in invested capital and left total_debt None.
        e = extract("INCY")
        self.assertAlmostEqual(e["total_debt"] / M, 4.1 + 27.6, delta=0.1)
        self.assertAlmostEqual(e["ic_components_dated"]["2024-12-31"]["debt"] / M, 38.0, delta=0.1)

    def test_E66_debt_raised_with_no_debt_line_filed_is_unread_not_leases(self):
        # Ford tags its borrowings in its own extension concepts: at 2024-12-31 it files
        # InterestPayableCurrent 1,346 while its only us-gaap debt concept is
        # FinanceLeaseLiability 805 ($M). a604340 read 805 as Ford's debt (ROIC 56-133%).
        e = extract("F")
        self.assertIsNone(e["total_debt"])
        for y in ("2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"):
            self.assertNotIn(y, e["ic_components_dated"] or {})

    def test_E67_a_combined_principal_amount_yields_to_the_carrying_lines(self):
        # AVGO FY2024 10-K borrowings note: principal 69,847, unamortized discount and issuance
        # costs (2,320); balance sheet 66,295 + 1,271 = 67,566 ($M). FY2025: 65,136.
        d = ic_debt("AVGO")
        self.assertEqual(d["2024-11-03"], 67566 * M)
        self.assertEqual(d["2025-11-02"], 65136 * M)

    def test_E68_a_fiscal_year_end_reads_the_annual_report_not_a_10q_comparative(self):
        # AVGO's 10-Q of 2022-06-09 re-tags FY2021 LongTermDebtNoncurrent as 41,235 (principal);
        # the 10-Ks file 39,440. The FY2021 10-K's lines (39,440 + 290 current) hold the finance
        # leases (lease note: 26 current, 39 long-term), so none is added; the FY2022 10-K
        # restates the current portion as 264 without leases, the figure read: 39,704.
        self.assertEqual(ic_debt("AVGO")["2021-10-31"], (39440 + 264) * M)
        # TTM total debt (2026-08-02): carrying lines 59,419, not the combined principal 61,079
        self.assertEqual(extract("AVGO")["total_debt"], 59419 * M)


    def test_E72_debt_tagged_where_the_tree_does_not_read_is_unknown_not_leases(self):
        # Cinemark 10-K FY2025: borrowings only as DebtInstrumentCarryingAmount 1,897.3 and
        # extension concepts, no interest payable; FinanceLeaseLiability 110.2 ($M). a6eacdf
        # read the leases as total debt (a 94% understatement that looked like a value).
        e = extract("CNK")
        self.assertIsNone(e["total_debt"])
        self.assertIsNone(e["total_debt_prior"])
        self.assertTrue(any("DebtInstrumentCarryingAmount" in n and "หนี้" in n for n in e["xbrl_flags"]),
                        e["xbrl_flags"])
        # a debt-free year with no such evidence is still 0 (AXON FY2019-21, E65)
        d = ic_debt("AXON")
        self.assertEqual([d[y] for y in ("2019-12-31", "2020-12-31", "2021-12-31")], [0, 0, 0])

    def test_E72_evidence_tags_are_never_read_as_values(self):
        for c in ("DebtInstrumentCarryingAmount", "DebtInstrumentFaceAmount", "LongTermDebtFairValue",
                  "SecuredDebt", "UnsecuredDebt", "LinesOfCreditCurrent", "OtherLongTermDebt"):
            self.assertIn(c, S.UNREAD_DEBT)
            self.assertNotIn(c, S.DEBT_CONCEPTS)

    def test_E73_longtermdebt_means_what_the_filing_it_came_from_says(self):
        # Biogen's 10-K tags LongTermDebt as the total (6,295.8 = 4,547.2 + 1,748.6 at
        # 2024-12-31); its 10-Qs tag it as the noncurrent line (4,547.2 for the same date).
        # Q3 2026 10-Q: LongTermDebt 7,292.0 + current portion of term loan 400.0 = 7,692.0.
        self.assertEqual(extract("BIIB")["total_debt"], 7692 * M)
        d = ic_debt("BIIB")
        self.assertEqual(d["2024-12-31"], 6295.8 * M)

    def test_E74_a_year_with_evidence_of_unread_debt_is_not_debt_free(self):
        # REGN FY2017: capital and facility lease obligations 703.5 in an extension concept,
        # finance-lease interest 19.5 for the year; NVO 2015: current debt DKK 1,073M, and
        # companyfacts holds no liabilities side for the date. Both read 0 at a6eacdf.
        self.assertNotIn("2017-12-31", ic_debt("REGN"))
        self.assertNotIn("2015-12-31", ic_debt("NVO"))
        self.assertIn("2018-12-31", ic_debt("REGN"))
        self.assertIn("2018-12-31", ic_debt("NVO"))

    def test_E74_a_debt_balance_filed_on_the_date_overrides_a_line_filed_at_zero(self):
        # UTHR 10-K FY2024: LinesOfCreditCurrent 300.0 beside LongTermLineOfCredit 0; CELH 10-K
        # FY2016: NotesPayableRelatedPartiesNoncurrent 4.5 beside ConvertibleNotesPayable 0.
        # Both read 0 at a1905a9. UTHR FY2025 files the line of credit at 0: debt-free.
        self.assertNotIn("2024-12-31", ic_debt("UTHR"))
        self.assertEqual(ic_debt("UTHR")["2025-12-31"], 0)
        self.assertNotIn("2016-12-31", ic_debt("CELH"))
        self.assertNotIn("2015-12-31", ic_debt("CELH"))

if __name__ == "__main__":
    unittest.main()
