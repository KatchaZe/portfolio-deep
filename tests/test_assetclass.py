"""test_assetclass — Damodaran S3 bond duration + rate stress. Pure/offline."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.engine import risk


def test_effective_duration():
    # construct r = -(D/10000)*dy with D=10 exactly -> recover ~10
    D = 10.0
    dy = [(-3 if i % 2 else 4) for i in range(40)]            # bps moves, varies
    r = [-(D / 10000.0) * x for x in dy]
    est = risk.effective_duration(r, dy)
    assert est is not None and abs(est - 10.0) < 0.5, est
    assert risk.effective_duration([0.1], [1]) is None        # too few
    assert risk.effective_duration([0.0] * 30, [0] * 30) is None  # no yield variance
    print("effective_duration OK")


def test_rate_stress():
    # +100bps, a 50% bond sleeve with duration 16 -> -8% on that sleeve -> -4% port
    w = {"TLT": 0.5, "AAPL": 0.5}
    dur = {"TLT": 16.0}                                        # AAPL not rate-sensitive
    assert risk.rate_stress(w, dur, 100) == -8.0
    assert risk.rate_stress({"AAPL": 1.0}, {}, 100) == 0.0     # no bonds -> no loss
    print("rate_stress OK")


if __name__ == "__main__":
    test_effective_duration()
    test_rate_stress()
    print("\nALL test_assetclass PASSED")
