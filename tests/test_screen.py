"""test_screen — Damodaran S13/S19 GARP cheap x quality ranking. Pure/offline."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import screen


def test_garp():
    assert screen.garp_score(None, 4) is None
    assert screen.garp_score(4, 5) == 9
    assert screen.is_candidate(3, 3) is True
    assert screen.is_candidate(2.5, 5) is False        # not quality enough
    assert screen.is_candidate(5, 2) is False          # not cheap enough
    items = [
        {"ticker": "A", "economics": 5, "price": 4},   # 9, candidate
        {"ticker": "B", "economics": 2, "price": 2},   # 4, not candidate
        {"ticker": "C", "economics": 4, "price": None}, # dropped
        {"ticker": "D", "economics": 4.5, "price": 4.5},# 9, candidate
    ]
    r = screen.rank(items)
    assert [x["ticker"] for x in r] == ["A", "D", "B"], r   # C dropped; A==D tie keeps order
    assert r[0]["candidate"] is True and r[-1]["candidate"] is False
    print("garp screen OK")


if __name__ == "__main__":
    test_garp()
    print("\nALL test_screen PASSED")
