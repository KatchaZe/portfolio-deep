"""SEC companyfacts have no fiscal-Q4 single-quarter fact for most US filers: Q4 sits
inside the 10-K. Quarter histories therefore skipped every Q4 (a "5-quarter" history
covered ~18 months with gaps). Q4 is derived as FY - 9M YTD (or FY - Q1..Q3), never
over a reported Q4, and the field's provenance says so."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import sec_edgar as se
from domain.facts import FinancialFacts

FY = ("2024-11-04", "2025-11-02")
Q = [("2024-11-04", "2025-02-02"), ("2025-02-03", "2025-05-04"), ("2025-05-05", "2025-08-03")]


def _facts(concept, unit, fy, quarters, ytd9=None, q4=None):
    rows = [{"start": FY[0], "end": FY[1], "val": fy, "filed": "2025-12-10", "form": "10-K"}]
    rows += [{"start": s, "end": e, "val": v, "filed": "2025-09-05", "form": "10-Q"}
             for (s, e), v in zip(Q, quarters)]
    if ytd9 is not None:
        rows.append({"start": FY[0], "end": Q[2][1], "val": ytd9, "filed": "2025-09-05", "form": "10-Q"})
    if q4 is not None:
        rows.append({"start": "2025-08-04", "end": FY[1], "val": q4, "filed": "2025-12-10", "form": "10-K"})
    return {"us-gaap": {concept: {"units": {unit: rows}}}}


def test_q4_from_nine_month_ytd():
    x = se._Xbrl(_facts("Revenues", "USD", 100, [20, 21, 22], ytd9=64))
    q = x.quarters("Revenues")
    assert q[FY[1]] == 36, q                       # 100 - 64, not 100 - 63
    assert list(q) == sorted(q, reverse=True), "newest first"


def test_q4_from_three_quarters_when_no_ytd():
    x = se._Xbrl(_facts("Revenues", "USD", 100, [20, 21, 22]))
    assert x.quarters("Revenues")[FY[1]] == 37


def test_reported_q4_is_never_overwritten():
    x = se._Xbrl(_facts("Revenues", "USD", 100, [20, 21, 22], ytd9=64, q4=99))
    assert x.quarters("Revenues")[FY[1]] == 99


def test_incomplete_year_derives_nothing():
    x = se._Xbrl(_facts("Revenues", "USD", 100, [20, 21]))
    assert FY[1] not in x.quarters("Revenues")


def test_eps_q4_is_clean_of_float_noise():
    x = se._Xbrl(_facts("EarningsPerShareDiluted", "USD/shares", 1.30, [0.30, 0.31, 0.32], ytd9=0.93))
    assert x.quarters("EarningsPerShareDiluted", "USD/shares")[FY[1]] == 0.37


def test_real_filer_gains_its_fiscal_q4_with_provenance():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "tests", "fixtures", "AVGO", "sec_companyfacts.json")
    with open(path, encoding="utf-8") as fh:
        cf = json.load(fh)
    ff, d = se.populate(FinancialFacts("AVGO"), cf)
    fy_ends = [e for e, _ in d["revenue_annuals_dated"]][:2]
    for end in fy_ends:
        assert end in d["revenue_quarters"], (end, sorted(d["revenue_quarters"])[-6:])
        assert end in d["eps_quarters"], end
    assert "Q4" in ff.provenance["eps_quarters"], ff.provenance["eps_quarters"]


if __name__ == "__main__":
    from tests._harness import run_suite
    run_suite(globals(), "ALL test_q4_derived OK")
