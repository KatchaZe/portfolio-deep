"""Regression checks for the 2026-10 Damodaran audit fixes (one section per item)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import check, run_suite   # noqa: E402


def _fact(start, end, val, filed=None):
    return {"start": start, "end": end, "val": val, "filed": filed or end, "form": "10-Q"}


def _companyfacts(extra):
    rev = [_fact("2025-01-01", "2025-12-31", 100e9), _fact("2026-01-01", "2026-06-30", 60e9),
           _fact("2025-01-01", "2025-06-30", 50e9)]
    ns = {"RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": rev}}}
    ns.update(extra)
    return {"facts": {"us-gaap": ns}}


def test_f1_dead_tag_never_wins():
    from sources import sec_edgar as S
    dead = [_fact("2020-01-01", "2020-12-31", 0.455e9)]
    live = [_fact("2025-01-01", "2025-12-31", 6.0e9), _fact("2026-01-01", "2026-06-30", 3.5e9),
            _fact("2025-01-01", "2025-06-30", 2.9e9)]
    d = S.extract(_companyfacts({
        "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": dead}},
        "PaymentsToAcquireProductiveAssets": {"units": {"USD": live}}}))
    check(abs(d["capex"] - 6.6e9) < 1, "capex comes from the tag still being filed (6.6B), "
          "not the first ladder tag frozen in 2020", "got %r" % d["capex"])
    d = S.extract(_companyfacts({"PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": dead}}}))
    check(d["capex"] is None, "a tag dead for years reads as missing, not as today's value",
          "got %r" % d["capex"])
    d = S.extract(_companyfacts({"InterestExpenseNonoperating": {"units": {"USD": live}}}))
    check(d["interest_expense"] is not None, "InterestExpenseNonoperating is on the interest ladder")


if __name__ == "__main__":
    run_suite(globals(), "ALL test_damodaran_fixes_2026_10 PASSED")
