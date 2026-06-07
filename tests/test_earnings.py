"""
test_earnings — EPS-surprise history parsing + grading + confidence effect.

Offline/synthetic (Yahoo earningsHistory is not in the saved fixtures, and the
sandbox can't reach Yahoo). Locks the BEAT/MEET/MISS thresholds, the oldest->newest
ordering, and the bounded confidence nudge so the feature can't silently regress.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import yahoo
from domain.facts import FinancialFacts
from pipeline.validate import _earnings_confidence


def _qs(history):
    return {"quoteSummary": {"result": [{"earningsHistory": {"history": history}}]}}


def _q(date, act, est, surprise):
    return {"quarter": {"fmt": date}, "epsActual": {"raw": act},
            "epsEstimate": {"raw": est}, "surprisePercent": {"raw": surprise}}


def test_grading_and_order():
    # Yahoo returns NEWEST first; parser must flip to oldest->newest.
    hist = [
        _q("2025-03-31", 1.10, 1.00, 10.0),   # newest  -> beat
        _q("2024-12-31", 0.99, 1.00, -1.0),   #          -> meet (within +/-2%)
        _q("2024-09-30", 0.80, 1.00, -20.0),  #          -> miss
        _q("2024-06-30", 2.05, 2.00, 2.5),    # oldest  -> beat
    ]
    out = yahoo.parse_earnings_history(_qs(hist))
    assert len(out) == 4, out
    # oldest -> newest
    assert [e["quarter"] for e in out] == ["2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31"], out
    assert [e["grade"] for e in out] == ["beat", "miss", "meet", "beat"], out
    print("grading + ordering OK:", [e["grade"] for e in out])


def test_surprise_fallback_and_caps():
    # No surprisePercent supplied -> computed from actual vs estimate.
    hist = [{"quarter": {"fmt": "2025-03-31"}, "epsActual": {"raw": 1.20},
             "epsEstimate": {"raw": 1.00}}]              # +20% -> beat
    out = yahoo.parse_earnings_history(_qs(hist))
    assert out and out[0]["grade"] == "beat" and out[0]["surprise_pct"] == 20.0, out
    # absent module -> [] (old fixtures must not crash)
    assert yahoo.parse_earnings_history({"quoteSummary": {"result": [{}]}}) == []
    assert yahoo.parse_earnings_history({}) == []
    print("surprise fallback + empty-safe OK")


def test_confidence_nudge_bounds():
    def ff_with(grades):
        ff = FinancialFacts("TEST")
        ff.earnings_surprises = [{"grade": g} for g in grades]
        return ff
    # all 4 beats -> +10 (max)
    assert _earnings_confidence(ff_with(["beat"] * 4)) == 10
    # all 4 misses -> -10 (min)
    assert _earnings_confidence(ff_with(["miss"] * 4)) == -10
    # 2 beat / 2 miss -> 0
    assert _earnings_confidence(ff_with(["beat", "beat", "miss", "miss"])) == 0
    # 3 beat / 1 meet -> round(3/4*10)=8
    assert _earnings_confidence(ff_with(["beat", "beat", "beat", "meet"])) == 8
    # fewer than 2 quarters -> no effect, no flag
    f1 = ff_with(["beat"])
    assert _earnings_confidence(f1) == 0 and not f1.flags
    # a flag is recorded when applied (transparency)
    f2 = ff_with(["beat", "beat", "miss", "meet"])
    _earnings_confidence(f2)
    assert any("earnings" in fl for fl in f2.flags), f2.flags
    print("confidence nudge bounds + flag OK")


if __name__ == "__main__":
    test_grading_and_order()
    test_surprise_fallback_and_caps()
    test_confidence_nudge_bounds()
    print("\nALL EARNINGS TESTS PASSED ✅")
