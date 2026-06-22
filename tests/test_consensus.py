"""
test_consensus — forward-EPS blend (median + dispersion) and multi-source EPS
reconcile. Pure/synthetic; locks the priority order, agreement tagging, and the
confidence nudge so the blend can't silently regress.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import consensus
from domain.facts import FinancialFacts
from pipeline.validate import _consensus_confidence


def test_blend_median_and_dispersion():
    b = consensus.blend_forward_eps({"yahoo": 10.0, "fmp": 12.0, "finnhub": 11.0})
    assert b["value"] == 11.0, b                 # median of 3
    assert b["low"] == 10.0 and b["high"] == 12.0, b
    assert b["n"] == 3, b
    assert b["spread_pct"] == round((12 - 10) / 11 * 100, 1), b
    # two sources -> average; non-positive / bad values dropped
    b2 = consensus.blend_forward_eps({"yahoo": 8.0, "fmp": 10.0, "x": 0, "y": None})
    assert b2["value"] == 9.0 and b2["n"] == 2, b2
    # single source -> value passthrough, no real spread
    b1 = consensus.blend_forward_eps({"yahoo": 7.5})
    assert b1["value"] == 7.5 and b1["n"] == 1 and b1["spread_pct"] == 0.0, b1
    # nothing usable -> None
    assert consensus.blend_forward_eps({"a": 0, "b": -1}) is None
    assert consensus.blend_forward_eps({}) is None
    print("blend median + dispersion OK")


def _row(grade):
    return {"quarter": "2026-03-31", "grade": grade}


def test_reconcile_priority_and_agreement():
    # Yahoo present -> primary; FMP confirms, Finnhub disagrees (beat vs miss)
    by = {"fmp": [_row("beat")], "yahoo": [_row("beat")], "finnhub": [_row("miss")]}
    r = consensus.reconcile_earnings(by)
    assert r["primary"] == "yahoo", r
    assert r["disagree"] is True, r
    assert "fmp✓" in r["provenance"] and "finnhub disagree" in r["provenance"], r
    assert r["agree"] == 1 and r["total"] == 2, r
    # Yahoo empty -> FMP becomes primary
    r2 = consensus.reconcile_earnings({"fmp": [_row("meet")], "finnhub": [_row("meet")]})
    assert r2["primary"] == "fmp" and r2["disagree"] is False, r2
    assert r2["agree"] == 1, r2
    # all empty -> safe
    r3 = consensus.reconcile_earnings({})
    assert r3["list"] == [] and r3["primary"] is None, r3
    print("reconcile priority + agreement OK:", r["provenance"])


def test_consensus_confidence_nudge():
    def ff_with(n, spread):
        ff = FinancialFacts("T")
        ff.forward_eps_n = n
        ff.forward_eps_spread_pct = spread
        return ff
    assert _consensus_confidence(ff_with(3, 5.0)) == 4      # tight -> +4
    assert _consensus_confidence(ff_with(2, 18.0)) == 0     # moderate -> 0
    assert _consensus_confidence(ff_with(3, 40.0)) == -6    # wide -> -6
    f1 = ff_with(1, 0.0)
    assert _consensus_confidence(f1) == 0 and not f1.flags  # <2 sources -> no effect
    print("consensus confidence nudge OK")


if __name__ == "__main__":
    test_blend_median_and_dispersion()
    test_reconcile_priority_and_agreement()
    test_consensus_confidence_nudge()
    print("\nALL CONSENSUS TESTS PASSED")
