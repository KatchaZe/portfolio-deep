"""
test_advice — Damodaran action-synthesis line (domain/advice.py).
Pure/offline. The advice must (1) never raise, (2) follow the value->quality->
timing hierarchy, (3) produce a sensible Action for the main archetypes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain import advice


def row(**kw):
    base = {"composite": 3.5, "upside_pct": None, "net_upside_pct": None,
            "key_metrics": {}, "eq_verdict": None, "momentum_v2": None,
            "rev_verdict": None, "rev_implied_cagr": None, "rev_actual_1y": None}
    base.update(kw)
    return base


def test_cheap_quality_momentum():
    a = advice.build(row(upside_pct=25, net_upside_pct=20,
                         key_metrics={"spread_pct": 15}, eq_verdict="CLEAN",
                         momentum_v2={"mom_label": "Positive"}))
    assert "margin of safety" in a and "Action:" in a
    assert "ครบทั้งสามเงื่อนไข" in a
    print("cheap+quality+momentum OK")


def test_expensive_good_business():
    a = advice.build(row(upside_pct=-40, composite=4.2,
                         key_metrics={"spread_pct": 30}, eq_verdict="CLEAN",
                         momentum_v2={"mom_label": "Positive"}))
    assert "ราคาแพง" in a or "แพง" in a
    assert "ไม่เพิ่ม" in a
    print("expensive-good-business OK")


def test_preprofit_aggressive():
    a = advice.build(row(rev_implied_cagr=74.3, rev_actual_1y=55.8,
                         rev_verdict="Aggressive", composite=2.2))
    assert "pre-profit" in a and "74" in a and "56" in a or "55" in a
    print("pre-profit OK")


def test_value_trap():
    a = advice.build(row(upside_pct=30, key_metrics={"spread_pct": -5},
                         eq_verdict="LOW"))
    assert "value trap" in a
    print("value-trap OK")


def test_crash_guard_vetoes_momentum():
    a = advice.build(row(upside_pct=20, key_metrics={"spread_pct": 12},
                         eq_verdict="CLEAN",
                         momentum_v2={"mom_label": "Strong", "crash_guard": True}))
    assert "RISK-OFF" in a
    assert "ครบทั้งสามเงื่อนไข" not in a       # momentum must NOT count while vetoed
    print("crash-guard veto OK")


def test_never_raises_on_garbage():
    assert advice.build({}) == "" or isinstance(advice.build({}), str)
    assert isinstance(advice.build({"upside_pct": "x", "key_metrics": None}), str)
    print("garbage-safety OK")


if __name__ == "__main__":
    test_cheap_quality_momentum()
    test_expensive_good_business()
    test_preprofit_aggressive()
    test_value_trap()
    test_crash_guard_vetoes_momentum()
    test_never_raises_on_garbage()
    print("\nALL ADVICE TESTS PASSED ✅")
