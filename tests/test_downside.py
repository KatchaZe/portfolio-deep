"""
test_downside — Damodaran S2/S4 downside-risk helpers (semideviation, Sortino,
downside beta, portfolio returns). Pure/offline.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.engine import risk


def test_semideviation_sortino():
    assert risk.semideviation([0.01, 0.02, 0.01]) == 0.0          # no downside
    assert risk.sortino([0.01, 0.02, 0.01]) is None               # can't divide
    r = [0.02, -0.03, 0.01, -0.01, 0.00]
    assert risk.semideviation(r) > 0                              # has downside
    print("semideviation/sortino OK")


def test_downside_beta():
    mkt = [-0.02, 0.01, -0.03, 0.02, -0.01, 0.00, -0.02, 0.01, -0.04, 0.03] * 2
    asset = [1.5 * x if x < 0 else 0.5 * x for x in mkt]          # 1.5x on down days
    db = risk.downside_beta(asset, mkt)
    assert db is not None and abs(db - 1.5) < 0.2, db
    assert risk.downside_beta([0.1], [0.1]) is None               # too few obs
    print("downside_beta OK")


def test_portfolio_returns():
    rbt = {"A": [0.10, 0.20], "B": [0.00, 0.00]}
    pr = risk.portfolio_returns(rbt, ["A", "B"], {"A": 0.5, "B": 0.5})
    assert abs(pr[0] - 0.05) < 1e-9 and abs(pr[1] - 0.10) < 1e-9, pr
    assert risk.portfolio_returns({}, [], {}) == []
    print("portfolio_returns OK")


if __name__ == "__main__":
    test_semideviation_sortino()
    test_downside_beta()
    test_portfolio_returns()
    print("\nALL test_downside PASSED")
