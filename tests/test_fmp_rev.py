"""
test_fmp_rev — FMP immediate revenue-surprise parser (Phase 3) + the assumption
flags. Pure/synthetic; confirms revenue beat/miss grades, defensive field names
(stable vs legacy), and that silent engine fallbacks get flagged.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import fmp
from domain.facts import FinancialFacts
from pipeline.validate import _assumption_flags


def test_revenue_surprise_stable_shape():
    # stable /earnings: revenueActual + revenueEstimated (newest first -> sorted)
    raw = [
        {"date": "2026-03-31", "revenueActual": 1.10e10, "revenueEstimated": 1.00e10},  # +10 beat
        {"date": "2025-12-31", "revenueActual": 9.9e9, "revenueEstimated": 1.00e10},     # -1 meet
        {"date": "2025-09-30", "revenueActual": 8.0e9, "revenueEstimated": 1.00e10},     # -20 miss
        {"date": "2026-06-30", "revenueActual": None, "revenueEstimated": 1.2e10},       # no actual -> skip
    ]
    out = fmp.parse_revenue_surprises(raw)
    assert [e["quarter"] for e in out] == ["2025-09-30", "2025-12-31", "2026-03-31"], out
    assert [e["grade"] for e in out] == ["miss", "meet", "beat"], out
    assert out[-1]["surprise_pct"] == 10.0, out
    print("revenue surprise (stable) OK:", [e["grade"] for e in out])


def test_revenue_surprise_legacy_shape_and_empty():
    # legacy earning_calendar: revenue + revenueEstimated
    raw = [{"date": "2026-03-31", "revenue": 5.2e9, "revenueEstimated": 5.0e9}]   # +4 beat
    out = fmp.parse_revenue_surprises(raw)
    assert out and out[0]["grade"] == "beat" and out[0]["rev_actual"] == 5.2e9, out
    # no revenue fields (EPS-only plan) -> [] so caller uses build-forward
    assert fmp.parse_revenue_surprises([{"date": "2026-03-31", "epsActual": 1.0}]) == []
    assert fmp.parse_revenue_surprises([]) == [] and fmp.parse_revenue_surprises(None) == []
    print("revenue surprise (legacy/empty) OK")


def test_assumption_flags():
    # all three fallbacks fire when inputs are absent
    ff = FinancialFacts("T")                       # beta None, no tax inputs, growth None
    _assumption_flags(ff)
    joined = " | ".join(ff.flags)
    assert "beta missing" in joined and "tax rate defaults to 21%" in joined \
        and "growth missing" in joined, ff.flags
    # fully sourced inputs -> no assumption flags
    ok = FinancialFacts("U")
    ok.beta = 1.1
    ok.income_before_tax = 1000.0
    ok.tax_expense = 210.0                          # 21% in-band, but it's SOURCED
    ok.growth_lt = 0.12
    ok.shares_diluted = 1e9                         # S3: absent shares is itself an assumption flag
    _assumption_flags(ok)
    assert ok.flags == [], ok.flags
    # ...and its absence must be reported, since it disables the forward-EPS
    # ceiling and collapses WACC to an unweighted Ke
    noshares = FinancialFacts("V")
    noshares.beta, noshares.income_before_tax = 1.1, 1000.0
    noshares.tax_expense, noshares.growth_lt = 210.0, 0.12
    _assumption_flags(noshares)
    assert any("shares outstanding missing" in f for f in noshares.flags), noshares.flags
    print("assumption flags OK")


if __name__ == "__main__":
    test_revenue_surprise_stable_shape()
    test_revenue_surprise_legacy_shape_and_empty()
    test_assumption_flags()
    print("\nALL FMP-REV / ASSUMPTION TESTS PASSED")
