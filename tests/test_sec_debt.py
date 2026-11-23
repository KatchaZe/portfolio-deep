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


if __name__ == "__main__":
    unittest.main()
