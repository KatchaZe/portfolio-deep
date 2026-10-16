"""A tag ladder must read the tag that is still being filed, not the first tag in list
order that ever had a value. NVDA's capex came off PaymentsToAcquirePropertyPlantAndEquipment,
last filed for FY2020 ($0.46B), while the live tag said $6.6B; 40+ field/company pairs in
the real cache lagged the latest filing by more than 120 days the same way."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import sec_edgar as se
from domain.facts import FinancialFacts
from tests._harness import check, run_suite


def _flow(start, end, val, filed):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": "10-K"}


def _facts(**concepts):
    rev = [_flow("2024-01-01", "2024-12-31", 900, "2025-02-01"),
           _flow("2025-01-01", "2025-12-31", 1000, "2026-02-01")]
    ns = {"Revenues": {"units": {"USD": rev}}}
    ns.update({k: {"units": {"USD": v}} for k, v in concepts.items()})
    return {"facts": {"us-gaap": ns}}


def test_the_live_tag_wins_over_a_dead_tag_earlier_in_the_ladder():
    d = se.extract(_facts(
        PaymentsToAcquirePropertyPlantAndEquipment=[_flow("2020-01-01", "2020-12-31", 46, "2021-02-01")],
        PaymentsToAcquireProductiveAssets=[_flow("2025-01-01", "2025-12-31", 657, "2026-02-01")]))
    check(d["capex"] == 657, "capex is read off the tag filed for the latest year", str(d["capex"]))
    check("capex" not in d["stale_fields"], "no stale flag when a live tag exists")


def test_a_ladder_with_only_a_dead_tag_is_refused_and_flagged():
    d = se.extract(_facts(
        PaymentsToAcquirePropertyPlantAndEquipment=[_flow("2020-01-01", "2020-12-31", 46, "2021-02-01")]))
    check(d["capex"] is None, "a value last filed five years ago is not used as today's", str(d["capex"]))
    check("capex" in d["stale_fields"], "the refusal is recorded")
    ff, _ = se.populate(FinancialFacts(ticker="ZZZ"), _facts(
        PaymentsToAcquirePropertyPlantAndEquipment=[_flow("2020-01-01", "2020-12-31", 46, "2021-02-01")]))
    check(any("stale tag - capex" in f for f in ff.flags), "and reaches the card as a DATA flag", str(ff.flags))


def test_interest_reads_the_2024_nonoperating_tag():
    d = se.extract(_facts(
        InterestExpense=[_flow("2023-01-01", "2023-12-31", 32, "2024-02-01")],
        InterestExpenseNonoperating=[_flow("2025-01-01", "2025-12-31", 225, "2026-02-01")]))
    check(d["interest_expense"] == 225, "interest expense follows the tag filers moved to in 2024",
          str(d["interest_expense"]))


def test_pretax_falls_back_to_domestic_plus_foreign():
    d = se.extract(_facts(
        IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest=[
            _flow("2018-01-01", "2018-12-31", 10, "2019-02-01")],
        IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic=[
            _flow("2025-01-01", "2025-12-31", 300, "2026-02-01")],
        IncomeLossFromContinuingOperationsBeforeIncomeTaxesForeign=[
            _flow("2025-01-01", "2025-12-31", 200, "2026-02-01")]))
    check(d["income_before_tax"] == 500, "pretax = domestic + foreign when the total tag died",
          str(d["income_before_tax"]))
    check("income_before_tax" not in d["stale_fields"], "a field filled by its legs is not flagged stale")


if __name__ == "__main__":
    run_suite(globals(), "ALL test_stale_tags PASSED")
