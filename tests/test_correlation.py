"""test_correlation — Correlation Monitor pure math + per-portfolio Diversification
Philosophy. Pure/offline (no network, no fixtures)."""
import os
import sys
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.engine import risk
from domain import diversification


def test_pair_corr():
    assert abs(risk.pair_corr([1, 2, 3, 4], [1, 2, 3, 4]) - 1.0) < 1e-9
    assert abs(risk.pair_corr([1, 2, 3, 4], [4, 3, 2, 1]) + 1.0) < 1e-9
    assert risk.pair_corr([1], [1]) is None            # too few points
    assert risk.pair_corr([1, 1, 1], [1, 2, 3]) is None  # flat series -> None
    print("pair_corr OK")


def test_sector_and_pairs():
    tk = ["NVDA", "AMD", "LLY"]
    secs = {"NVDA": "Tech", "AMD": "Tech", "LLY": "Health"}
    M = [[1, 0.8, 0.2], [0.8, 1, 0.15], [0.2, 0.15, 1]]
    sc = risk.sector_corr(M, tk, secs)
    assert sc["sector_avg"]["Tech"] == {"avg": 0.8, "n": 1}, sc
    assert round(sc["avg_pairwise"], 3) == round((0.8 + 0.2 + 0.15) / 3, 3)
    tp = risk.top_pairs(M, tk, k=2)
    assert tp["high"][0] == ["NVDA", "AMD", 0.8]
    assert tp["low"][0][2] == 0.15
    print("sector_corr + top_pairs OK")


def test_downside_and_rolling():
    mkt = [0.01 * math.sin(i * 0.7) for i in range(150)]
    a = [1.3 * m + 0.0004 * math.cos(i) for i, m in enumerate(mkt)]
    dc = risk.downside_corr(a, a, mkt)                  # corr of a vs itself on down days = 1
    assert dc is not None and dc > 0.9, dc
    assert risk.downside_corr([1, 1, 1], [1, 1, 1], [-1, -1, -1]) is None  # flat -> None
    r = risk.rolling_corr(a, a, 60)
    assert len(r) == 150 - 60 + 1 and all(abs(x - 1.0) < 1e-6 for x in r)
    assert risk.rolling_corr(a, a, 999) == []           # window > series
    print("downside_corr + rolling_corr OK")


def test_philosophy():
    out = diversification.diversification_philosophy(
        n_holdings=8, enb=3.4, enb_crisis=1.6, eff_n=6.0, top_sector="Technology",
        top_sector_wt=62, top_sector_corr=0.67, avg_pairwise=0.36,
        avg_pairwise_crisis=0.80, bench_nasdaq_corr=0.93, downside_corr=0.61,
        top_risk_driver="NVDA")
    assert set(out) == {"gauge", "pillars", "story_normal", "story_crisis"}, out.keys()
    g = out["gauge"]
    assert g["ratio"] == round(3.4 / 6.0, 2)
    assert g["fragility_pct"] == round((1 - 1.6 / 3.4) * 100)
    assert g["r2_nasdaq"] == round(0.93 ** 2, 2)
    assert len(out["pillars"]) == 6
    assert "Nasdaq" in out["story_normal"]
    # must never crash on missing data (1 holding / no returns)
    o2 = diversification.diversification_philosophy(
        n_holdings=1, enb=None, enb_crisis=None, eff_n=None)
    assert o2["gauge"]["ratio"] is None and len(o2["pillars"]) == 6
    print("diversification_philosophy OK")


if __name__ == "__main__":
    test_pair_corr()
    test_sector_and_pairs()
    test_downside_and_rolling()
    test_philosophy()
    print("\nALL test_correlation PASSED")
