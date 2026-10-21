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


def test_outyear_reacceleration_held():
    print("2 a consensus out-year that jumps >10pp above the year before is held (TSLA, MSFT)")
    tsla = _facts(rev_growth_path=[[0.1294, 32], [0.1307, 32], [0.1866, 32], [0.5424, 30], [0.0545, 16]])
    held = []
    p = E.consensus_growth_path(tsla, held)
    check(p[3] == 0.1866, "TSLA FY2029 +54% held at the year before (18.7%)", str(p))
    check(p[4] == 0.0545 and len(held) == 1, "the slower year 5 is kept and the hold is reported")
    msft = _facts(rev_growth_path=[[0.178, 39], [0.1963, 39], [0.2165, 27], [0.1747, 13], [0.3007, 13]])
    p = E.consensus_growth_path(msft)
    check(p[4] == 0.1747 and p[2] == 0.2165, "MSFT year 5 +30% held at 17.5%; +2pp in year 3 is kept", str(p))
    near = _facts(rev_growth_path=[[0.10, 20], [0.35, 20]])
    check(E.consensus_growth_path(near)[1] == 0.30, "years 1-2 are not jump-tested (only capped)")


if __name__ == "__main__":
    run_suite(globals(), "ALL test_round8_2026_10_21 PASSED")
