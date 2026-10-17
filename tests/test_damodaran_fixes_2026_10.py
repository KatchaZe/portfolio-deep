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


def _inst(end, val):
    return {"end": end, "val": val, "filed": end, "form": "10-Q"}


def test_f4_cash_includes_marketable_securities_on_one_date():
    from sources import sec_edgar as S
    cash = {"units": {"USD": [_inst("2026-06-30", 55.9e9), _inst("2025-12-31", 30.7e9)]}}
    secs = {"units": {"USD": [_inst("2026-06-30", 186.6e9), _inst("2025-12-31", 64.4e9)]}}
    d = S.extract(_companyfacts({"CashAndCashEquivalentsAtCarryingValue": cash,
                                 "MarketableSecuritiesCurrent": secs}))
    check(abs(d["cash"] - 242.5e9) < 1, "cash = cash & equivalents + marketable securities (GOOGL 242.5B)",
          "got %r" % d["cash"])
    check(abs(d["cash_prior"] - 95.1e9) < 1, "the prior year adds securities on its own date too",
          "got %r" % d["cash_prior"])
    stale = {"units": {"USD": [_inst("2025-10-26", 49.1e9)]}}
    d = S.extract(_companyfacts({"CashAndCashEquivalentsAtCarryingValue": cash,
                                 "MarketableSecuritiesCurrent": stale}))
    check(abs(d["cash"] - 55.9e9) < 1, "a securities leg from another date is never added (NVDA)",
          "got %r" % d["cash"])
    comb = {"units": {"USD": [_inst("2026-06-30", 76.8e9)]}}
    d = S.extract(_companyfacts({"CashAndCashEquivalentsAtCarryingValue": cash,
                                 "CashCashEquivalentsAndShortTermInvestments": comb,
                                 "ShortTermInvestments": secs}))
    check(abs(d["cash"] - 76.8e9) < 1, "a filed combined total wins over summing legs (MSFT)",
          "got %r" % d["cash"])


if __name__ == "__main__":
    run_suite(globals(), "ALL test_damodaran_fixes_2026_10 PASSED")
