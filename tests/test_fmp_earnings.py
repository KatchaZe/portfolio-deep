"""
test_fmp_earnings — FMP earnings parsing (the Yahoo fallback / cross-check) and
the reconcile policy in refresh.

Offline/synthetic: locks the field-name handling (stable vs legacy endpoints),
the BEAT/MEET/MISS thresholds, oldest->newest ordering + 4-cap, and the
source-selection / flagging rules so the fallback can't silently regress.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import fmp
from domain.facts import FinancialFacts
from pipeline.refresh import _reconcile_earnings


def test_parse_stable_shape():
    # FMP /stable/earnings — newest first, includes a future (unreported) row.
    raw = [
        {"symbol": "X", "date": "2026-08-01", "epsActual": None, "epsEstimated": 1.30},  # future -> skip
        {"symbol": "X", "date": "2026-05-01", "epsActual": 1.20, "epsEstimated": 1.00},  # +20 beat
        {"symbol": "X", "date": "2026-02-01", "epsActual": 0.99, "epsEstimated": 1.00},  # -1   meet
        {"symbol": "X", "date": "2025-11-01", "epsActual": 0.70, "epsEstimated": 1.00},  # -30  miss
        {"symbol": "X", "date": "2025-08-01", "epsActual": 2.01, "epsEstimated": 2.00},  # +0.5 meet
    ]
    out = fmp.parse_earnings(raw)
    assert [e["quarter"] for e in out] == ["2025-08-01", "2025-11-01", "2026-02-01", "2026-05-01"], out
    assert [e["grade"] for e in out] == ["meet", "miss", "meet", "beat"], out
    assert out[-1]["surprise_pct"] == 20.0, out
    print("parse stable shape OK:", [e["grade"] for e in out])


def test_parse_legacy_shape_and_cap():
    # FMP /api/v3/earnings-surprises — different field names; >4 rows -> keep last 4.
    raw = [{"date": f"2025-{m:02d}-01", "actualEarningResult": 1.0 + i * 0.1,
            "estimatedEarning": 1.0} for i, m in enumerate((1, 4, 7, 10, 12))]
    out = fmp.parse_earnings(raw)
    assert len(out) == 4 and out[0]["quarter"] == "2025-04-01", out   # oldest (Jan) dropped
    assert out[0]["eps_actual"] == 1.1, out
    print("parse legacy shape + 4-cap OK")


def test_parse_empty_safe():
    assert fmp.parse_earnings([]) == []
    assert fmp.parse_earnings(None) == []
    assert fmp.parse_earnings([{"date": "2025-01-01", "epsEstimated": 1.0}]) == []  # no actual
    print("parse empty-safe OK")


def test_parse_quote():
    # stable shape (list) + legacy field name; empty-safe.
    assert fmp.parse_quote([{"symbol": "NVO", "price": 47.8, "sharesOutstanding": 3.35e9}]) \
        == {"price": 47.8, "shares": 3.35e9}
    assert fmp.parse_quote([{"price": 10.0, "sharesOutstandingDil": 5}]) == {"price": 10.0, "shares": 5}
    assert fmp.parse_quote([]) == {"price": None, "shares": None}
    assert fmp.parse_quote(None) == {"price": None, "shares": None}
    print("parse_quote OK")


def _es(grades):
    return [{"grade": g, "eps_actual": 1.0, "eps_estimate": 1.0} for g in grades]


def test_reconcile_prefers_yahoo_and_verifies():
    ff = FinancialFacts("X")
    yh, fm = _es(["beat", "meet"]), _es(["meet", "beat"])   # latest both 'beat'-ish, no conflict
    out = _reconcile_earnings(ff, yh, fm)
    assert out is yh, "should keep Yahoo when present"
    assert ff.provenance["earnings_surprises"] == "yahoo+fmp✓", ff.provenance
    assert not any("disagree" in f for f in ff.flags), ff.flags
    print("reconcile keep-yahoo + verify OK")


def test_reconcile_flags_disagreement():
    ff = FinancialFacts("X")
    yh, fm = _es(["beat", "beat"]), _es(["meet", "miss"])   # latest: beat vs miss -> conflict
    _reconcile_earnings(ff, yh, fm)
    assert any("disagree" in f for f in ff.flags), ff.flags
    print("reconcile disagreement flag OK")


def test_reconcile_fallback_to_fmp():
    ff = FinancialFacts("X")
    fm = _es(["beat", "meet"])
    out = _reconcile_earnings(ff, [], fm)
    assert out is fm, "should use FMP when Yahoo empty"
    assert ff.provenance["earnings_surprises"] == "fmp", ff.provenance
    assert any("via FMP" in f for f in ff.flags), ff.flags
    # both empty -> empty, no crash
    ff2 = FinancialFacts("Y")
    assert _reconcile_earnings(ff2, [], []) == []
    print("reconcile fallback-to-FMP OK")


if __name__ == "__main__":
    test_parse_stable_shape()
    test_parse_legacy_shape_and_cap()
    test_parse_empty_safe()
    test_parse_quote()
    test_reconcile_prefers_yahoo_and_verifies()
    test_reconcile_flags_disagreement()
    test_reconcile_fallback_to_fmp()
    print("\nALL FMP-EARNINGS TESTS PASSED ✅")
