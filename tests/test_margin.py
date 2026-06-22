"""
test_margin — operating-margin trend builder. Pure/synthetic (no network); locks
the YoY matching, threshold, and 4-quarter cap.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import margin_track


def test_yoy_trend_and_grade():
    # 5 quarters; op margin = op/rev. YoY compares same quarter prior year.
    rev = {"2025-01-31": 100, "2025-04-30": 100, "2025-07-31": 100, "2025-10-31": 100,
           "2026-01-31": 100}
    op = {"2025-01-31": 30, "2025-04-30": 30, "2025-07-31": 30, "2025-10-31": 30,
          "2026-01-31": 35}                       # +5pp YoY vs 2025-01-31
    h = margin_track.build(op, rev)
    assert len(h) == 4, h                          # capped to 4 (oldest dropped)
    last = h[-1]
    assert last["quarter"] == "2026-01-31", last
    assert abs(last["op_margin"] - 0.35) < 1e-9, last
    assert last["op_margin_yoy"] == 0.30, last
    assert last["delta_pp"] == 5.0 and last["trend"] == "expand", last
    # first three have no prior-year match -> flat, no yoy
    assert h[0]["op_margin_yoy"] is None and h[0]["trend"] == "flat", h[0]
    print("yoy trend + grade + cap OK:", [(x["quarter"], x["trend"]) for x in h])


def test_flat_and_contract_threshold():
    rev = {"2025-06-30": 200, "2026-06-30": 200}
    # +0.4pp -> within 0.5pp band -> flat
    op_flat = {"2025-06-30": 40.0, "2026-06-30": 40.8}     # 20.0% -> 20.4%
    hf = margin_track.build(op_flat, rev)
    assert hf[-1]["trend"] == "flat", hf[-1]
    # -3pp -> contract
    op_down = {"2025-06-30": 40.0, "2026-06-30": 34.0}     # 20% -> 17%
    hd = margin_track.build(op_down, rev)
    assert hd[-1]["trend"] == "contract" and hd[-1]["delta_pp"] == -3.0, hd[-1]
    print("flat/contract threshold OK")


def test_pairing_and_empty():
    # only quarters present in BOTH dicts are used; zero/negative revenue skipped
    h = margin_track.build({"2026-03-31": 10, "2026-06-30": 12},
                           {"2026-03-31": 0, "2026-06-30": 100})
    assert len(h) == 1 and h[0]["quarter"] == "2026-06-30", h
    # empty / None inputs never crash
    assert margin_track.build(None, None) == []
    assert margin_track.build({}, {"2026-06-30": 100}) == []
    print("pairing + empty-safe OK")


if __name__ == "__main__":
    test_yoy_trend_and_grade()
    test_flat_and_contract_threshold()
    test_pairing_and_empty()
    print("\nALL MARGIN-TRACK TESTS PASSED")
