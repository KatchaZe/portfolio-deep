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



def test_bands_from_damodaran_history():
    """2026-10-19: the band is the 25th/75th percentile of his 1961-2025 implied ERPs,
    not a fixed 4.0%/5.5% (which called the long-run median 4.10% nearly expensive)."""
    from sources.damodaran_snapshot import ERP_HISTORY
    lo, hi, n, y0, y1 = mv.erp_bands(ERP_HISTORY)
    assert (lo, hi, n, y0, y1) == (0.0348, 0.0508, 65, 1961, 2025), (lo, hi, n)
    # 3.9%: "expensive" under the old fixed band, "fair" against its own history
    assert mv.overlay(0.043, 0.039, 25.0)["regime"] == "expensive"
    o = mv.overlay(0.043, 0.039, 25.0, ERP_HISTORY)
    assert o["regime"] == "fair" and o["erp_low_pct"] == 3.48 and "1961-2025" in o["band_source"]
    assert mv.overlay(0.043, 0.034, 25.0, ERP_HISTORY)["regime"] == "expensive"
    assert mv.overlay(0.043, 0.052, 25.0, ERP_HISTORY)["regime"] == "cheap"
    # too little history -> the fixed band, and the strip says so
    assert "not available" in mv.overlay(0.043, 0.039, 25.0, {2024: 0.04})["band_source"]
    print("ERP history bands OK")


def test_spy_trailing_pe():
    from sources import yahoo
    qs = lambda pe: {"quoteSummary": {"result": [{"summaryDetail": {"trailingPE": {"raw": pe}}}]}}
    assert yahoo.parse_trailing_pe(qs(27.31)) == 27.31
    assert yahoo.parse_trailing_pe(qs(400.0)) is None          # outside the sane band
    assert yahoo.parse_trailing_pe({"_error": "http 429"}) is None
    print("SPY trailing P/E parse OK")


if __name__ == "__main__":
    from tests._harness import run_suite
    run_suite(globals(), "ALL test_market_valuation PASSED")
