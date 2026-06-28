"""test_pead — Damodaran S25 PEAD drift bias. Pure/offline."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain import pead


def test_pead():
    assert pead.signal(None)["bias"] is None
    assert pead.signal([])["bias"] is None
    s = pead.signal([{"grade": "beat", "surprise_pct": 12, "quarter": "2026Q1"}])
    assert s["bias"] == "up" and s["strength"] == "strong", s
    s2 = pead.signal([{"grade": "miss", "surprise_pct": -3, "quarter": "2026Q1"}])
    assert s2["bias"] == "down" and s2["strength"] == "mild", s2
    s3 = pead.signal([{"grade": "beat", "surprise_pct": 20}], [{"grade": "miss"}])
    assert s3["bias"] == "up" and s3["strength"] == "mild", s3      # mixed -> weak
    s4 = pead.signal([{"grade": "meet", "surprise_pct": 0.5}])
    assert s4["bias"] == "neutral", s4
    print("pead OK")


if __name__ == "__main__":
    test_pead()
    print("\nALL test_pead PASSED")
