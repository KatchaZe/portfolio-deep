"""SEC extract fixes S1-S5 (2026-11-21). Each test reads a trimmed real companyfacts file
(tests/fixtures/sec_s, cut from the 2026-11-21 SEC download by build.py) through the real
sources.sec_edgar.extract, and each was seen red before its fix. Expected values are
arithmetic on the facts as filed, written out by hand."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sources import sec_edgar as S  # noqa: E402
from verify import reference as R  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sec_s")


def extract(t):
    with open(os.path.join(FIX, t + ".json")) as fh:
        return S.extract(json.load(fh))


def flags(d):
    return " | ".join(d.get("xbrl_flags") or [])


class SecExtractS(unittest.TestCase):
    def test_S1_proxy_statement_facts_never_replace_the_10k(self):
        # INGN's DEF 14A (filed 2026-04-28) tags FY2025 NetIncomeLoss +22.747M; the 10-K
        # says -22.747M. TTM = FY2025 -22.747 + 9M2026 -28.329 - 9M2025 -15.620
        d = extract("INGN")
        self.assertEqual(d["net_income"], -22747000 - 28329000 + 15620000)
        self.assertIn("DEF 14A", flags(d))
        self.assertIn("net_income", flags(d))

    def test_S1_a_clean_file_carries_no_form_note(self):
        self.assertNotIn("non-periodic", flags(extract("BJ")))

    def test_S2_stale_operating_income_concept_falls_to_pretax_plus_interest(self):
        # ASTS last tagged OperatingIncomeLoss for the quarter ending 2021-03-31
        d = extract("ASTS")
        self.assertTrue(d["operating_income_derived"])
        self.assertEqual(d["operating_income"], -817851000 + 88900000)
        self.assertIn("OperatingIncomeLoss", flags(d))
        self.assertIn("2021-03-31", flags(d))

    def test_S2_klac_and_jnj_operating_margin_is_current(self):
        # KLAC OperatingIncomeLoss ends 2015-03-31 (margin read 4.4%); JNJ's 2015-03-29
        self.assertEqual(extract("KLAC")["operating_income"], 6120531000 + 281292000)
        self.assertEqual(extract("JNJ")["operating_income"], 17737000000 + 984000000)

    def test_S3_dds_fallback_reads_the_current_pretax_concept(self):
        # DDS's first PRETAX concept was last filed for 2025-11-01; the other is current
        d = extract("DDS")
        self.assertEqual(d["operating_income"], 840372000 + 41056000)

    def test_S4_interest_concept_with_the_newest_period_wins(self):
        # LLY: InterestExpense ends 2024-03-31; InterestExpenseNonoperating is filed for
        # FY2025 (annually only), so TTM pretax + the FY2025 interest
        d = extract("LLY")
        self.assertEqual(d["operating_income"], 34769000000 + 895000000)
        oi = dict(d["operating_income_annuals_dated"])
        self.assertEqual(oi["2025-12-31"], 25731000000 + 895000000)
        self.assertEqual(oi["2024-12-31"], 12680000000 + 781000000)
        # years before the new concept keep the old one's interest
        self.assertEqual(oi["2021-12-31"], 6155500000 + 339800000)
        # KLAC: InterestExpense TTM ends 2024-03-31 (301.9M), Nonoperating is current
        self.assertEqual(extract("KLAC")["operating_income"] - 6120531000, 281292000)

    def test_S5_leases_are_read_off_one_balance_sheet_date(self):
        # BJ: OperatingLeaseLiability 93.6M at 2026-07-31 is a footnote figure; the
        # balance sheet of 2026-08-01 carries noncurrent 1,956.2M + current 187.9M
        d = extract("BJ")
        self.assertEqual(d["operating_leases"], 1956213000 + 187865000)
        self.assertEqual(d["operating_leases_prior"], 1959378000 + 173521000)   # 2025-08-02
        # DG: total filed only in the 10-K (2026-01-30); legs at 2026-07-31
        d = extract("DG")
        self.assertEqual(d["operating_leases"], 9630280000 + 1572005000)
        self.assertEqual(d["operating_leases_prior"], 9820261000 + 1502571000)  # 2025-08-01



def reference(t):
    with open(os.path.join(FIX, t + ".json")) as fh:
        return R.company_reference(json.load(fh), symbol=t)


class Reference(unittest.TestCase):
    """verify/reference.py, the independent verifier, on the same trimmed facts."""

    def test_S1_reference_ignores_the_proxy_statement(self):
        self.assertEqual(reference("INGN")["net_income_ttm"], -22747000 - 28329000 + 15620000)

    def test_reference_ttm_follows_the_latest_ytd_when_quarters_disagree(self):
        # INGN's Q3 10-Q: 9M2026 233.122M; its standalone Q1 + Q2 were never re-filed
        self.assertEqual(reference("INGN")["revenue_ttm"], 348668000 + 233122000 - 226732000)

    def test_reference_revenue_stays_on_one_concept_across_years(self):
        # OPCH's FY2025 10-K files FY2022 at 3,869.0M (ASC 606) and 3,944.7M (Revenues)
        r = reference("OPCH")
        self.assertEqual(r["revenue_fy3"], 3869036000)
        self.assertAlmostEqual(r["cagr3_fy"], (5649519000 / 3869036000) ** (1 / 3) - 1, places=12)

    def test_reference_balance_sheet_is_the_newest_one(self):
        # TGT stopped tagging CashAndCashEquivalentsAtCarryingValue in 2017
        r = reference("TGT")
        self.assertEqual(r["balance_date"], "2026-08-01")
        self.assertEqual(r["cash"], 5411000000)

    def test_reference_cash_includes_current_debt_securities(self):
        # NVDA 2026-07-26: cash 22,443M + DebtSecuritiesCurrent 34,143M
        self.assertEqual(reference("NVDA")["cash"], 22443000000 + 34143000000)


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=1).result
    sys.exit(0 if r.wasSuccessful() else 1)
