"""test_round8_2026_10_21 — fair-value audit of 2026-10-21 (round 8).

Each test fails on the code before its fix (main 451db98).

Run: python -m tests.test_round8_2026_10_21
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.facts import FinancialFacts             # noqa: E402
from domain.engine import deep_v82 as E             # noqa: E402
from sources import sec_edgar                       # noqa: E402
from tests._harness import check, run_suite         # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RF = 0.0513


def _facts(ticker="NEWCO", **kw):
    f = FinancialFacts(ticker)
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def test_revenue_top_line_beats_contract_subset():
    print("1 revenue: the income-statement total beats ASC 606 contract revenue (MELI)")
    alts = [("Revenues", 35.18e9)]
    v, c, rest = sec_edgar._prefer_top_line(None, 24.46e9, sec_edgar.REV[0], alts)
    check(c == "Revenues" and v == 35.18e9, "MELI: Revenues $35.18B replaces contract $24.46B", f"{c} {v}")
    check((sec_edgar.REV[0], 24.46e9) in rest, "the contract figure stays listed as the conflict")
    v, c, _ = sec_edgar._prefer_top_line(None, 0.61e9, sec_edgar.REV[0],
                                         [("RevenuesNetOfInterestExpense", 4.31e9)])
    check(c == "RevenuesNetOfInterestExpense", "a bank's net revenue is a top line (SOFI)")
    v, c, _ = sec_edgar._prefer_top_line(None, 50e9, sec_edgar.REV[0], [("Revenues", 45e9)])
    check(c == sec_edgar.REV[0], "a SMALLER Revenues figure does not replace contract revenue")


if __name__ == "__main__":
    run_suite(globals(), "ALL test_round8_2026_10_21 PASSED")
