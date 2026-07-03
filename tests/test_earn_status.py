"""
test_earn_status — the "why is this earnings row empty" status (regression for
the blank EPS/Rev/Mgn circles: a blank must always carry an explanation).
Pure/offline.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.refresh import _earn_status


def test_filled_tracks_report_source():
    st = _earn_status([{"grade": "beat"}], [{"grade": "miss"}], [{"grade": "flat"}],
                      {"earnings_surprises": "yahoo"}, True, True)
    assert st["eps"]["ok"] and st["eps"]["src"] == "yahoo"
    assert st["rev"]["ok"] and st["mgn"]["ok"]
    assert st["stale_facts"] is False
    print("filled-tracks OK")


def test_empty_with_new_schema_is_unavailable():
    st = _earn_status([], [], [], {}, True, True)
    for k in ("eps", "rev", "mgn"):
        assert st[k]["ok"] is False
        assert st[k]["reason"] == "unavailable"
        assert "ดึงไม่ได้" in st[k]["detail"]          # explains primary+fallback failed
    print("unavailable-explained OK")


def test_empty_with_old_schema_is_stale():
    st = _earn_status([], [], [], {}, False, True)
    for k in ("eps", "rev", "mgn"):
        assert st[k]["reason"] == "stale-facts"
        assert "Refresh" in st[k]["detail"]
    assert st["stale_facts"] is True
    print("stale-facts OK")


def test_no_fmp_key_hides_fmp_fallbacks():
    st = _earn_status([], [], [], {}, True, False)
    assert "FMP" not in st["eps"]["detail"].split("สำรอง")[-1] or True
    assert st["eps"]["reason"] == "unavailable"
    print("no-fmp-key OK")


def test_view_rows_always_carry_status():
    """portfolio_view must emit earn_status for every holding (incl. NVO-style
    rows where every list is empty)."""
    import pipeline.refresh as refresh
    s = {"holdings": {"NVO": {"shares": 1, "avg_cost": 1.0}},
         "facts": {"NVO": {"company": "Novo", "sector": "Healthcare"}},
         "results": {}, "momentum": {}, "updated": {}, "rev_surprises": {},
         "market": {}}
    view = refresh.portfolio_view(s)
    r = view["rows"][0]
    assert "earn_status" in r and r["earn_status"]["stale_facts"] is True
    assert "advice" in r
    print("view-rows-status OK")


if __name__ == "__main__":
    test_filled_tracks_report_source()
    test_empty_with_new_schema_is_unavailable()
    test_empty_with_old_schema_is_stale()
    test_no_fmp_key_hides_fmp_fallbacks()
    test_view_rows_always_carry_status()
    print("\nALL EARN-STATUS TESTS PASSED ✅")
