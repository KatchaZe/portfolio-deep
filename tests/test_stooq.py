"""
test_stooq — Stooq symbol mapping + CSV parser. Pure/synthetic (no network);
locks the fallback price adapter so a Stooq format quirk can't silently blank it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources import stooq


def test_to_symbol():
    assert stooq.to_symbol("NVDA") == "nvda.us"
    assert stooq.to_symbol(" tsm ") == "tsm.us"
    assert stooq.to_symbol("BRK.B") == "brk-b.us"     # class dot -> dash
    assert stooq.to_symbol("aapl.us") == "aapl.us"    # already suffixed
    assert stooq.to_symbol("") == ""
    print("to_symbol OK")


def test_parse_csv():
    csv_text = ("Date,Open,High,Low,Close,Volume\n"
                "2026-06-15,100.0,102.0,99.0,101.5,1000000\n"
                "2026-06-16,101.5,103.0,101.0,102.8,1200000\n"
                "2026-06-17,102.8,104.0,102.0,,900000\n")     # missing close -> skipped
    d = stooq.parse_csv(csv_text)
    assert d["closes"] == [101.5, 102.8], d
    assert d["volumes"] == [1000000.0, 1200000.0], d
    assert d["dates"] == ["2026-06-15", "2026-06-16"], d
    print("parse_csv OK:", d["closes"])


def test_parse_bad_body():
    # unknown symbol -> Stooq returns 'N/D' (no header) -> empty, never crashes
    assert stooq.parse_csv("N/D") == {"closes": [], "volumes": [], "dates": []}
    assert stooq.parse_csv("") == {"closes": [], "volumes": [], "dates": []}
    print("bad-body safe OK")


if __name__ == "__main__":
    test_to_symbol()
    test_parse_csv()
    test_parse_bad_body()
    print("\nALL STOOQ TESTS PASSED")
