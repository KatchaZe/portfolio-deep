"""
test_rev — build-forward revenue beat/miss: estimate parsing + snapshot/grade/cap.
Pure/synthetic (no network); locks the accumulation logic so it can't regress.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import yahoo
from pipeline import rev_track


def test_parse_revenue_estimate():
    qs = {"quoteSummary": {"result": [{"earningsTrend": {"trend": [
        {"period": "-1q", "endDate": "2026-01-31", "revenueEstimate": {"avg": {"raw": 9e9}}},
        {"period": "0q", "endDate": "2026-04-30", "revenueEstimate": {"avg": {"raw": 1.0e10}}},
        {"period": "+1q", "endDate": "2026-07-31", "revenueEstimate": {"avg": {"raw": 1.1e10}}},
    ]}}]}}
    r = yahoo.parse_revenue_estimate(qs)
    assert r == {"quarter_end": "2026-04-30", "estimate": 1.0e10}, r
    # missing -> None, never crashes
    assert yahoo.parse_revenue_estimate({"quoteSummary": {"result": [{}]}}) is None
    print("parse_revenue_estimate OK")


def test_snapshot_then_grade():
    s = {}
    # refresh 1: estimate captured, no actual yet -> no history
    rev_track.update(s, "X", {"quarter_end": "2026-01-31", "estimate": 100.0}, {}, "2026-01-10")
    assert s["rev_surprises"]["X"] == []
    assert "2026-01-31" in s["rev_snapshots"]["X"]
    # refresh 2: actual 110 arrives for that quarter (+10% beat); new quarter snapshotted
    rev_track.update(s, "X", {"quarter_end": "2026-04-30", "estimate": 200.0},
                     {"2026-01-31": 110.0}, "2026-04-10")
    h = s["rev_surprises"]["X"]
    assert len(h) == 1 and h[0]["quarter"] == "2026-01-31", h
    assert h[0]["grade"] == "beat" and h[0]["surprise_pct"] == 10.0, h
    assert "2026-01-31" not in s["rev_snapshots"]["X"]      # graded snapshot removed
    assert "2026-04-30" in s["rev_snapshots"]["X"]          # next pending
    print("snapshot -> grade OK:", h[0])


def test_meet_miss_and_cap():
    s = {"rev_snapshots": {"Y": {}}, "rev_surprises": {"Y": []}}
    # feed 5 quarters of (estimate, then actual) -> only last 4 kept
    quarters = [("2025-01-31", 100, 130),   # +30 beat
                ("2025-04-30", 100, 101),   # +1  meet
                ("2025-07-31", 100, 80),    # -20 miss
                ("2025-10-31", 100, 100),   # 0   meet
                ("2026-01-31", 100, 150)]   # +50 beat
    for qe, est, act in quarters:
        rev_track.update(s, "Y", {"quarter_end": qe, "estimate": est}, {}, "d")  # snapshot
        rev_track.update(s, "Y", None, {qe: act}, "d")                            # grade
    h = s["rev_surprises"]["Y"]
    assert len(h) == 4, h                                  # capped
    assert [x["quarter"] for x in h][0] == "2025-04-30"    # oldest dropped
    assert [x["grade"] for x in h] == ["meet", "miss", "meet", "beat"], h
    print("meet/miss + 4-cap OK:", [x["grade"] for x in h])


if __name__ == "__main__":
    test_parse_revenue_estimate()
    test_snapshot_then_grade()
    test_meet_miss_and_cap()
    print("\nALL REV-TRACK TESTS PASSED")
