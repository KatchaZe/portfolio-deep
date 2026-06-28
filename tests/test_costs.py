"""
test_costs — Damodaran S6 net-of-cost/tax helper + S16/S35 benchmark trailing
return. Pure/offline.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain import costs
from pipeline import refresh


def test_net_upside():
    # gross 40%, 0.20% round-trip cost, 15% tax on the gain:
    #   after_cost = 39.8 ; tax = 0.15*39.8 = 5.97 ; net = 33.83 -> 33.8
    assert costs.net_upside(40.0, 0.15, 20) == 33.8
    assert costs.net_upside(None, 0.15, 20) is None
    assert costs.net_upside(-10.0, 0.15, 20) == -10.2     # loss: cost only, no tax
    assert costs.net_upside(25.0, 0.0, 0) == 25.0         # no cost/tax -> unchanged
    print("net_upside OK")


def test_weighted_trailing_return():
    rows = [
        {"market_value": 100, "momentum_v2": {"roc_12m": 0.20}},
        {"market_value": 300, "momentum_v2": {"roc_12m": 0.00}},
        {"market_value": 50,  "momentum_v2": {"roc_12m": None}},    # ignored (no return)
        {"market_value": None, "momentum_v2": {"roc_12m": 0.50}},   # ignored (no MV)
    ]
    # weighted = (100*0.20 + 300*0.00) / 400 = 0.05
    assert abs(refresh.weighted_trailing_return(rows) - 0.05) < 1e-9
    assert refresh.weighted_trailing_return([]) is None
    print("weighted_trailing_return OK")


if __name__ == "__main__":
    test_net_upside()
    test_weighted_trailing_return()
    print("\nALL test_costs PASSED")
