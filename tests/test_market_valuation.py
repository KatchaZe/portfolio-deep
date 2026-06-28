"""test_market_valuation — Damodaran S32/33 overlay. Pure/offline."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import market_valuation as mv


def test_overlay():
    assert mv.overlay(0.043, 0.035, 25.0)["regime"] == "expensive"   # low ERP
    assert mv.overlay(0.043, 0.060, 16.0)["regime"] == "cheap"       # high ERP
    o = mv.overlay(0.043, 0.047, 22.0)
    assert o["regime"] == "fair"
    assert o["earnings_yield_pct"] == round(100 / 22.0, 2)
    assert o["fed_spread_pct"] == round((1 / 22.0 - 0.043) * 100, 2)
    assert mv.overlay(0.043, None, 22.0)["regime"] is None           # no ERP -> None
    print("market overlay OK")


if __name__ == "__main__":
    test_overlay()
    print("\nALL test_market_valuation PASSED")
